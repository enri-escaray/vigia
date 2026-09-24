"""Reglas basadas en zonas: intrusión, presencia y merodeo."""

from __future__ import annotations

from vigia.rules.base import FrameContext, Rule, RuleHit, describe, describe_many, where, zone_key
from vigia.types import Severity


class IntrusionRule(Rule):
    type = "intrusion"
    title = "Intrusión en zona restringida"
    description = "Alguien (o algo) entra a una zona definida."
    default_severity = Severity.ALTA
    default_cooldown = 30.0
    PARAMS = {"categorias": ["persona"], "segundos_min": 0.5}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._entered: dict[tuple[int, str], float] = {}
        self._alerted: set[tuple[int, str]] = set()

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        hits: list[RuleHit] = []
        min_seconds = float(self.p["segundos_min"])
        present: set[tuple[int, str]] = set()
        relevant = self.relevant(ctx)
        for zone in self.zones:
            zk = zone_key(zone)
            inside = [tr for tr in relevant if self.inside(tr, zone)]
            new = []
            for tr in inside:
                key = (tr.id, zk)
                present.add(key)
                start = self._entered.setdefault(key, ctx.t)
                if key not in self._alerted and ctx.t - start >= min_seconds:
                    self._alerted.add(key)
                    new.append(tr)
            if new:
                hits.append(self.hit(self._message(new, zone), key=zk, tracks=inside, zone=zone))
        for key in list(self._entered):
            if key not in present:
                del self._entered[key]
                self._alerted.discard(key)
        return hits

    def _message(self, tracks, zone) -> str:
        verb = "ingresó" if len(tracks) == 1 else "ingresaron"
        return f"{describe_many(tracks)} {verb}{where(zone) or ' al área vigilada'}"


class PresenceRule(IntrusionRule):
    type = "presencia"
    title = "Presencia no autorizada"
    description = "Cualquier persona visible mientras el sistema está armado."
    default_severity = Severity.CRITICA
    default_cooldown = 60.0
    PARAMS = {"categorias": ["persona"], "segundos_min": 1.0}

    def _message(self, tracks, zone) -> str:
        verb = "detectada" if len(tracks) == 1 else "detectados"
        return f"{describe_many(tracks)} {verb}{where(zone)} con la modalidad armada"


class LoiteringRule(Rule):
    type = "merodeo"
    title = "Merodeo / permanencia sospechosa"
    description = "Alguien permanece demasiado tiempo en una zona."
    default_severity = Severity.MEDIA
    default_cooldown = 120.0
    # requiere_movimiento: solo cuenta objetos que se movieron alguna vez (ver
    # Track.moved). Evita que un auto estacionado, o un cartel o una baliza que
    # el modelo confunde con un vehículo, pase por "vehículo detenido".
    PARAMS = {"categorias": ["persona"], "segundos": 30.0, "tolerancia": 3.0, "requiere_movimiento": False}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._state: dict[tuple[int, str], list[float]] = {}  # clave -> [entrada, última vez dentro]
        self._alerted: set[tuple[int, str]] = set()

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        limit = float(self.p["segundos"])
        tolerance = float(self.p["tolerancia"])
        hits: list[RuleHit] = []
        candidates = self.relevant(ctx)
        if self.p["requiere_movimiento"]:
            candidates = [tr for tr in candidates if tr.moved]
        for zone in self.zones:
            zk = zone_key(zone)
            for tr in candidates:
                if not self.inside(tr, zone):
                    continue
                key = (tr.id, zk)
                state = self._state.get(key)
                if state is None:
                    self._state[key] = [ctx.t, ctx.t]
                    continue
                state[1] = ctx.t
                stay = ctx.t - state[0]
                if stay >= limit and key not in self._alerted:
                    self._alerted.add(key)
                    hits.append(
                        self.hit(
                            f"{describe(tr)} permanece {stay:.0f} s{where(zone)}",
                            # Por zona, no por objeto: si el seguimiento pierde a la
                            # persona y la retoma con otro número, no se repite la alerta.
                            key=zk,
                            tracks=[tr],
                            zone=zone,
                            segundos=round(stay, 1),
                        )
                    )
        for key, (_, last_inside) in list(self._state.items()):
            if ctx.t - last_inside > tolerance:
                del self._state[key]
                self._alerted.discard(key)
        return hits
