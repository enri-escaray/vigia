"""Utilidades compartidas por las pruebas."""

from __future__ import annotations

from vigia.geometry import Line, Zone
from vigia.rules.base import FrameContext, Rule, RuleConfig
from vigia.types import Detection
from vigia.vision.tracker import Track

FRAME = (1000, 1000)


def make_track(tid: int, bbox, category: str = "persona", t: float = 0.0, confidence: float = 0.9, label: str = "person") -> Track:
    return Track(
        id=tid,
        category=category,
        label=label,
        bbox=tuple(float(v) for v in bbox),
        confidence=confidence,
        first_seen=t,
        last_seen=t,
        frame_size=FRAME,
        hits=5,
        confirmed=True,
    )


def move(track: Track, bbox, t: float, confidence: float = 0.9) -> Track:
    track.update(Detection(tuple(float(v) for v in bbox), confidence, track.label, track.category), t, FRAME)
    return track


def box_at(cx: float, bottom: float, w: float = 60, h: float = 170):
    """Caja con el punto de apoyo (centro inferior) en (cx, bottom)."""
    return (cx - w / 2, bottom - h, cx + w / 2, bottom)


def build_rule(cls: type[Rule], zones: dict[str, Zone] | None = None, lines: dict[str, Line] | None = None, zone_names=None, **params) -> Rule:
    cfg = RuleConfig(
        type=cls.type,
        name=cls.type,
        severity=cls.default_severity,
        cooldown=cls.default_cooldown,
        zones=list(zone_names or []),
        params=params,
    )
    rule = cls.for_camera(cfg, f"prueba.{cls.type}", zones or {}, lines or {})
    assert rule is not None
    return rule


def ctx(t: float, tracks, **kwargs) -> FrameContext:
    return FrameContext(t=t, frame_size=FRAME, tracks=list(tracks), **kwargs)


SQUARE = Zone("caja", ((0.5, 0.5), (0.9, 0.5), (0.9, 0.9), (0.5, 0.9)))
