"""Tipos básicos compartidos por todo el sistema."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import IntEnum

BBox = tuple[float, float, float, float]  # x1, y1, x2, y2 en píxeles


def strip_accents(text: str) -> str:
    """Quita tildes y eñes: las fuentes de OpenCV solo dibujan ASCII."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


class Severity(IntEnum):
    BAJA = 1
    MEDIA = 2
    ALTA = 3
    CRITICA = 4

    @classmethod
    def parse(cls, value: "Severity | str | int") -> "Severity":
        if isinstance(value, Severity):
            return value
        if isinstance(value, int):
            return cls(value)
        key = strip_accents(str(value)).strip().upper()
        try:
            return cls[key]
        except KeyError:
            opciones = ", ".join(s.label for s in cls)
            raise ValueError(f"severidad desconocida '{value}' (opciones: {opciones})") from None

    @property
    def label(self) -> str:
        return self.name.lower()

    @property
    def display(self) -> str:
        return {"BAJA": "Baja", "MEDIA": "Media", "ALTA": "Alta", "CRITICA": "Crítica"}[self.name]


@dataclass(slots=True)
class Detection:
    bbox: BBox
    confidence: float
    label: str  # clase original del modelo (p. ej. "person")
    category: str  # categoría del sistema (p. ej. "persona")


CATEGORY_NAMES = {
    "persona": "Persona",
    "vehiculo": "Vehículo",
    "equipaje": "Bolso/equipaje",
    "arma": "Objeto peligroso",
    "animal": "Animal",
    "movimiento": "Movimiento",
}


CATEGORY_PLURALS = {
    "persona": "personas",
    "vehiculo": "vehículos",
    "equipaje": "bolsos",
    "arma": "objetos peligrosos",
    "animal": "animales",
    "movimiento": "objetos en movimiento",
}


def category_display(category: str) -> str:
    return CATEGORY_NAMES.get(category, category.replace("_", " ").capitalize())


def category_plural(categories: set[str] | list[str]) -> str:
    """Sustantivo plural para contar objetos de una o varias categorías."""
    cats = set(categories)
    if len(cats) == 1:
        (cat,) = cats
        return CATEGORY_PLURALS.get(cat, f"{cat.replace('_', ' ')}s")
    return "objetos"
