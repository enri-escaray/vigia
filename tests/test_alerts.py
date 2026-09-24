import time

import numpy as np

from helpers import make_track
from vigia.alerts.manager import AlertManager
from vigia.alerts.model import Alert
from vigia.alerts.notifiers import Notifier, NotifierHub
from vigia.alerts.recorder import ClipRecorder, detect_codec
from vigia.alerts.store import AlertStore
from vigia.hub import EventBroadcaster
from vigia.rules.base import RuleHit
from vigia.types import Severity


class Recorder(Notifier):
    name = "prueba"

    def __init__(self, min_severity=Severity.MEDIA):
        super().__init__({"severidad_minima": min_severity})
        self.sent = []

    def send(self, alert, snapshot):
        self.sent.append((alert, snapshot))


def hit(key="k", severity=Severity.ALTA, cooldown=30.0):
    return RuleHit(
        rule_id="modo.intrusion",
        rule_type="intrusion",
        title="Intrusión",
        message="Persona #1 ingresó",
        severity=severity,
        key=key,
        cooldown=cooldown,
        tracks=[make_track(1, (100, 100, 200, 400))],
        zone="caja",
    )


def make_manager(tmp_path, notifier):
    store = AlertStore(tmp_path / "vigia.db")
    hub = NotifierHub([notifier])
    return AlertManager(store, tmp_path, hub, EventBroadcaster()), store, hub


def test_cooldown_and_evidence(tmp_path):
    notifier = Recorder()
    manager, store, hub = make_manager(tmp_path, notifier)
    frame = np.zeros((480, 640, 3), np.uint8)
    t0 = 1_760_000_000.0
    first = manager.process(hit(), "cam1", "Entrada", "hogar", frame, t0)
    assert first is not None and first.id == 1
    assert (tmp_path / first.snapshot).is_file()
    assert manager.process(hit(), "cam1", "Entrada", "hogar", frame, t0 + 10) is None  # enfriamiento
    assert manager.process(hit(key="otra"), "cam1", "Entrada", "hogar", frame, t0 + 10) is not None
    assert manager.process(hit(), "cam2", "Patio", "hogar", frame, t0 + 10) is not None  # otra cámara
    assert manager.process(hit(), "cam1", "Entrada", "hogar", frame, t0 + 31) is not None
    hub.close()
    assert len(notifier.sent) == 4
    assert notifier.sent[0][1] == tmp_path / first.snapshot
    assert len(store.list()) == 4


def test_loitering_does_not_repeat_when_track_id_changes(tmp_path):
    from helpers import SQUARE, box_at, build_rule, ctx

    from vigia.rules.zones import LoiteringRule

    manager, store, hub = make_manager(tmp_path, Recorder())
    rule = build_rule(LoiteringRule, {"caja": SQUARE}, zone_names=["caja"], segundos=5)
    alerts = []
    for tid, start in ((1, 0), (2, 20)):  # la misma persona, "perdida" y retomada con otro número
        person = make_track(tid, box_at(700, 700), t=float(start))
        for t in range(start, start + 10):
            for h in rule.evaluate(ctx(float(t), [person])):
                alerts.append(manager.process(h, "cam1", "Entrada", "m", None, 1_760_000_000.0 + t))
    hub.close()
    assert len([a for a in alerts if a is not None]) == 1
    assert len(store.list()) == 1


def test_notifier_severity_filter(tmp_path):
    notifier = Recorder(min_severity=Severity.CRITICA)
    manager, _, hub = make_manager(tmp_path, notifier)
    manager.process(hit(severity=Severity.ALTA), "cam1", "Entrada", "hogar", None, time.time())
    manager.process(hit(key="x", severity=Severity.CRITICA), "cam1", "Entrada", "hogar", None, time.time())
    hub.close()
    assert [a.severity for a, _ in notifier.sent] == [Severity.CRITICA]


def test_store_filters_ack_and_purge(tmp_path):
    store = AlertStore(tmp_path / "vigia.db")
    base = dict(camera_name="C", mode="m", rule_id="m.r", rule_type="r", title="t", message="m")
    ids = [
        store.add(Alert(ts=100.0, camera_id="a", severity=Severity.BAJA, snapshot="capturas/x.jpg", **base)),
        store.add(Alert(ts=200.0, camera_id="b", severity=Severity.CRITICA, **base)),
        store.add(Alert(ts=300.0, camera_id="a", severity=Severity.ALTA, extra={"k": 1}, **base)),
    ]
    assert [a.id for a in store.list()] == ids[::-1]
    assert [a.id for a in store.list(camera_id="a")] == [ids[2], ids[0]]
    assert [a.id for a in store.list(min_severity=3)] == [ids[2], ids[1]]
    assert [a.id for a in store.list(before_id=ids[2])] == [ids[1], ids[0]]
    assert store.get(ids[2]).extra == {"k": 1}
    assert store.acknowledge(ids[1]) and not store.acknowledge(ids[1])
    assert [a.id for a in store.list(pending_only=True)] == [ids[2], ids[0]]
    assert store.summary(since=0)["pendientes"] == 2
    assert store.acknowledge_all() == 2
    assert store.purge_older_than(150.0) == ["capturas/x.jpg"]
    assert len(store.list()) == 2


def test_clip_storage_limit_deletes_oldest(tmp_path):
    import os

    from vigia.alerts.recorder import enforce_clip_storage

    day = tmp_path / "clips" / "2026-09-01"
    day.mkdir(parents=True)
    for i in range(5):
        clip = day / f"clip{i}.mp4"
        clip.write_bytes(b"x" * 1000)
        os.utime(clip, (1000 + i, 1000 + i))  # clip0 es el más viejo
    (day / "clip9.parcial.mp4").write_bytes(b"x" * 5000)  # en escritura: no se toca
    assert enforce_clip_storage(tmp_path / "clips", 2500) == 3
    assert sorted(p.name for p in day.iterdir()) == ["clip3.mp4", "clip4.mp4", "clip9.parcial.mp4"]
    assert enforce_clip_storage(tmp_path / "clips", 0) == 0  # 0 = sin límite


def test_clip_closes_on_time_when_the_camera_loses_signal(tmp_path):
    """Caso real: la cámara se cortó; el clip quedaba abierto hasta que volvía la
    imagen 80 s después y salía de 86 s (el máximo es 30)."""
    import cv2

    codec = detect_codec(tmp_path / "clips")
    recorder = ClipRecorder("cam1", tmp_path, codec, pre=1.0, post=1.0, max_len=30)
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), 90, np.uint8))
    t0 = 1_760_000_000.0
    for i in range(20):  # llega imagen durante 2 s y después se corta
        recorder.push(t0 + i * 0.1, buf.tobytes())
    rel = recorder.trigger(t0 + 20.0)  # alerta "sin señal" a los 20 s
    recorder.tick(t0 + 20.5)
    assert recorder._job is not None  # todavía no terminó el clip
    recorder.tick(t0 + 21.5)
    assert recorder._job is None  # se cerró sin esperar a que vuelva la imagen
    recorder.push(t0 + 80.0, buf.tobytes())  # vuelve la señal: no se agrega a ningún clip
    recorder.flush()
    cap = cv2.VideoCapture(str(tmp_path / rel))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    assert 5 <= frames <= 11 and frames / fps < 2  # el último segundo antes del corte


def test_clip_recorder_limits_width(tmp_path):
    import cv2

    codec = detect_codec(tmp_path / "clips")
    recorder = ClipRecorder("cam1", tmp_path, codec, pre=0.5, post=0.5, max_width=320)
    ok, buf = cv2.imencode(".jpg", np.full((360, 640, 3), 90, np.uint8))
    t0 = 1_760_000_000.0
    for i in range(5):
        recorder.push(t0 + i * 0.1, buf.tobytes())
    rel = recorder.trigger(t0 + 0.5)
    for i in range(5, 12):
        recorder.push(t0 + i * 0.1, buf.tobytes())
    recorder.flush()
    cap = cv2.VideoCapture(str(tmp_path / rel))
    ok, frame = cap.read()
    cap.release()
    assert ok and frame.shape[1] == 320 and frame.shape[0] == 180


def test_clip_recorder_writes_pre_and_post_event(tmp_path):
    import cv2

    codec = detect_codec(tmp_path / "clips")
    assert codec is not None
    recorder = ClipRecorder("cam1", tmp_path, codec, pre=1.0, post=1.0)
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), 90, np.uint8))
    jpeg = buf.tobytes()
    t0 = 1_760_000_000.0
    for i in range(20):  # 2 s previos a 10 fps
        recorder.push(t0 + i * 0.1, jpeg)
    rel = recorder.trigger(t0 + 2.0)
    assert rel and rel.startswith("clips/")
    assert recorder.trigger(t0 + 2.2) == rel  # una segunda alerta reutiliza el mismo clip
    for i in range(20, 36):
        recorder.push(t0 + i * 0.1, jpeg)
    recorder.flush()
    clip = tmp_path / rel
    assert clip.is_file() and clip.stat().st_size > 0
    cap = cv2.VideoCapture(str(clip))
    frames = 0
    while cap.read()[0]:
        frames += 1
    cap.release()
    assert frames >= 20  # ~1 s antes + ~1.2 s después
