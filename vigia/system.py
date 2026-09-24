"""Orquestador: arma todos los componentes a partir de la configuración."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from datetime import datetime, timedelta

from vigia import __version__
from vigia.alerts.manager import AlertManager
from vigia.alerts.model import Alert
from vigia.alerts.notifiers import NotifierHub, build_notifiers
from vigia.alerts.recorder import detect_codec, enforce_clip_storage
from vigia.alerts.store import AlertStore
from vigia.config import Config, ConfigError, save_camera_geometry
from vigia.geometry import Line, Zone
from vigia.hub import EventBroadcaster
from vigia.modes import ModeManager
from vigia.pipeline import CameraPipeline

log = logging.getLogger(__name__)


class VigiaSystem:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.data_dir = config.data_dir
        for sub in ("capturas", "clips"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

        self.broadcaster = EventBroadcaster()
        self.store = AlertStore(self.data_dir / "vigia.db")
        self.notifiers = NotifierHub(build_notifiers(config.notifications))
        self.alerts = AlertManager(self.store, self.data_dir, self.notifiers, self.broadcaster)
        self.modes = ModeManager(config.modes, state_file=self.data_dir / "estado.json")
        self.modes.add_listener(self._on_mode_change)

        codec = detect_codec(self.data_dir / "clips") if config.recording.clips else None
        self.pipelines: dict[str, CameraPipeline] = {
            cam.id: CameraPipeline(cam, config, self.modes, self.alerts, codec)
            for cam in config.cameras
            if cam.enabled
        }
        if not self.pipelines:
            raise ConfigError("Todas las cámaras están desactivadas ('activo: no')")

        self.started_at = time.time()
        self.stopping = threading.Event()
        self._maintenance: threading.Thread | None = None
        self._geometry_lock = threading.Lock()

    # --- ciclo de vida ------------------------------------------------------------------
    def start(self) -> None:
        for warning in self.config.warnings:
            log.warning("Configuración: %s", warning)
        log.info(
            "Vigía %s: %d cámara(s), modalidad '%s' (%s)",
            __version__,
            len(self.pipelines),
            self.modes.current,
            "automática" if self.modes.selection == "auto" else "manual",
        )
        for pipeline in self.pipelines.values():
            pipeline.start()
        self._maintenance = threading.Thread(target=self._maintenance_loop, name="mantenimiento", daemon=True)
        self._maintenance.start()

    def stop(self) -> None:
        if self.stopping.is_set():
            return
        self.stopping.set()
        log.info("Deteniendo Vigía...")
        for pipeline in self.pipelines.values():
            pipeline.stop()
        self.notifiers.close()
        if self._maintenance is not None:
            self._maintenance.join(timeout=5)
        self.store.close()
        log.info("Vigía detenido")

    def _maintenance_loop(self) -> None:
        last_purge = last_storage = 0.0
        while not self.stopping.wait(2.0):
            try:
                self.modes.tick()
                self.broadcaster.publish({"tipo": "estado", "estado": self.status()})
                now = time.time()
                if now - last_purge > 3600:
                    last_purge = now
                    self.purge_old()
                if now - last_storage > 60:
                    last_storage = now
                    self.enforce_storage()
            except Exception:  # noqa: BLE001
                log.exception("Error en la tarea de mantenimiento")

    def enforce_storage(self) -> None:
        limit = int(self.config.recording.max_storage_gb * 1024**3)
        deleted = enforce_clip_storage(self.data_dir / "clips", limit)
        if deleted:
            log.info("Espacio de clips: se borraron %d clips antiguos (límite %.1f GB)", deleted, self.config.recording.max_storage_gb)

    def _on_mode_change(self, old: str, new: str) -> None:
        self.broadcaster.publish({"tipo": "modalidad", "modalidades": self.modes.info()})

    # --- operaciones --------------------------------------------------------------------
    def set_mode(self, name: str) -> dict:
        version = self.modes.version
        self.modes.select(name)
        info = self.modes.info()
        if self.modes.version == version:  # cambió solo la selección (p. ej. manual -> auto): avisar igual
            self.broadcaster.publish({"tipo": "modalidad", "modalidades": info})
        return info

    def status(self) -> dict:
        return {
            "sistema": self.config.system.name,
            "version": __version__,
            "iniciado": datetime.fromtimestamp(self.started_at).isoformat(timespec="seconds"),
            "hora": datetime.now().isoformat(timespec="seconds"),
            "modalidad": self.modes.current,
            "seleccion": self.modes.selection,
            "camaras": [p.status() for p in self.pipelines.values()],
            "alertas": self.store.summary(time.time() - 86400),
        }

    def update_geometry(self, camera_id: str, zones: list[Zone], lines: list[Line]) -> dict:
        pipeline = self.pipelines.get(camera_id)
        if pipeline is None:
            raise KeyError(camera_id)
        for kind, items in (("zona", zones), ("línea", lines)):
            names = [i.name for i in items]
            dup = {n for n in names if names.count(n) > 1}
            if dup:
                raise ValueError(f"hay {kind}s con el mismo nombre: {', '.join(sorted(dup))}")
        with self._geometry_lock:
            if self.config.path is not None:
                save_camera_geometry(self.config.path, camera_id, zones, lines)
            pipeline.set_geometry(zones, lines)
        log.info("Cámara %s: %d zona(s) y %d línea(s) guardadas", pipeline.camera.name, len(zones), len(lines))
        return pipeline.status()

    def test_alert(self) -> Alert:
        pipeline = next(iter(self.pipelines.values()))
        frame = pipeline._last_annotated
        return self.alerts.test_alert(pipeline.camera.id, pipeline.camera.name, self.modes.current, frame, time.time())

    def purge_old(self) -> None:
        """Borra alertas y evidencias más antiguas que 'retencion_dias'."""
        days = self.config.system.retention_days
        if days <= 0:
            return
        limit = datetime.now() - timedelta(days=days)
        removed = self.store.purge_older_than(limit.timestamp())
        for rel in removed:
            (self.data_dir / rel).unlink(missing_ok=True)
        for sub in ("capturas", "clips"):
            base = self.data_dir / sub
            if not base.is_dir():
                continue
            for folder in base.iterdir():
                try:
                    day = datetime.strptime(folder.name, "%Y-%m-%d")
                except ValueError:
                    continue
                if folder.is_dir() and day < limit - timedelta(days=1):
                    shutil.rmtree(folder, ignore_errors=True)
        if removed:
            log.info("Retención: se borraron %d evidencias antiguas", len(removed))
