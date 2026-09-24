"""Seguimiento multi-objeto (IoU + distancia con predicción de velocidad).

Asigna un identificador estable a cada objeto detectado y guarda su historial,
que las reglas usan para medir permanencia, velocidad, desplazamiento, etc.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from vigia.geometry import Point, bbox_anchor, bbox_area, bbox_center, bbox_size, distance, iou
from vigia.types import BBox, Detection

# Un objeto "se movió" cuando se alejó de su posición inicial al menos esta
# fracción de su altura. Un cartel o una baliza "tiemblan" menos de 0,15 alturas
# de una detección a otra (medido en cámaras reales).
MOVE_FRACTION = 0.5
MOVE_STEPS = 2  # detecciones seguidas lejos del origen
# "Llegar de golpe y quedarse quieto": un paso de al menos REST_JUMP alturas a
# más de REST_SPEED alturas por segundo, y en la detección siguiente no moverse
# más de REST_RADIUS. Un vehículo real frena de a poco; eso delata que el
# seguimiento pasó de un auto a algo quieto (típicamente un cartel junto al que
# pasó el auto antes de perderse bajo un puente).
REST_JUMP = 0.5
REST_SPEED = 2.0
REST_RADIUS = 0.25
# Asociación por cercanía (sin superposición): tamaños compatibles entre la
# posición prevista del objeto y la nueva detección.
HEIGHT_RATIO = (0.67, 1.5)
AREA_RATIO = (0.5, 2.0)


@dataclass(eq=False)
class Track:
    id: int
    category: str
    label: str
    bbox: BBox
    confidence: float
    first_seen: float
    last_seen: float
    frame_size: tuple[int, int] = (1, 1)
    hits: int = 1
    confirmed: bool = False
    max_confidence: float = 0.0
    velocity: tuple[float, float] = (0.0, 0.0)  # px/s del centro
    history: deque = field(default_factory=lambda: deque(maxlen=400))  # (t, bbox)
    moved: bool = False  # si se alejó de su posición inicial (media altura) en detecciones seguidas
    confidences: deque = field(default_factory=lambda: deque(maxlen=12))  # de las últimas detecciones
    _away: int = 0

    def __post_init__(self) -> None:
        self.max_confidence = max(self.max_confidence, self.confidence)
        if not self.history:
            self.history.append((self.last_seen, self.bbox))
        if not self.confidences:
            self.confidences.append(self.confidence)
        self.origin: Point = self.center

    # --- propiedades geométricas -------------------------------------------------
    @property
    def center(self) -> Point:
        return bbox_center(self.bbox)

    @property
    def anchor(self) -> Point:
        return bbox_anchor(self.bbox)

    @property
    def anchor_norm(self) -> Point:
        w, h = self.frame_size
        ax, ay = self.anchor
        return (ax / w, ay / h)

    @property
    def width(self) -> float:
        return bbox_size(self.bbox)[0]

    @property
    def height(self) -> float:
        return bbox_size(self.bbox)[1]

    @property
    def recent_confidence(self) -> float:
        """Confianza media de las últimas detecciones (más estable que la última)."""
        return sum(self.confidences) / len(self.confidences)

    # --- historial ------------------------------------------------------------------
    def _entry_at(self, t: float) -> tuple[float, BBox]:
        """Entrada más reciente del historial con tiempo <= t (o la más antigua)."""
        for entry in reversed(self.history):
            if entry[0] <= t:
                return entry
        return self.history[0]

    def displacement(self, window: float) -> float:
        """Distancia (px) recorrida por el centro entre ahora y hace `window` segundos."""
        _, b0 = self._entry_at(self.last_seen - window)
        c0, c1 = bbox_center(b0), self.center
        return math.hypot(c1[0] - c0[0], c1[1] - c0[1])

    def speed(self, window: float = 1.0) -> float:
        """Velocidad media (px/s) del centro durante la ventana indicada."""
        t0, b0 = self._entry_at(self.last_seen - window)
        dt = self.last_seen - t0
        if dt < window * 0.4:
            return 0.0
        c0, c1 = bbox_center(b0), self.center
        return math.hypot(c1[0] - c0[0], c1[1] - c0[1]) / dt

    def predict(self, t: float) -> BBox:
        dt = min(max(t - self.last_seen, 0.0), 1.0)
        dx, dy = self.velocity[0] * dt, self.velocity[1] * dt
        x1, y1, x2, y2 = self.bbox
        return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)

    def update(self, det: Detection, t: float, frame_size: tuple[int, int]) -> None:
        dt = t - self.last_seen
        if dt > 0:
            (cx0, cy0), (cx1, cy1) = self.center, bbox_center(det.bbox)
            vx, vy = (cx1 - cx0) / dt, (cy1 - cy0) / dt
            self.velocity = (0.5 * self.velocity[0] + 0.5 * vx, 0.5 * self.velocity[1] + 0.5 * vy)
        self.bbox = det.bbox
        self.confidence = det.confidence
        self.label = det.label
        self.max_confidence = max(self.max_confidence, det.confidence)
        self.last_seen = t
        self.hits += 1
        self.frame_size = frame_size
        self.history.append((t, det.bbox))
        self.confidences.append(det.confidence)
        if self._jumped_to_rest():
            # Desde aquí es otro objeto, quieto: pierde el movimiento heredado y la
            # confianza de las detecciones anteriores (las del auto).
            self.moved = False
            self._away = 0
            self.origin = self.center
            self.velocity = (0.0, 0.0)
            recent = list(self.confidences)[-2:]
            self.confidences.clear()
            self.confidences.extend(recent)
        elif not self.moved:
            # Varias detecciones seguidas lejos del origen: un salto aislado (p. ej.
            # una asociación equivocada con algo que pasa al lado) no alcanza.
            far = distance(self.origin, self.center) >= MOVE_FRACTION * max(self.height, 1.0)
            self._away = self._away + 1 if far else 0
            self.moved = self._away >= MOVE_STEPS

    def _jumped_to_rest(self) -> bool:
        if len(self.history) < 3:
            return False
        (t0, b0), (t1, b1), (_, b2) = self.history[-3], self.history[-2], self.history[-1]
        h = max(self.height, 1.0)
        landing = bbox_center(b1)
        if distance(bbox_center(b2), landing) > REST_RADIUS * h:
            return False  # sigue en movimiento
        step = distance(bbox_center(b0), landing)
        return step >= REST_JUMP * h and step / max(t1 - t0, 1e-3) >= REST_SPEED * h


@dataclass(eq=False)
class _Spot:
    bbox: BBox
    first: float  # primera vez que se vio ahí un objeto quieto
    last: float  # última vez
    seen: int = 1
    learned: bool = False


class StaticClutter:
    """Memoria de lugares con objetos quietos que el detector confunde con otra
    cosa: un cartel o una baliza vistos como "vehículo".

    Cuentan los objetos seguidos que nunca se movieron y que el modelo ve con
    poca confianza (`confident`: un vehículo real estacionado suele detectarse
    con más seguridad y no se toca). Esos lugares:

    - quedan "fijados" mientras se los siga viendo así (con pausas de hasta
      `forget_after` s: el modelo ve un cartel a ratos). Lo detectado en un lugar
      fijado solo puede asociarse con un objeto que se superpone, no por
      cercanía: así un auto que pasa junto al cartel y se pierde bajo un puente
      no "salta" al cartel ni le transfiere su movimiento;
    - pasan a ser decorado si se siguen viendo durante `learn_after` s: desde
      entonces lo que se detecte justo ahí se descarta.

    Un lugar por el que pasa un objeto en movimiento es un carril, no decorado:
    se descarta como candidato. Y para descartar una detección como decorado
    tiene que coincidir casi exactamente con él (IoU >= LEARNED_IOU): un auto
    que pasa por encima no coincide tanto, así que tampoco lo mantiene vivo."""

    MIN_SEEN = 10  # observaciones antes de aprender un decorado
    MIN_STILL = 1.5  # segundos quieto antes de fijar un lugar (un auto lejano y lento ya se movió)
    LEARNED_IOU = 0.7

    def __init__(
        self,
        categories: set[str],
        learn_after: float = 30.0,
        forget_after: float = 20.0,
        iou_min: float = 0.5,
        confident: float = 0.5,
    ) -> None:
        self.categories = set(categories)
        self.learn_after = learn_after
        self.forget_after = forget_after
        self.iou_min = iou_min
        self.confident = confident
        self._spots: list[_Spot] = []

    def _match(self, bbox: BBox) -> _Spot | None:
        best, best_iou = None, self.iou_min
        for spot in self._spots:
            overlap = iou(spot.bbox, bbox)
            if overlap >= best_iou:
                best, best_iou = spot, overlap
        return best

    def learn(self, tracks: list[Track], t: float) -> None:
        """Se llama después de cada seguimiento con los objetos actuales."""
        for tr in tracks:
            if tr.category not in self.categories or tr.last_seen != t:
                continue
            spot = self._match(tr.bbox)
            if tr.moved:
                if spot is not None and not spot.learned:
                    self._spots.remove(spot)  # pasan cosas en movimiento: es un carril
                continue
            if tr.hits < 3 or t - tr.first_seen < self.MIN_STILL or tr.recent_confidence >= self.confident:
                continue
            if spot is None:
                self._spots.append(_Spot(tr.bbox, t, t))
                continue
            spot.bbox, spot.last, spot.seen = tr.bbox, t, spot.seen + 1
            if not spot.learned and self.learn_after > 0 and t - spot.first >= self.learn_after and spot.seen >= self.MIN_SEEN:
                spot.learned = True

    def filter(self, detections: list[Detection], t: float) -> tuple[list[Detection], set[int]]:
        """Devuelve las detecciones que siguen (sin el decorado aprendido) y los
        índices, dentro de esa lista, de las que están en un lugar fijado."""
        self._spots = [s for s in self._spots if t - s.last <= self.forget_after]
        if not self._spots:
            return detections, set()
        kept: list[Detection] = []
        pinned: set[int] = set()
        for det in detections:
            spot = self._match(det.bbox) if det.category in self.categories else None
            if spot is not None and spot.learned and iou(spot.bbox, det.bbox) >= self.LEARNED_IOU:
                spot.last = t  # sigue ahí: es decorado
                continue
            if spot is not None:
                pinned.add(len(kept))
            kept.append(det)
        return kept, pinned

    @property
    def learned(self) -> int:
        return sum(s.learned for s in self._spots)


class Tracker:
    """Asociación codiciosa detección↔objeto por IoU y, si no hay solape
    (movimiento rápido o pocas detecciones por segundo), por distancia relativa
    al tamaño del objeto."""

    def __init__(
        self,
        iou_threshold: float = 0.25,
        max_age: float = 2.0,
        min_hits: int = 3,
        max_age_by_category: dict[str, float] | None = None,
        max_distance: float = 1.0,
        max_distance_by_category: dict[str, float] | None = None,
        min_hits_by_category: dict[str, int] | None = None,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.min_hits = max(1, min_hits)
        self.min_hits_by_category = {k: max(1, v) for k, v in (min_hits_by_category or {}).items()}
        self.max_age_by_category = dict(max_age_by_category or {})
        # Distancia máxima (en alturas del objeto) para asociar sin solape. Los
        # vehículos rápidos recorren más que su altura entre dos detecciones.
        self.max_distance = max_distance
        self.max_distance_by_category = dict(max_distance_by_category or {})
        self._tracks: dict[int, Track] = {}
        self._next_id = 1
        self.removed: list[int] = []

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks.values())

    def confirmed(self) -> list[Track]:
        # Copia primero: el panel lo consulta desde otro hilo mientras la cámara actualiza.
        return [t for t in list(self._tracks.values()) if t.confirmed]

    def reset(self) -> None:
        self._tracks.clear()
        self.removed = []

    def _min_hits(self, category: str) -> int:
        return self.min_hits_by_category.get(category, self.min_hits)

    def update(
        self, detections: list[Detection], t: float, frame_size: tuple[int, int], pinned: set[int] | None = None
    ) -> list[Track]:
        """`pinned`: índices de detecciones en lugares donde hay algo quieto (ver
        StaticClutter); solo se asocian con un objeto que se les superpone."""
        self.removed = []
        pinned = pinned or set()
        tracks = list(self._tracks.values())
        pairs: list[tuple[float, int, int]] = []
        for ti, tr in enumerate(tracks):
            pred = tr.predict(t)
            ph = max(bbox_size(pred)[1], 1.0)
            pcx, pcy = bbox_center(pred)
            limit = self.max_distance_by_category.get(tr.category, self.max_distance)
            for di, det in enumerate(detections):
                if det.category != tr.category:
                    continue
                overlap = iou(pred, det.bbox)
                if overlap >= self.iou_threshold:
                    pairs.append((1.0 + overlap, ti, di))
                    continue
                if di in pinned:
                    continue
                # Por cercanía solo si el tamaño es parecido: de una detección a la
                # siguiente un vehículo cambia de tamaño poco (se acerca o se aleja).
                dh = max(bbox_size(det.bbox)[1], 1.0)
                if not HEIGHT_RATIO[0] <= dh / ph <= HEIGHT_RATIO[1]:
                    continue
                area_ratio = max(bbox_area(det.bbox), 1.0) / max(bbox_area(pred), 1.0)
                if not AREA_RATIO[0] <= area_ratio <= AREA_RATIO[1]:
                    continue  # tamaños muy distintos: no es el mismo objeto
                dcx, dcy = bbox_center(det.bbox)
                dist = math.hypot(dcx - pcx, dcy - pcy) / max(ph, dh)
                if dist <= limit:
                    pairs.append((0.99 * (1.0 - dist / limit), ti, di))

        pairs.sort(key=lambda p: p[0], reverse=True)
        used_t: set[int] = set()
        used_d: set[int] = set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            tr = tracks[ti]
            tr.update(detections[di], t, frame_size)
            if tr.hits >= self._min_hits(tr.category):
                tr.confirmed = True

        for di, det in enumerate(detections):
            if di in used_d:
                continue
            tr = Track(
                id=self._next_id,
                category=det.category,
                label=det.label,
                bbox=det.bbox,
                confidence=det.confidence,
                first_seen=t,
                last_seen=t,
                frame_size=frame_size,
                confirmed=self._min_hits(det.category) <= 1,
            )
            self._tracks[tr.id] = tr
            self._next_id += 1

        for ti, tr in enumerate(tracks):
            if ti in used_t:
                continue
            limit = self.max_age_by_category.get(tr.category, self.max_age)
            if not tr.confirmed:
                limit = min(limit, max(self.max_age / 2, 0.5))
            if t - tr.last_seen > limit:
                del self._tracks[tr.id]
                self.removed.append(tr.id)

        return list(self._tracks.values())
