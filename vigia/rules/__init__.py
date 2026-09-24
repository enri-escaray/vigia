"""Reglas de análisis y motor que las evalúa según la modalidad activa."""

from __future__ import annotations

import logging
import time

from vigia.geometry import Line, Zone
from vigia.rules.base import FrameContext, Rule, RuleConfig, RuleHit
from vigia.rules.behavior import CrowdRule, FallRule, FightRule, RunningRule
from vigia.rules.lines import LineCrossingRule
from vigia.rules.objects import AbandonedObjectRule, DangerousObjectRule
from vigia.rules.sabotage import SabotageRule
from vigia.rules.zones import IntrusionRule, LoiteringRule, PresenceRule

log = logging.getLogger(__name__)

RULE_TYPES: dict[str, type[Rule]] = {
    cls.type: cls
    for cls in (
        IntrusionRule,
        PresenceRule,
        LoiteringRule,
        LineCrossingRule,
        AbandonedObjectRule,
        DangerousObjectRule,
        CrowdRule,
        RunningRule,
        FallRule,
        FightRule,
        SabotageRule,
    )
}


class RuleEngine:
    """Conjunto de reglas de una modalidad instanciadas para una cámara."""

    def __init__(
        self,
        mode_name: str,
        rule_configs: list[RuleConfig],
        camera_id: str,
        zones: dict[str, Zone],
        lines: dict[str, Line],
    ) -> None:
        self.mode = mode_name
        self.rules: list[Rule] = []
        for rc in rule_configs:
            if rc.cameras and camera_id not in rc.cameras:
                continue
            rule = RULE_TYPES[rc.type].for_camera(rc, f"{mode_name}.{rc.name}", zones, lines)
            if rule is not None:
                self.rules.append(rule)
        self._last_error: dict[str, float] = {}

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        hits: list[RuleHit] = []
        for rule in self.rules:
            try:
                hits.extend(rule.evaluate(ctx))
            except Exception:  # noqa: BLE001 - una regla defectuosa no debe detener la cámara
                now = time.monotonic()
                if now - self._last_error.get(rule.id, -1e9) > 60:
                    self._last_error[rule.id] = now
                    log.exception("Error evaluando la regla %s", rule.id)
        return hits


__all__ = ["FrameContext", "Rule", "RuleConfig", "RuleEngine", "RuleHit", "RULE_TYPES"]
