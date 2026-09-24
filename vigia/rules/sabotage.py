"""Regla de sabotaje: cámara tapada, desenfocada, movida, a oscuras o sin señal."""

from __future__ import annotations

from vigia.rules.base import FrameContext, Rule, RuleHit
from vigia.types import Severity

TITLES = {
    "obstruida": "Cámara obstruida o tapada",
    "desenfocada": "Cámara desenfocada o empañada",
    "movida": "Cámara movida o redirigida",
    "oscura": "Pérdida súbita de iluminación",
    "sin_senal": "Cámara sin señal",
}

MESSAGES = {
    "obstruida": "La imagen quedó casi uniforme: posible mano, tela o pintura sobre el lente",
    "desenfocada": "La nitidez cayó bruscamente: posible lente rociado, empañado o desenfocado",
    "movida": "La escena ya no coincide con la de referencia: la cámara fue girada o movida",
    "oscura": "La imagen quedó a oscuras de golpe",
}


class SabotageRule(Rule):
    type = "sabotaje"
    title = "Sabotaje de cámara"
    description = "Detecta cámaras tapadas, desenfocadas, movidas, a oscuras o desconectadas."
    default_severity = Severity.ALTA
    default_cooldown = 300.0
    PARAMS = {"detectar": ["obstruida", "desenfocada", "movida", "oscura", "sin_senal"], "segundos_sin_senal": 10.0}
    CHOICES = {"detectar": tuple(TITLES)}
    uses_zones = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_state: str | None = None
        self._offline_alerted = False

    def evaluate(self, ctx: FrameContext) -> list[RuleHit]:
        enabled = set(self.p["detectar"])
        if not ctx.online:
            if (
                "sin_senal" in enabled
                and not self._offline_alerted
                and ctx.offline_since is not None
                and ctx.t - ctx.offline_since >= float(self.p["segundos_sin_senal"])
            ):
                self._offline_alerted = True
                return [
                    self.hit(
                        f"Sin imagen desde hace {ctx.t - ctx.offline_since:.0f} s (cable cortado, apagada o red caída)",
                        key="sin_senal",
                        title=TITLES["sin_senal"],
                    )
                ]
            return []

        self._offline_alerted = False
        state = ctx.tamper.state if ctx.tamper else "ok"
        if state not in MESSAGES:
            self._last_state = None
            return []
        if state == self._last_state or state not in enabled:
            return []
        self._last_state = state
        return [self.hit(MESSAGES[state], key=state, title=TITLES[state], estado=state)]
