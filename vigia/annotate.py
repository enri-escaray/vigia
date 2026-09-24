"""Dibujo de zonas, líneas, objetos y estado sobre el video."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable

import cv2
import numpy as np

from vigia.geometry import Line, Zone
from vigia.types import strip_accents
from vigia.vision.tracker import Track

# Colores BGR
CATEGORY_COLORS = {
    "persona": (90, 220, 90),
    "vehiculo": (255, 170, 60),
    "equipaje": (60, 220, 255),
    "arma": (60, 60, 255),
    "animal": (220, 120, 220),
    "movimiento": (255, 230, 0),
}
DEFAULT_COLOR = (200, 200, 200)
ZONE_COLOR = (0, 190, 255)
ZONE_BUSY_COLOR = (60, 60, 255)
ZONE_IGNORE_COLOR = (130, 130, 130)
LINE_COLOR = (255, 200, 40)
ALERT_COLOR = (40, 40, 235)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def hex_to_bgr(value: str, default: tuple[int, int, int] = (200, 120, 40)) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return default
    try:
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default
    return (b, g, r)


def _text(img: np.ndarray, text: str, org: tuple[int, int], scale: float, color, thickness: int = 1, bg=None) -> None:
    text = strip_accents(text)
    (tw, th), base = cv2.getTextSize(text, FONT, scale, thickness)
    x, y = org
    if bg is not None:
        cv2.rectangle(img, (x - 3, y - th - 4), (x + tw + 3, y + base + 1), bg, -1)
    cv2.putText(img, text, (x, y), FONT, scale, color, thickness, cv2.LINE_AA)


class Annotator:
    def __init__(self, camera_name: str) -> None:
        self.camera_name = camera_name

    def draw(
        self,
        frame: np.ndarray,
        *,
        tracks: Iterable[Track],
        zones: Iterable[Zone],
        lines: Iterable[Line],
        mode_label: str,
        mode_color: str,
        fps: float,
        now: float,
        banner: str | None = None,
    ) -> np.ndarray:
        out = frame.copy()
        h, w = out.shape[:2]
        scale = max(0.4, min(1.0, w / 1400))
        thick = 1 if w < 900 else 2
        tracks = list(tracks)
        zones = list(zones)

        if zones:
            polys = []
            for zone in zones:
                pts = np.array(zone.to_pixels(w, h), np.int32)
                if zone.name.lower().startswith(("ignorar", "excluir")):
                    polys.append((zone, pts, ZONE_IGNORE_COLOR))  # zona de exclusión
                    continue
                busy = any(zone.contains(tr.anchor_norm) for tr in tracks)
                polys.append((zone, pts, ZONE_BUSY_COLOR if busy else ZONE_COLOR))
            # Relleno semitransparente solo dentro del rectángulo que abarca las zonas.
            all_pts = np.concatenate([p for _, p, _ in polys])
            x0, y0 = (int(v) for v in np.clip(all_pts.min(axis=0), 0, [w - 1, h - 1]))
            x1, y1 = (int(v) for v in np.clip(all_pts.max(axis=0) + 1, 1, [w, h]))
            roi = out[y0:y1, x0:x1]
            overlay = roi.copy()
            offset = np.array([x0, y0], np.int32)
            for _, pts, color in polys:
                cv2.fillPoly(overlay, [pts - offset], color)
            cv2.addWeighted(overlay, 0.18, roi, 0.82, 0, dst=roi)
            for zone, pts, color in polys:
                cv2.polylines(out, [pts], True, color, thick + 1, cv2.LINE_AA)
                x, y = pts.min(axis=0)
                _text(out, zone.name, (int(x) + 6, int(y) + int(22 * scale) + 4), 0.55 * scale + 0.1, (255, 255, 255), 1, bg=color)

        for line in lines:
            a, b = line.to_pixels(w, h)
            cv2.line(out, a, b, LINE_COLOR, thick + 1, cv2.LINE_AA)
            for p in (a, b):
                cv2.circle(out, p, thick + 3, LINE_COLOR, -1, cv2.LINE_AA)
            # Flecha hacia el lado de "entrada" (derecha de A->B vista en pantalla).
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / length, dx / length
            mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
            tip = (int(mid[0] + nx * 40 * scale + nx * 10), int(mid[1] + ny * 40 * scale + ny * 10))
            cv2.arrowedLine(out, mid, tip, LINE_COLOR, thick + 1, cv2.LINE_AA, tipLength=0.35)
            _text(out, line.name, (a[0] + 6, a[1] - 6), 0.5 * scale + 0.1, (30, 30, 30), 1, bg=LINE_COLOR)

        for tr in tracks:
            color = CATEGORY_COLORS.get(tr.category, DEFAULT_COLOR)
            # Entre dos detecciones el objeto sigue moviéndose: se dibuja donde
            # debería estar ahora según su velocidad (si no, el recuadro queda atrás).
            x1, y1, x2, y2 = (int(v) for v in tr.predict(now))
            cv2.rectangle(out, (x1, y1), (x2, y2), color, thick + 1, cv2.LINE_AA)
            label = f"{tr.category} #{tr.id}"
            if tr.category != "movimiento":
                label += f" {tr.confidence:.0%}"
            _text(out, label, (x1 + 2, max(14, y1 - 5)), 0.5 * scale + 0.08, (20, 20, 20), 1, bg=color)

        # Barra superior: cámara, hora, modalidad y fps. En imágenes angostas se
        # omite primero el nombre de la cámara y después los fps.
        bar_h = int(30 * scale) + 8
        text_scale = 0.55 * scale + 0.1
        cv2.rectangle(out, (0, 0), (w, bar_h), (18, 18, 18), -1)
        stamp = datetime.fromtimestamp(now).strftime("%d/%m/%Y %H:%M:%S")
        mode_text = strip_accents(f"MODO: {mode_label.upper()}")
        (tw, _), _ = cv2.getTextSize(mode_text, FONT, text_scale, 1)
        fps_text = f"{fps:4.1f} fps"
        (fw, _), _ = cv2.getTextSize(fps_text, FONT, 0.5 * scale, 1)
        left = f"{self.camera_name}  |  {stamp}"
        room = w - tw - fw - 56
        if cv2.getTextSize(strip_accents(left), FONT, text_scale, 1)[0][0] > room:
            left = stamp
            if cv2.getTextSize(left, FONT, text_scale, 1)[0][0] > room + fw + 10:
                fps_text, fw = "", 0
        x = w - tw - fw - (30 if fw else 14)
        _text(out, left, (8, bar_h - 9), text_scale, (235, 235, 235), 1)
        cv2.rectangle(out, (x - 8, 3), (x + tw + 8, bar_h - 3), hex_to_bgr(mode_color), -1)
        _text(out, mode_text, (x, bar_h - 9), text_scale, (255, 255, 255), 1)
        if fps_text:
            _text(out, fps_text, (w - fw - 10, bar_h - 9), 0.5 * scale, (170, 170, 170), 1)

        if banner:
            border = max(4, int(8 * scale))
            cv2.rectangle(out, (0, 0), (w - 1, h - 1), ALERT_COLOR, border)
            band = int(34 * scale) + 10
            cv2.rectangle(out, (0, h - band), (w, h), ALERT_COLOR, -1)
            _text(out, f"ALERTA: {banner}", (12, h - band // 2 + int(8 * scale)), 0.65 * scale + 0.15, (255, 255, 255), thick)
        return out

    @staticmethod
    def highlight(frame: np.ndarray, tracks: Iterable[Track], title: str) -> np.ndarray:
        """Marca en rojo los objetos que dispararon una alerta (para la captura)."""
        out = frame.copy()
        h, w = out.shape[:2]
        scale = max(0.4, min(1.0, w / 1400))
        for tr in tracks:
            x1, y1, x2, y2 = (int(v) for v in tr.bbox)
            pad = 6
            cv2.rectangle(out, (x1 - pad, y1 - pad), (x2 + pad, y2 + pad), ALERT_COLOR, 3, cv2.LINE_AA)
        band = int(34 * scale) + 10
        cv2.rectangle(out, (0, h - band), (w, h), ALERT_COLOR, -1)
        _text(out, title, (12, h - band // 2 + int(8 * scale)), 0.65 * scale + 0.15, (255, 255, 255), 2 if w >= 900 else 1)
        return out


def placeholder(width: int, height: int, title: str, subtitle: str = "") -> np.ndarray:
    """Imagen para cámaras sin señal o videos finalizados."""
    img = np.full((height, width, 3), 22, np.uint8)
    cv2.line(img, (0, 0), (width, height), (40, 40, 40), 2)
    cv2.line(img, (width, 0), (0, height), (40, 40, 40), 2)
    scale = max(0.6, width / 900)
    text = strip_accents(title)
    (tw, th), _ = cv2.getTextSize(text, FONT, scale, 2)
    cv2.putText(img, text, ((width - tw) // 2, height // 2), FONT, scale, (80, 80, 235), 2, cv2.LINE_AA)
    if subtitle:
        sub = strip_accents(subtitle)
        (sw, _), _ = cv2.getTextSize(sub, FONT, scale * 0.6, 1)
        cv2.putText(img, sub, ((width - sw) // 2, height // 2 + th + 16), FONT, scale * 0.6, (180, 180, 180), 1, cv2.LINE_AA)
    return img
