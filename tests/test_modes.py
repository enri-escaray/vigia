from datetime import datetime, time

import pytest

from vigia.config import ModeConfig, ModesConfig, ScheduleEntry, parse_days
from vigia.modes import ModeManager


def entry(mode, days, start, end):
    return ScheduleEntry(mode, parse_days(days), time(*start), time(*end))


def test_schedule_same_day():
    e = entry("comercio", "lun-vie", (9, 0), (20, 0))
    assert e.matches(datetime(2026, 9, 21, 10, 0))  # lunes
    assert not e.matches(datetime(2026, 9, 21, 20, 0))
    assert not e.matches(datetime(2026, 9, 26, 10, 0))  # sábado


def test_schedule_overnight():
    e = entry("nocturno", ["vie"], (23, 0), (6, 0))
    assert e.matches(datetime(2026, 9, 25, 23, 30))  # viernes noche
    assert e.matches(datetime(2026, 9, 26, 5, 59))  # madrugada del sábado
    assert not e.matches(datetime(2026, 9, 26, 23, 30))  # sábado noche: no
    assert not e.matches(datetime(2026, 9, 25, 5, 0))  # madrugada del viernes: viene del jueves


def test_parse_days_variants():
    assert parse_days("todos") == frozenset(range(7))
    assert parse_days("laborables") == frozenset(range(5))
    assert parse_days(["sáb", "domingo"]) == frozenset({5, 6})
    assert parse_days("vie-lun") == frozenset({4, 5, 6, 0})


def modes_cfg(initial="auto"):
    definitions = {name: ModeConfig(name, name.capitalize(), "", "#fff", []) for name in ("hogar", "nocturno", "ausente")}
    return ModesConfig(
        initial=initial,
        default="hogar",
        schedule=[entry("nocturno", "todos", (23, 0), (6, 0))],
        definitions=definitions,
    )


def ts(hour, minute=0):
    return datetime(2026, 9, 22, hour, minute).timestamp()


def test_manager_follows_schedule_and_manual_override(tmp_path):
    now = {"t": ts(12)}
    changes = []
    manager = ModeManager(modes_cfg(), tmp_path / "estado.json", clock=lambda: now["t"])
    manager.add_listener(lambda old, new: changes.append((old, new)))
    assert manager.current == "hogar" and manager.selection == "auto"
    now["t"] = ts(23, 30)
    assert manager.tick() is True and manager.current == "nocturno"
    assert manager.select("ausente") == "ausente"
    now["t"] = ts(12)
    assert manager.tick() is False and manager.current == "ausente"  # manual: ignora el horario
    assert changes == [("hogar", "nocturno"), ("nocturno", "ausente")]

    # La selección manual sobrevive a un reinicio.
    reloaded = ModeManager(modes_cfg(), tmp_path / "estado.json", clock=lambda: now["t"])
    assert reloaded.current == "ausente" and reloaded.selection == "ausente"
    reloaded.select("auto")
    assert reloaded.current == "hogar"


def test_manager_rejects_unknown_mode(tmp_path):
    manager = ModeManager(modes_cfg(), tmp_path / "estado.json", clock=lambda: ts(12))
    with pytest.raises(ValueError):
        manager.select("fiesta")


def test_initial_mode_from_config():
    manager = ModeManager(modes_cfg(initial="ausente"), None, clock=lambda: ts(12))
    assert manager.current == "ausente"
    info = manager.info()
    assert info["actual"] == "ausente" and not info["automatica"]
    assert [m["nombre"] for m in info["modalidades"]] == ["hogar", "nocturno", "ausente"]
