"""Detección de sabotaje: cámara tapada, desenfocada, movida u oscurecida.

Se compara cada imagen (reducida a ~160 px) con una línea base aprendida al
arrancar: brillo, contraste, nitidez y mapa de bordes. Una condición anómala
debe mantenerse `hold` segundos para declararse.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import cv2
import numpy as np

from vigia.types import BBox

TAMPER_STATES = ("obstruida", "desenfocada", "movida", "oscura")

STATE_LABELS = {
    "calibrando": "Calibrando",
    "ok": "Normal",
    "obstruida": "Obstruida",
    "desenfocada": "Desenfocada",
    "movida": "Movida",
    "oscura": "Sin iluminación",
}


@dataclass
class TamperStatus:
    state: str = "calibrando"
    since: float | None = None
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def tampered(self) -> bool:
        return self.state in TAMPER_STATES


class TamperDetector:
    def __init__(
        self,
        warmup: float = 5.0,
        hold: float = 3.0,
        moved_hold: float = 10.0,
        interval: float = 0.5,
        rebaseline_after: float = 60.0,
        reference_refresh: float = 30.0,
        min_std: float = 8.0,
        std_ratio: float = 0.3,
        sharp_ratio: float = 0.2,
        dark_level: float = 25.0,
        edge_match_min: float = 0.3,
        width: int = 160,
    ) -> None:
        self.warmup = warmup
        self.hold = hold
        # Una cámara movida queda así indefinidamente: esperar más evita confundirla
        # con alguien que pasa muy cerca del lente.
        self.moved_hold = moved_hold
        self.interval = interval
        self.rebaseline_after = rebaseline_after
        self.reference_refresh = reference_refresh
        self.min_std = min_std
        self.std_ratio = std_ratio
        self.sharp_ratio = sharp_ratio
        self.dark_level = dark_level
        self.edge_match_min = edge_match_min
        self.width = width
        self._kernel = np.ones((3, 3), np.uint8)
        self.reset()

    def reset(self) -> None:
        self.status = TamperStatus()
        self._base: dict[str, float] | None = None
        self._warm: list[tuple[float, float, float]] = []
        self._start: float | None = None
        self._last = float("-inf")
        self._candidate: str | None = None
        self._candidate_since = 0.0
        self._ref_edges: np.ndarray | None = None
        self._ref_dilated: np.ndarray | None = None
        self._ref_time = 0.0

    # ------------------------------------------------------------------------------
    def update(self, frame: np.ndarray, t: float, ignore: list[BBox] | None = None) -> TamperStatus:
        """`ignore`: cajas de personas/objetos detectados. Se excluyen de la
        comparación de bordes para no confundir a alguien muy cerca de la cámara
        (típico de una webcam) con una cámara girada."""
        if t - self._last < self.interval:
            return self.status
        self._last = t
        gray = self._prepare(frame)
        mask = self._foreground_mask(gray.shape, frame.shape, ignore)
        mean_arr, std_arr = cv2.meanStdDev(gray)
        mean, std = float(mean_arr[0][0]), float(std_arr[0][0])
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        edges = cv2.Canny(gray, 50, 150)
        metrics = {"brillo": round(mean, 1), "contraste": round(std, 1), "nitidez": round(sharp, 1)}

        if self._start is None:
            self._start = t
        if self._base is None:
            self._warm.append((mean, std, sharp))
            if t - self._start >= self.warmup and len(self._warm) >= 3:
                self._set_baseline(
                    statistics.median(w[0] for w in self._warm),
                    statistics.median(w[1] for w in self._warm),
                    statistics.median(w[2] for w in self._warm),
                    edges,
                    t,
                )
                self.status = TamperStatus("ok", t, metrics)
            else:
                self.status = TamperStatus("calibrando", None, metrics)
            return self.status

        match = self._edge_match(edges, mask)
        if match is not None:
            metrics["coincidencia"] = round(match, 2)
        candidate = self._classify(mean, std, sharp, match)

        if candidate == "ok":
            self._candidate = None
            if self.status.state != "ok":
                self.status = TamperStatus("ok", t, metrics)
            else:
                self.status.metrics = metrics
            self._adapt(mean, std, sharp, edges, match, t, refresh_reference=mask is None)
            return self.status

        if candidate != self._candidate:
            self._candidate, self._candidate_since = candidate, t
        hold = self.moved_hold if candidate == "movida" else self.hold
        if t - self._candidate_since >= hold and self.status.state != candidate:
            self.status = TamperStatus(candidate, self._candidate_since, metrics)
        else:
            self.status.metrics = metrics
        # Si la cámara quedó apuntando a otro lado mucho tiempo (p. ej. se
        # reubicó a propósito), se acepta la nueva vista como referencia.
        if self.status.state == "movida" and t - (self.status.since or t) >= self.rebaseline_after:
            self._set_baseline(mean, std, sharp, edges, t)
            self._candidate = None
            self.status = TamperStatus("ok", t, metrics)
        return self.status

    # ------------------------------------------------------------------------------
    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        size = (self.width, max(1, int(round(h * self.width / w))))
        small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        if small.ndim == 3:
            small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(small, (3, 3), 0)

    def _set_baseline(self, mean: float, std: float, sharp: float, edges: np.ndarray, t: float) -> None:
        self._base = {"mean": mean, "std": std, "sharp": sharp}
        self._set_reference(edges, t)

    def _set_reference(self, edges: np.ndarray, t: float) -> None:
        self._ref_edges = edges.copy()
        self._ref_dilated = cv2.dilate(edges, self._kernel)
        self._ref_time = t

    @staticmethod
    def _foreground_mask(small_shape: tuple, frame_shape: tuple, boxes: list[BBox] | None) -> np.ndarray | None:
        """Máscara (255 = fondo) que excluye, con un margen, las cajas de los objetos detectados."""
        if not boxes:
            return None
        sh, sw = small_shape[:2]
        fh, fw = frame_shape[:2]
        sx, sy = sw / fw, sh / fh
        mask = np.full((sh, sw), 255, np.uint8)
        for x1, y1, x2, y2 in boxes:
            px, py = (x2 - x1) * 0.15, (y2 - y1) * 0.15
            top_left = (int((x1 - px) * sx), int((y1 - py) * sy))
            bottom_right = (int((x2 + px) * sx), int((y2 + py) * sy))
            cv2.rectangle(mask, top_left, bottom_right, 0, -1)
        return mask

    def _edge_match(self, edges: np.ndarray, mask: np.ndarray | None = None) -> float | None:
        """Coincidencia entre los bordes actuales y los de referencia (0..1).

        Se toma el máximo de las dos direcciones: una persona delante de la
        cámara agrega bordes (baja una) pero no borra la escena (la otra se
        mantiene); si la cámara se movió, ambas caen. Con `mask` solo se
        compara el fondo; si queda muy poco fondo visible, no se opina."""
        if self._ref_edges is None or self._ref_dilated is None:
            return None
        ref, ref_dilated = self._ref_edges, self._ref_dilated
        if mask is not None:
            if np.count_nonzero(mask) < 0.3 * mask.size:
                return None
            edges = cv2.bitwise_and(edges, mask)
            ref = cv2.bitwise_and(ref, mask)
            ref_dilated = cv2.bitwise_and(ref_dilated, mask)
        ref_count = int(np.count_nonzero(ref))
        count = int(np.count_nonzero(edges))
        if ref_count < 40 or count < 40:
            return None
        cur_in_ref = np.count_nonzero(cv2.bitwise_and(edges, ref_dilated)) / count
        ref_in_cur = np.count_nonzero(cv2.bitwise_and(ref, cv2.dilate(edges, self._kernel))) / ref_count
        return float(max(cur_in_ref, ref_in_cur))

    def _classify(self, mean: float, std: float, sharp: float, match: float | None) -> str:
        base = self._base
        assert base is not None
        if std < max(self.min_std, self.std_ratio * base["std"]):
            return "obstruida"
        if base["mean"] > self.dark_level * 2.5 and mean < self.dark_level:
            return "oscura"
        if base["sharp"] > 25 and sharp < self.sharp_ratio * base["sharp"]:
            return "desenfocada"
        if match is not None and match < self.edge_match_min:
            return "movida"
        return "ok"

    def _adapt(
        self,
        mean: float,
        std: float,
        sharp: float,
        edges: np.ndarray,
        match: float | None,
        t: float,
        refresh_reference: bool = True,
    ) -> None:
        """Adaptación lenta a cambios graduales (atardecer, nubes...). La
        referencia de bordes solo se renueva con la escena vacía."""
        base = self._base
        assert base is not None
        alpha = 0.05
        base["mean"] += alpha * (mean - base["mean"])
        base["std"] += alpha * (std - base["std"])
        base["sharp"] += alpha * (sharp - base["sharp"])
        if refresh_reference and t - self._ref_time >= self.reference_refresh and (match is None or match >= 0.6):
            self._set_reference(edges, t)
