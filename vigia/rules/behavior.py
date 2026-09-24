"""Reglas de comportamiento: aglomeración, carrera, caída y altercado."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vigia.geometry import bbox_union, distance
from vigia.rules.base import FrameContext, Rule, RuleHit, describe, where, zone_key
from vigia.types import BBox, Severity, category_plural
from vigia.vision.tracker import Track


class CrowdRule(Rule):
    type = "aglomeracion"
    title = "Aglomeración"
    description = "Hay más personas de lo permitido en una zona durante un tiempo."
    default_severity = Severity.MEDIA
    default_cooldown = 180.0
    PARAMS = {"categorias": ["persona"], "umbral": 5, "segundos": 5.0}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._since: dict[str, float] = {}
        self._alerted: set[str] = set()

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        threshold = int(self.p["umbral"])
        hits: list[RuleHit] = []
        for zone in self.zones:
            zk = zone_key(zone)
            inside = [tr for tr in self.relevant(ctx) if self.inside(tr, zone)]
            if len(inside) < threshold:
                self._since.pop(zk, None)
                self._alerted.discard(zk)
                continue
            since = self._since.setdefault(zk, ctx.t)
            if zk not in self._alerted and ctx.t - since >= float(self.p["segundos"]):
                self._alerted.add(zk)
                hits.append(
                    self.hit(
                        f"{len(inside)} {category_plural({t.category for t in inside})}{where(zone)} (umbral: {threshold})",
                        key=zk,
                        tracks=inside,
                        zone=zone,
                        cantidad=len(inside),
                    )
                )
        return hits


class RunningRule(Rule):
    type = "carrera"
    title = "Persona corriendo / posible huida"
    description = "Movimiento anormalmente rápido (medido en alturas del cuerpo por segundo)."
    default_severity = Severity.MEDIA
    default_cooldown = 60.0
    PARAMS = {"categorias": ["persona"], "velocidad": 1.5, "segundos": 0.6}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fast_since: dict[int, float] = {}
        self._alerted: set[int] = set()

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        threshold = float(self.p["velocidad"])
        hits: list[RuleHit] = []
        alive: set[int] = set()
        for zone in self.zones:
            for tr in self.relevant(ctx):
                if tr.id in alive or not self.inside(tr, zone) or tr.height < 10:
                    continue
                alive.add(tr.id)
                speed = tr.speed(window=0.8) / tr.height
                if speed < threshold:
                    self._fast_since.pop(tr.id, None)
                    continue
                since = self._fast_since.setdefault(tr.id, ctx.t)
                if tr.id not in self._alerted and ctx.t - since >= float(self.p["segundos"]):
                    self._alerted.add(tr.id)
                    hits.append(
                        self.hit(
                            f"{describe(tr)} se desplaza a {speed:.1f} cuerpos/s{where(zone)}",
                            key=str(tr.id),
                            tracks=[tr],
                            zone=zone,
                            velocidad=round(speed, 2),
                        )
                    )
        self._fast_since = {k: v for k, v in self._fast_since.items() if k in alive}
        self._alerted &= alive
        return hits


@dataclass
class _FallState:
    standing_at: float | None = None
    lying_since: float | None = None
    alerted: bool = False


class FallRule(Rule):
    type = "caida"
    title = "Posible caída de persona"
    description = "Una persona pasa de estar de pie a estar tendida y no se levanta."
    default_severity = Severity.ALTA
    default_cooldown = 120.0
    PARAMS = {"categorias": ["persona"], "proporcion": 1.2, "segundos": 2.0, "ventana_de_pie": 5.0}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._state: dict[int, _FallState] = {}

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        lying_ratio = float(self.p["proporcion"])
        hits: list[RuleHit] = []
        alive: set[int] = set()
        for zone in self.zones:
            for tr in self.relevant(ctx):
                if tr.id in alive or not self.inside(tr, zone) or tr.height < 10:
                    continue
                alive.add(tr.id)
                st = self._state.setdefault(tr.id, _FallState())
                ratio = tr.width / tr.height
                if ratio <= 0.8:
                    st.standing_at = ctx.t
                    st.lying_since = None
                    st.alerted = False
                    continue
                if ratio < lying_ratio:
                    continue  # postura intermedia (agachado, sentado)
                if st.lying_since is None:
                    recently_standing = st.standing_at is not None and ctx.t - st.standing_at <= float(self.p["ventana_de_pie"])
                    if not recently_standing:
                        continue  # ya estaba tendido al aparecer (p. ej. en un sofá)
                    st.lying_since = ctx.t
                if not st.alerted and ctx.t - st.lying_since >= float(self.p["segundos"]):
                    st.alerted = True
                    hits.append(
                        self.hit(
                            f"{describe(tr)} en el suelo hace {ctx.t - st.lying_since:.0f} s{where(zone)}",
                            key=str(tr.id),
                            tracks=[tr],
                            zone=zone,
                        )
                    )
        self._state = {k: v for k, v in self._state.items() if k in alive}
        return hits


class FightRule(Rule):
    """Heurística experimental: dos personas muy juntas, sin desplazarse, con
    mucho movimiento de brazos/cuerpo entre ellas durante unos segundos."""

    type = "altercado"
    title = "Posible altercado (experimental)"
    description = "Dos o más personas juntas con movimiento brusco sostenido."
    default_severity = Severity.ALTA
    default_cooldown = 120.0
    PARAMS = {
        "categorias": ["persona"],
        "proximidad": 0.8,  # distancia entre centros, en alturas
        "energia": 0.18,  # fracción de píxeles en movimiento entre ambos
        "desplazamiento_max": 0.8,  # en alturas, dentro de la ventana
        "ventana": 2.0,
        "segundos": 1.5,
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._since: dict[tuple[int, int], float] = {}
        self._alerted: set[tuple[int, int]] = set()

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        if ctx.motion is None:
            return []
        hits: list[RuleHit] = []
        active: set[tuple[int, int]] = set()
        window = float(self.p["ventana"])
        for zone in self.zones:
            people = [tr for tr in self.relevant(ctx) if self.inside(tr, zone) and tr.height >= 10]
            for i, a in enumerate(people):
                for b in people[i + 1 :]:
                    key = (min(a.id, b.id), max(a.id, b.id))
                    if key in active:
                        continue
                    height = (a.height + b.height) / 2
                    if distance(a.center, b.center) > float(self.p["proximidad"]) * height:
                        continue
                    max_disp = float(self.p["desplazamiento_max"])
                    if a.displacement(window) / a.height > max_disp or b.displacement(window) / b.height > max_disp:
                        continue
                    energy = motion_energy(ctx.motion, ctx.frame_size, bbox_union([a.bbox, b.bbox]))
                    if energy < float(self.p["energia"]):
                        continue
                    active.add(key)
                    since = self._since.setdefault(key, ctx.t)
                    if key not in self._alerted and ctx.t - since >= float(self.p["segundos"]):
                        self._alerted.add(key)
                        hits.append(
                            self.hit(
                                f"{describe(a)} y {describe(b)} con movimiento brusco sostenido{where(zone)}",
                                key=f"{key[0]}-{key[1]}:{zone_key(zone)}",
                                tracks=[a, b],
                                zone=zone,
                                energia=round(energy, 2),
                            )
                        )
        self._since = {k: v for k, v in self._since.items() if k in active}
        self._alerted &= active
        return hits


def motion_energy(mask: np.ndarray, frame_size: tuple[int, int], bbox: BBox) -> float:
    """Fracción de píxeles con movimiento dentro de una caja (en coordenadas de la imagen original)."""
    w, h = frame_size
    mh, mw = mask.shape[:2]
    x1 = int(max(0, bbox[0]) * mw / w)
    x2 = int(min(w, bbox[2]) * mw / w)
    y1 = int(max(0, bbox[1]) * mh / h)
    y2 = int(min(h, bbox[3]) * mh / h)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return float(mask[y1:y2, x1:x2].mean())
