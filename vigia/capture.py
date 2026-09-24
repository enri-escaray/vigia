"""Captura de video desde webcams, cámaras IP (RTSP/HTTP) o archivos.

Cada fuente se lee en su propio hilo y solo se conserva el último cuadro: así
el análisis nunca trabaja con imágenes atrasadas. Si la conexión se cae, se
reintenta indefinidamente.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from vigia.alerts.recorder import cv_path

log = logging.getLogger(__name__)

_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
OPEN_TIMEOUT_MS = 8000
READ_TIMEOUT_MS = 5000  # una lectura de red que tarda más se da por fallida (y se reintenta)


def source_kind(source: str | int) -> str:
    if isinstance(source, int):
        return "webcam"
    if _URL.match(source):
        return "stream"
    return "archivo"


class VideoSource:
    def __init__(
        self,
        source: str | int,
        name: str = "",
        loop: bool = True,
        realtime: bool = True,
        reconnect_delay: float = 3.0,
        rtsp_tcp: bool = True,
        base_dir: Path | None = None,
    ) -> None:
        self.source = source
        self.name = name or str(source)
        self.kind = source_kind(source)
        if self.kind == "archivo":
            path = Path(str(source))
            if not path.is_absolute() and base_dir is not None:
                path = base_dir / path
            self.path: Path | None = path
        else:
            self.path = None
        self.loop = loop
        self.realtime = realtime
        self.reconnect_delay = reconnect_delay
        self.rtsp_tcp = rtsp_tcp
        # HLS (.m3u8) entrega el video por segmentos de varios segundos: los
        # cuadros llegan en ráfagas y entre ráfagas no hay nada. Se reproducen a
        # su velocidad normal y se tolera un hueco mayor antes de dar la señal por perdida.
        self.is_hls = self.kind == "stream" and ".m3u8" in str(source).lower()
        self.max_gap = 12.0 if self.is_hls else 3.0
        # Sin imagen durante este tiempo se reconecta, con margen para que un corte
        # breve se recupere antes de que el panel (max_gap) o las alertas lo noten.
        self.stall_after = 0.75 * self.max_gap

        self.status = "conectando"
        self.error = ""
        self.finished = False
        self.input_fps = 0.0
        self.resolution: tuple[int, int] | None = None

        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- ciclo de vida ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"captura-{self.name}", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def read(self, after_seq: int, timeout: float = 1.0) -> tuple[int, np.ndarray] | None:
        """Devuelve el cuadro más reciente si es más nuevo que `after_seq`."""
        with self._cond:
            if self._seq <= after_seq:
                self._cond.wait(timeout)
            if self._seq <= after_seq or self._frame is None:
                return None
            return self._seq, self._frame

    # --- hilo de captura ----------------------------------------------------------------
    def _open(self) -> cv2.VideoCapture | None:
        if self.kind == "webcam":
            cap = cv2.VideoCapture(int(self.source), cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(int(self.source))
            if not cap.isOpened() and os.name == "nt":
                cap = cv2.VideoCapture(int(self.source))
        elif self.kind == "stream":
            if self.rtsp_tcp and str(self.source).lower().startswith("rtsp") and "OPENCV_FFMPEG_CAPTURE_OPTIONS" not in os.environ:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
            params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS, cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS]
            try:
                cap = cv2.VideoCapture(str(self.source), cv2.CAP_FFMPEG, params)
            except (cv2.error, TypeError):
                cap = cv2.VideoCapture(str(self.source))
        else:
            assert self.path is not None
            if not self.path.is_file():
                self.error = f"no existe el archivo {self.path}"
                return None
            cap = cv2.VideoCapture(cv_path(self.path))
        if not cap.isOpened():
            cap.release()
            return None
        if self.kind != "archivo":
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _publish(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame = frame
            self._seq += 1
            self._cond.notify_all()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.status = "conectando"
            cap = self._open()
            if cap is None:
                self.status = "sin señal"
                self.error = self.error or "no se pudo abrir la fuente de video"
                log.warning("Cámara %s: %s; reintento en %.0f s", self.name, self.error, self.reconnect_delay)
                self._stop.wait(self.reconnect_delay)
                continue
            log.info("Cámara %s conectada (%s)", self.name, "HLS" if self.is_hls else self.kind)
            self.status = "en línea"
            self.error = ""
            paced = (self.kind == "archivo" and self.realtime) or self.is_hls
            native_fps = cap.get(cv2.CAP_PROP_FPS) if paced else 0.0
            if not 1.0 <= native_fps <= 120.0:
                native_fps = 25.0 if self.kind == "archivo" else 15.0
            next_due = time.monotonic()
            failures = 0
            last_ok = time.monotonic()
            count, window_start = 0, time.monotonic()
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if self.kind == "archivo":
                        if not self.loop:
                            self.finished = True
                            self.status = "finalizado"
                            cap.release()
                            log.info("Cámara %s: fin del video", self.name)
                            return
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        if failures < 3:
                            continue
                    # Cada lectura fallida puede tardar hasta el tiempo límite de
                    # lectura: se cuenta el tiempo sin imagen, no los intentos.
                    elif failures < 10 and time.monotonic() - last_ok < self.stall_after:
                        self._stop.wait(0.05)
                        continue
                    self.error = "se perdió la señal"
                    break
                failures = 0
                last_ok = time.monotonic()
                self.resolution = (frame.shape[1], frame.shape[0])
                self._publish(frame)
                count += 1
                elapsed = time.monotonic() - window_start
                if elapsed >= 2.0:
                    self.input_fps = count / elapsed
                    count, window_start = 0, time.monotonic()
                if paced:
                    next_due += 1.0 / native_fps
                    delay = next_due - time.monotonic()
                    if delay > 0:
                        self._stop.wait(delay)
                    elif delay < -1.0:
                        next_due = time.monotonic()  # se atrasó (red lenta): no acumular demora
            cap.release()
            if not self._stop.is_set():
                # La fuente andaba: el primer reintento es casi inmediato (si vuelve a
                # fallar la apertura, se espera reconnect_delay entre intentos).
                delay = min(1.0, self.reconnect_delay)
                self.status = "sin señal"
                log.warning("Cámara %s: %s; reconectando en %.0f s", self.name, self.error, delay)
                self._stop.wait(delay)
        self.status = "detenida"


def probe_webcams(max_index: int = 5) -> list[dict]:
    """Busca webcams locales (índices 0..max_index)."""
    found = []
    for index in range(max_index + 1):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(index)
        try:
            if not cap.isOpened():
                continue
            ok, frame = cap.read()
            if ok and frame is not None:
                found.append({"indice": index, "resolucion": f"{frame.shape[1]}x{frame.shape[0]}"})
        finally:
            cap.release()
    return found
