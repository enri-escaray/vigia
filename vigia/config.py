"""Carga y validación de la configuración (YAML con claves en español).

Los valores pueden usar variables de entorno con ``${VARIABLE}`` o
``${VARIABLE:-valor_por_defecto}``; si junto al YAML hay un archivo ``.env``,
se carga automáticamente.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from vigia.geometry import Line, Point, Zone
from vigia.rules import RULE_TYPES
from vigia.rules.base import COMMON_KEYS, RuleConfig
from vigia.types import Severity, strip_accents
from vigia.vision.detectors import DEFAULT_CATEGORIES


class ConfigError(ValueError):
    """Error de configuración con un mensaje pensado para el usuario."""


# ---------------------------------------------------------------------------------
# Modelo de configuración
# ---------------------------------------------------------------------------------
@dataclass
class SystemConfig:
    name: str = "Vigía"
    data_dir: Path = Path("datos")
    log_level: str = "INFO"
    retention_days: int = 30


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    user: str = ""
    password: str = ""


@dataclass
class DetectorConfig:
    kind: str = "auto"  # auto | yolo | hog | movimiento
    model: str = "modelos/yolo11n.pt"
    confidence: float = 0.4
    image_size: int = 640
    device: str = ""
    categories: dict[str, list[str]] = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_CATEGORIES.items()})
    confidence_by_category: dict[str, float] = field(default_factory=lambda: {"arma": 0.5})
    motion_min_area: float = 0.002
    threads: int = 0  # hilos de PyTorch; 0 = automático


@dataclass
class TrackerConfig:
    iou_threshold: float = 0.25
    max_age: float = 2.0
    min_hits: int = 3
    # Los vehículos cruzan rápido la imagen: con 2 detecciones se confirman antes
    # (sus falsos positivos, como un cartel, se filtran porque no se mueven).
    min_hits_by_category: dict[str, int] = field(default_factory=lambda: {"vehiculo": 2})
    max_age_by_category: dict[str, float] = field(default_factory=lambda: {"equipaje": 10.0})
    max_distance: float = 1.0
    max_distance_by_category: dict[str, float] = field(default_factory=lambda: {"vehiculo": 2.5})
    # Categorías que solo cuentan (y se dibujan) si el objeto se movió alguna vez
    # o si el modelo está bastante seguro de lo que ve (confianza media de sus
    # últimas detecciones de al menos `static_confidence`): así un cartel o una
    # baliza que el modelo confunde con un auto no aparecen, y un auto
    # estacionado sí.
    moving_only: list[str] = field(default_factory=lambda: ["vehiculo"])
    static_confidence: float = 0.5
    # En esas categorías, lo que se detecta quieto y con poca confianza en el
    # mismo lugar durante este tiempo pasa a ser "decorado" y se descarta (0 = nunca).
    static_after: float = 30.0


@dataclass
class RecordingConfig:
    clips: bool = True
    pre_seconds: float = 5.0
    post_seconds: float = 5.0
    max_seconds: float = 30.0
    jpeg_quality: int = 80
    clip_width: int = 960  # ancho máximo de los clips (el tamaño del archivo crece con la resolución)
    max_storage_gb: float = 5.0  # al superarse se borran los clips más viejos (0 = sin límite)


@dataclass
class CameraConfig:
    id: str
    name: str
    source: str | int
    enabled: bool = True
    fps: float = 10.0
    detect_fps: float = 5.0
    idle_fps: float = 1.0
    max_width: int = 1280
    loop: bool = True
    rtsp_tcp: bool = True
    zones: list[Zone] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    mode: str | None = None  # modalidad fija de esta cámara; None = sigue la modalidad general
    detector: DetectorConfig | None = None  # ajustes de detección propios; None = los generales
    tracker: TrackerConfig | None = None  # ajustes de seguimiento propios; None = los generales

    @property
    def safe_source(self) -> str:
        """Fuente sin contraseña, apta para mostrar."""
        return re.sub(r"(://[^:/@]+:)[^@]+@", r"\1***@", str(self.source))


@dataclass
class ModeConfig:
    name: str
    label: str
    description: str
    color: str
    rules: list[RuleConfig]


@dataclass
class ScheduleEntry:
    mode: str
    days: frozenset[int]  # 0 = lunes ... 6 = domingo
    start: dtime
    end: dtime

    def matches(self, dt: datetime) -> bool:
        t, wd = dt.time(), dt.weekday()
        if self.start == self.end:
            return wd in self.days
        if self.start < self.end:
            return wd in self.days and self.start <= t < self.end
        # Tramo que cruza la medianoche: [inicio, 24h) del día indicado y [0, fin) del día siguiente.
        if t >= self.start:
            return wd in self.days
        return t < self.end and (wd - 1) % 7 in self.days

    def to_dict(self) -> dict:
        return {
            "modalidad": self.mode,
            "dias": [DAY_NAMES[d] for d in sorted(self.days)],
            "desde": self.start.strftime("%H:%M"),
            "hasta": self.end.strftime("%H:%M"),
        }


@dataclass
class ModesConfig:
    initial: str
    default: str
    schedule: list[ScheduleEntry]
    definitions: dict[str, ModeConfig]
    camera_only: set[str] = field(default_factory=set)  # usadas solo como modalidad fija de alguna cámara


@dataclass
class NotificationsConfig:
    min_severity: Severity = Severity.MEDIA
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Config:
    path: Path | None
    base_dir: Path
    system: SystemConfig
    web: WebConfig
    detector: DetectorConfig
    tracker: TrackerConfig
    recording: RecordingConfig
    cameras: list[CameraConfig]
    modes: ModesConfig
    notifications: NotificationsConfig
    warnings: list[str] = field(default_factory=list)

    @property
    def data_dir(self) -> Path:
        d = self.system.data_dir
        return d if d.is_absolute() else (self.base_dir / d)

    def camera(self, cam_id: str) -> CameraConfig | None:
        return next((c for c in self.cameras if c.id == cam_id), None)


# ---------------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------------
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_KIND_NAMES = {bool: "sí/no", int: "un número entero", float: "un número", str: "un texto", list: "una lista", dict: "una sección"}
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

DAY_NAMES = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
_DAY_ALIASES = {
    "lun": 0, "lunes": 0, "mar": 1, "martes": 1, "mie": 2, "miercoles": 2, "jue": 3, "jueves": 3,
    "vie": 4, "viernes": 4, "sab": 5, "sabado": 5, "dom": 6, "domingo": 6,
}  # fmt: skip
_DAY_GROUPS = {
    "todos": range(7), "diario": range(7), "laborables": range(5), "fin_de_semana": (5, 6), "finde": (5, 6),
}  # fmt: skip
_MODE_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#14b8a6", "#ec4899", "#64748b"]


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def load_dotenv(path: Path) -> None:
    """Carga un archivo .env sencillo (CLAVE=valor). No pisa variables ya definidas."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _coerce(value: Any, kind: type | None, path: str) -> Any:
    if kind is None:
        return value
    try:
        if kind is bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                v = strip_accents(value).strip().lower()
                if v in ("si", "true", "1", "yes", "on", "activo"):
                    return True
                if v in ("no", "false", "0", "off", "inactivo"):
                    return False
            if isinstance(value, (int, float)):
                return bool(value)
            raise ValueError
        if kind is int:
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if number != int(number):
                raise ValueError
            return int(number)
        if kind is float:
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if kind is str:
            if isinstance(value, (dict, list)):
                raise ValueError
            return str(value)
        if kind is list:
            if isinstance(value, (list, tuple)):
                return list(value)
            if isinstance(value, str):
                return [s.strip() for s in value.split(",") if s.strip()]
            return [value]
        if kind is dict:
            if isinstance(value, dict):
                return dict(value)
            raise ValueError
    except (TypeError, ValueError):
        raise ConfigError(f"'{path}': valor inválido {value!r} (se esperaba {_KIND_NAMES[kind]})") from None
    return value


class _Section:
    """Lector de una sección que recuerda su ruta (para los mensajes de error)
    y detecta claves desconocidas, típicamente errores de tipeo."""

    def __init__(self, data: Any, path: str) -> None:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ConfigError(f"'{path}' debe ser una sección con claves y valores")
        self.data = data
        self.path = path
        self._used: set[str] = set()

    def _key_path(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def get(self, key: str, default: Any = None, kind: type | None = None) -> Any:
        self._used.add(key)
        value = self.data.get(key)
        if value is None:
            return default
        return _coerce(value, kind, self._key_path(key))

    def section(self, key: str) -> "_Section":
        self._used.add(key)
        return _Section(self.data.get(key), self._key_path(key))

    def done(self) -> None:
        unknown = [str(k) for k in self.data if k not in self._used]
        if unknown:
            where = self.path or "la raíz del archivo"
            raise ConfigError(f"Clave(s) desconocida(s) en {where}: {', '.join(unknown)}")


def _parse_points(value: Any, path: str, min_points: int = 1, exact: int | None = None) -> tuple[Point, ...]:
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"'{path}' debe ser una lista de puntos [x, y]")
    points: list[Point] = []
    for i, p in enumerate(value):
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            raise ConfigError(f"'{path}[{i}]' debe ser un punto [x, y]")
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError):
            raise ConfigError(f"'{path}[{i}]': coordenadas inválidas {p!r}") from None
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ConfigError(
                f"'{path}[{i}]': las coordenadas van de 0 a 1 (fracción del ancho y alto de la imagen); "
                f"se recibió [{x}, {y}]. Puede dibujar las zonas desde el panel web."
            )
        points.append((x, y))
    if exact is not None and len(points) != exact:
        raise ConfigError(f"'{path}' debe tener exactamente {exact} puntos")
    if len(points) < min_points:
        raise ConfigError(f"'{path}' debe tener al menos {min_points} puntos")
    return tuple(points)


def _parse_name(value: Any, path: str) -> str:
    name = str(value or "").strip()
    if not name or not _NAME_PATTERN.match(name):
        raise ConfigError(f"'{path}': nombre inválido {value!r} (use letras, números, '_' o '-', sin espacios)")
    return name


def parse_days(value: Any, path: str = "dias") -> frozenset[int]:
    tokens = _coerce(value, list, path)
    days: set[int] = set()
    for token in tokens:
        key = strip_accents(str(token)).strip().lower().replace(" ", "_")
        if key in _DAY_GROUPS:
            days.update(_DAY_GROUPS[key])
        elif "-" in key and all(part in _DAY_ALIASES for part in key.split("-", 1)):
            a, b = (_DAY_ALIASES[part] for part in key.split("-", 1))
            d = a
            while True:
                days.add(d)
                if d == b:
                    break
                d = (d + 1) % 7
        elif key in _DAY_ALIASES:
            days.add(_DAY_ALIASES[key])
        else:
            raise ConfigError(f"'{path}': día desconocido '{token}' (use lun, mar, mie, jue, vie, sab, dom, lun-vie, todos...)")
    if not days:
        raise ConfigError(f"'{path}' no puede estar vacío")
    return frozenset(days)


def parse_time(value: Any, path: str) -> dtime:
    text = str(value).strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not m:
        raise ConfigError(f"'{path}': hora inválida '{text}' (formato HH:MM)")
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh == 24 and mm == 0:
        return dtime(23, 59, 59, 999999)
    if hh > 23 or mm > 59:
        raise ConfigError(f"'{path}': hora inválida '{text}'")
    return dtime(hh, mm)


# ---------------------------------------------------------------------------------
# Secciones
# ---------------------------------------------------------------------------------
def _parse_system(sec: _Section) -> SystemConfig:
    cfg = SystemConfig(
        name=sec.get("nombre", "Vigía", str),
        data_dir=Path(sec.get("datos", "datos", str)),
        log_level=sec.get("nivel_log", "INFO", str).upper(),
        retention_days=sec.get("retencion_dias", 30, int),
    )
    sec.done()
    return cfg


def _parse_web(sec: _Section) -> WebConfig:
    cfg = WebConfig(
        enabled=sec.get("activo", True, bool),
        host=sec.get("host", "127.0.0.1", str),
        port=sec.get("puerto", 8080, int),
        user=sec.get("usuario", "", str),
        password=sec.get("clave", "", str),
    )
    sec.done()
    if bool(cfg.user) != bool(cfg.password):
        raise ConfigError("web: para exigir contraseña defina 'usuario' y 'clave' (o deje ambos vacíos)")
    return cfg


def _parse_detector(sec: _Section, base: DetectorConfig | None = None) -> DetectorConfig:
    """Lee la sección de detección. Con `base` (la configuración general), la
    sección de una cámara solo reemplaza lo que indica."""
    cfg = copy.deepcopy(base) if base is not None else DetectorConfig()
    cfg.kind = strip_accents(sec.get("tipo", cfg.kind, str)).strip().lower()
    if cfg.kind not in ("auto", "yolo", "hog", "movimiento"):
        raise ConfigError(f"{sec.path}.tipo: '{cfg.kind}' no es válido (auto, yolo, hog o movimiento)")
    cfg.model = sec.get("modelo", cfg.model, str)
    cfg.confidence = sec.get("confianza", cfg.confidence, float)
    cfg.image_size = sec.get("tamano_imagen", cfg.image_size, int)
    cfg.device = sec.get("dispositivo", cfg.device, str)
    cfg.motion_min_area = sec.get("area_min_movimiento", cfg.motion_min_area, float)
    cfg.threads = max(0, sec.get("hilos", cfg.threads, int))
    for cat, labels in (sec.get("categorias", {}, dict) or {}).items():
        cfg.categories[str(cat)] = [str(x) for x in _coerce(labels or [], list, f"{sec.path}.categorias.{cat}")]
    for cat, conf in (sec.get("confianza_por_categoria", {}, dict) or {}).items():
        cfg.confidence_by_category[str(cat)] = _coerce(conf, float, f"{sec.path}.confianza_por_categoria.{cat}")
    sec.done()
    return cfg


def _parse_tracker(sec: _Section, base: TrackerConfig | None = None) -> TrackerConfig:
    """Lee la sección de seguimiento. Con `base` (la configuración general), la
    sección de una cámara solo reemplaza lo que indica."""
    cfg = copy.deepcopy(base) if base is not None else TrackerConfig()
    cfg.iou_threshold = sec.get("iou_min", cfg.iou_threshold, float)
    cfg.max_age = sec.get("edad_max", cfg.max_age, float)
    cfg.min_hits = sec.get("confirmaciones", cfg.min_hits, int)
    for cat, hits in (sec.get("confirmaciones_por_categoria", {}, dict) or {}).items():
        cfg.min_hits_by_category[str(cat)] = _coerce(hits, int, f"{sec.path}.confirmaciones_por_categoria.{cat}")
    for cat, age in (sec.get("edad_max_por_categoria", {}, dict) or {}).items():
        cfg.max_age_by_category[str(cat)] = _coerce(age, float, f"{sec.path}.edad_max_por_categoria.{cat}")
    cfg.max_distance = sec.get("distancia_max", cfg.max_distance, float)
    for cat, dist in (sec.get("distancia_max_por_categoria", {}, dict) or {}).items():
        cfg.max_distance_by_category[str(cat)] = _coerce(dist, float, f"{sec.path}.distancia_max_por_categoria.{cat}")
    cfg.moving_only = [str(c) for c in sec.get("solo_en_movimiento", cfg.moving_only, list)]
    cfg.static_confidence = sec.get("confianza_quietos", cfg.static_confidence, float)
    cfg.static_after = max(0.0, sec.get("descartar_fijos_tras", cfg.static_after, float))
    sec.done()
    return cfg


def _parse_recording(sec: _Section) -> RecordingConfig:
    cfg = RecordingConfig(
        clips=sec.get("clips", True, bool),
        pre_seconds=sec.get("segundos_antes", 5.0, float),
        post_seconds=sec.get("segundos_despues", 5.0, float),
        max_seconds=sec.get("duracion_max", 30.0, float),
        jpeg_quality=sec.get("calidad_jpeg", 80, int),
        clip_width=sec.get("ancho_clip", 960, int),
        max_storage_gb=sec.get("espacio_max_gb", 5.0, float),
    )
    sec.done()
    cfg.jpeg_quality = max(30, min(100, cfg.jpeg_quality))
    cfg.clip_width = max(320, cfg.clip_width)
    cfg.max_storage_gb = max(0.0, cfg.max_storage_gb)
    return cfg


def _parse_camera(raw: Any, idx: int, detector: DetectorConfig, tracker: TrackerConfig) -> CameraConfig:
    path = f"camaras[{idx}]"
    sec = _Section(raw, path)
    cam_id = _parse_name(sec.get("id", f"cam{idx + 1}", str), f"{path}.id")
    source = sec.get("fuente")
    if source is None or source == "" or isinstance(source, bool):
        raise ConfigError(f"La cámara '{cam_id}' necesita 'fuente': índice de webcam (0), URL rtsp://... / http://... o un archivo de video")
    if isinstance(source, (int, float)):
        source = int(source)
    else:
        source = str(source).strip()
        if source.isdigit():
            source = int(source)

    zones = []
    for j, z in enumerate(sec.get("zonas", [], list)):
        zsec = _Section(z, f"{path}.zonas[{j}]")
        name = _parse_name(zsec.get("nombre"), f"{path}.zonas[{j}].nombre")
        zones.append(Zone(name, _parse_points(zsec.get("puntos"), f"{path}.zonas[{j}].puntos", min_points=3)))
        zsec.done()
    lines = []
    for j, ln in enumerate(sec.get("lineas", [], list)):
        lsec = _Section(ln, f"{path}.lineas[{j}]")
        name = _parse_name(lsec.get("nombre"), f"{path}.lineas[{j}].nombre")
        a, b = _parse_points(lsec.get("puntos"), f"{path}.lineas[{j}].puntos", exact=2)
        lines.append(Line(name, a, b))
        lsec.done()
    for kind, items in (("zona", zones), ("línea", lines)):
        names = [i.name for i in items]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise ConfigError(f"La cámara '{cam_id}' tiene {kind}s repetidas: {', '.join(sorted(dup))}")

    cfg = CameraConfig(
        id=cam_id,
        name=sec.get("nombre", cam_id, str),
        source=source,
        enabled=sec.get("activo", True, bool),
        fps=sec.get("fps", 10.0, float),
        detect_fps=sec.get("fps_deteccion", 5.0, float),
        idle_fps=sec.get("fps_reposo", 1.0, float),
        max_width=sec.get("ancho_max", 1280, int),
        loop=sec.get("repetir", True, bool),
        rtsp_tcp=sec.get("rtsp_tcp", True, bool),
        zones=zones,
        lines=lines,
        mode=sec.get("modalidad", None, str),
    )
    det_raw = sec.get("deteccion", None, dict)
    if det_raw:
        cfg.detector = _parse_detector(_Section(det_raw, f"{path}.deteccion"), base=detector)
    trk_raw = sec.get("seguimiento", None, dict)
    if trk_raw:
        cfg.tracker = _parse_tracker(_Section(trk_raw, f"{path}.seguimiento"), base=tracker)
    sec.done()
    if cfg.fps <= 0 or cfg.detect_fps <= 0 or cfg.idle_fps < 0:
        raise ConfigError(f"La cámara '{cam_id}': fps y fps_deteccion deben ser mayores que 0")
    cfg.detect_fps = min(cfg.detect_fps, cfg.fps)
    cfg.max_width = max(320, cfg.max_width)
    return cfg


def _parse_rule(raw: Any, mode: str, index: int) -> RuleConfig | None:
    path = f"modalidades.definiciones.{mode}.reglas[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"'{path}' debe ser una regla con 'tipo' y sus parámetros")
    rtype = strip_accents(str(raw.get("tipo", ""))).strip().lower()
    if rtype not in RULE_TYPES:
        raise ConfigError(f"'{path}': tipo de regla desconocido '{raw.get('tipo')}'. Tipos válidos: {', '.join(RULE_TYPES)}")
    cls = RULE_TYPES[rtype]
    unknown = sorted(str(k) for k in raw if k not in COMMON_KEYS and k not in cls.PARAMS)
    if unknown:
        valid = ", ".join(sorted(COMMON_KEYS | set(cls.PARAMS)))
        raise ConfigError(f"'{path}' ({rtype}): parámetro(s) desconocido(s): {', '.join(unknown)}. Válidos: {valid}")
    if not _coerce(raw.get("activo", True), bool, f"{path}.activo"):
        return None

    params: dict[str, Any] = {}
    for key, default in cls.PARAMS.items():
        if raw.get(key) is None:
            continue
        value = _coerce(raw[key], type(default), f"{path}.{key}")
        if isinstance(default, list):
            value = [str(v) for v in value]
        params[key] = value
    try:
        cls.validate_params(params)
        severity = Severity.parse(raw.get("severidad") or cls.default_severity)
    except ValueError as exc:
        raise ConfigError(f"'{path}' ({rtype}): {exc}") from None
    cameras = raw.get("camaras")
    title = None
    if raw.get("titulo"):
        title = _coerce(raw["titulo"], str, f"{path}.titulo").strip() or None
    return RuleConfig(
        type=rtype,
        title=title,
        name=_parse_name(raw.get("nombre") or rtype, f"{path}.nombre"),
        severity=severity,
        cooldown=_coerce(raw.get("enfriamiento", cls.default_cooldown), float, f"{path}.enfriamiento"),
        cameras=[str(c) for c in _coerce(cameras, list, f"{path}.camaras")] if cameras else None,
        zones=[str(z) for z in _coerce(raw.get("zonas") or [], list, f"{path}.zonas")],
        params=params,
    )


def _parse_modes(sec: _Section) -> ModesConfig:
    defs_raw = sec.get("definiciones", None, dict)
    if not defs_raw:
        raise ConfigError("Defina al menos una modalidad en 'modalidades.definiciones'")
    definitions: dict[str, ModeConfig] = {}
    for i, (raw_name, raw) in enumerate(defs_raw.items()):
        name = _parse_name(raw_name, f"modalidades.definiciones.{raw_name}")
        msec = _Section(raw, f"modalidades.definiciones.{name}")
        rules: list[RuleConfig] = []
        for j, rule_raw in enumerate(msec.get("reglas", [], list)):
            rule = _parse_rule(rule_raw, name, j)
            if rule is None:
                continue
            if any(r.name == rule.name for r in rules):
                rule.name = f"{rule.name}_{j + 1}"
            rules.append(rule)
        definitions[name] = ModeConfig(
            name=name,
            label=msec.get("etiqueta", name.replace("_", " ").capitalize(), str),
            description=msec.get("descripcion", "", str),
            color=msec.get("color", _MODE_COLORS[i % len(_MODE_COLORS)], str),
            rules=rules,
        )
        msec.done()

    schedule: list[ScheduleEntry] = []
    for i, raw in enumerate(sec.get("horario", [], list)):
        path = f"modalidades.horario[{i}]"
        ssec = _Section(raw, path)
        mode = str(ssec.get("modalidad", "", str))
        if mode not in definitions:
            raise ConfigError(f"'{path}.modalidad': '{mode}' no está definida (opciones: {', '.join(definitions)})")
        schedule.append(
            ScheduleEntry(
                mode=mode,
                days=parse_days(ssec.get("dias", "todos"), f"{path}.dias"),
                start=parse_time(ssec.get("desde", "00:00"), f"{path}.desde"),
                end=parse_time(ssec.get("hasta", "00:00"), f"{path}.hasta"),
            )
        )
        ssec.done()

    default = sec.get("por_defecto", next(iter(definitions)), str)
    if default not in definitions:
        raise ConfigError(f"modalidades.por_defecto: '{default}' no está definida")
    initial = sec.get("inicial", "auto", str)
    if initial != "auto" and initial not in definitions:
        raise ConfigError(f"modalidades.inicial: '{initial}' no está definida (use 'auto' o una de: {', '.join(definitions)})")
    sec.done()
    return ModesConfig(initial=initial, default=default, schedule=schedule, definitions=definitions)


def _parse_notifications(sec: _Section) -> NotificationsConfig:
    min_severity = _parse_severity(sec.get("severidad_minima", "media"), "notificaciones.severidad_minima")
    channels: dict[str, dict[str, Any]] = {}
    for name in ("sonido", "webhook", "telegram", "correo"):
        raw = sec.get(name, None, dict)
        if raw is None:
            continue
        channel = dict(raw)
        channel["activo"] = _coerce(channel.get("activo", True), bool, f"notificaciones.{name}.activo")
        channel["severidad_minima"] = _parse_severity(
            channel.get("severidad_minima") or min_severity, f"notificaciones.{name}.severidad_minima"
        )
        channels[name] = channel
    sec.done()
    return NotificationsConfig(min_severity=min_severity, channels=channels)


def _parse_severity(value: Any, path: str) -> Severity:
    try:
        return Severity.parse(value)
    except ValueError as exc:
        raise ConfigError(f"'{path}': {exc}") from None


# ---------------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------------
def parse_config(data: dict, path: Path | None = None, base_dir: Path | None = None) -> Config:
    data = expand_env(data or {})
    root = _Section(data, "")
    detector = _parse_detector(root.section("deteccion"))
    tracker = _parse_tracker(root.section("seguimiento"))
    cameras_raw = root.get("camaras", [], list)
    if not cameras_raw:
        raise ConfigError("Defina al menos una cámara en 'camaras'")
    cameras = [_parse_camera(raw, i, detector, tracker) for i, raw in enumerate(cameras_raw)]
    ids = [c.id for c in cameras]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ConfigError(f"Hay cámaras con el mismo id: {', '.join(sorted(dup))}")

    cfg = Config(
        path=path,
        base_dir=base_dir or (path.parent if path else Path.cwd()),
        system=_parse_system(root.section("sistema")),
        web=_parse_web(root.section("web")),
        detector=detector,
        tracker=tracker,
        recording=_parse_recording(root.section("grabacion")),
        cameras=cameras,
        modes=_parse_modes(root.section("modalidades")),
        notifications=_parse_notifications(root.section("notificaciones")),
    )
    root.done()
    modes = cfg.modes
    for cam in cfg.cameras:
        if cam.mode and cam.mode not in modes.definitions:
            raise ConfigError(
                f"La cámara '{cam.id}' usa la modalidad '{cam.mode}', que no está definida "
                f"(opciones: {', '.join(modes.definitions)})"
            )
    global_modes = {e.mode for e in modes.schedule} | {modes.default, modes.initial}
    modes.camera_only = {cam.mode for cam in cfg.cameras if cam.mode} - global_modes
    cfg.warnings = _cross_check(cfg)
    return cfg


def _cross_check(cfg: Config) -> list[str]:
    """Advertencias por referencias que no existen (probables errores de tipeo)."""
    warnings: list[str] = []
    zone_names = {z.name for c in cfg.cameras for z in c.zones}
    line_names = {ln.name for c in cfg.cameras for ln in c.lines}
    cam_ids = {c.id for c in cfg.cameras}
    for mode in cfg.modes.definitions.values():
        for rule in mode.rules:
            where = f"modalidad '{mode.name}', regla '{rule.name}'"
            for z in rule.zones:
                if z not in zone_names:
                    warnings.append(f"{where}: ninguna cámara tiene la zona '{z}' (la regla no se aplicará hasta que exista)")
            for ln in rule.params.get("lineas", []) if rule.type == "cruce_linea" else []:
                if ln not in line_names:
                    warnings.append(f"{where}: ninguna cámara tiene la línea '{ln}'")
            for cam in rule.cameras or []:
                if cam not in cam_ids:
                    warnings.append(f"{where}: la cámara '{cam}' no existe")
    return warnings


def load_config(path: str | Path) -> Config:
    path = Path(path).absolute()
    if not path.is_file():
        raise ConfigError(f"No existe el archivo de configuración: {path}")
    load_dotenv(path.parent / ".env")
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - se informa cualquier error de sintaxis YAML
        raise ConfigError(f"El archivo {path.name} no es un YAML válido:\n{exc}") from None
    if data is not None and not isinstance(data, dict):
        raise ConfigError(f"{path.name}: el archivo debe contener secciones (clave: valor)")
    return parse_config(data or {}, path=path, base_dir=path.parent)


# --- Guardado de zonas/líneas desde el panel ---------------------------------------
# Se reemplazan solo las líneas de texto de los bloques `zonas` y `lineas` de la
# cámara (ruamel se usa únicamente para ubicarlas). Así el resto del archivo,
# incluidos los comentarios que siguen a esos bloques, queda intacto.
_GEOMETRY_KEY_LINE = re.compile(r"^\s*(?:zonas|lineas)\s*:\s*(?:\[\s*\])?\s*(#.*)?$")
_YAML_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n"}


def _yaml_name(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name) and name.lower() not in _YAML_RESERVED:
        return name
    return f'"{name}"'  # los nombres ya están validados: no hay nada que escapar


def _geometry_block(key: str, items: list[Zone] | list[Line], col: int, eol_comment: str | None) -> list[str]:
    pad = " " * col
    head = f"{pad}{key}:" + ("" if items else " []") + (f"  {eol_comment}" if eol_comment else "")
    out = [head]
    for item in items:
        points = ", ".join(f"[{x!r}, {y!r}]" for x, y in item.to_dict()["puntos"])
        out.append(f"{pad}  - nombre: {_yaml_name(item.name)}")
        out.append(f"{pad}    puntos: [{points}]")
    return out


def _value_end(lines: list[str], start: int, col: int) -> int:
    """Primera línea después del valor de la clave ubicada en `start` (columna `col`).
    Las líneas en blanco y los comentarios que siguen al valor quedan fuera."""
    end = start + 1
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        indent = len(lines[i]) - len(lines[i].lstrip(" "))
        if indent > col or (indent == col and (stripped.startswith("- ") or stripped == "-")):
            end = i + 1
            continue
        break
    return end


def _splice_geometry(text: str, camera_id: str, key: str, items: list[Zone] | list[Line], filename: str) -> str:
    data = YAML().load(text)
    cameras = data.get("camaras") if isinstance(data, dict) else None
    cam = None
    for idx, candidate in enumerate(cameras or []):
        if isinstance(candidate, dict) and str(candidate.get("id") or f"cam{idx + 1}") == camera_id:
            cam = candidate
            break
    if cam is None:
        raise ConfigError(f"No se encontró la cámara '{camera_id}' en {filename}")
    if cam.fa.flow_style():
        raise ConfigError(f"La cámara '{camera_id}' está escrita en una sola línea; use el formato de bloque para editarla desde el panel")
    lines = text.splitlines()
    if key in cam:
        start, col = cam.lc.key(key)
        end = _value_end(lines, start, col)
        match = _GEOMETRY_KEY_LINE.match(lines[start])
        lines[start:end] = _geometry_block(key, items, col, match.group(1) if match else None)
    else:
        anchors = ("zonas", "fuente") if key == "lineas" else ("fuente",)
        anchor = next((a for a in anchors if a in cam), next(iter(cam)))
        start, col = cam.lc.key(anchor)
        end = _value_end(lines, start, col)
        lines[end:end] = _geometry_block(key, items, col, None)
    return "\n".join(lines) + "\n"


def save_camera_geometry(path: Path, camera_id: str, zones: list[Zone], lines: list[Line]) -> None:
    """Escribe las zonas y líneas de una cámara en el YAML sin tocar el resto del
    archivo. Valida el resultado antes de escribir y deja una copia en ``.bak``."""
    original = path.read_bytes().decode("utf-8")  # sin traducir saltos de línea: se respetan los CRLF
    text = original.replace("\r\n", "\n")
    for key, items in (("zonas", zones), ("lineas", lines)):
        text = _splice_geometry(text, camera_id, key, items, path.name)
    check = parse_config(YAML(typ="safe").load(text), path=path)
    cam = check.camera(camera_id)
    if cam is None or [z.name for z in cam.zones] != [z.name for z in zones] or [ln.name for ln in cam.lines] != [ln.name for ln in lines]:
        raise ConfigError(f"No se pudo actualizar {path.name} de forma segura; edite las zonas a mano")
    if "\r\n" in original:
        text = text.replace("\n", "\r\n")
    shutil.copyfile(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)
