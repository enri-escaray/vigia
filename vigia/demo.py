"""Demostración sin cámaras: genera un video sintético con situaciones
sospechosas y una configuración que usa el detector de movimiento.

Guion (se repite cada 45 s):
  3-11 s   una persona cruza la línea «acceso» de izquierda a derecha (entrada)
  12-32 s  otra entra a la zona «restringida» y se queda allí (intrusión y merodeo)
  33-36 s  alguien pasa corriendo de derecha a izquierda (carrera y cruce de salida)
  38-43 s  la cámara es tapada (sabotaje)
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from vigia.alerts.recorder import cv_path

WIDTH, HEIGHT, FPS, DURATION = 960, 540, 15, 45.0

# Geometría de la demo (normalizada), compartida con la configuración generada.
ZONE_RESTRICTED = [[0.62, 0.42], [0.97, 0.42], [0.97, 0.97], [0.62, 0.97]]
LINE_ACCESS = [[0.35, 0.97], [0.35, 0.08]]  # de abajo hacia arriba: "entrada" = izquierda -> derecha


def _background() -> np.ndarray:
    rng = np.random.default_rng(7)
    img = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
    for y in range(HEIGHT):  # pared y piso con degradé
        shade = 120 + int(50 * y / HEIGHT) if y > HEIGHT * 0.35 else 150 - int(40 * y / HEIGHT)
        img[y, :] = (shade - 10, shade, shade + 8)
    img[int(HEIGHT * 0.35) : int(HEIGHT * 0.36), :] = (70, 75, 80)
    cv2.rectangle(img, (40, 40), (190, 185), (85, 60, 40), -1)  # puerta
    cv2.rectangle(img, (52, 52), (178, 180), (110, 80, 55), 3)
    cv2.rectangle(img, (420, 60), (620, 150), (160, 200, 215), -1)  # ventana
    for x in range(420, 621, 50):
        cv2.line(img, (x, 60), (x, 150), (90, 90, 90), 3)
    cv2.rectangle(img, (720, 110), (900, 190), (60, 90, 120), -1)  # estante
    for x in range(730, 900, 22):
        cv2.rectangle(img, (x, 120), (x + 14, 180), (int(rng.integers(40, 200)), int(rng.integers(40, 200)), int(rng.integers(40, 200))), -1)
    for _ in range(260):  # baldosas / textura fija del piso
        x, y = int(rng.integers(0, WIDTH)), int(rng.integers(int(HEIGHT * 0.4), HEIGHT))
        cv2.circle(img, (x, y), int(rng.integers(2, 5)), (95, 100, 105), -1)
    for x in range(0, WIDTH, 80):
        cv2.line(img, (x, int(HEIGHT * 0.36)), (x - 120, HEIGHT), (105, 110, 115), 1)
    return img


_BG = _background()
_NOISE: list[tuple[np.ndarray, np.ndarray]] = []


def _noise_bank() -> list[tuple[np.ndarray, np.ndarray]]:
    """Patrones de ruido de sensor precalculados (generarlos en cada cuadro es lento)."""
    if not _NOISE:
        gen = np.random.default_rng(11)
        for _ in range(8):
            _NOISE.append(
                (
                    gen.integers(0, 4, (HEIGHT, WIDTH, 3), dtype=np.uint8),
                    gen.integers(0, 4, (HEIGHT, WIDTH, 3), dtype=np.uint8),
                )
            )
    return _NOISE


def _person(img: np.ndarray, foot_x: float, foot_y: float, height: int, color: tuple[int, int, int], phase: float) -> None:
    """Silueta simple: piernas, torso, brazos y cabeza (coordenadas del punto de apoyo)."""
    x, y = int(foot_x), int(foot_y)
    body_w = max(8, height // 4)
    leg = int(height * 0.45)
    swing = int(math.sin(phase) * body_w * 0.5)
    cv2.line(img, (x, y - leg), (x - body_w // 3 + swing, y), color, max(3, body_w // 3))
    cv2.line(img, (x, y - leg), (x + body_w // 3 - swing, y), color, max(3, body_w // 3))
    cv2.rectangle(img, (x - body_w // 2, y - int(height * 0.82)), (x + body_w // 2, y - leg), color, -1)
    cv2.line(img, (x - body_w // 2, y - int(height * 0.78)), (x - body_w // 2 - swing // 2, y - int(height * 0.5)), color, max(2, body_w // 4))
    cv2.line(img, (x + body_w // 2, y - int(height * 0.78)), (x + body_w // 2 + swing // 2, y - int(height * 0.5)), color, max(2, body_w // 4))
    cv2.circle(img, (x, y - int(height * 0.9)), max(5, int(height * 0.09)), color, -1)


def render_frame(t: float, rng: np.random.Generator | None = None) -> np.ndarray:
    """Cuadro del video de demostración en el segundo t."""
    t = t % DURATION
    img = _BG.copy()
    # A: cruza la línea de izquierda a derecha por el fondo (fuera de la zona restringida).
    if 3.0 <= t < 11.0:
        k = (t - 3.0) / 8.0
        _person(img, -40 + k * (WIDTH + 80), HEIGHT * 0.33, 120, (40, 45, 60), t * 9)
    # B: entra a la zona restringida y se queda (con pequeños movimientos).
    if 12.0 <= t < 32.0:
        if t < 14.0:
            k = (t - 12.0) / 2.0
            fx, fy = WIDTH + 40 - k * 260, HEIGHT * 0.9 - k * 50
            phase = t * 9
        elif t < 30.0:
            fx = WIDTH - 220 + math.sin(t * 1.3) * 14
            fy = HEIGHT * 0.9 - 50 + math.cos(t * 0.9) * 6
            phase = math.sin(t * 2) * 0.6
        else:
            k = (t - 30.0) / 2.0
            fx, fy = WIDTH - 220 + k * 280, HEIGHT * 0.9 - 50
            phase = t * 9
        _person(img, fx, fy, 170, (35, 30, 90), phase)
    # C: pasa corriendo de derecha a izquierda.
    if 33.0 <= t < 36.0:
        k = (t - 33.0) / 3.0
        _person(img, WIDTH + 40 - k * (WIDTH + 80), HEIGHT * 0.33, 125, (70, 40, 30), t * 18)
    # Sabotaje: algo tapa la cámara.
    if 38.0 <= t < 43.0:
        img[:] = (28, 30, 32)
    if rng is not None:  # ruido del sensor
        bank = _noise_bank()
        up, down = bank[int(rng.integers(0, len(bank)))]
        img = cv2.subtract(cv2.add(img, up), down)
    return img


def write_demo_video(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(cv_path(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT))
    if not writer.isOpened():
        raise RuntimeError(f"no se pudo crear el video de demostración en {path}")
    rng = np.random.default_rng(1)
    for i in range(int(DURATION * FPS)):
        writer.write(render_frame(i / FPS, rng))
    writer.release()
    return path


def demo_config_yaml(video: str, port: int) -> str:
    zone = ", ".join(f"[{x}, {y}]" for x, y in ZONE_RESTRICTED)
    line = ", ".join(f"[{x}, {y}]" for x, y in LINE_ACCESS)
    return f"""# Configuración de la demostración (generada por `python -m vigia demo`).
sistema:
  nombre: "Vigía · Demostración"
  datos: .
  retencion_dias: 2

web:
  puerto: {port}

grabacion:
  espacio_max_gb: 0.5   # la demo genera alertas cada pocos segundos

deteccion:
  tipo: movimiento   # el video es sintético: YOLO no reconocería las siluetas

camaras:
  - id: demo
    nombre: "Pasillo (demo)"
    fuente: {video}
    fps: 10
    fps_deteccion: 5
    zonas:
      - nombre: restringida
        puntos: [{zone}]
    lineas:
      - nombre: acceso
        puntos: [{line}]

modalidades:
  inicial: demo
  por_defecto: demo
  definiciones:
    demo:
      etiqueta: "Demostración"
      descripcion: "Cruce de línea, intrusión, merodeo, carrera y sabotaje sobre manchas de movimiento"
      color: "#a855f7"
      reglas:
        - tipo: cruce_linea
          lineas: [acceso]
          categorias: [movimiento]
          severidad: media
        - tipo: intrusion
          zonas: [restringida]
          categorias: [movimiento]
          severidad: alta
        - tipo: merodeo
          zonas: [restringida]
          categorias: [movimiento]
          segundos: 8
          severidad: media
        - tipo: carrera
          categorias: [movimiento]
          velocidad: 1.5
          severidad: media
        - tipo: sabotaje
          severidad: critica
    desarmado:
      etiqueta: "Desarmado"
      descripcion: "Solo vigila la integridad de la cámara"
      color: "#64748b"
      reglas:
        - tipo: sabotaje
          severidad: media

notificaciones:
  severidad_minima: media
  sonido:
    activo: no
"""


def prepare_demo(folder: Path, port: int = 8080) -> Path:
    """Crea (si faltan) el video y la configuración de la demo; devuelve la ruta del YAML.
    Si la configuración ya existe se respeta (conserva las zonas editadas en el panel)."""
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "escena_demo.mp4"
    if not video.is_file():
        write_demo_video(video)
    config = folder / "config_demo.yaml"
    if not config.is_file():
        config.write_text(demo_config_yaml(video.name, port), encoding="utf-8")
    return config
