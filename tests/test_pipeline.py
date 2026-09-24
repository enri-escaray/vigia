"""Prueba de punta a punta: el video sintético de la demo pasa por todo el
sistema (detector de movimiento, seguimiento, reglas, alertas y evidencias)."""

import numpy as np
from ruamel.yaml import YAML

from vigia.alerts.manager import AlertManager
from vigia.alerts.notifiers import NotifierHub
from vigia.alerts.recorder import detect_codec
from vigia.alerts.store import AlertStore
from vigia.config import parse_config
from vigia.demo import DURATION, demo_config_yaml, render_frame
from vigia.hub import EventBroadcaster
from vigia.modes import ModeManager
from vigia.pipeline import CameraPipeline
from vigia.vision.detectors import MotionDetector

T0 = 1_760_000_000.0


def build(tmp_path):
    data = YAML(typ="safe").load(demo_config_yaml("no_se_usa.mp4", 8080))
    cfg = parse_config(data, base_dir=tmp_path)
    store = AlertStore(tmp_path / "vigia.db")
    alerts = AlertManager(store, cfg.data_dir, NotifierHub([]), EventBroadcaster())
    modes = ModeManager(cfg.modes, None, clock=lambda: T0)
    pipeline = CameraPipeline(
        cfg.cameras[0], cfg, modes, alerts, detect_codec(tmp_path / "clips"), detector_factory=MotionDetector
    )
    return pipeline, store


def test_demo_scene_triggers_expected_alerts(tmp_path):
    pipeline, store = build(tmp_path)
    rng = np.random.default_rng(0)
    alerts = []
    fps = 10
    for i in range(int(DURATION * fps)):
        t = i / fps
        alerts += pipeline.process_frame(render_frame(t, rng), T0 + t)
    pipeline.recorder.flush()

    by_type = {}
    for a in alerts:
        by_type.setdefault(a.rule_type, []).append(a)
    assert set(by_type) == {"cruce_linea", "intrusion", "merodeo", "carrera", "sabotaje"}, [
        (a.rule_type, round(a.ts - T0, 1), a.message) for a in alerts
    ]
    crossings = sorted(a.extra["sentido"] for a in by_type["cruce_linea"])
    assert crossings == ["entrada", "salida"]
    assert 12 <= by_type["intrusion"][0].ts - T0 <= 16
    assert by_type["merodeo"][0].ts - T0 >= 20
    assert 33 <= by_type["carrera"][0].ts - T0 <= 36.5
    assert by_type["sabotaje"][0].extra["estado"] == "obstruida"
    assert 40 <= by_type["sabotaje"][0].ts - T0 <= 43

    # Evidencias: captura de cada alerta y al menos un clip escrito.
    assert all((tmp_path / a.snapshot).is_file() for a in alerts)
    assert any(a.clip and (tmp_path / a.clip).is_file() for a in alerts)
    assert len(store.list()) == len(alerts)


def test_signal_loss_raises_alert(tmp_path):
    pipeline, store = build(tmp_path)
    rng = np.random.default_rng(0)
    for i in range(20):
        pipeline.process_frame(render_frame(i / 10, rng), T0 + i / 10)
    pipeline._last_frame_time = T0 + 2.0
    for k in range(5, 20):
        pipeline._idle(T0 + 2.0 + k)
    alerts = store.list()
    assert [a.rule_type for a in alerts] == ["sabotaje"]
    assert alerts[0].title == "Cámara sin señal"
    assert alerts[0].snapshot is not None  # usa el último cuadro visto
    status = pipeline.status()
    assert status["estado"] == "sin señal" and not status["en_linea"]
    assert status["objetos"] == 0 and pipeline.tracker.tracks == []  # no quedan objetos "congelados"


def test_no_false_signal_loss_while_first_connecting(tmp_path):
    pipeline, store = build(tmp_path)
    pipeline._started_at = T0
    for k in range(1, 15):  # la cámara todavía se está conectando
        pipeline._idle(T0 + k)
    assert store.list() == []
    assert pipeline.hub.latest()[1] is not None  # muestra "CONECTANDO..."
    for k in range(15, 30):  # nunca llegó imagen: ahora sí es una falla
        pipeline._idle(T0 + k)
    assert [a.title for a in store.list()] == ["Cámara sin señal"]


def test_fixed_mode_ignores_global_mode(tmp_path):
    pipeline, _ = build(tmp_path)
    pipeline.camera.mode = "desarmado"  # la modalidad general es "demo"
    pipeline.process_frame(render_frame(0), T0)
    assert pipeline.engine.mode == "desarmado"
    assert [r.id for r in pipeline.engine.rules] == ["desarmado.sabotaje"]
    status = pipeline.status()
    assert status["modalidad"] == "desarmado" and status["modalidad_fija"] is True


class _StubDetector:
    """Detector falso: un "vehículo" quieto que el modelo ve con poca confianza
    (un cartel), un auto estacionado que ve con seguridad, uno que avanza y otro
    dentro de una zona de exclusión."""

    name = "prueba"
    categories = {"vehiculo"}

    def __init__(self):
        self.calls = 0

    def detect(self, frame):
        from vigia.types import Detection

        self.calls += 1
        x = 100 + self.calls * 20
        return [
            Detection((800, 300, 860, 340), 0.3, "truck", "vehiculo"),  # cartel: nunca se mueve
            Detection((600, 150, 700, 200), 0.8, "bus", "vehiculo"),  # estacionado
            Detection((x, 400, x + 60, 440), 0.45, "car", "vehiculo"),  # auto real
            Detection((50, 450, 110, 490), 0.6, "car", "vehiculo"),  # dentro de "ignorar_baliza"
        ]


def test_static_vehicles_and_ignore_zones_are_not_shown(tmp_path):
    from vigia.geometry import Zone

    pipeline, _ = build(tmp_path)
    pipeline._detector_factory = _StubDetector
    pipeline.set_geometry([Zone("ignorar_baliza", ((0.0, 0.7), (0.2, 0.7), (0.2, 1.0), (0.0, 1.0)))], [])
    for i in range(12):
        pipeline.process_frame(render_frame(i / 10), T0 + i * 0.25)
    labels = sorted(tr.label for tr in pipeline.tracker.tracks)
    assert labels == ["bus", "car", "truck"]  # lo de la zona de exclusión ni siquiera se sigue
    # El cartel quieto no cuenta; el auto que avanza y el estacionado (reconocido con seguridad), sí.
    assert sorted(tr.label for tr in pipeline.active_tracks()) == ["bus", "car"]
    assert pipeline.status()["objetos"] == 2


def test_geometry_hot_update(tmp_path):
    pipeline, _ = build(tmp_path)
    pipeline.process_frame(render_frame(0), T0)
    assert {r.id for r in pipeline.engine.rules} >= {"demo.intrusion", "demo.cruce_linea"}
    pipeline.set_geometry([], [])
    pipeline.process_frame(render_frame(0.1), T0 + 0.1)
    assert {r.id for r in pipeline.engine.rules} == {"demo.carrera", "demo.sabotaje"}
