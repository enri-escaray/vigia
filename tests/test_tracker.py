from vigia.types import Detection
from vigia.vision.tracker import Tracker

FRAME = (1000, 1000)


def det(x, y, w=50, h=150, category="persona"):
    return Detection((x, y, x + w, y + h), 0.9, "person", category)


def test_same_object_keeps_id_and_gets_confirmed():
    tracker = Tracker(min_hits=3)
    ids = set()
    for i in range(6):
        tracks = tracker.update([det(100 + i * 10, 200)], i * 0.2, FRAME)
        assert len(tracks) == 1
        ids.add(tracks[0].id)
        if i < 2:
            assert not tracks[0].confirmed
    assert len(ids) == 1
    assert tracker.confirmed()[0].hits == 6


def test_two_objects_get_different_ids():
    tracker = Tracker(min_hits=1)
    for i in range(4):
        tracker.update([det(100 + i * 5, 200), det(600 - i * 5, 200)], i * 0.2, FRAME)
    tracks = tracker.tracks
    assert len(tracks) == 2
    assert tracks[0].center[0] < tracks[1].center[0]


def test_fast_object_matched_by_distance():
    tracker = Tracker(min_hits=1)
    # 80 px por paso con cajas de 50 px de ancho: no hay solape, pero sí cercanía.
    for i in range(5):
        tracker.update([det(100 + i * 80, 300)], i * 0.2, FRAME)
    assert len(tracker.tracks) == 1
    assert tracker.tracks[0].speed(window=0.8) > 300


def test_fast_vehicle_needs_category_distance():
    """Un auto chico (40 px de alto) que avanza 90 px entre detecciones, como en
    una autopista de noche a 4 detecciones por segundo."""
    def run(tracker):
        for i in range(6):
            tracker.update([det(100 + i * 90, 400, w=60, h=40, category="vehiculo")], i * 0.25, FRAME)
        return tracker

    assert len(run(Tracker(min_hits=3)).confirmed()) == 0  # cada detección parece un auto nuevo
    tracked = run(Tracker(min_hits=3, max_distance_by_category={"vehiculo": 2.5}))
    assert len(tracked.tracks) == 1 and len(tracked.confirmed()) == 1


def test_moved_flag_distinguishes_signs_from_cars():
    tracker = Tracker(min_hits=1, max_distance_by_category={"vehiculo": 2.5})
    for i in range(8):
        tracker.update(
            [
                det(800 + (i % 2) * 2, 600, w=40, h=30, category="vehiculo"),  # baliza: solo "tiembla" 2 px
                det(100 + i * 25, 300, w=60, h=40, category="vehiculo"),  # auto: avanza
            ],
            i * 0.25,
            FRAME,
        )
    by_x = sorted(tracker.tracks, key=lambda t: t.center[0])
    assert [t.moved for t in by_x] == [True, False]


def test_reflector_is_not_swapped_with_a_passing_car():
    """Caso real: una baliza (chica y quieta) que a veces no se detecta, y un auto
    que pasa justo al lado. La baliza no debe "heredar" el movimiento del auto."""
    tracker = Tracker(min_hits=2, max_distance_by_category={"vehiculo": 2.5})
    reflector = det(800, 600, w=20, h=25, category="vehiculo")
    for i in range(12):
        car = det(680 + i * 30, 585, w=60, h=40, category="vehiculo")
        dets = [car] if i % 3 == 1 else [car, reflector]  # la baliza falla una de cada tres
        tracker.update(dets, i * 0.25, FRAME)
    small = [t for t in tracker.tracks if t.width < 30]
    big = [t for t in tracker.tracks if t.width >= 30]
    assert len(small) == 1 and not small[0].moved
    assert len(big) == 1 and big[0].moved


def test_static_clutter_learns_objects_not_busy_places():
    """Un cartel quieto que el modelo ve a ratos y con poca confianza se aprende
    como decorado; los autos que pasan uno tras otro por el mismo punto de un
    carril, no (este era un error real), ni un auto estacionado que el modelo
    reconoce con seguridad."""
    from vigia.vision.tracker import StaticClutter

    tracker = Tracker(min_hits=2, max_distance_by_category={"vehiculo": 2.5})
    clutter = StaticClutter({"vehiculo"}, learn_after=30, forget_after=20)
    sign = Detection((900, 500, 960, 545), 0.3, "truck", "vehiculo")
    parked = Detection((500, 700, 600, 760), 0.8, "car", "vehiculo")
    person = det(900, 500, w=60, h=45)  # otras categorías no se tocan
    for step in range(200):  # 50 s a 4 detecciones por segundo
        t = step * 0.25
        car = det(100 + (step % 8) * 60, 300, w=60, h=40, category="vehiculo")  # un auto nuevo cada 2 s
        dets = [car, parked, person] + ([sign] if step % 5 else [])  # el cartel se ve a ratos
        kept, pinned = clutter.filter(dets, t)
        assert car in kept and parked in kept and person in kept
        if sign in dets and t <= 31.0:
            assert sign in kept
            assert (kept.index(sign) in pinned) == (t >= 2.0)  # lugar fijado tras 1,5 s quieto
        elif sign in dets and t >= 32.5:
            assert sign not in kept  # ya es decorado
        tracker.update(kept, t, FRAME, pinned)
        clutter.learn(tracker.tracks, t)
    assert clutter.learned == 1
    clutter.filter([], 70.0)  # el cartel dejó de verse más de 20 s: se olvida
    assert clutter.learned == 0 and sign in clutter.filter([sign], 70.5)[0]


def test_track_that_jumps_onto_a_still_object_loses_its_movement():
    """Caso real: un auto que se pierde bajo un puente y su seguimiento "salta"
    al cartel de al lado. Un auto no frena de golpe: se vuelve un objeto quieto."""
    tracker = Tracker(min_hits=2, max_distance_by_category={"vehiculo": 2.5})
    for i in range(4):  # un auto que avanza rápido
        tracker.update([Detection((100 + i * 50, 400, 160 + i * 50, 440), 0.8, "car", "vehiculo")], i * 0.25, FRAME)
    (car,) = tracker.tracks
    assert car.moved
    sign = Detection((340, 395, 400, 435), 0.3, "truck", "vehiculo")  # donde "debería" estar el auto
    tracker.update([sign], 1.0, FRAME)
    assert car.moved  # todavía no se sabe
    tracker.update([sign], 1.25, FRAME)
    assert tracker.tracks == [car] and not car.moved  # llegó de golpe y se quedó quieto
    assert car.recent_confidence == 0.3  # la confianza del auto no cuenta


def test_gradual_stop_keeps_movement():
    tracker = Tracker(min_hits=2)
    xs = [100, 150, 195, 230, 255, 268, 273, 274, 274, 274]  # frena de a poco
    for i, x in enumerate(xs):
        tracker.update([Detection((x, 400, x + 60, 440), 0.3, "car", "vehiculo")], i * 0.25, FRAME)
    (car,) = tracker.tracks
    assert car.moved


def test_pinned_detection_is_only_taken_by_overlap():
    """Una detección en un lugar fijado (algo quieto) no se asocia por cercanía
    con un objeto que viene moviéndose."""

    def run(pinned):
        tracker = Tracker(min_hits=1, max_distance_by_category={"vehiculo": 2.5})
        for i in range(3):
            tracker.update([Detection((100 + i * 50, 400, 160 + i * 50, 440), 0.8, "car", "vehiculo")], i * 0.25, FRAME)
        tracker.update([Detection((290, 380, 350, 420), 0.3, "truck", "vehiculo")], 0.75, FRAME, pinned)
        return tracker

    assert len(run(set()).tracks) == 1  # por cercanía, el auto "salta" a la detección
    assert len(run({0}).tracks) == 2  # fijada: es otro objeto


def test_lost_object_is_forgotten_after_max_age():
    tracker = Tracker(min_hits=1, max_age=1.0)
    tracker.update([det(100, 100)], 0.0, FRAME)
    tracker.update([], 0.5, FRAME)
    assert len(tracker.tracks) == 1
    tracker.update([], 1.6, FRAME)
    assert tracker.tracks == []
    assert tracker.removed


def test_categories_are_not_mixed():
    tracker = Tracker(min_hits=1)
    tracker.update([det(100, 100, category="persona")], 0.0, FRAME)
    tracker.update([det(100, 100, category="vehiculo")], 0.2, FRAME)
    assert sorted(t.category for t in tracker.tracks) == ["persona", "vehiculo"]


def test_category_specific_max_age():
    tracker = Tracker(min_hits=1, max_age=1.0, max_age_by_category={"equipaje": 10.0})
    tracker.update([det(100, 100, category="equipaje"), det(500, 100)], 0.0, FRAME)
    tracker.update([], 5.0, FRAME)
    assert [t.category for t in tracker.tracks] == ["equipaje"]
