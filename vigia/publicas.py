"""Cámaras públicas en vivo para probar Vigía con video real.

Fuente: Caltrans (Departamento de Transporte de California), portal CWWP2.
Son datos abiertos y gratuitos, sin clave de API: cientos de cámaras de
autopista con video en vivo (HLS) y una imagen fija que se actualiza.
Documentación: https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm

Muchas cámaras figuran "en servicio" pero su transmisión no está activa en un
momento dado, por eso se verifica cada una antes de usarla.
"""

from __future__ import annotations

import concurrent.futures as cf
import re
import unicodedata
from dataclasses import dataclass

import requests

JSON_URL = "https://cwwp2.dot.ca.gov/data/d{d}/cctv/cctvStatusD{d:02d}.json"
DISTRICTS = {
    1: "Eureka",
    2: "Redding",
    3: "Sacramento",
    4: "Área de la Bahía de San Francisco",
    5: "San Luis Obispo",
    6: "Fresno",
    7: "Los Ángeles",
    8: "San Bernardino",
    9: "Bishop",
    10: "Stockton",
    11: "San Diego",
    12: "Condado de Orange",
}
_HEADERS = {"User-Agent": "Vigia/1.0 (videovigilancia; pruebas con datos abiertos de Caltrans)"}


@dataclass
class PublicCamera:
    id: str
    name: str
    place: str
    district: int
    stream: str
    image: str | None = None


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def list_caltrans(district: int, search: str | None = None, session: requests.Session | None = None) -> list[PublicCamera]:
    """Cámaras en servicio con video de un distrito (opcionalmente filtradas por texto)."""
    http = session or requests
    response = http.get(JSON_URL.format(d=district), headers=_HEADERS, timeout=30)
    response.raise_for_status()
    cameras: list[PublicCamera] = []
    for item in response.json().get("data", []):
        cctv = item.get("cctv", {})
        image = cctv.get("imageData", {})
        stream = (image.get("streamingVideoURL") or "").strip()
        if not stream or str(cctv.get("inService")).lower() != "true":
            continue
        location = cctv.get("location", {})
        name = location.get("locationName") or stream
        place = location.get("nearbyPlace") or ""
        if search and _fold(search) not in _fold(f"{name} {place}"):
            continue
        cameras.append(
            PublicCamera(
                id=_slug(name),
                name=name,
                place=place,
                district=district,
                stream=stream,
                image=(image.get("static") or {}).get("currentImageURL"),
            )
        )
    return cameras


def is_live(url: str, timeout: float = 8.0, session: requests.Session | None = None) -> bool:
    """La transmisión HLS responde con una lista de reproducción válida."""
    try:
        response = (session or requests).get(url, headers=_HEADERS, timeout=timeout)
    except requests.RequestException:
        return False
    return response.status_code == 200 and "#EXTM3U" in response.text[:200]


def find_live(district: int, count: int = 4, search: str | None = None, workers: int = 16) -> list[PublicCamera]:
    """Devuelve hasta `count` cámaras cuya transmisión está activa ahora."""
    candidates = list_caltrans(district, search)
    live: list[PublicCamera] = []
    pool = cf.ThreadPoolExecutor(workers)
    try:
        futures = {pool.submit(is_live, cam.stream): cam for cam in candidates}
        for future in cf.as_completed(futures):
            if future.result():
                live.append(futures[future])
                if len(live) >= count:
                    break
    finally:
        pool.shutdown(wait=True, cancel_futures=True)  # no sigue probando las que faltan
    order = {id(cam): i for i, cam in enumerate(candidates)}
    return sorted(live, key=lambda cam: order[id(cam)])


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", _fold(text)).strip("_")
    return (slug or "camara")[:40]


def _yaml_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def config_yaml(cameras: list[PublicCamera], port: int = 8080) -> str:
    """Configuración lista para usar con las cámaras dadas y la modalidad "Vía pública"."""
    blocks = []
    used: set[str] = set()
    for cam in cameras:
        cam_id = cam.id
        n = 2
        while cam_id in used:
            cam_id, n = f"{cam.id}_{n}", n + 1
        used.add(cam_id)
        title = f"{cam.name} ({cam.place})" if cam.place else cam.name
        blocks.append(
            f"  - id: {cam_id}\n"
            f"    nombre: {_yaml_str(title)}\n"
            f"    fuente: {_yaml_str(cam.stream)}\n"
            f"    fps: 8\n"
            f"    fps_deteccion: 4\n"
            f"    zonas: []\n"
            f"    lineas: []\n"
        )
    districts = ", ".join(sorted({f"{c.district} ({DISTRICTS.get(c.district, '?')})" for c in cameras}))
    return f"""# Cámaras públicas de autopista de Caltrans (California) — distrito(s) {districts}.
# Generado por `python -m vigia publicas`. Datos abiertos y gratuitos:
# https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm
#
# Las reglas de "banquina" (vehículo detenido) y "sentido" (contramano) se
# activan cuando dibuje esa zona y esa línea en el panel ("Zonas y líneas").
# En la línea "sentido", la flecha debe apuntar en el sentido de circulación
# correcto: cruzarla al revés dispara la alerta.

sistema:
  nombre: "Vigía · Vía pública"
  datos: datos/publicas

web:
  puerto: {port}

deteccion:
  tipo: auto
  modelo: modelos/yolo11n.pt
  confianza: 0.25   # de noche el modelo ve los autos con poca confianza

seguimiento:
  confirmaciones: 3
  edad_max: 2.5
  edad_max_por_categoria:
    vehiculo: 4

grabacion:
  espacio_max_gb: 2

camaras:
{"".join(blocks)}
modalidades:
  inicial: auto
  por_defecto: transito
  definiciones:
    transito:
      etiqueta: "Vía pública"
      descripcion: "Autopista: peatones en la calzada, vehículos detenidos en la banquina, contramano, congestión y cámaras caídas."
      color: "#0ea5e9"
      reglas:
        - tipo: intrusion
          nombre: peaton_en_autopista
          titulo: "Peatón en la autopista"
          categorias: [persona]
          segundos_min: 1.0
          severidad: critica
        - tipo: merodeo
          nombre: vehiculo_detenido
          titulo: "Vehículo detenido en la banquina"
          zonas: [banquina]
          categorias: [vehiculo]
          requiere_movimiento: si
          segundos: 20
          severidad: alta
        - tipo: cruce_linea
          nombre: contramano
          titulo: "Vehículo en contramano"
          lineas: [sentido]
          categorias: [vehiculo]
          direccion: salida
          severidad: critica
        - tipo: aglomeracion
          nombre: congestion
          titulo: "Congestión"
          categorias: [vehiculo]
          umbral: 12
          segundos: 30
          enfriamiento: 600
          severidad: media
        - tipo: sabotaje
          detectar: [obstruida, movida, sin_senal]
          segundos_sin_senal: 20
          severidad: media
    desarmado:
      etiqueta: "Desarmado"
      descripcion: "Solo avisa si una cámara deja de transmitir."
      color: "#64748b"
      reglas:
        - tipo: sabotaje
          detectar: [sin_senal]
          severidad: baja

notificaciones:
  severidad_minima: media
  sonido:
    activo: no
"""
