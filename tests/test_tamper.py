import cv2
import numpy as np

from vigia.vision.tamper import TamperDetector


def scene(seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((360, 640, 3), 120, np.uint8)
    for _ in range(40):
        x, y = int(rng.integers(0, 600)), int(rng.integers(0, 330))
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        cv2.rectangle(img, (x, y), (x + int(rng.integers(10, 80)), y + int(rng.integers(10, 60))), color, -1)
    return img


def feed(detector: TamperDetector, frame: np.ndarray, start: float, seconds: float, step: float = 0.5) -> str:
    t = start
    while t < start + seconds:
        detector.update(frame, t)
        t += step
    return detector.status.state


def calibrated() -> TamperDetector:
    detector = TamperDetector(warmup=2.0, hold=2.0, interval=0.5)
    assert feed(detector, scene(), 0, 3) == "ok"
    return detector


def test_normal_scene_stays_ok():
    detector = calibrated()
    assert feed(detector, scene(), 3, 10) == "ok"


def test_covered_camera():
    detector = calibrated()
    covered = np.full((360, 640, 3), 30, np.uint8)
    assert feed(detector, covered, 3, 1.0) == "ok"  # aún no pasa el tiempo de confirmación
    assert feed(detector, covered, 4, 3.0) == "obstruida"
    assert feed(detector, scene(), 7, 1.0) == "ok"  # se recupera


def test_blurred_camera():
    detector = calibrated()
    blurred = cv2.GaussianBlur(scene(), (0, 0), 12)
    assert feed(detector, blurred, 3, 4) == "desenfocada"


def test_moved_camera():
    detector = calibrated()
    assert feed(detector, scene(seed=99), 3, 5) == "ok"  # una cámara movida se confirma a los 10 s
    assert feed(detector, scene(seed=99), 8, 6) == "movida"


def test_moved_camera_detected_even_with_people_in_view():
    detector = calibrated()
    t = 3.0
    while t < 16:
        detector.update(scene(seed=99), t, ignore=[(20, 40, 120, 300)])
        t += 0.5
    assert detector.status.state == "movida"


def test_person_close_to_the_lens_is_not_a_moved_camera():
    """Con una webcam, la cabeza de quien está enfrente ocupa buena parte de la
    imagen y se mueve: no debe confundirse con una cámara girada."""
    detector = calibrated()
    base = scene()
    t = 3.0
    for i in range(40):
        frame = base.copy()
        cx = 150 + (i % 10) * 35
        cv2.ellipse(frame, (cx, 250), (130, 170), 0, 0, 360, (40, 60, 90), -1)
        cv2.circle(frame, (cx - 40, 220), 14, (230, 230, 230), -1)
        detector.update(frame, t, ignore=[(cx - 130, 80, cx + 130, 360)])
        t += 0.5
    assert detector.status.state == "ok"


def test_sudden_darkness():
    detector = calibrated()
    dark = (scene() * 0.12).astype(np.uint8)
    assert feed(detector, dark, 3, 4) in ("oscura", "obstruida")
