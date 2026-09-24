"""Clips de evidencia: se guardan los segundos previos y posteriores a una alerta."""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class Codec:
    api: int  # backend de OpenCV (cv2.CAP_*)
    fourcc: str
    ext: str
    max_width: int | None = None  # para códecs lentos se reduce la resolución del clip
    playable: bool = True  # si el navegador lo reproduce


# Orden de preferencia: primero H.264 (rápido y reproducible en el navegador);
# en Windows, Media Foundation lo codifica sin bibliotecas extra.
_CANDIDATES = (
    *((Codec(cv2.CAP_MSMF, "avc1", ".mp4"),) if os.name == "nt" else ()),
    Codec(cv2.CAP_FFMPEG, "avc1", ".mp4"),
    Codec(cv2.CAP_FFMPEG, "VP80", ".webm", max_width=640),
    Codec(cv2.CAP_ANY, "mp4v", ".mp4", playable=False),
    Codec(cv2.CAP_ANY, "MJPG", ".avi", playable=False),
)
_codec_lock = threading.Lock()
_codec_cache: dict[str, Codec | None] = {}


def cv_path(path: Path) -> str:
    """OpenCV en Windows no abre rutas con caracteres no ASCII: se intenta con una relativa."""
    text = str(path)
    if text.isascii():
        return text
    try:
        rel = os.path.relpath(path)
        if rel.isascii():
            return rel
    except ValueError:
        pass
    return text


def _open_writer(codec: Codec, path: Path, fps: float, size: tuple[int, int]) -> cv2.VideoWriter:
    return cv2.VideoWriter(cv_path(path), codec.api, cv2.VideoWriter_fourcc(*codec.fourcc), fps, size)


def detect_codec(work_dir: Path) -> Codec | None:
    """Prueba (una sola vez) qué códec de video puede escribir esta instalación de OpenCV."""
    with _codec_lock:
        if "codec" in _codec_cache:
            return _codec_cache["codec"]
        work_dir.mkdir(parents=True, exist_ok=True)
        frame = np.zeros((48, 64, 3), np.uint8)
        found = None
        for codec in _CANDIDATES:
            probe = work_dir / f"_prueba_codec{codec.ext}"
            try:
                writer = _open_writer(codec, probe, 10, (64, 48))
                ok = writer.isOpened()
                if ok:
                    for _ in range(3):
                        writer.write(frame)
                writer.release()
                ok = ok and probe.exists() and probe.stat().st_size > 0
            except cv2.error:
                ok = False
            finally:
                try:
                    probe.unlink(missing_ok=True)
                except OSError:
                    pass
            if ok:
                found = codec
                break
        _codec_cache["codec"] = found
        if found is None:
            log.warning("No hay códec de video disponible: las alertas guardarán solo la captura")
        elif not found.playable:
            log.warning("Clips en %s (%s): el navegador no los reproduce, se podrán descargar", found.fourcc, found.ext)
        else:
            log.info("Clips de video: %s (%s)", found.fourcc, found.ext)
        return found


@dataclass
class _ClipJob:
    start: float
    end: float
    path: Path
    rel: str
    frames: list[tuple[float, bytes]]


class ClipRecorder:
    """Mantiene un búfer circular de cuadros JPEG y, al dispararse una alerta,
    arma un clip con los `pre` segundos anteriores y los `post` siguientes. Si
    llegan más alertas durante el clip, este se extiende (hasta `max_len`)."""

    def __init__(
        self,
        camera_id: str,
        data_dir: Path,
        codec: Codec | None,
        enabled: bool = True,
        pre: float = 5.0,
        post: float = 5.0,
        max_len: float = 30.0,
        max_width: int | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.data_dir = data_dir
        self.codec = codec
        self.enabled = enabled and codec is not None
        self.pre = pre
        self.post = post
        self.max_len = max_len
        widths = [w for w in (max_width, codec.max_width if codec else None) if w]
        self.max_width = min(widths) if widths else None
        self._buffer: deque[tuple[float, bytes]] = deque()
        self._job: _ClipJob | None = None
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def push(self, t: float, jpeg: bytes) -> None:
        if not self.enabled:
            return
        finished = None
        with self._lock:
            self._buffer.append((t, jpeg))
            while self._buffer and self._buffer[0][0] < t - self.pre:
                self._buffer.popleft()
            if self._job is not None:
                if t <= self._job.end:
                    self._job.frames.append((t, jpeg))
                if t >= self._job.end:
                    finished, self._job = self._job, None
        if finished is not None:
            self._write_async(finished)

    def tick(self, now: float) -> None:
        """Cierra el clip en curso si ya pasó su final aunque no lleguen cuadros
        (cámara sin señal): si no, quedaría abierto hasta que vuelva la imagen."""
        with self._lock:
            if self._job is None or now < self._job.end:
                return
            finished, self._job = self._job, None
        self._write_async(finished)

    def trigger(self, t: float) -> str | None:
        """Pide un clip alrededor del instante t. Devuelve su ruta relativa."""
        if not self.enabled or self.codec is None:
            return None
        with self._lock:
            job = self._job
            if job is not None:
                job.end = min(max(job.end, t + self.post), job.start + self.max_len)
                return job.rel
            stamp = datetime.fromtimestamp(t)
            rel = f"clips/{stamp:%Y-%m-%d}/{stamp:%H%M%S}_{int(t * 1000) % 1000:03d}_{self.camera_id}{self.codec.ext}"
            self._job = _ClipJob(start=t - self.pre, end=t + self.post, path=self.data_dir / rel, rel=rel, frames=list(self._buffer))
            return rel

    def flush(self) -> None:
        """Cierra el clip en curso (al apagar el sistema) y espera las escrituras."""
        with self._lock:
            job, self._job = self._job, None
        if job is not None:
            self._write(job)
        for th in self._threads:
            th.join(timeout=15)

    def _write_async(self, job: _ClipJob) -> None:
        th = threading.Thread(target=self._write, args=(job,), name=f"clip-{self.camera_id}", daemon=True)
        th.start()
        self._threads = [x for x in self._threads if x.is_alive()] + [th]

    def _write(self, job: _ClipJob) -> None:
        assert self.codec is not None
        try:
            frames = job.frames
            if len(frames) < 2:
                return
            duration = frames[-1][0] - frames[0][0]
            fps = (len(frames) - 1) / duration if duration > 0 else 10.0
            fps = min(max(fps, 1.0), 30.0)
            first = _decode(frames[0][1])
            if first is None:
                return
            h, w = first.shape[:2]
            if self.max_width and w > self.max_width:
                h, w = int(h * self.max_width / w), self.max_width
            w, h = w - w % 2, h - h % 2  # H.264 exige dimensiones pares
            job.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = job.path.with_name(f"{job.path.stem}.parcial{job.path.suffix}")
            writer = _open_writer(self.codec, tmp, fps, (w, h))
            if not writer.isOpened():
                log.error("No se pudo crear el clip %s", job.rel)
                return
            for _, data in frames:
                img = _decode(data)
                if img is None:
                    continue
                if img.shape[:2] != (h, w):
                    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
                writer.write(img)
            writer.release()
            os.replace(tmp, job.path)
            log.info("Clip guardado: %s (%.1f s)", job.rel, duration)
        except Exception:  # noqa: BLE001
            log.exception("No se pudo escribir el clip %s", job.rel)


def _decode(data: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


_CLIP_SUFFIXES = {".mp4", ".webm", ".avi"}


def enforce_clip_storage(clips_dir: Path, max_bytes: int) -> int:
    """Como un grabador (DVR): si los clips superan `max_bytes`, borra los más
    viejos. Devuelve cuántos borró. Los clips que se están escribiendo no se tocan."""
    if max_bytes <= 0 or not clips_dir.is_dir():
        return 0
    clips = []
    for path in clips_dir.rglob("*"):
        if path.suffix.lower() in _CLIP_SUFFIXES and ".parcial" not in path.name and path.is_file():
            stat = path.stat()
            clips.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in clips)
    deleted = 0
    for _, size, path in sorted(clips):
        if total <= max_bytes:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        deleted += 1
    for folder in clips_dir.iterdir():
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    return deleted
