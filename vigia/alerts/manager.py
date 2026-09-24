"""Convierte los disparos de las reglas en alertas: anti-repetición, evidencias,
registro, aviso al panel web y notificaciones."""

from __future__ import annotations

import itertools
import logging
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from vigia.alerts.model import Alert
from vigia.alerts.notifiers import NotifierHub
from vigia.alerts.recorder import ClipRecorder
from vigia.alerts.store import AlertStore
from vigia.annotate import Annotator
from vigia.hub import EventBroadcaster
from vigia.rules.base import RuleHit
from vigia.types import Severity

log = logging.getLogger(__name__)


class AlertManager:
    def __init__(
        self,
        store: AlertStore,
        data_dir: Path,
        notifiers: NotifierHub,
        broadcaster: EventBroadcaster,
        jpeg_quality: int = 85,
    ) -> None:
        self.store = store
        self.data_dir = data_dir
        self.notifiers = notifiers
        self.broadcaster = broadcaster
        self.jpeg_quality = jpeg_quality
        self._last: dict[tuple[str, str, str], float] = {}
        self._lock = threading.Lock()
        self._snapshot_seq = itertools.count(1)

    def process(
        self,
        hit: RuleHit,
        camera_id: str,
        camera_name: str,
        mode: str,
        frame: np.ndarray | None,
        t: float,
        recorder: ClipRecorder | None = None,
    ) -> Alert | None:
        key = (camera_id, hit.rule_id, hit.key)
        with self._lock:
            last = self._last.get(key)
            if last is not None and t - last < hit.cooldown:
                return None
            self._last[key] = t
            if len(self._last) > 5000:
                self._last = {k: v for k, v in self._last.items() if t - v < 3600}

        alert = Alert(
            ts=t,
            camera_id=camera_id,
            camera_name=camera_name,
            mode=mode,
            rule_id=hit.rule_id,
            rule_type=hit.rule_type,
            title=hit.title,
            message=hit.message,
            severity=hit.severity,
            zone=hit.zone,
            track_ids=[tr.id for tr in hit.tracks],
            categories=sorted({tr.category for tr in hit.tracks}),
            extra=hit.extra,
        )
        if frame is not None:
            alert.snapshot = self._save_snapshot(Annotator.highlight(frame, hit.tracks, hit.title), camera_id, t)
        if recorder is not None:
            alert.clip = recorder.trigger(t)
        return self.register(alert)

    def register(self, alert: Alert) -> Alert:
        self.store.add(alert)
        log.warning("ALERTA #%s %s", alert.id, alert.text())
        self.broadcaster.publish({"tipo": "alerta", "alerta": alert.to_dict()})
        self.notifiers.dispatch(alert, self.snapshot_path(alert))
        return alert

    def snapshot_path(self, alert: Alert) -> Path | None:
        return self.data_dir / alert.snapshot if alert.snapshot else None

    def acknowledge(self, alert_id: int) -> bool:
        changed = self.store.acknowledge(alert_id)
        if changed:
            alert = self.store.get(alert_id)
            if alert:
                self.broadcaster.publish({"tipo": "alerta_actualizada", "alerta": alert.to_dict()})
        return changed

    def acknowledge_all(self) -> int:
        count = self.store.acknowledge_all()
        if count:
            self.broadcaster.publish({"tipo": "alertas_reconocidas", "cantidad": count})
        return count

    def test_alert(self, camera_id: str, camera_name: str, mode: str, frame: np.ndarray | None, t: float) -> Alert:
        alert = Alert(
            ts=t,
            camera_id=camera_id,
            camera_name=camera_name,
            mode=mode,
            rule_id="prueba",
            rule_type="prueba",
            title="Alerta de prueba",
            message="Si recibió este mensaje, las notificaciones funcionan.",
            severity=Severity.CRITICA,
        )
        if frame is not None:
            alert.snapshot = self._save_snapshot(Annotator.highlight(frame, [], alert.title), camera_id, t)
        return self.register(alert)

    def _save_snapshot(self, image: np.ndarray, camera_id: str, t: float) -> str | None:
        stamp = datetime.fromtimestamp(t)
        seq = next(self._snapshot_seq)  # varias alertas pueden compartir el mismo instante
        rel = f"capturas/{stamp:%Y-%m-%d}/{stamp:%H%M%S}_{int(t * 1000) % 1000:03d}_{camera_id}_{seq}.jpg"
        path = self.data_dir / rel
        try:
            ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if not ok:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(buf.tobytes())
            return rel
        except OSError:
            log.exception("No se pudo guardar la captura %s", rel)
            return None
