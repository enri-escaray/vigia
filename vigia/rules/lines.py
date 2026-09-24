"""Regla de cruce de línea virtual (tripwire)."""

from __future__ import annotations

from vigia.geometry import Line, Point, Zone
from vigia.rules.base import FrameContext, Rule, RuleConfig, RuleHit, describe
from vigia.types import Severity


class LineCrossingRule(Rule):
    type = "cruce_linea"
    title = "Cruce de línea virtual"
    description = "Alguien cruza una línea (puerta, reja, pasillo) en el sentido indicado."
    default_severity = Severity.ALTA
    default_cooldown = 10.0
    PARAMS = {"categorias": ["persona"], "lineas": [], "direccion": "ambas"}
    CHOICES = {"direccion": ("ambas", "entrada", "salida")}
    uses_zones = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last: dict[tuple[int, str], Point] = {}

    @classmethod
    def for_camera(cls, cfg: RuleConfig, rule_id: str, zones: dict[str, Zone], lines: dict[str, Line]) -> "Rule | None":
        wanted = cfg.params.get("lineas") or []
        selected = [lines[name] for name in wanted if name in lines] if wanted else list(lines.values())
        if not selected:
            return None
        return cls(cfg, rule_id, [None], selected)

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        direction = self.p["direccion"]
        hits: list[RuleHit] = []
        seen: set[tuple[int, str]] = set()
        for tr in self.relevant(ctx):
            pos = tr.anchor_norm
            for line in self.lines:
                key = (tr.id, line.name)
                seen.add(key)
                if line.side(pos) == 0:
                    continue  # justo sobre la línea: se espera al siguiente punto
                prev = self._last.get(key)
                self._last[key] = pos
                if prev is None:
                    continue
                crossed = line.crossing(prev, pos)
                if not crossed:
                    continue
                sense = "entrada" if crossed > 0 else "salida"
                if direction != "ambas" and direction != sense:
                    continue
                hits.append(
                    self.hit(
                        f"{describe(tr)} cruzó la línea «{line.name}» ({sense})",
                        key=f"{line.name}:{tr.id}",
                        tracks=[tr],
                        linea=line.name,
                        sentido=sense,
                    )
                )
        for key in list(self._last):
            if key not in seen:
                del self._last[key]
        return hits
