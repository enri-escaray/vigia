"""Genera el carrusel de LinkedIn de Vigía (10 diapositivas de 1080×1350) desde una sola fuente.

  python linkedin/herramientas/construir_carrusel.py [CARPETA_DEL_LIENZO [CANVAS_JSON_ACTUAL]]

Escribe:
  linkedin/carrusel/carrusel.html     todas las diapositivas, para exportar PNG y PDF
  CARPETA_DEL_LIENZO/project/         las mismas diapositivas como mesas de trabajo del
                                      lienzo Design de claude.ai (.dc.html + canvas.json)

Dirección visual: informe técnico sobre fondo oscuro. IBM Plex Sans y Mono, un solo color
de acento (el celeste del panel de Vigía), tablas con filetes, un gráfico a escala y las
capturas reales presentadas como figuras numeradas con su pie.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "carrusel"

# clave -> (ruta para el HTML local, url subida al lienzo)
IMAGES = {
    "panel": ("../imagenes/panel_4_camaras.jpg", "/_blob/008ce4f12e9a1516e0b5bdac03154c79"),
    "alerta": ("../imagenes/alerta_detalle.jpg", "/_blob/b444df74249b46a32e6cec12ece03645"),
    "cruce": ("../imagenes/cruce_long_beach.jpg", "/_blob/f41cdcc3830649d8f7442614d852a335"),
}

PAGE = "#0B111B"        # fondo de página (azul noche del panel)
CARD = "#111A27"        # superficies
INK = "#F2F5F9"         # títulos
TEXT = "#C9D3DF"        # texto
SLATE = "#A7B4C4"       # texto secundario
MUTED = "#7F8FA3"       # rótulos y pies
HAIR = "#1E2A3A"        # filetes
RULE = "#46566C"        # filetes fuertes (encabezados de tabla)
BORDER = "#2A3A4F"
SUBTLE = "#152032"      # fondos de agrupación
ACCENT = "#38BDF8"      # celeste del panel de Vigía
ACCENT_BG = "#0F2433"
ACCENT_LINE = "#1D4E6B"
BAR_BEFORE = "#5B6B80"
WARN = "#F5A524"
WARN_BG = "#2A1F0E"
WARN_LINE = "#5C4213"

SANS = "'IBM Plex Sans', 'Segoe UI', system-ui, sans-serif"
MONO = "'IBM Plex Mono', 'Cascadia Mono', Consolas, monospace"
FONTS = (
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
    "&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap"
)

W, H = 1080, 1350
M = 80                 # margen lateral
TOTAL = 10
TO = '<span style="white-space: nowrap;">trade-off</span>'
CALTRANS = "Video: cámaras públicas de Caltrans (datos abiertos)"


# --------------------------------------------------------------------------- piezas


def gap(px: int) -> str:
    return f'<div style="height: {px}px; flex-shrink: 0;"></div>'


SPACER = '<div style="flex-grow: 1;"></div>'


def eyebrow(text: str) -> str:
    return (
        f'<div style="font-size: 21px; font-weight: 600; letter-spacing: 0.08em; '
        f'text-transform: uppercase; color: {ACCENT}; line-height: 1.3;">{text}</div>'
    )


def title(text: str, size: int = 64, top: int = 16) -> str:
    return (
        f'<h1 style="margin: {top}px 0 0; font-size: {size}px; line-height: 1.12; font-weight: 600; '
        f'letter-spacing: -0.02em; color: {INK}; text-wrap: balance;">{text}</h1>'
    )


def lead(text: str, size: int = 30, top: int = 22, color: str = SLATE) -> str:
    return (
        f'<p style="margin: {top}px 0 0; font-size: {size}px; line-height: 1.45; color: {color}; '
        f'text-wrap: pretty;">{text}</p>'
    )


def label(text: str) -> str:
    return (
        f'<div style="font-size: 19px; font-weight: 600; letter-spacing: 0.08em; '
        f'text-transform: uppercase; color: {MUTED}; line-height: 1.3;">{text}</div>'
    )


def figure(img: dict, key: str, height: int, num: int, caption: str, pos: str = "center") -> str:
    return (
        f'<figure style="margin: 0; flex-shrink: 0;">'
        f'<div style="border-radius: 14px; overflow: hidden; border: 1px solid #33455C; '
        f'background: #0B1017;">'
        f'<img src="{img[key]}" alt="" style="display: block; width: 100%; height: {height}px; '
        f'object-fit: cover; object-position: {pos};"></div>'
        f'<figcaption style="margin-top: 16px; font-size: 21px; line-height: 1.4; color: {MUTED};">'
        f'<span style="font-weight: 600; color: {TEXT};">Fig. {num}</span>&nbsp;&nbsp;{caption}</figcaption>'
        f'</figure>'
    )


def table(head: list[str], rows: list[list[str]], widths: str, align: list[str] | None = None) -> str:
    align = align or ["left"] * len(head)
    cell_head = "".join(
        f'<div style="text-align: {a}; font-size: 18px; font-weight: 600; letter-spacing: 0.06em; '
        f'text-transform: uppercase; color: {MUTED};">{h}</div>'
        for h, a in zip(head, align)
    )
    out = (
        f'<div style="display: grid; grid-template-columns: {widths}; column-gap: 24px; '
        f'padding-bottom: 14px; border-bottom: 2px solid {RULE};">{cell_head}</div>'
    )
    for row in rows:
        cells = "".join(
            f'<div style="text-align: {a}; font-size: 28px; line-height: 1.35; '
            f'color: {INK if i == 0 else TEXT}; font-weight: {500 if i == 0 else 400}; '
            f'font-variant-numeric: tabular-nums;">{c}</div>'
            for i, (c, a) in enumerate(zip(row, align))
        )
        out += (
            f'<div style="display: grid; grid-template-columns: {widths}; column-gap: 24px; '
            f'padding: 20px 0; border-bottom: 1px solid {HAIR}; align-items: baseline;">{cells}</div>'
        )
    return f'<div style="display: flex; flex-direction: column; flex-shrink: 0;">{out}</div>'


def callout(tag: str, text: str) -> str:
    return (
        f'<div style="background: {ACCENT_BG}; border: 1px solid {ACCENT_LINE}; border-radius: 14px; '
        f'padding: 22px 28px 24px; display: flex; flex-direction: column; gap: 8px; flex-shrink: 0;">'
        f'<div style="font-size: 18px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; '
        f'color: {ACCENT};">{tag}</div>'
        f'<div style="font-size: 29px; font-weight: 600; line-height: 1.35; color: {INK};">{text}</div></div>'
    )


def note_box(tag: str, text: str) -> str:
    return (
        f'<div style="background: {SUBTLE}; border-radius: 14px; padding: 22px 28px 24px; '
        f'display: flex; flex-direction: column; gap: 8px; flex-shrink: 0;">'
        f'<div style="font-size: 18px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; '
        f'color: {MUTED};">{tag}</div>'
        f'<div style="font-size: 28px; line-height: 1.45; color: {TEXT};">{text}</div></div>'
    )


def code(text: str) -> str:
    return (
        f'<span style="font-family: {MONO}; font-size: 0.86em; background: {SUBTLE}; '
        f'border: 1px solid {HAIR}; border-radius: 6px; padding: 1px 7px; white-space: nowrap;">{text}</span>'
    )


def arrow() -> str:
    return (
        '<div style="display: flex; justify-content: center; height: 30px; align-items: center; flex-shrink: 0;">'
        '<svg width="20" height="26" viewBox="0 0 20 26" aria-hidden="true">'
        f'<path d="M10 2 V22 M4 16 L10 22 L16 16" fill="none" stroke="{BAR_BEFORE}" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"></path></svg></div>'
    )


def node(name: str, desc: str) -> str:
    return (
        f'<div style="background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; '
        f'padding: 16px 22px; display: grid; grid-template-columns: 170px minmax(0, 1fr); gap: 16px; '
        f'align-items: baseline; flex-shrink: 0;">'
        f'<div style="font-size: 24px; font-weight: 600; color: {INK};">{name}</div>'
        f'<div style="font-size: 24px; line-height: 1.38; color: {SLATE}; text-wrap: pretty;">{desc}</div></div>'
    )


def frame(n: int, body: str, source: str = "", cue: str = "") -> str:
    header = (
        f'<div style="display: flex; justify-content: space-between; align-items: center; height: 112px; '
        f'margin: 0 {M}px; border-bottom: 1px solid {HAIR}; flex-shrink: 0;">'
        f'<div style="display: flex; align-items: baseline; gap: 12px; font-size: 21px; line-height: 1;">'
        f'<span style="font-weight: 700; color: {INK};">Vigía</span>'
        f'<span style="color: {BORDER};">|</span>'
        f'<span style="color: {MUTED};">Caso práctico con Claude Opus 5.5</span></div>'
        f'<div style="font-size: 21px; color: {MUTED}; font-variant-numeric: tabular-nums;">'
        f'{n:02d} / {TOTAL:02d}</div></div>'
    )
    rule = HAIR if (source or cue) else "transparent"
    footer = (
        f'<div style="display: flex; justify-content: space-between; align-items: center; gap: 24px; '
        f'height: 88px; margin: 0 {M}px; border-top: 1px solid {rule}; flex-shrink: 0; '
        f'font-size: 19px; color: {MUTED};">'
        f'<span>{source}</span><span style="color: {ACCENT}; font-weight: 600;">{cue}</span></div>'
    )
    return (
        f'<div style="width: {W}px; height: {H}px; box-sizing: border-box; position: relative; '
        f'overflow: hidden; background: {PAGE}; color: {TEXT}; font-family: {SANS}; '
        f'display: flex; flex-direction: column;">{header}'
        f'<div style="flex-grow: 1; min-height: 0; padding: 48px {M}px 32px; display: flex; '
        f'flex-direction: column;">{body}</div>{footer}</div>'
    )


# --------------------------------------------------------------------------- diapositivas


def s_portada(img: dict) -> str:
    return (
        eyebrow("Visión por computadora · Arquitectura de software")
        + title("Recreando un sistema de videovigilancia con IA junto a Claude Opus 5.5", size=72)
        + lead("Objetivo, arquitectura y los trade-offs que estamos evaluando, con cámaras de tránsito "
               "en vivo de California.", size=30)
        + gap(46)
        + figure(img, "panel", 560, 1,
                 "Vigía analizando en vivo cuatro cámaras públicas de California, 23/09/2026.", pos="top")
    )


def s_objetivo(img: dict) -> str:
    items = [
        ("Procesamiento local", "El video no sale del equipo; no depende de servicios en la nube."),
        ("Sin reconocimiento facial", "Detecta categorías y comportamientos, no identifica personas."),
        ("Hardware modesto", "Un portátil con Intel Core i5 de 4 núcleos, sin GPU."),
        ("Evidencia en cada alerta", "Captura y clip con los segundos previos y posteriores."),
    ]
    cells = "".join(
        f'<div style="padding: 32px 0 34px; border-top: 1px solid {HAIR}; display: flex; '
        f'flex-direction: column; gap: 10px;">'
        f'<div style="font-size: 31px; font-weight: 600; color: {INK};">{t}</div>'
        f'<div style="font-size: 27px; line-height: 1.42; color: {SLATE};">{d}</div></div>'
        for t, d in items
    )
    return (
        eyebrow("Objetivo")
        + title("Detectar comportamientos sospechosos en video, en tiempo real")
        + lead("Qué se considera sospechoso depende de la modalidad activa: En casa, Nocturno, "
               "Ausente, Comercio o Vía pública.")
        + gap(48)
        + label("Restricciones de diseño")
        + gap(12)
        + f'<div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 48px; '
          f'border-bottom: 1px solid {HAIR};">{cells}</div>'
        + SPACER
        + f'<div style="border-top: 2px solid {RULE}; padding-top: 26px; font-size: 32px; font-weight: 600; '
          f'line-height: 1.35; color: {INK};">Cada decisión de arquitectura se evalúa contra estas '
          f'cuatro restricciones.</div>'
    )


def s_metodo(img: dict) -> str:
    def role(name: str, sub: str, items: list[str]) -> str:
        lis = "".join(
            f'<div style="display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 12px;">'
            f'<div style="width: 12px; height: 2px; background: {ACCENT}; margin-top: 18px;"></div>'
            f'<div style="font-size: 27px; line-height: 1.38; color: {TEXT}; text-wrap: pretty;">{i}</div></div>'
            for i in items
        )
        return (
            f'<div style="background: {CARD}; border: 1px solid {HAIR}; border-radius: 16px; '
            f'padding: 28px 28px 30px; display: flex; flex-direction: column; gap: 16px;">'
            f'<div><div style="font-size: 30px; font-weight: 600; color: {INK};">{name}</div>'
            f'<div style="font-size: 20px; color: {MUTED}; margin-top: 4px;">{sub}</div></div>'
            f'<div style="display: flex; flex-direction: column; gap: 12px;">{lis}</div></div>'
        )

    steps = ["Objetivo", "Propuesta", "Prueba con cámaras reales", "Medición", "Decisión"]
    step_cells = "".join(
        f'<div style="display: flex; flex-direction: column; align-items: center; gap: 12px; '
        f'text-align: center; position: relative;">'
        f'<div style="width: 44px; height: 44px; border-radius: 50%; background: {CARD}; '
        f'border: 2px solid {ACCENT}; color: {ACCENT}; font-size: 20px; font-weight: 600; '
        f'display: flex; align-items: center; justify-content: center; position: relative; z-index: 1;">{i}</div>'
        f'<div style="font-size: 22px; line-height: 1.3; color: {INK}; font-weight: 500;">{s}</div></div>'
        for i, s in enumerate(steps, start=1)
    )
    stats = "".join(
        f'<div style="display: flex; flex-direction: column; gap: 6px;">'
        f'<div style="font-size: 50px; font-weight: 600; letter-spacing: -0.02em; line-height: 1.05; '
        f'color: {INK}; font-variant-numeric: tabular-nums;">{v}</div>'
        f'<div style="font-size: 19px; line-height: 1.35; color: {MUTED};">{t}</div></div>'
        for v, t in [("98", "pruebas automáticas"), ("11", "reglas de comportamiento"),
                     ("7", "modalidades"), ("≈8.800", "líneas de código y pruebas")]
    )
    return (
        eyebrow("Método de trabajo")
        + title("Una colaboración con roles definidos")
        + gap(40)
        + f'<div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px;">'
        + role("Yo", "Dirección y criterio",
               ["Defino el objetivo y las restricciones", "Elijo qué probar y en qué condiciones",
                f"Tomo la decisión en cada {TO}"])
        + role("Claude Opus 5.5", "Diseño, implementación y medición",
               ["Propone la arquitectura y sus alternativas", "Escribe el código y las pruebas",
                "Mide antes y después de cada cambio"])
        + "</div>"
        + gap(46)
        + label("Ciclo de cada cambio")
        + gap(20)
        + f'<div style="position: relative; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px;">'
          f'<div style="position: absolute; left: 10%; right: 10%; top: 21px; height: 2px; background: {BORDER};"></div>'
          f'{step_cells}</div>'
        + SPACER
        + f'<div style="border-top: 1px solid {HAIR}; padding-top: 24px; display: grid; '
          f'grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 20px;">{stats}</div>'
    )


def s_arquitectura(img: dict) -> str:
    group = (
        f'<div style="background: {SUBTLE}; border: 1px solid {HAIR}; border-radius: 16px; '
        f'padding: 18px 20px 20px; display: flex; flex-direction: column; flex-shrink: 0;">'
        f'<div style="font-size: 18px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; '
        f'color: {MUTED}; margin-bottom: 12px;">Por cada cámara</div>'
        + node("Captura", "Hilo propio: conserva solo el último cuadro y reconecta sola.")
        + arrow()
        + node("Análisis", "Hilo propio: detección (YOLO11), seguimiento y reglas de la modalidad activa.")
        + f'<div style="font-size: 22px; line-height: 1.4; color: {SLATE}; margin-top: 12px; padding: 0 4px;">'
          f'En paralelo, detección de sabotaje: cámara tapada, movida, a oscuras o sin señal.</div>'
        + "</div>"
    )
    return (
        eyebrow("Arquitectura")
        + title("Un hilo por cámara y siempre el último cuadro")
        + lead("Si la inferencia es más lenta que la cámara, se descartan cuadros en vez de acumular retraso.")
        + gap(36)
        + node("Fuentes", "Webcam, cámaras IP (RTSP), transmisiones HLS o archivos.")
        + arrow()
        + group
        + arrow()
        + node("Alertas", "Anti-repetición, historial en SQLite, captura y clip de ±5 s.")
        + arrow()
        + node("Avisos", "Panel en vivo (SSE), Telegram, correo y webhooks; un hilo por canal.")
        + SPACER
        + f'<div style="font-size: 21px; line-height: 1.45; color: {MUTED};">OpenCV y PyTorch liberan el GIL '
          f'durante el cálculo pesado: por eso los hilos sí trabajan en paralelo.</div>'
    )


def s_prueba(img: dict) -> str:
    rows = [
        ("Cámaras", "4 transmisiones HLS públicas de Caltrans: Emeryville, Los Ángeles, Santa Mónica y Long Beach"),
        ("Equipo", "Portátil Intel Core i5-11300H, sin GPU"),
        ("Prueba previa", "16 alertas reales en 17 minutos: congestión, cámara obstruida y cámara sin señal"),
    ]
    kv = "".join(
        f'<div style="display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 24px; '
        f'padding: 18px 0; border-bottom: 1px solid {HAIR}; align-items: baseline;">'
        f'<div style="font-size: 18px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; '
        f'color: {MUTED};">{k}</div>'
        f'<div style="font-size: 27px; line-height: 1.4; color: {INK};">{v}</div></div>'
        for k, v in rows
    )
    return (
        eyebrow("Prueba en condiciones reales")
        + title("Probado con cámaras públicas de tránsito, en vivo")
        + gap(40)
        + figure(img, "alerta", 382, 2,
                 "Alerta real en la I-5 (Los Ángeles): congestión de 18 vehículos con umbral de 12, "
                 "registrada con captura y clip como evidencia.")
        + gap(36)
        + f'<div style="border-top: 2px solid {RULE};">{kv}</div>'
    )


def s_modelo(img: dict) -> str:
    return (
        eyebrow(f"{TO} 1 · Precisión frente a costo")
        + title("¿Un modelo más grande para todas las cámaras?")
        + gap(36)
        + figure(img, "cruce", 340, 3, "Cruce de Ocean Blvd, Long Beach: camiones vistos desde arriba (YOLO11s).",
                 pos="center 45%")
        + gap(36)
        + table(["Criterio", "YOLO11n", "YOLO11s"],
                [["Detecciones en el cruce", "1×", "3×"],
                 ["Uso de CPU", "1×", "2×"],
                 ["Autopista, de noche", "Referencia", "Sin mejora"]],
                "minmax(0, 1.7fr) minmax(0, 1fr) minmax(0, 1fr)", ["left", "right", "right"])
        + gap(28)
        + callout("Decisión", "YOLO11n por defecto; YOLO11s solo en el cruce.")
    )


def s_sensibilidad(img: dict) -> str:
    layers = [
        "Lo que nunca se movió no se cuenta.",
        "Lo quieto y dudoso durante 30 s pasa a ser «decorado».",
        "Zonas de exclusión dibujadas sobre la imagen.",
    ]
    layer_rows = "".join(
        f'<div style="display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 12px; '
        f'padding: 20px 0; border-bottom: 1px solid {HAIR}; align-items: baseline;">'
        f'<div style="font-size: 26px; font-weight: 600; color: {ACCENT}; font-variant-numeric: tabular-nums;">{i}</div>'
        f'<div style="font-size: 29px; line-height: 1.38; color: {INK};">{t}</div></div>'
        for i, t in enumerate(layers, start=1)
    )
    return (
        eyebrow(f"{TO} 2 · Sensibilidad frente a falsas alarmas")
        + title("Detectar más vehículos sin inventar vehículos")
        + lead("De noche, bajar el umbral de confianza de 0,40 a 0,25 pasó de menos de 1 a 3-4 autos "
               "detectados por cuadro. Pero un cartel verde y una baliza empezaron a contarse como vehículos.")
        + gap(40)
        + label("Solución: un filtro en tres capas")
        + gap(10)
        + f'<div style="border-top: 1px solid {HAIR};">{layer_rows}</div>'
        + gap(52)
        + label("Resultado con la misma grabación de 3 minutos")
        + gap(16)
        + table(["Métrica", "Antes", "Después"],
                [["Cartel contado como vehículo", "94 cuadros", "Destellos de 1 cuadro"],
                 ["Autos rápidos cercanos marcados", "59 %", "79 %"]],
                "minmax(0, 1.45fr) minmax(0, 0.8fr) minmax(0, 1.35fr)", ["left", "right", "right"])
    )


def s_cpu(img: dict) -> str:
    def bar(name: str, pct: int, color: str) -> str:
        return (
            f'<div style="display: grid; grid-template-columns: 130px minmax(0, 1fr); align-items: center;">'
            f'<div style="font-size: 22px; font-weight: 500; color: {TEXT};">{name}</div>'
            f'<div style="position: relative; height: 66px;">'
            f'<div style="position: absolute; left: 0; top: 0; bottom: 0; width: {pct}%; background: {color}; '
            f'border-radius: 0 6px 6px 0;"></div>'
            f'<div style="position: absolute; left: calc({pct}% + 16px); top: 50%; transform: translateY(-50%); '
            f'font-size: 46px; font-weight: 600; letter-spacing: -0.01em; color: {INK}; line-height: 1; '
            f'font-variant-numeric: tabular-nums;">{pct}&nbsp;%</div></div></div>'
        )

    grid = "".join(
        f'<div style="position: absolute; left: {v}%; top: 0; bottom: 0; width: 1px; background: {HAIR};"></div>'
        for v in (0, 25, 50, 75, 100)
    )
    ticks = "".join(
        f'<div style="position: absolute; left: {v}%; transform: translateX(-50%); font-size: 18px; '
        f'color: {MUTED}; font-variant-numeric: tabular-nums;">{v}</div>'
        for v in (0, 25, 50, 75, 100)
    )
    chart = (
        f'<div style="background: {CARD}; border: 1px solid {HAIR}; border-radius: 16px; '
        f'padding: 26px 28px 22px; flex-shrink: 0;">'
        f'<div style="display: flex; justify-content: space-between; align-items: baseline; gap: 20px;">'
        f'<div><div style="font-size: 24px; font-weight: 600; color: {INK};">Uso de CPU con 3 cámaras en vivo</div>'
        f'<div style="font-size: 19px; color: {MUTED}; margin-top: 4px;">Intel Core i5-11300H sin GPU · '
        f'~7 cuadros por segundo por cámara en ambos casos</div></div>'
        f'<div style="font-size: 30px; font-weight: 600; color: {ACCENT}; white-space: nowrap;">−62&nbsp;%</div></div>'
        f'<div style="position: relative; margin-top: 26px; display: flex; flex-direction: column; gap: 16px;">'
        f'<div style="position: absolute; left: 130px; right: 0; top: -6px; bottom: -6px;">{grid}</div>'
        f'{bar("Antes", 64, BAR_BEFORE)}{bar("Después", 24, ACCENT)}</div>'
        f'<div style="display: grid; grid-template-columns: 130px minmax(0, 1fr); margin-top: 12px;">'
        f'<div style="font-size: 18px; color: {MUTED};">% de CPU</div>'
        f'<div style="position: relative; height: 24px;">{ticks}</div></div></div>'
    )
    changes = [
        f"Hilos en reposo entre inferencias: {code('KMP_BLOCKTIME=1')} y {code('OMP_WAIT_POLICY=PASSIVE')}",
        "Menos hilos por inferencia: núcleos ÷ 4.",
        "Detección desacoplada del análisis y modo reposo con la escena quieta.",
    ]
    change_rows = "".join(
        f'<div style="display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 12px;">'
        f'<div style="width: 12px; height: 2px; background: {ACCENT}; margin-top: 18px;"></div>'
        f'<div style="font-size: 28px; line-height: 1.45; color: {TEXT}; text-wrap: pretty;">{c}</div></div>'
        for c in changes
    )
    return (
        eyebrow(f"{TO} 3 · Simplicidad frente a rendimiento")
        + title("Hilos por cámara: simples, hasta que medimos la CPU")
        + gap(40)
        + chart
        + gap(48)
        + label("Diagnóstico")
        + lead("Cada cámara ejecuta su propia inferencia y los hilos de PyTorch/OpenMP quedaban en espera "
               "activa entre una y otra.", size=29, top=10, color=TEXT)
        + gap(40)
        + label("Cambios")
        + gap(12)
        + f'<div style="display: flex; flex-direction: column; gap: 14px;">{change_rows}</div>'
    )


def s_abiertos(img: dict) -> str:
    rows = [
        ("Tracker propio frente a ByteTrack o DeepSORT", "En evaluación",
         "Liviano y sin dependencias, pero sin reidentificación por apariencia: una oclusión larga "
         "puede cambiar el ID."),
        ("Detecciones por segundo frente a vehículos rápidos", "En evaluación",
         "A 4 detecciones por segundo, un auto cercano cruza la imagen en 1 o 2 s y a veces se marca tarde."),
        ("PyTorch frente a ONNX u OpenVINO", "Pendiente",
         "Medir cuánto se gana en CPU antes de cambiar el motor de inferencia."),
        ("Heurística frente a estimación de pose", "Pendiente",
         "La regla de altercado es liviana pero experimental; un modelo de pose daría más señal "
         "a costa de CPU."),
        ("Precisión y recall", "Pendiente",
         "Evaluar con videos etiquetados (CAVIAR, PETS 2006); hoy no hay métricas formales."),
    ]

    def badge(text: str) -> str:
        warm = text == "En evaluación"
        return (
            f'<div style="flex-shrink: 0; font-size: 17px; font-weight: 600; letter-spacing: 0.06em; '
            f'text-transform: uppercase; padding: 6px 12px; border-radius: 8px; line-height: 1.2; '
            f'color: {WARN if warm else SLATE}; background: {WARN_BG if warm else SUBTLE}; '
            f'border: 1px solid {WARN_LINE if warm else HAIR};">{text}</div>'
        )

    items = "".join(
        f'<div style="padding: 20px 0 22px; border-bottom: 1px solid {HAIR}; display: flex; '
        f'flex-direction: column; gap: 10px;">'
        f'<div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 20px;">'
        f'<div style="font-size: 29px; font-weight: 600; line-height: 1.3; color: {INK}; text-wrap: balance;">{t}</div>{badge(s)}</div>'
        f'<div style="font-size: 26px; line-height: 1.42; color: {SLATE}; text-wrap: pretty;">{d}</div></div>'
        for t, s, d in rows
    )
    return (
        eyebrow("Próximos pasos")
        + title(f"Los {TO}s que siguen abiertos")
        + gap(36)
        + f'<div style="border-top: 2px solid {RULE};">{items}</div>'
        + SPACER
        + f'<div style="font-size: 22px; line-height: 1.45; color: {MUTED}; text-wrap: pretty;">'
          f'<span style="font-weight: 600; color: {TEXT};">Ya decidido:</span> SSE en lugar de WebSockets '
          f'(el flujo va en un solo sentido), SQLite local y video al navegador por long-polling de JPEG.</div>'
    )


def s_conclusiones(img: dict) -> str:
    items = [
        ("Las cámaras reales fallan de formas que un video local no muestra.",
         "Transmisiones HLS que llegan en ráfagas, imágenes grises y cortes de señal: varios de esos "
         "casos hoy son pruebas automáticas."),
        ("Medir antes de optimizar.",
         "El costo de los hilos solo apareció al medir con 3 cámaras en vivo."),
        ("Cada decisión, respaldada por un dato.",
         "Opus 5.5 propone y mide; la decisión final se toma con los números a la vista."),
    ]
    rows = "".join(
        f'<div style="padding: 22px 0 24px; border-bottom: 1px solid {HAIR}; display: flex; '
        f'flex-direction: column; gap: 8px;">'
        f'<div style="font-size: 31px; font-weight: 600; line-height: 1.3; color: {INK}; text-wrap: balance;">{t}</div>'
        f'<div style="font-size: 27px; line-height: 1.42; color: {SLATE}; text-wrap: pretty;">{d}</div></div>'
        for t, d in items
    )
    return (
        eyebrow("Conclusiones")
        + title("Lo que aprendimos al salir del entorno de prueba")
        + gap(36)
        + f'<div style="border-top: 2px solid {RULE};">{rows}</div>'
        + gap(32)
        + note_box("Alcance", "No entrené un modelo (uso YOLO11 preentrenado), no está en producción "
                              "y todavía no medí precisión ni recall.")
        + SPACER
        + f'<div style="font-size: 40px; font-weight: 600; line-height: 1.25; color: {INK};">'
          f'¿Qué {TO} habrías resuelto distinto?</div>'
        + gap(14)
        + f'<div style="font-size: 21px; color: {MUTED};">Python · YOLO11 · OpenCV · PyTorch · FastAPI · '
          f'SSE · SQLite · JavaScript</div>'
    )


SLIDES = [
    # (archivo, título en el lienzo, función, fuente al pie, indicación)
    ("Main", "Portada", s_portada, CALTRANS, "Desliza para ver el detalle →"),
    ("02-objetivo", "Objetivo", s_objetivo, "", ""),
    ("03-metodo", "Método de trabajo", s_metodo, "", ""),
    ("04-arquitectura", "Arquitectura", s_arquitectura, "", ""),
    ("05-prueba-en-vivo", "Prueba en vivo", s_prueba, CALTRANS + " · captura del panel de Vigía", ""),
    ("06-tradeoff-modelo", "Trade-off: modelo", s_modelo,
     "Fuente: mediciones propias con grabaciones de Caltrans, 22 y 23/09/2026", ""),
    ("07-tradeoff-sensibilidad", "Trade-off: sensibilidad", s_sensibilidad,
     "Fuente: mediciones propias con grabaciones de Caltrans, 23/09/2026", ""),
    ("08-tradeoff-cpu", "Trade-off: CPU", s_cpu, "Fuente: medición propia con 3 cámaras de Caltrans, 22/09/2026", ""),
    ("09-proximos-pasos", "Próximos pasos", s_abiertos, "", ""),
    ("10-conclusiones", "Conclusiones", s_conclusiones, "Caso práctico hecho junto a Claude Opus 5.5", ""),
]
assert len(SLIDES) == TOTAL


def slides(which: int) -> list[tuple[str, str, str]]:
    img = {k: v[which] for k, v in IMAGES.items()}
    return [
        (stem, name, frame(i, fn(img), source, cue))
        for i, (stem, name, fn, source, cue) in enumerate(SLIDES, start=1)
    ]


# --------------------------------------------------------------------------- salidas


def write_export_html() -> Path:
    body = "\n".join(
        f'<section class="slide" id="s{i:02d}">{markup}</section>'
        for i, (_, _, markup) in enumerate(slides(0), start=1)
    )
    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Carrusel de Vigía</title>
<link rel="stylesheet" href="{FONTS}">
<style>
@page {{ size: {W}px {H}px; margin: 0; }}
html, body {{ margin: 0; background: {PAGE}; }}
.slide {{ width: {W}px; height: {H}px; overflow: hidden; break-after: page; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "carrusel.html"
    path.write_text(html, encoding="utf-8")
    return path


def dc_html(name: str, markup: str) -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{name}</title>
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link rel="stylesheet" href="{FONTS.replace('&', '&amp;')}">
<style>
body{{margin:0;font-family:{SANS};background:{PAGE};color:{TEXT}}}
a{{color:{ACCENT}}}a:hover{{color:#7DD3FC}}
</style>
</helmet>
{markup}
</x-dc>
<script type="text/x-dc" data-dc-script data-props='{{"$preview":{{"width":{W},"height":{H}}}}}'>
class Component extends DCLogic {{
renderVals() {{
return {{}};
}}
}}
</script>
</body>
</html>
"""


def write_canvas(root: Path, existing: Path | None = None) -> dict:
    """Escribe las mesas de trabajo y el índice. Devuelve {ruta: ruta | None} para publicar.

    Con `existing` (el canvas.json leído del lienzo) conserva sus claves y quita las
    mesas de trabajo que ya no existen.
    """
    current = json.loads(existing.read_text(encoding="utf-8")) if existing else {}
    previous = list(current.get("boards", {}))
    project = root / "project"
    project.mkdir(parents=True, exist_ok=True)
    boards, order, files = {}, [], {}
    for i, (stem, name, markup) in enumerate(slides(1)):
        fname = f"{stem}.dc.html"
        (project / fname).write_text(dc_html(name, markup), encoding="utf-8")
        boards[fname] = {"x": i * (W + 80), "y": 0, "w": W, "h": H, "title": f"{i + 1:02d} · {name}"}
        order.append(fname)
        files[f"project/{fname}"] = f"project/{fname}"
    for old in previous or []:
        if old not in boards:
            files[f"project/{old}"] = None
    row_w = len(SLIDES) * W + (len(SLIDES) - 1) * 80
    post = (ROOT / "publicacion.md").read_text(encoding="utf-8").strip()
    canvas = {
        "v": 3,
        "createdOnFiles": {"v": 1, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "title": "Carrusel de Vigía",
        "launch": {"view": "canvas"},
        "pages": [],
        "designSystems": [],
        **current,
    }
    canvas["boards"] = boards
    canvas["order"] = order
    canvas["notes"] = {
        **current.get("notes", {}),
        "titulo": {"x": 0, "y": -330, "text": "Vigía × Claude Opus 5.5 · carrusel para LinkedIn (1080 × 1350)",
                   "kind": "title1", "maxW": row_w},
        "texto": {"x": 0, "y": H + 160, "text": "TEXTO DE LA PUBLICACIÓN\n\n" + post,
                  "w": 1500, "maxH": 2600, "size": "l"},
    }
    (project / "canvas.json").write_text(json.dumps(canvas, ensure_ascii=False, indent=1), encoding="utf-8")
    return files


if __name__ == "__main__":
    print("HTML:", write_export_html())
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
        existing = Path(sys.argv[2]) if len(sys.argv) > 2 else None
        print("Lienzo:", json.dumps(write_canvas(root, existing), ensure_ascii=False))
