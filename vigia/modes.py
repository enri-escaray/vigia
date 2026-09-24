"""Modalidades de vigilancia: qué reglas están activas y cuándo.

La modalidad puede elegirse a mano (queda fija hasta volver a "auto") o
resolverse por horario. La selección manual se guarda en disco para que un
corte de luz no desarme el sistema.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from vigia.config import ModeConfig, ModesConfig

log = logging.getLogger(__name__)

AUTO = "auto"


class ModeManager:
    def __init__(self, cfg: ModesConfig, state_file: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self._clock = clock
        self._lock = threading.RLock()
        self._state_file = state_file
        self._listeners: list[Callable[[str, str], None]] = []
        self._override: str | None = None
        selection = self._load_state() or cfg.initial
        if selection != AUTO and selection in cfg.definitions:
            self._override = selection
        self._current = self._resolve(self._clock())
        self.version = 0

    # --- consulta ---------------------------------------------------------------------
    @property
    def current(self) -> str:
        return self._current

    @property
    def selection(self) -> str:
        return self._override or AUTO

    def definition(self, name: str | None = None) -> ModeConfig:
        return self.cfg.definitions[name or self._current]

    def info(self) -> dict:
        with self._lock:
            return {
                "actual": self._current,
                "seleccion": self.selection,
                "automatica": self._override is None,
                "por_defecto": self.cfg.default,
                "horario": [e.to_dict() for e in self.cfg.schedule],
                "modalidades": [
                    {
                        "nombre": m.name,
                        "etiqueta": m.label,
                        "descripcion": m.description,
                        "color": m.color,
                        "solo_camaras": m.name in self.cfg.camera_only,
                        "reglas": [
                            {"tipo": r.type, "nombre": r.name, "titulo": r.title, "severidad": r.severity.label, "zonas": r.zones}
                            for r in m.rules
                        ],
                    }
                    for m in self.cfg.definitions.values()
                ],
            }

    # --- cambios ------------------------------------------------------------------------
    def add_listener(self, callback: Callable[[str, str], None]) -> None:
        self._listeners.append(callback)

    def select(self, name: str) -> str:
        """Elige una modalidad a mano, o ``auto`` para volver al horario."""
        if name != AUTO and name not in self.cfg.definitions:
            opciones = ", ".join([AUTO, *self.cfg.definitions])
            raise ValueError(f"modalidad desconocida '{name}' (opciones: {opciones})")
        with self._lock:
            self._override = None if name == AUTO else name
            self._save_state()
        log.info("Modalidad seleccionada: %s", name)
        self.tick()
        return self._current

    def tick(self, now: float | None = None) -> bool:
        """Recalcula la modalidad vigente; devuelve True si cambió."""
        with self._lock:
            new = self._resolve(now if now is not None else self._clock())
            if new == self._current:
                return False
            old, self._current = self._current, new
            self.version += 1
            origin = "manual" if self._override else "horario"
        log.warning("Modalidad: %s -> %s (%s)", old, new, origin)
        for callback in self._listeners:
            try:
                callback(old, new)
            except Exception:  # noqa: BLE001
                log.exception("Error notificando el cambio de modalidad")
        return True

    def _resolve(self, now: float) -> str:
        if self._override:
            return self._override
        dt = datetime.fromtimestamp(now)
        for entry in self.cfg.schedule:
            if entry.matches(dt):
                return entry.mode
        return self.cfg.default

    # --- persistencia -------------------------------------------------------------------
    def _load_state(self) -> str | None:
        if not self._state_file or not self._state_file.is_file():
            return None
        try:
            value = json.loads(self._state_file.read_text(encoding="utf-8")).get("modalidad")
        except (OSError, ValueError, AttributeError):
            return None
        if value == AUTO or value in self.cfg.definitions:
            return value
        return None

    def _save_state(self) -> None:
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps({"modalidad": self.selection}), encoding="utf-8")
        except OSError:
            log.exception("No se pudo guardar el estado de la modalidad")
