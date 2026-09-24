import shutil
from pathlib import Path

import pytest

from vigia.config import ConfigError, expand_env, load_config, parse_config, save_camera_geometry
from vigia.geometry import Line, Zone
from vigia.types import Severity

ROOT = Path(__file__).resolve().parents[1]


def minimal(**overrides):
    data = {
        "camaras": [{"id": "c1", "fuente": 0, "zonas": [{"nombre": "z", "puntos": [[0, 0], [1, 0], [1, 1]]}]}],
        "modalidades": {"definiciones": {"m": {"reglas": [{"tipo": "intrusion", "zonas": ["z"]}]}}},
    }
    data.update(overrides)
    return data


def test_example_config_is_valid():
    cfg = load_config(ROOT / "config.yaml")
    assert [c.id for c in cfg.cameras] == ["cam1", "autopista", "cruce"]
    assert set(cfg.modes.definitions) == {"desarmado", "hogar", "nocturno", "ausente", "comercio", "transito", "espacio_publico"}
    assert cfg.modes.default == "hogar"
    assert cfg.modes.camera_only == {"transito", "espacio_publico"}
    # Solo avisa de las zonas/líneas de las cámaras públicas que se dibujan en el panel.
    assert len(cfg.warnings) == 3 and all(("banquina" in w) or ("sentido" in w) or ("cruce" in w) for w in cfg.warnings)
    rule = cfg.modes.definitions["nocturno"].rules[1]
    assert rule.type == "cruce_linea" and rule.params["direccion"] == "entrada"
    assert cfg.notifications.channels["sonido"]["severidad_minima"] == Severity.ALTA


def test_defaults_are_filled():
    cfg = parse_config(minimal(), base_dir=Path("."))
    rule = cfg.modes.definitions["m"].rules[0]
    assert rule.severity == Severity.ALTA and rule.cooldown == 30.0
    assert cfg.modes.initial == "auto" and cfg.modes.default == "m"
    assert cfg.detector.kind == "auto"


def test_unknown_rule_parameter_is_reported():
    data = minimal()
    data["modalidades"]["definiciones"]["m"]["reglas"][0]["segundoz"] = 3
    with pytest.raises(ConfigError, match="segundoz"):
        parse_config(data)


def test_unknown_rule_type_is_reported():
    data = minimal()
    data["modalidades"]["definiciones"]["m"]["reglas"] = [{"tipo": "teletransporte"}]
    with pytest.raises(ConfigError, match="teletransporte"):
        parse_config(data)


def test_invalid_choice_is_reported():
    data = minimal()
    data["modalidades"]["definiciones"]["m"]["reglas"] = [{"tipo": "cruce_linea", "direccion": "arriba"}]
    with pytest.raises(ConfigError, match="direccion"):
        parse_config(data)


def test_zone_coordinates_must_be_normalized():
    data = minimal()
    data["camaras"][0]["zonas"][0]["puntos"] = [[0, 0], [640, 0], [640, 480]]
    with pytest.raises(ConfigError, match="de 0 a 1"):
        parse_config(data)


def test_unknown_section_key_is_reported():
    with pytest.raises(ConfigError, match="camara"):
        parse_config(minimal(camara=[]))


def test_schedule_must_reference_existing_mode():
    data = minimal()
    data["modalidades"]["horario"] = [{"modalidad": "fantasma", "desde": "20:00", "hasta": "06:00"}]
    with pytest.raises(ConfigError, match="fantasma"):
        parse_config(data)


def test_missing_zone_produces_warning():
    data = minimal()
    data["modalidades"]["definiciones"]["m"]["reglas"].append({"tipo": "merodeo", "zonas": ["bodega"]})
    cfg = parse_config(data)
    assert any("bodega" in w for w in cfg.warnings)


def test_camera_fixed_mode():
    data = minimal()
    data["camaras"].append({"id": "calle", "fuente": "https://x/y.m3u8", "modalidad": "via"})
    data["modalidades"]["definiciones"]["via"] = {"reglas": [{"tipo": "sabotaje"}]}
    cfg = parse_config(data)
    assert cfg.camera("calle").mode == "via" and cfg.camera("c1").mode is None
    assert cfg.modes.camera_only == {"via"}
    data["camaras"][1]["modalidad"] = "inexistente"
    with pytest.raises(ConfigError, match="inexistente"):
        parse_config(data)


def test_camera_detection_overrides_only_what_it_sets():
    data = minimal(deteccion={"confianza": 0.4, "dispositivo": "cpu", "confianza_por_categoria": {"arma": 0.6}})
    data["camaras"].append({"id": "noche", "fuente": 1, "deteccion": {"confianza": 0.25}})
    cfg = parse_config(data)
    assert cfg.camera("c1").detector is None and cfg.detector.confidence == 0.4
    night = cfg.camera("noche").detector
    assert night.confidence == 0.25 and night.device == "cpu" and night.confidence_by_category["arma"] == 0.6
    assert night is not cfg.detector and night.categories is not cfg.detector.categories
    data["camaras"][1]["deteccion"] = {"confianzza": 0.2}
    with pytest.raises(ConfigError, match="confianzza"):
        parse_config(data)


def test_camera_tracking_overrides_only_what_it_sets():
    data = minimal(seguimiento={"confirmaciones": 4, "confianza_quietos": 0.6})
    data["camaras"].append({"id": "cruce", "fuente": 1, "seguimiento": {"confianza_quietos": 0.35}})
    cfg = parse_config(data)
    assert cfg.camera("c1").tracker is None and cfg.tracker.static_confidence == 0.6
    assert cfg.tracker.min_hits_by_category == {"vehiculo": 2}  # valor por defecto
    cross = cfg.camera("cruce").tracker
    assert cross.static_confidence == 0.35 and cross.min_hits == 4
    assert cross is not cfg.tracker and cross.moving_only is not cfg.tracker.moving_only
    data["camaras"][1]["seguimiento"] = {"confianza_quieto": 0.3}
    with pytest.raises(ConfigError, match="confianza_quieto"):
        parse_config(data)


def test_env_expansion(monkeypatch):
    monkeypatch.setenv("VIGIA_PRUEBA", "secreto")
    assert expand_env({"a": "${VIGIA_PRUEBA}", "b": ["x-${NO_EXISTE:-defecto}"]}) == {"a": "secreto", "b": ["x-defecto"]}


def test_rtsp_password_is_masked():
    data = minimal()
    data["camaras"][0]["fuente"] = "rtsp://admin:clave123@192.168.1.10:554/stream"
    cfg = parse_config(data)
    assert cfg.cameras[0].safe_source == "rtsp://admin:***@192.168.1.10:554/stream"


ZONES = [Zone("caja", ((0.1, 0.1), (0.4, 0.1), (0.4, 0.5)))]
LINES = [Line("puerta", (0.5, 0.9), (0.5, 0.1))]


def _changed_lines(before: str, after: str) -> tuple[set[str], set[str]]:
    old, new = before.splitlines(), after.splitlines()
    return set(old) - set(new), set(new) - set(old)


def test_save_geometry_only_touches_geometry_lines(tmp_path):
    path = tmp_path / "config.yaml"
    shutil.copyfile(ROOT / "config.yaml", path)
    before = path.read_text(encoding="utf-8")
    save_camera_geometry(path, "cam1", ZONES, LINES)
    after = path.read_text(encoding="utf-8")
    removed, added = _changed_lines(before, after)
    assert all("nombre:" in ln or "puntos:" in ln for ln in removed | added), (removed, added)
    # Los comentarios que siguen al bloque de la cámara (ejemplos RTSP y la
    # explicación de las modalidades) deben seguir ahí.
    assert "# Cámara IP por RTSP" in after and "#  MODALIDADES:" in after
    assert "    puntos: [[0.1, 0.1], [0.4, 0.1], [0.4, 0.5]]" in after
    assert (tmp_path / "config.yaml.bak").read_text(encoding="utf-8") == before
    cfg = load_config(path)
    assert [z.name for z in cfg.cameras[0].zones] == ["caja"]
    assert cfg.cameras[0].lines[0].a == (0.5, 0.9)
    assert any("restringida" in w for w in cfg.warnings)  # las reglas aún piden la zona borrada


def test_save_geometry_empty_and_back(tmp_path):
    path = tmp_path / "config.yaml"
    shutil.copyfile(ROOT / "config.yaml", path)
    save_camera_geometry(path, "cam1", [], [])
    text = path.read_text(encoding="utf-8")
    assert "    zonas: []" in text and "    lineas: []" in text and "# Cámara IP por RTSP" in text
    save_camera_geometry(path, "cam1", ZONES, LINES)
    cfg = load_config(path)
    assert [z.name for z in cfg.cameras[0].zones] == ["caja"] and [ln.name for ln in cfg.cameras[0].lines] == ["puerta"]


def test_save_geometry_inserts_missing_keys_and_keeps_crlf(tmp_path):
    path = tmp_path / "config.yaml"
    text = (
        "camaras:\n"
        "  - id: patio\n"
        "    fuente: 0   # webcam\n"
        "    fps: 8\n"
        "\n"
        "  # - id: otra (ejemplo comentado)\n"
        "modalidades:\n"
        "  definiciones:\n"
        "    m:\n"
        "      reglas:\n"
        "        - tipo: sabotaje\n"
    )
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    save_camera_geometry(path, "patio", ZONES, LINES)
    raw = path.read_bytes().decode("utf-8")
    assert "\r\n" in raw and "\n" not in raw.replace("\r\n", "")
    assert "    fuente: 0   # webcam\r\n    zonas:\r\n      - nombre: caja" in raw
    assert "  # - id: otra (ejemplo comentado)" in raw
    cfg = load_config(path)
    assert cfg.cameras[0].fps == 8 and cfg.cameras[0].zones[0].name == "caja"
    assert cfg.cameras[0].lines[0].name == "puerta"


def test_save_geometry_quotes_reserved_names(tmp_path):
    path = tmp_path / "config.yaml"
    shutil.copyfile(ROOT / "config.yaml", path)
    save_camera_geometry(path, "cam1", [Zone("true", ZONES[0].points), Zone("2", ZONES[0].points)], [])
    assert [z.name for z in load_config(path).cameras[0].zones] == ["true", "2"]
