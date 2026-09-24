"""Detectores de objetos intercambiables.

- ``yolo``: red neuronal YOLO (Ultralytics). Personas, vehículos, bolsos,
  cuchillos, bates... Es el recomendado.
- ``hog``: detector clásico de personas de OpenCV. No descarga nada, pero es
  menos preciso y solo ve personas grandes y de pie.
- ``movimiento``: sustracción de fondo. Reporta "manchas" de movimiento sin
  saber qué son; útil en equipos muy modestos.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from vigia.types import Detection

if TYPE_CHECKING:
    from vigia.config import DetectorConfig

log = logging.getLogger(__name__)

# Clases del modelo (COCO) -> categorías del sistema.
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "persona": ["person"],
    "vehiculo": ["bicycle", "car", "motorcycle", "bus", "truck"],
    "equipaje": ["backpack", "handbag", "suitcase"],
    "arma": ["knife", "baseball bat"],
    "animal": ["bird", "cat", "dog", "horse", "sheep", "cow"],
}


class DetectorError(RuntimeError):
    pass


class Detector:
    name = "base"
    categories: set[str] = set()

    def detect(self, frame: np.ndarray) -> list[Detection]:
        raise NotImplementedError


class YoloDetector(Detector):
    name = "yolo"

    def __init__(
        self,
        model: str = "yolo11n.pt",
        confidence: float = 0.4,
        image_size: int = 640,
        device: str = "",
        categories: dict[str, list[str]] | None = None,
        confidence_by_category: dict[str, float] | None = None,
        base_dir: Path | None = None,
        threads: int = 0,
    ) -> None:
        try:
            import torch
            from ultralytics import YOLO
        except ImportError as exc:
            raise DetectorError("falta el paquete 'ultralytics' (pip install ultralytics)") from exc

        # Cada cámara corre su propia inferencia en paralelo: si cada una usa todos
        # los núcleos, los hilos compiten entre sí y se desperdicia CPU.
        torch.set_num_threads(threads if threads > 0 else max(1, (os.cpu_count() or 4) // 4))

        model_path = Path(model)
        if not model_path.is_absolute() and base_dir is not None:
            model_path = base_dir / model_path
        model_path.parent.mkdir(parents=True, exist_ok=True)
        self.model = YOLO(str(model_path))
        self.model_path = model_path
        self.confidence = confidence
        self.image_size = image_size
        self.device = device or None
        self.confidence_by_category = dict(confidence_by_category or {})

        label_to_cat = {
            label: cat for cat, labels in (categories or DEFAULT_CATEGORIES).items() for label in labels
        }
        self._names: dict[int, str] = {int(k): v for k, v in self.model.names.items()}
        self._class_cat = {cid: label_to_cat[name] for cid, name in self._names.items() if name in label_to_cat}
        if not self._class_cat:
            raise DetectorError("ninguna clase del modelo está asignada a una categoría (revise 'deteccion.categorias')")
        self.categories = set(self._class_cat.values())
        self._model_conf = min([confidence, *self.confidence_by_category.values()])

    def detect(self, frame: np.ndarray) -> list[Detection]:
        results = self.model.predict(
            frame,
            imgsz=self.image_size,
            conf=self._model_conf,
            classes=list(self._class_cat),
            device=self.device,
            verbose=False,
        )
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        classes = boxes.cls.cpu().numpy().astype(int)
        out: list[Detection] = []
        for (x1, y1, x2, y2), conf, cid in zip(xyxy, confs, classes):
            category = self._class_cat.get(int(cid))
            if category is None:
                continue
            if conf < self.confidence_by_category.get(category, self.confidence):
                continue
            out.append(Detection((float(x1), float(y1), float(x2), float(y2)), float(conf), self._names[int(cid)], category))
        return out


class HogDetector(Detector):
    name = "hog"
    categories = {"persona"}

    def __init__(self, width: int = 640, min_weight: float = 0.4) -> None:
        if not hasattr(cv2, "HOGDescriptor"):
            raise DetectorError("esta versión de OpenCV no incluye HOG (se quitó en OpenCV 5)")
        self.width = width
        self.min_weight = min_weight
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        scale = min(1.0, self.width / w)
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame
        rects, weights = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        if len(rects) == 0:
            return []
        boxes = [[int(x), int(y), int(bw), int(bh)] for (x, y, bw, bh) in rects]
        scores = [float(s) for s in np.ravel(weights)]
        keep = cv2.dnn.NMSBoxes(boxes, scores, self.min_weight, 0.45)
        inv = 1.0 / scale
        out: list[Detection] = []
        for i in np.ravel(keep):
            x, y, bw, bh = boxes[int(i)]
            bbox = (x * inv, y * inv, (x + bw) * inv, (y + bh) * inv)
            out.append(Detection(bbox, min(1.0, scores[int(i)]), "person", "persona"))
        return out


class MotionDetector(Detector):
    name = "movimiento"
    categories = {"movimiento"}

    def __init__(
        self,
        min_area: float = 0.002,
        max_area: float = 0.5,
        width: int = 480,
        history: int = 300,
        var_threshold: float = 32,
    ) -> None:
        self.min_area = min_area
        self.max_area = max_area
        self.width = width
        self.bg = cv2.createBackgroundSubtractorMOG2(history=history, varThreshold=var_threshold, detectShadows=True)
        self._open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        scale = min(1.0, self.width / w)
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame
        small = cv2.GaussianBlur(small, (5, 5), 0)
        mask = self.bg.apply(small)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)  # descarta sombras (127)
        total = mask.shape[0] * mask.shape[1]
        if cv2.countNonZero(mask) > self.max_area * total:
            return []  # cambio global: luz encendida/apagada o cámara tapada
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._open)
        mask = cv2.dilate(mask, self._dilate, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        inv = 1.0 / scale
        out: list[Detection] = []
        for contour in contours:
            if cv2.contourArea(contour) < self.min_area * total:
                continue
            x, y, bw, bh = cv2.boundingRect(contour)
            if bw * bh > self.max_area * total:
                continue
            out.append(Detection((x * inv, y * inv, (x + bw) * inv, (y + bh) * inv), 1.0, "movimiento", "movimiento"))
        return out


def create_detector(cfg: "DetectorConfig", base_dir: Path | None = None) -> Detector:
    """Crea el detector configurado. En modo ``auto`` prueba YOLO, luego HOG y
    por último el detector de movimiento, que siempre está disponible."""
    kind = cfg.kind
    if kind in ("auto", "yolo"):
        try:
            return YoloDetector(
                model=cfg.model,
                confidence=cfg.confidence,
                image_size=cfg.image_size,
                device=cfg.device,
                categories=cfg.categories,
                confidence_by_category=cfg.confidence_by_category,
                base_dir=base_dir,
                threads=cfg.threads,
            )
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de carga pasa al respaldo
            if kind == "yolo":
                raise DetectorError(f"no se pudo cargar YOLO: {exc}") from exc
            log.warning("YOLO no disponible (%s).", exc)
        try:
            detector: Detector = HogDetector()
            log.warning("Se usará HOG de OpenCV: solo personas y menos preciso.")
            return detector
        except DetectorError as exc:
            log.warning("HOG no disponible (%s). Se usará el detector de movimiento.", exc)
            return MotionDetector(min_area=cfg.motion_min_area)
    if kind == "hog":
        return HogDetector()
    if kind == "movimiento":
        return MotionDetector(min_area=cfg.motion_min_area)
    raise DetectorError(f"tipo de detector desconocido: {kind}")
