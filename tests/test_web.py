import base64
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from vigia.alerts.model import Alert
from vigia.config import load_config
from vigia.demo import demo_config_yaml
from vigia.system import VigiaSystem
from vigia.types import Severity
from vigia.web.server import create_app


@pytest.fixture
def system(tmp_path):
    path = tmp_path / "config_demo.yaml"
    path.write_text(demo_config_yaml("escena.mp4", 8080), encoding="utf-8")
    sys_ = VigiaSystem(load_config(path))  # no se inicia: no hay cámaras reales
    yield sys_
    sys_.notifiers.close()
    sys_.store.close()


@pytest.fixture
def client(system):
    return TestClient(create_app(system))


def add_alert(system, severity=Severity.ALTA):
    alert = Alert(
        ts=time.time(), camera_id="demo", camera_name="Pasillo", mode="demo", rule_id="demo.intrusion",
        rule_type="intrusion", title="Intrusión", message="Movimiento #1 ingresó", severity=severity,
    )  # fmt: skip
    return system.alerts.register(alert)


def test_index_and_static(client):
    res = client.get("/")
    assert res.status_code == 200 and "Vigía" in res.text
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_status_and_modes(client):
    status = client.get("/api/estado").json()
    assert status["modalidad"] == "demo"
    assert status["camaras"][0]["id"] == "demo"
    modes = client.get("/api/modalidades").json()
    assert [m["nombre"] for m in modes["modalidades"]] == ["demo", "desarmado"]
    res = client.post("/api/modalidad", json={"modalidad": "desarmado"})
    assert res.status_code == 200 and res.json()["actual"] == "desarmado"
    assert client.post("/api/modalidad", json={"modalidad": "nada"}).status_code == 400
    assert client.post("/api/modalidad", json={"modalidad": "auto"}).json()["automatica"] is True


def test_alerts_listing_and_ack(client, system):
    first = add_alert(system, Severity.MEDIA)
    second = add_alert(system, Severity.CRITICA)
    data = client.get("/api/alertas").json()
    assert [a["id"] for a in data["alertas"]] == [second.id, first.id]
    assert data["resumen"]["pendientes"] == 2
    assert [a["id"] for a in client.get("/api/alertas?severidad_min=4").json()["alertas"]] == [second.id]
    res = client.post(f"/api/alertas/{first.id}/reconocer")
    assert res.status_code == 200 and res.json()["reconocida"] is True
    assert [a["id"] for a in client.get("/api/alertas?pendientes=true").json()["alertas"]] == [second.id]
    assert client.post("/api/alertas/reconocer-todas").json()["reconocidas"] == 1
    assert client.get("/api/alertas/9999").status_code == 404


def test_clip_state(client, system):
    fresh = add_alert(system)
    fresh.clip = "clips/2026-09-22/inexistente.mp4"
    old = Alert(
        ts=time.time() - 3600, camera_id="demo", camera_name="Pasillo", mode="demo", rule_id="demo.intrusion",
        rule_type="intrusion", title="Intrusión", message="vieja", severity=Severity.ALTA, clip="clips/x/borrado.mp4",
    )  # fmt: skip
    system.store.add(old)
    system.store._conn.execute("UPDATE alertas SET clip = ? WHERE id = ?", (fresh.clip, fresh.id))
    states = {a["id"]: a["clip_estado"] for a in client.get("/api/alertas").json()["alertas"]}
    assert states[fresh.id] == "generando" and states[old.id] == "no_disponible"


def test_frame_endpoint(client, system):
    ok, buf = cv2.imencode(".jpg", np.zeros((90, 160, 3), np.uint8))
    system.pipelines["demo"].hub.publish(buf.tobytes(), np.zeros((90, 160, 3), np.uint8))
    res = client.get("/api/camaras/demo/cuadro?despues=0")
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert res.headers["x-seq"] == "1"
    assert client.get("/api/camaras/demo/captura").status_code == 200
    assert client.get("/api/camaras/otra/cuadro").status_code == 404


def test_geometry_update_persists(client, system, tmp_path):
    body = {
        "zonas": [{"nombre": "caja", "puntos": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]}],
        "lineas": [{"nombre": "puerta", "puntos": [[0.2, 0.9], [0.2, 0.1]]}],
    }
    res = client.put("/api/camaras/demo/geometria", json=body)
    assert res.status_code == 200, res.text
    assert [z["nombre"] for z in res.json()["zonas"]] == ["caja"]
    reloaded = load_config(tmp_path / "config_demo.yaml")
    assert reloaded.cameras[0].zones[0].name == "caja"
    bad = {"zonas": [{"nombre": "x", "puntos": [[0.1, 0.1], [0.2, 0.2]]}], "lineas": []}
    assert client.put("/api/camaras/demo/geometria", json=bad).status_code == 400
    dup = {"zonas": [body["zonas"][0], body["zonas"][0]], "lineas": []}
    assert client.put("/api/camaras/demo/geometria", json=dup).status_code == 400
    assert client.put("/api/camaras/demo/geometria", json={"zonas": [{"nombre": "con espacio", "puntos": [[0, 0], [1, 0], [1, 1]]}]}).status_code == 422


def test_basic_auth(system):
    system.config.web.user = "admin"
    system.config.web.password = "clave-segura"
    client = TestClient(create_app(system))
    assert client.get("/api/estado").status_code == 401
    token = base64.b64encode(b"admin:clave-segura").decode()
    assert client.get("/api/estado", headers={"Authorization": f"Basic {token}"}).status_code == 200
    wrong = base64.b64encode(b"admin:otra").decode()
    assert client.get("/", headers={"Authorization": f"Basic {wrong}"}).status_code == 401
