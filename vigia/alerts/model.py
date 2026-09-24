from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from vigia.types import Severity


@dataclass
class Alert:
    ts: float
    camera_id: str
    camera_name: str
    mode: str
    rule_id: str
    rule_type: str
    title: str
    message: str
    severity: Severity
    zone: str | None = None
    track_ids: list[int] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    snapshot: str | None = None  # ruta relativa a la carpeta de datos (con "/")
    clip: str | None = None
    acknowledged: bool = False
    acknowledged_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    @property
    def when(self) -> datetime:
        return datetime.fromtimestamp(self.ts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "fecha": self.when.isoformat(timespec="seconds"),
            "camara_id": self.camera_id,
            "camara": self.camera_name,
            "modalidad": self.mode,
            "regla": self.rule_id,
            "tipo": self.rule_type,
            "titulo": self.title,
            "mensaje": self.message,
            "severidad": self.severity.label,
            "severidad_texto": self.severity.display,
            "nivel": int(self.severity),
            "zona": self.zone,
            "objetos": self.track_ids,
            "categorias": self.categories,
            "captura": f"/media/{self.snapshot}" if self.snapshot else None,
            "clip": f"/media/{self.clip}" if self.clip else None,
            "reconocida": self.acknowledged,
            "reconocida_ts": self.acknowledged_at,
            "extra": self.extra,
        }

    def text(self) -> str:
        """Resumen de una línea, para consola y notificaciones de texto."""
        return f"[{self.severity.display.upper()}] {self.camera_name}: {self.title} — {self.message}"
