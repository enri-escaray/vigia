"""Procesamiento de una cámara: captura -> detección -> seguimiento -> reglas -> alertas."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import cv2
import numpy as np

from vigia.alerts.manager import AlertManager
from vigia.alerts.model import Alert
from vigia.alerts.recorder import ClipRecorder, Codec
from vigia.annotate import Annotator, placeholder
from vigia.capture import VideoSource
from vigia.config import CameraConfig, Config
from vigia.geometry import Line, Zone
from vigia.hub import FrameHub
from vigia.modes import ModeManager
from vigia.rules import FrameContext, RuleEngine
from vigia.vision.detectors import Detector, MotionDetector, create_detector
from vigia.vision.tamper import STATE_LABELS, TamperDetector
from vigia.vision.tracker import StaticClutter, Tracker

log = logging.getLogger(__name__)

MOTION_WIDTH = 160  # ancho de la imagen reducida para medir movimiento global
IDLE_MOTION = 0.004  # fracción de píxeles cambiados por debajo de la cual la escena está "quieta"
OFFLINE_AFTER = 3.0  # segundos sin cuadros para considerar la cámara sin señal
STARTUP_GRACE = 15.0  # margen para la primera conexión (RTSP/HLS pueden tardar en abrir)
BANNER_SECONDS = 4.0
_DETECTOR_LOCK = threading.Lock()


def is_ignore_zone(name: str) -> bool:
    """Zonas de exclusión: lo que se detecta dentro se descarta."""
    return name.lower().startswith(("ignorar", "excluir"))


def _anchor_norm(bbox, w: int, h: int) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2 / w, bbox[3] / h)


class CameraPipeline:
    def __init__(
        self,
        camera: CameraConfig,
        config: Config,
        modes: ModeManager,
        alerts: AlertManager,
        codec: Codec | None,
        detector_factory: Callable[[], Detector] | None = None,
    ) -> None:
        self.camera = camera
        self.config = config
        self.modes = modes
        self.alerts = alerts
        self._detector_factory = detector_factory or (
            lambda: create_detector(camera.detector or config.detector, config.base_dir)
        )
        self.detector: Detector | None = None

        self.source = VideoSource(
            camera.source,
            name=camera.name,
            loop=camera.loop,
            rtsp_tcp=camera.rtsp_tcp,
            base_dir=config.base_dir,
        )
        tc = camera.tracker or config.tracker
        self.tracker = Tracker(
            tc.iou_threshold,
            tc.max_age,
            tc.min_hits,
            tc.max_age_by_category,
            tc.max_distance,
            tc.max_distance_by_category,
            tc.min_hits_by_category,
        )
        self.tamper = TamperDetector()
        self.hub = FrameHub()
        self.recorder = ClipRecorder(
            camera.id,
            config.data_dir,
            codec,
            enabled=config.recording.clips,
            pre=config.recording.pre_seconds,
            post=config.recording.post_seconds,
            max_len=config.recording.max_seconds,
            max_width=config.recording.clip_width,
        )
        self.annotator = Annotator(camera.name)
        self.zones: dict[str, Zone] = {z.name: z for z in camera.zones}
        self.lines: dict[str, Line] = {ln.name: ln for ln in camera.lines}

        self.engine: RuleEngine | None = None
        self._engine_key: tuple[str, int] | None = None
        self._geometry_version = 0
        self._moving_only = set(tc.moving_only)
        self._static_confidence = tc.static_confidence
        self.clutter = StaticClutter(self._moving_only, learn_after=tc.static_after, confident=tc.static_confidence)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._prev_small: np.ndarray | None = None
        self._last_detect = float("-inf")
        self._last_proc: float | None = None
        self._last_frame_time: float | None = None
        self._offline_since: float | None = None
        self._last_offline_eval = 0.0
        self._last_annotated: np.ndarray | None = None
        self._banner: tuple[str, float] | None = None
        self._started_at = time.time()

        self.fps = 0.0
        self.motion_level = 0.0
        self.error = ""

    # --- ciclo de vida ------------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"camara-{self.camera.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.source.stop()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self.recorder.flush()

    def set_geometry(self, zones: list[Zone], lines: list[Line]) -> None:
        """Actualiza zonas y líneas en caliente (desde el editor web)."""
        self.zones = {z.name: z for z in zones}
        self.lines = {ln.name: ln for ln in lines}
        self.camera.zones = list(zones)
        self.camera.lines = list(lines)
        self._geometry_version += 1

    def _ensure_detector(self) -> Detector:
        if self.detector is None:
            with _DETECTOR_LOCK:  # evita descargas simultáneas del modelo desde varias cámaras
                self.detector = self._detector_factory()
            log.info(
                "Cámara %s: detector '%s' (categorías: %s)",
                self.camera.name,
                self.detector_label,
                ", ".join(sorted(self.detector.categories)),
            )
        return self.detector

    @property
    def detector_label(self) -> str | None:
        """Nombre del detector; en YOLO, el del modelo (p. ej. "yolo11s")."""
        if self.detector is None:
            return None
        model = getattr(self.detector, "model_path", None)
        return model.stem if model is not None else self.detector.name

    def _ensure_engine(self) -> RuleEngine:
        mode = self.camera.mode or self.modes.current
        key = (mode, self._geometry_version)
        if self.engine is None or self._engine_key != key:
            definition = self.modes.definition(mode)
            self.engine = RuleEngine(mode, definition.rules, self.camera.id, self.zones, self.lines)
            self._engine_key = key
            log.info(
                "Cámara %s: modalidad '%s' con %d regla(s): %s",
                self.camera.name,
                mode,
                len(self.engine.rules),
                ", ".join(r.id.split(".", 1)[1] for r in self.engine.rules) or "ninguna",
            )
        return self.engine

    # --- bucle principal ----------------------------------------------------------------
    def _run(self) -> None:
        self.source.start()  # se conecta mientras se carga el modelo
        try:
            self._ensure_detector()
        except Exception as exc:  # noqa: BLE001
            self.error = f"detector: {exc}"
            log.error("Cámara %s: no se pudo iniciar el detector: %s", self.camera.name, exc)
            self.detector = MotionDetector(min_area=self.config.detector.motion_min_area)
        self._started_at = time.time()  # la espera de conexión cuenta desde aquí
        last_seq = 0
        period = 1.0 / self.camera.fps
        while not self._stop.is_set():
            item = self.source.read(last_seq, timeout=0.5)
            now = time.time()
            if item is None:
                self._idle(now)
                continue
            last_seq, frame = item
            self._last_frame_time = now
            if self._offline_since is not None:
                log.info("Cámara %s: señal recuperada", self.camera.name)
                self._offline_since = None
            started = time.monotonic()
            try:
                self.process_frame(frame, now)
            except Exception:  # noqa: BLE001 - se registra y se sigue con el siguiente cuadro
                log.exception("Cámara %s: error procesando un cuadro", self.camera.name)
                self._stop.wait(0.5)
            wait = period - (time.monotonic() - started)
            if wait > 0:
                self._stop.wait(wait)

    def _idle(self, now: float) -> None:
        """Sin cuadros nuevos: fin de video o posible pérdida de señal."""
        self.recorder.tick(now)
        if self.source.finished:
            if now - self._last_offline_eval >= 2.0:
                self._last_offline_eval = now
                self._publish_placeholder("VIDEO FINALIZADO", self.camera.name)
            return
        reference = self._last_frame_time or self._started_at
        never_connected = self._last_frame_time is None
        if now - reference < (STARTUP_GRACE if never_connected else max(OFFLINE_AFTER, self.source.max_gap)):
            if never_connected and now - self._last_offline_eval >= 1.0:
                self._last_offline_eval = now
                self._publish_placeholder("CONECTANDO...", self.camera.name)
            return
        if self._offline_since is None:
            self._offline_since = reference
            # Lo que se seguía ya no vale: al volver la imagen no debe mezclarse
            # con lo nuevo, y mientras tanto no hay objetos a la vista.
            self.tracker.reset()
            log.warning("Cámara %s: sin señal", self.camera.name)
        if now - self._last_offline_eval < 1.0:
            return
        self._last_offline_eval = now
        self._publish_placeholder("SIN SEÑAL", f"{self.camera.name} · {now - self._offline_since:.0f} s")
        engine = self._ensure_engine()
        width, height = self.source.resolution or (1280, 720)
        ctx = FrameContext(
            t=now,
            frame_size=(width, height),
            tracks=[],
            zones=self.zones,
            lines=self.lines,
            online=False,
            offline_since=self._offline_since,
        )
        for hit in engine.evaluate(ctx):
            self._emit(hit, self._last_annotated, now)

    def _publish_placeholder(self, title: str, subtitle: str) -> None:
        width, height = self.source.resolution or (1280, 720)
        scale = min(1.0, self.camera.max_width / width)
        img = placeholder(int(width * scale), int(height * scale), title, subtitle)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            self.hub.publish(buf.tobytes())

    # --- procesamiento de un cuadro -----------------------------------------------------
    def process_frame(self, frame: np.ndarray, t: float) -> list[Alert]:
        h, w = frame.shape[:2]
        if w > self.camera.max_width:
            scale = self.camera.max_width / w
            frame = cv2.resize(frame, (self.camera.max_width, int(round(h * scale))), interpolation=cv2.INTER_AREA)
            h, w = frame.shape[:2]

        if self._last_proc is not None and t > self._last_proc:
            inst = 1.0 / (t - self._last_proc)
            self.fps = inst if self.fps == 0 else 0.9 * self.fps + 0.1 * inst
        self._last_proc = t

        engine = self._ensure_engine()
        detector = self._ensure_detector()
        motion_mask = self._motion(frame)

        hits = []
        if self._detection_due(t, detector):
            self._last_detect = t
            try:
                detections = detector.detect(frame)
                self.error = ""
            except Exception as exc:  # noqa: BLE001
                detections = []
                if self.error != f"detector: {exc}":
                    log.exception("Cámara %s: fallo del detector", self.camera.name)
                self.error = f"detector: {exc}"
            detections, pinned = self.clutter.filter(self._without_ignored(detections, w, h), t)
            self.tracker.update(detections, t, (w, h), pinned)
            self.clutter.learn(self.tracker.tracks, t)
            foreground = [tr.bbox for tr in self.tracker.tracks if t - tr.last_seen <= 1.0]
            tamper = self.tamper.update(frame, t, ignore=foreground)
            ctx = FrameContext(
                t=t,
                frame_size=(w, h),
                tracks=self.active_tracks(),
                zones=self.zones,
                lines=self.lines,
                tamper=tamper,
                motion=motion_mask,
            )
            hits = engine.evaluate(ctx)

        visible = [tr for tr in self.active_tracks() if t - tr.last_seen <= max(0.6, 2.5 / self.camera.detect_fps)]
        mode = self.modes.definition(engine.mode)
        banner = self._banner[0] if self._banner and t - self._banner[1] <= BANNER_SECONDS else None
        annotated = self.annotator.draw(
            frame,
            tracks=visible,
            zones=self.zones.values(),
            lines=self.lines.values(),
            mode_label=mode.label,
            mode_color=mode.color,
            fps=self.fps,
            now=t,
            banner=banner,
        )
        self._last_annotated = annotated

        alerts = [a for a in (self._emit(hit, annotated, t) for hit in hits) if a is not None]

        ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, self.config.recording.jpeg_quality])
        if ok:
            jpeg = buf.tobytes()
            self.hub.publish(jpeg, frame)
            self.recorder.push(t, jpeg)
        return alerts

    def active_tracks(self) -> list:
        """Objetos confirmados que cuentan para las reglas y se dibujan. En las
        categorías "solo en movimiento" (vehículos, por defecto) se excluyen los
        que nunca se movieron y el modelo ve con poca confianza: carteles,
        balizas, etc. confundidos con un auto."""
        return [
            tr
            for tr in self.tracker.confirmed()
            if tr.category not in self._moving_only or tr.moved or tr.recent_confidence >= self._static_confidence
        ]

    def _without_ignored(self, detections: list, w: int, h: int) -> list:
        """Descarta lo detectado dentro de las zonas de exclusión (nombre 'ignorar...')."""
        ignored = [z for z in self.zones.values() if is_ignore_zone(z.name)]
        if not ignored:
            return detections
        return [d for d in detections if not any(z.contains(_anchor_norm(d.bbox, w, h)) for z in ignored)]

    def _emit(self, hit, frame: np.ndarray | None, t: float) -> Alert | None:
        alert = self.alerts.process(
            hit,
            camera_id=self.camera.id,
            camera_name=self.camera.name,
            mode=self.engine.mode if self.engine else (self.camera.mode or self.modes.current),
            frame=frame,
            t=t,
            recorder=self.recorder,
        )
        if alert is not None:
            self._banner = (alert.title, t)
        return alert

    def _motion(self, frame: np.ndarray) -> np.ndarray | None:
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (MOTION_WIDTH, max(1, int(h * MOTION_WIDTH / w))), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0)
        prev, self._prev_small = self._prev_small, small
        if prev is None or prev.shape != small.shape:
            self.motion_level = 0.0
            return None
        mask = (cv2.absdiff(small, prev) > 25).astype(np.uint8)
        self.motion_level = float(mask.mean())
        return mask

    def _detection_due(self, t: float, detector: Detector) -> bool:
        interval = 1.0 / self.camera.detect_fps
        if (
            self.camera.idle_fps > 0
            and not isinstance(detector, MotionDetector)
            and not self.tracker.tracks
            and self.motion_level < IDLE_MOTION
        ):
            interval = max(interval, 1.0 / self.camera.idle_fps)  # escena quieta: se ahorra CPU
        return t - self._last_detect >= interval * 0.95

    # --- estado para el panel -----------------------------------------------------------
    def status(self) -> dict:
        tamper = self.tamper.status
        online = self._offline_since is None and self.source.status == "en línea"
        return {
            "id": self.camera.id,
            "nombre": self.camera.name,
            "fuente": self.camera.safe_source,
            "tipo_fuente": self.source.kind,
            "estado": "sin señal" if self._offline_since is not None else self.source.status,
            "en_linea": online,
            "error": self.error or self.source.error,
            "fps": round(self.fps, 1),
            "fps_entrada": round(self.source.input_fps, 1),
            "resolucion": list(self.source.resolution) if self.source.resolution else None,
            "detector": self.detector_label,
            "objetos": len(self.active_tracks()),
            "fijos_descartados": self.clutter.learned,
            "movimiento": round(self.motion_level, 3),
            "integridad": tamper.state,
            "integridad_texto": STATE_LABELS.get(tamper.state, tamper.state),
            "modalidad": self.camera.mode or self.modes.current,
            "modalidad_fija": bool(self.camera.mode),
            "reglas": [r.id.split(".", 1)[1] for r in self.engine.rules] if self.engine else [],
            "zonas": [z.to_dict() for z in self.zones.values()],
            "lineas": [ln.to_dict() for ln in self.lines.values()],
        }
