"""Infraestructura común de las reglas de análisis de comportamiento."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, ClassVar, Iterable

import numpy as np

from vigia.geometry import Line, Zone
from vigia.types import Severity, category_display
from vigia.vision.tamper import TamperStatus
from vigia.vision.tracker import Track

# Claves válidas en cualquier regla (además de los PARAMS propios de cada tipo).
COMMON_KEYS = {"tipo", "nombre", "titulo", "severidad", "enfriamiento", "camaras", "zonas", "activo"}


@dataclass
class RuleConfig:
    type: str
    name: str
    severity: Severity
    cooldown: float
    cameras: list[str] | None = None
    zones: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    title: str | None = None  # título personalizado de la alerta (p. ej. "Peatón en la autopista")

    def to_dict(self) -> dict:
        return {
            "tipo": self.type,
            "nombre": self.name,
            "titulo": self.title,
            "severidad": self.severity.label,
            "enfriamiento": self.cooldown,
            "camaras": self.cameras,
            "zonas": self.zones,
            **self.params,
        }


@dataclass
class FrameContext:
    """Todo lo que una regla puede consultar en un instante dado."""

    t: float
    frame_size: tuple[int, int]
    tracks: list[Track]  # objetos confirmados y activos
    zones: dict[str, Zone] = field(default_factory=dict)
    lines: dict[str, Line] = field(default_factory=dict)
    online: bool = True
    offline_since: float | None = None
    tamper: TamperStatus | None = None
    motion: np.ndarray | None = None  # máscara 0/1 de píxeles que cambiaron (imagen reducida)

    @property
    def diag(self) -> float:
        return math.hypot(*self.frame_size)


@dataclass
class RuleHit:
    rule_id: str
    rule_type: str
    title: str
    message: str
    severity: Severity
    key: str  # identifica el "evento" para no repetir la alerta (enfriamiento)
    cooldown: float
    tracks: list[Track] = field(default_factory=list)
    zone: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Rule:
    type: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    default_severity: ClassVar[Severity] = Severity.MEDIA
    default_cooldown: ClassVar[float] = 60.0
    PARAMS: ClassVar[dict[str, Any]] = {}
    CHOICES: ClassVar[dict[str, tuple[str, ...]]] = {}  # valores permitidos por parámetro
    uses_zones: ClassVar[bool] = True

    def __init__(self, cfg: RuleConfig, rule_id: str, zones: list[Zone | None], lines: list[Line] | None = None):
        self.cfg = cfg
        self.id = rule_id
        self.zones = zones  # [None] significa "toda la imagen"
        self.lines = lines or []
        self.p: dict[str, Any] = {**self.PARAMS, **cfg.params}
        self.categories = set(self.p.get("categorias", []))

    @classmethod
    def validate_params(cls, params: dict[str, Any]) -> None:
        for key, choices in cls.CHOICES.items():
            value = params.get(key, cls.PARAMS.get(key))
            values = value if isinstance(value, list) else [value]
            invalid = [v for v in values if v not in choices]
            if invalid:
                raise ValueError(f"valor inválido para '{key}': {', '.join(map(str, invalid))} (opciones: {', '.join(choices)})")

    @classmethod
    def for_camera(cls, cfg: RuleConfig, rule_id: str, zones: dict[str, Zone], lines: dict[str, Line]) -> "Rule | None":
        """Instancia la regla para una cámara, o None si no aplica (la cámara no
        tiene ninguna de las zonas que la regla pide)."""
        if cls.uses_zones and cfg.zones:
            selected: list[Zone | None] = [zones[name] for name in cfg.zones if name in zones]
            if not selected:
                return None
        else:
            selected = [None]
        return cls(cfg, rule_id, selected)

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        raise NotImplementedError

    # --- utilidades para las subclases ---------------------------------------------
    def hit(
        self,
        message: str,
        key: str,
        tracks: Iterable[Track] = (),
        zone: Zone | None = None,
        title: str | None = None,
        **extra: Any,
    ) -> RuleHit:
        return RuleHit(
            rule_id=self.id,
            rule_type=self.type,
            title=title or self.cfg.title or self.title,
            message=message,
            severity=self.cfg.severity,
            key=key,
            cooldown=self.cfg.cooldown,
            tracks=list(tracks),
            zone=zone.name if zone else None,
            extra=extra,
        )

    def relevant(self, ctx: FrameContext) -> list[Track]:
        return [t for t in ctx.tracks if t.category in self.categories]

    @staticmethod
    def inside(track: Track, zone: Zone | None) -> bool:
        return zone is None or zone.contains(track.anchor_norm)


def zone_key(zone: Zone | None) -> str:
    return zone.name if zone else "*"


def where(zone: Zone | None) -> str:
    return f" en la zona «{zone.name}»" if zone else ""


def describe(track: Track) -> str:
    return f"{category_display(track.category)} #{track.id}"


def describe_many(tracks: list[Track]) -> str:
    if len(tracks) == 1:
        return describe(tracks[0])
    ids = ", ".join(f"#{t.id}" for t in tracks)
    return f"{len(tracks)} objetos ({ids})"
