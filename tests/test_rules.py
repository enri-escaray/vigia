import numpy as np

from helpers import SQUARE, box_at, build_rule, ctx, make_track, move
from vigia.geometry import Line, Zone
from vigia.rules import RuleEngine
from vigia.rules.base import RuleConfig
from vigia.rules.behavior import CrowdRule, FallRule, FightRule, RunningRule
from vigia.rules.lines import LineCrossingRule
from vigia.rules.objects import AbandonedObjectRule, DangerousObjectRule
from vigia.rules.sabotage import SabotageRule
from vigia.rules.zones import IntrusionRule, LoiteringRule, PresenceRule
from vigia.types import Severity
from vigia.vision.tamper import TamperStatus

ZONES = {"caja": SQUARE}


def test_intrusion_requires_dwell_and_fires_once():
    rule = build_rule(IntrusionRule, ZONES, zone_names=["caja"], segundos_min=0.5)
    person = make_track(1, box_at(200, 300))
    assert rule.evaluate(ctx(0.0, [person])) == []  # fuera de la zona
    move(person, box_at(700, 700), 1.0)
    assert rule.evaluate(ctx(1.0, [person])) == []  # recién entra
    hits = rule.evaluate(ctx(1.6, [person]))
    assert len(hits) == 1 and hits[0].zone == "caja" and "Persona #1" in hits[0].message
    assert rule.evaluate(ctx(2.0, [person])) == []  # no se repite
    move(person, box_at(200, 300), 3.0)
    rule.evaluate(ctx(3.0, [person]))
    move(person, box_at(700, 700), 4.0)
    rule.evaluate(ctx(4.0, [person]))
    assert len(rule.evaluate(ctx(4.6, [person]))) == 1  # reingreso: nuevo evento


def test_intrusion_ignores_other_categories():
    rule = build_rule(IntrusionRule, ZONES, zone_names=["caja"])
    dog = make_track(2, box_at(700, 700), category="animal")
    assert rule.evaluate(ctx(0, [dog])) == [] and rule.evaluate(ctx(5, [dog])) == []


def test_rule_not_built_when_camera_lacks_zone():
    cfg = RuleConfig("intrusion", "intrusion", Severity.ALTA, 30, zones=["bodega"])
    assert IntrusionRule.for_camera(cfg, "x.intrusion", ZONES, {}) is None


def test_presence_whole_frame():
    rule = build_rule(PresenceRule)
    person = make_track(1, box_at(100, 900))
    assert rule.evaluate(ctx(0.0, [person])) == []
    hits = rule.evaluate(ctx(1.2, [person]))
    assert len(hits) == 1 and hits[0].severity == Severity.CRITICA


def test_loitering():
    rule = build_rule(LoiteringRule, ZONES, zone_names=["caja"], segundos=30)
    person = make_track(1, box_at(700, 700))
    hits = []
    for t in range(0, 40):
        hits += rule.evaluate(ctx(float(t), [person]))
    assert len(hits) == 1
    assert hits[0].extra["segundos"] >= 30


def test_loitering_tolerates_short_exits_but_resets_on_long_ones():
    rule = build_rule(LoiteringRule, ZONES, zone_names=["caja"], segundos=10, tolerancia=3)
    person = make_track(1, box_at(700, 700))
    for t in range(0, 6):
        assert rule.evaluate(ctx(float(t), [person])) == []
    move(person, box_at(200, 200), 6.0)
    for t in range(6, 12):  # 6 s fuera: se reinicia
        assert rule.evaluate(ctx(float(t), [person])) == []
    move(person, box_at(700, 700), 12.0)
    assert all(rule.evaluate(ctx(float(t), [person])) == [] for t in range(12, 20))


def test_loitering_can_require_previous_movement():
    rule = build_rule(LoiteringRule, ZONES, zone_names=["caja"], categorias=["vehiculo"], segundos=5, requiere_movimiento=True)
    sign = make_track(1, (650, 650, 750, 700), category="vehiculo", label="truck")  # un cartel: nunca se mueve
    car = make_track(2, (300, 650, 400, 700), category="vehiculo", label="car")
    hits = []
    xs = [300, 410, 510, 590, 640, 665, 675, 678]  # el auto llega, frena y se detiene dentro de la zona
    for t in range(0, 20):
        x = xs[min(t, len(xs) - 1)]
        move(car, (x, 650, x + 100, 700), float(t))
        move(sign, (650, 650, 750, 700), float(t))
        hits += rule.evaluate(ctx(float(t), [sign, car]))
    assert [h.tracks[0].id for h in hits] == [2]


def test_line_crossing_direction_filter():
    line = Line("puerta", (0.5, 0.95), (0.5, 0.05))  # entrada = izquierda -> derecha
    both = build_rule(LineCrossingRule, lines={"puerta": line})
    only_out = build_rule(LineCrossingRule, lines={"puerta": line}, direccion="salida")
    person = make_track(1, box_at(300, 600))
    assert both.evaluate(ctx(0, [person])) == [] and only_out.evaluate(ctx(0, [person])) == []
    move(person, box_at(700, 600), 1.0)
    hits = both.evaluate(ctx(1, [person]))
    assert len(hits) == 1 and hits[0].extra["sentido"] == "entrada"
    assert only_out.evaluate(ctx(1, [person])) == []
    move(person, box_at(300, 600), 2.0)
    assert both.evaluate(ctx(2, [person]))[0].extra["sentido"] == "salida"
    assert len(only_out.evaluate(ctx(2, [person]))) == 1


def test_line_rule_skipped_without_lines():
    cfg = RuleConfig("cruce_linea", "cruce_linea", Severity.ALTA, 10, params={"lineas": ["puerta"]})
    assert LineCrossingRule.for_camera(cfg, "x", {}, {}) is None


def test_abandoned_object_after_owner_leaves():
    rule = build_rule(AbandonedObjectRule, segundos=20)
    bag = make_track(10, (480, 640, 540, 700), category="equipaje", label="suitcase")
    person = make_track(1, box_at(520, 700))
    hits = []
    for t in range(0, 5):  # el dueño está junto al bolso
        move(bag, (480, 640, 540, 700), float(t))
        move(person, box_at(520, 700), float(t))
        hits += rule.evaluate(ctx(float(t), [bag, person]))
    for t in range(5, 40):  # el dueño se fue
        move(bag, (480, 640, 540, 700), float(t))
        hits += rule.evaluate(ctx(float(t), [bag]))
    assert len(hits) == 1
    assert 20 <= hits[0].extra["segundos"] <= 22
    assert "suitcase" in hits[0].message


def test_object_never_attended_is_ignored():
    rule = build_rule(AbandonedObjectRule, segundos=5)
    bag = make_track(10, (480, 640, 540, 700), category="equipaje")
    assert all(rule.evaluate(ctx(float(t), [bag])) == [] for t in range(30))


def test_dangerous_object_needs_confidence_and_hits():
    rule = build_rule(DangerousObjectRule, confianza_min=0.5, detecciones_min=3)
    knife = make_track(5, (500, 500, 540, 560), category="arma", confidence=0.3, label="knife")
    knife.hits = 1
    knife.max_confidence = 0.3
    assert rule.evaluate(ctx(0, [knife])) == []
    move(knife, (500, 500, 540, 560), 0.2, confidence=0.7)
    move(knife, (500, 500, 540, 560), 0.4, confidence=0.7)
    holder = make_track(1, box_at(520, 700))
    hits = rule.evaluate(ctx(0.4, [knife, holder]))
    assert len(hits) == 1 and hits[0].severity == Severity.CRITICA
    assert {t.id for t in hits[0].tracks} == {5, 1}
    assert rule.evaluate(ctx(0.6, [knife, holder])) == []


def test_crowd():
    rule = build_rule(CrowdRule, umbral=4, segundos=3)
    people = [make_track(i, box_at(100 + i * 150, 800)) for i in range(4)]
    assert rule.evaluate(ctx(0, people)) == []
    assert len(rule.evaluate(ctx(3.5, people))) == 1
    assert rule.evaluate(ctx(5, people)) == []
    assert rule.evaluate(ctx(6, people[:2])) == []
    rule.evaluate(ctx(7, people))
    assert len(rule.evaluate(ctx(10.5, people))) == 1  # se rearma al bajar del umbral


def test_crowd_message_names_the_counted_category():
    rule = build_rule(CrowdRule, categorias=["vehiculo"], umbral=2, segundos=0)
    cars = [make_track(i, box_at(100 + i * 200, 800), category="vehiculo", label="car") for i in range(3)]
    assert rule.evaluate(ctx(1, cars))[0].message == "3 vehículos (umbral: 2)"


def test_custom_title_overrides_default():
    cfg = RuleConfig("intrusion", "peaton", Severity.CRITICA, 30, params={"segundos_min": 0}, title="Peatón en la autopista")
    rule = IntrusionRule.for_camera(cfg, "m.peaton", {}, {})
    hits = rule.evaluate(ctx(0, [make_track(1, box_at(500, 500))]))
    assert hits[0].title == "Peatón en la autopista"


def test_running_vs_walking():
    rule = build_rule(RunningRule, velocidad=1.5, segundos=0.6)
    runner = make_track(1, box_at(100, 600, h=200))
    walker = make_track(2, box_at(100, 900, h=200))
    hits = []
    for i in range(1, 15):
        t = i * 0.2
        move(runner, box_at(100 + 100 * i, 600, h=200), t)  # 500 px/s = 2.5 cuerpos/s
        move(walker, box_at(100 + 30 * i, 900, h=200), t)  # 150 px/s = 0.75 cuerpos/s
        hits += rule.evaluate(ctx(t, [runner, walker]))
    assert [h.tracks[0].id for h in hits] == [1]


def test_fall_detection():
    rule = build_rule(FallRule, segundos=2.0)
    person = make_track(1, box_at(500, 800, w=70, h=180))
    hits = rule.evaluate(ctx(0, [person]))
    for i in range(1, 20):
        t = 0.5 + i * 0.25
        move(person, (400, 720, 600, 800), t)  # tendido: 200x80
        hits += rule.evaluate(ctx(t, [person]))
    assert len(hits) == 1


def test_fall_ignored_if_already_lying():
    rule = build_rule(FallRule, segundos=1.0)
    person = make_track(1, (400, 720, 600, 800))
    assert all(rule.evaluate(ctx(t * 0.5, [person])) == [] for t in range(20))


def test_fight_heuristic():
    rule = build_rule(FightRule, segundos=1.5)
    a = make_track(1, box_at(480, 700))
    b = make_track(2, box_at(540, 700))
    busy = np.ones((100, 100), np.uint8)
    calm = np.zeros((100, 100), np.uint8)
    assert all(rule.evaluate(ctx(t * 0.2, [a, b], motion=calm)) == [] for t in range(15))
    hits = []
    for i in range(15, 30):
        hits += rule.evaluate(ctx(i * 0.2, [a, b], motion=busy))
    assert len(hits) == 1


def test_sabotage_states_and_offline():
    rule = build_rule(SabotageRule, segundos_sin_senal=10)
    ok = TamperStatus("ok", 0)
    covered = TamperStatus("obstruida", 5)
    assert rule.evaluate(ctx(0, [], tamper=ok)) == []
    hits = rule.evaluate(ctx(6, [], tamper=covered))
    assert len(hits) == 1 and hits[0].title == "Cámara obstruida o tapada"
    assert rule.evaluate(ctx(7, [], tamper=covered)) == []
    assert rule.evaluate(ctx(8, [], tamper=ok)) == []
    assert rule.evaluate(ctx(20, [], online=False, offline_since=15)) == []
    hits = rule.evaluate(ctx(26, [], online=False, offline_since=15))
    assert len(hits) == 1 and hits[0].key == "sin_senal"
    assert rule.evaluate(ctx(30, [], online=False, offline_since=15)) == []


def test_engine_filters_by_camera_and_zone():
    rules = [
        RuleConfig("intrusion", "intrusion", Severity.ALTA, 30, zones=["caja"]),
        RuleConfig("intrusion", "bodega", Severity.ALTA, 30, zones=["bodega"]),
        RuleConfig("presencia", "presencia", Severity.CRITICA, 60, cameras=["otra"]),
        RuleConfig("sabotaje", "sabotaje", Severity.ALTA, 300),
    ]
    engine = RuleEngine("modo", rules, "cam1", {"caja": SQUARE}, {})
    assert [r.id for r in engine.rules] == ["modo.intrusion", "modo.sabotaje"]


def test_engine_survives_failing_rule():
    engine = RuleEngine("modo", [RuleConfig("sabotaje", "sabotaje", Severity.ALTA, 300)], "cam1", {}, {})

    def boom(_ctx):
        raise RuntimeError("falla")

    engine.rules[0].evaluate = boom
    assert engine.evaluate(ctx(0, [])) == []


def test_zone_named_rule_in_zone_only():
    left = Zone("izq", ((0, 0), (0.5, 0), (0.5, 1), (0, 1)))
    rule = build_rule(IntrusionRule, {"izq": left, "caja": SQUARE}, zone_names=["izq"], segundos_min=0)
    right_person = make_track(1, box_at(800, 800))
    assert rule.evaluate(ctx(0, [right_person])) == []
    left_person = make_track(2, box_at(200, 800))
    assert len(rule.evaluate(ctx(0, [left_person]))) == 1
