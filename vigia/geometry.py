"""Geometría: cajas, zonas (polígonos) y líneas virtuales.

Las zonas y líneas se guardan en coordenadas normalizadas (0..1) para que no
dependan de la resolución de la cámara.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from vigia.types import BBox

Point = tuple[float, float]


def bbox_center(b: BBox) -> Point:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def bbox_anchor(b: BBox) -> Point:
    """Punto de apoyo (centro inferior): donde la persona u objeto toca el suelo."""
    return ((b[0] + b[2]) / 2.0, b[3])


def bbox_size(b: BBox) -> tuple[float, float]:
    return (max(0.0, b[2] - b[0]), max(0.0, b[3] - b[1]))


def bbox_area(b: BBox) -> float:
    w, h = bbox_size(b)
    return w * h


def bbox_union(boxes: Iterable[BBox]) -> BBox:
    boxes = list(boxes)
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def iou(a: BBox, b: BBox) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    union = bbox_area(a) + bbox_area(b) - inter
    return inter / union if union > 0 else 0.0


def distance(p: Point, q: Point) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def point_in_polygon(pt: Point, poly: Sequence[Point]) -> bool:
    """Algoritmo de ray casting."""
    x, y = pt
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def cross(o: Point, a: Point, b: Point) -> float:
    """Producto cruz (a - o) x (b - o)."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    eps = 1e-12
    return (
        min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps
    )


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1 = cross(q1, q2, p1)
    d2 = cross(q1, q2, p2)
    d3 = cross(p1, p2, q1)
    d4 = cross(p1, p2, q2)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return (
        (d1 == 0 and _on_segment(q1, q2, p1))
        or (d2 == 0 and _on_segment(q1, q2, p2))
        or (d3 == 0 and _on_segment(p1, p2, q1))
        or (d4 == 0 and _on_segment(p1, p2, q2))
    )


@dataclass(frozen=True)
class Zone:
    name: str
    points: tuple[Point, ...]  # normalizados 0..1

    def contains(self, pt_norm: Point) -> bool:
        return point_in_polygon(pt_norm, self.points)

    def to_pixels(self, w: int, h: int) -> list[tuple[int, int]]:
        return [(int(round(x * w)), int(round(y * h))) for x, y in self.points]

    def to_dict(self) -> dict:
        return {"nombre": self.name, "puntos": [[round(x, 4), round(y, 4)] for x, y in self.points]}


@dataclass(frozen=True)
class Line:
    """Línea virtual A→B. La "entrada" es cruzar desde la izquierda hacia la
    derecha de A→B (tal como se ve en pantalla); el panel dibuja una flecha
    que apunta hacia el lado de entrada."""

    name: str
    a: Point
    b: Point

    def side(self, p: Point) -> float:
        """> 0: lado de entrada (hacia donde apunta la flecha); < 0: lado opuesto."""
        return cross(self.a, self.b, p)

    def crossing(self, prev: Point, curr: Point) -> int:
        """+1 si cruzó en sentido de entrada, -1 si fue de salida, 0 si no cruzó."""
        s1, s2 = self.side(prev), self.side(curr)
        if s1 == 0 or s2 == 0 or (s1 > 0) == (s2 > 0):
            return 0
        if not segments_intersect(prev, curr, self.a, self.b):
            return 0
        return 1 if s1 < 0 < s2 else -1

    def to_pixels(self, w: int, h: int) -> tuple[tuple[int, int], tuple[int, int]]:
        return (
            (int(round(self.a[0] * w)), int(round(self.a[1] * h))),
            (int(round(self.b[0] * w)), int(round(self.b[1] * h))),
        )

    def to_dict(self) -> dict:
        return {
            "nombre": self.name,
            "puntos": [[round(self.a[0], 4), round(self.a[1], 4)], [round(self.b[0], 4), round(self.b[1], 4)]],
        }
