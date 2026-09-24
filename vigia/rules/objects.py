"""Reglas sobre objetos: objeto abandonado y objeto peligroso (armas)."""

from __future__ import annotations

from dataclasses import dataclass

from vigia.geometry import distance, iou
from vigia.rules.base import FrameContext, Rule, RuleHit, describe, describe_many, where, zone_key
from vigia.types import Severity
from vigia.vision.tracker import Track


@dataclass
class _ObjectState:
    attended_at: float | None = None  # última vez que tuvo una persona cerca
    still_since: float | None = None  # desde cuándo está quieto
    alerted: bool = False


class AbandonedObjectRule(Rule):
    type = "objeto_abandonado"
    title = "Objeto abandonado"
    description = "Un bolso/maleta queda quieto y sin su dueño cerca."
    default_severity = Severity.ALTA
    default_cooldown = 300.0
    PARAMS = {
        "categorias": ["equipaje"],
        "categorias_dueno": ["persona"],
        "segundos": 30.0,
        "distancia_dueno": 1.5,  # en alturas de la persona
        "movimiento_max": 0.5,  # en alturas del objeto
        "requiere_dueno": True,
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._state: dict[int, _ObjectState] = {}

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        limit = float(self.p["segundos"])
        owner_categories = set(self.p["categorias_dueno"])
        owners = [tr for tr in ctx.tracks if tr.category in owner_categories]
        hits: list[RuleHit] = []
        alive: set[int] = set()
        for zone in self.zones:
            for obj in self.relevant(ctx):
                if not self.inside(obj, zone) or obj.id in alive:
                    continue
                alive.add(obj.id)
                st = self._state.setdefault(obj.id, _ObjectState())
                if self._has_owner_near(obj, owners):
                    st.attended_at = ctx.t

                moved = obj.displacement(min(limit, 5.0)) / max(obj.height, 1.0)
                if moved > float(self.p["movimiento_max"]):
                    st.still_since = None
                    st.alerted = False  # alguien lo levantó / lo movió
                elif st.still_since is None:
                    st.still_since = ctx.t

                if st.alerted or st.still_since is None:
                    continue
                if st.attended_at is None:
                    if self.p["requiere_dueno"]:
                        continue  # nunca se vio a nadie con el objeto: probablemente es parte del lugar
                    alone_since = max(obj.first_seen, st.still_since)
                else:
                    alone_since = st.attended_at
                alone = ctx.t - alone_since
                if alone >= limit and ctx.t - st.still_since >= min(limit, 3.0):
                    st.alerted = True
                    hits.append(
                        self.hit(
                            f"{describe(obj)} ({obj.label}) sin dueño hace {alone:.0f} s{where(zone)}",
                            key=f"{obj.id}",
                            tracks=[obj],
                            zone=zone,
                            segundos=round(alone, 1),
                        )
                    )
        for obj_id in list(self._state):
            if obj_id not in alive:
                del self._state[obj_id]
        return hits

    def _has_owner_near(self, obj: Track, owners: list[Track]) -> bool:
        factor = float(self.p["distancia_dueno"])
        for person in owners:
            if iou(obj.bbox, person.bbox) > 0:
                return True
            if distance(obj.center, person.center) <= factor * max(person.height, 1.0):
                return True
        return False


class DangerousObjectRule(Rule):
    type = "objeto_peligroso"
    title = "Objeto peligroso / posible arma"
    description = "Se ve un cuchillo, bate u otra arma (según el modelo)."
    default_severity = Severity.CRITICA
    default_cooldown = 60.0
    PARAMS = {"categorias": ["arma"], "confianza_min": 0.5, "detecciones_min": 3}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._alerted: set[tuple[int, str]] = set()

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        min_conf = float(self.p["confianza_min"])
        min_hits = int(self.p["detecciones_min"])
        hits: list[RuleHit] = []
        alive: set[tuple[int, str]] = set()
        persons = [tr for tr in ctx.tracks if tr.category == "persona"]
        for zone in self.zones:
            zk = zone_key(zone)
            new: list[Track] = []
            for tr in self.relevant(ctx):
                if not self.inside(tr, zone):
                    continue
                key = (tr.id, zk)
                alive.add(key)
                if key in self._alerted or tr.hits < min_hits or tr.max_confidence < min_conf:
                    continue
                self._alerted.add(key)
                new.append(tr)
            if new:
                holders = [p for p in persons if any(self._near(obj, p) for obj in new)]
                labels = ", ".join(sorted({t.label for t in new}))
                hits.append(
                    self.hit(
                        f"{describe_many(new)} ({labels}){where(zone)}"
                        + (f"; cerca de {describe_many(holders)}" if holders else ""),
                        key=zk,
                        tracks=new + holders,
                        zone=zone,
                        confianza=round(max(t.max_confidence for t in new), 2),
                    )
                )
        self._alerted &= alive
        return hits

    @staticmethod
    def _near(obj: Track, person: Track) -> bool:
        return iou(obj.bbox, person.bbox) > 0 or distance(obj.center, person.center) <= person.height
