"""Panel web y API REST (FastAPI)."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import mimetypes
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vigia import __version__
from vigia.alerts.model import Alert
from vigia.config import ConfigError
from vigia.geometry import Line, Point, Zone
from vigia.pipeline import CameraPipeline
from vigia.system import VigiaSystem

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Windows a veces registra tipos MIME incorrectos para estas extensiones.
for _type, _ext in (("text/javascript", ".js"), ("text/css", ".css"), ("video/webm", ".webm"), ("video/mp4", ".mp4")):
    mimetypes.add_type(_type, _ext)


class RevalidatedStaticFiles(StaticFiles):
    """Archivos del panel: el navegador revalida siempre (tras actualizar Vigía no queda una versión vieja)."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class ModeIn(BaseModel):
    modalidad: str


class ShapeIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    puntos: list[list[float]]


class GeometryIn(BaseModel):
    zonas: list[ShapeIn] = []
    lineas: list[ShapeIn] = []


def _points(raw: list[list[float]], min_points: int, exact: int | None = None) -> tuple[Point, ...]:
    points = []
    for p in raw:
        if len(p) != 2:
            raise ValueError("cada punto debe ser [x, y]")
        points.append((round(min(1.0, max(0.0, float(p[0]))), 4), round(min(1.0, max(0.0, float(p[1]))), 4)))
    if exact is not None and len(points) != exact:
        raise ValueError(f"cada línea necesita exactamente {exact} puntos")
    if len(points) < min_points:
        raise ValueError(f"cada zona necesita al menos {min_points} puntos")
    return tuple(points)


def _sse(event: dict) -> str:
    return f"event: {event.get('tipo', 'mensaje')}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def create_app(system: VigiaSystem) -> FastAPI:
    app = FastAPI(title="Vigía", version=__version__, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    web = system.config.web

    if web.user:
        expected = (web.user.encode("utf-8"), web.password.encode("utf-8"))

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            header = request.headers.get("authorization", "")
            if header[:6].lower() == "basic ":
                try:
                    user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                except (binascii.Error, UnicodeDecodeError):
                    user, password = "", ""
                ok_user = secrets.compare_digest(user.encode("utf-8"), expected[0])
                ok_pass = secrets.compare_digest(password.encode("utf-8"), expected[1])
                if ok_user and ok_pass:
                    return await call_next(request)
            return Response(
                "Autenticación requerida",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Vigia", charset="UTF-8"'},
            )

    def pipeline(cam_id: str) -> CameraPipeline:
        found = system.pipelines.get(cam_id)
        if found is None:
            raise HTTPException(404, f"No existe la cámara '{cam_id}'")
        return found

    rec = system.config.recording
    clip_window = rec.max_seconds + rec.post_seconds + 60  # después de esto ya debería existir

    def alert_dict(alert: Alert) -> dict:
        data = alert.to_dict()
        ready = bool(alert.clip) and (system.data_dir / alert.clip).is_file()
        data["clip_listo"] = ready
        if not alert.clip:
            data["clip_estado"] = None
        elif ready:
            data["clip_estado"] = "listo"
        else:
            data["clip_estado"] = "generando" if time.time() - alert.ts < clip_window else "no_disponible"
        return data

    # --- páginas ----------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    # --- estado y modalidades ---------------------------------------------------------------
    @app.get("/api/estado")
    def estado() -> dict:
        return system.status()

    @app.get("/api/modalidades")
    def modalidades() -> dict:
        return system.modes.info()

    @app.post("/api/modalidad")
    def cambiar_modalidad(body: ModeIn) -> dict:
        try:
            return system.set_mode(body.modalidad)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    # --- cámaras ------------------------------------------------------------------------------
    @app.get("/api/camaras")
    def camaras() -> list[dict]:
        return [p.status() for p in system.pipelines.values()]

    @app.get("/api/camaras/{cam_id}/cuadro")
    async def cuadro(cam_id: str, despues: int = 0) -> Response:
        """Último cuadro anotado. Espera hasta 2 s a que haya uno más nuevo que `despues`."""
        cam = pipeline(cam_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        while True:
            seq, jpeg = cam.hub.latest()
            if jpeg is not None and seq > despues:
                break
            if loop.time() >= deadline or system.stopping.is_set():
                if jpeg is None:
                    return Response(status_code=204)
                break
            await asyncio.sleep(0.03)
        return Response(jpeg, media_type="image/jpeg", headers={"X-Seq": str(seq), "Cache-Control": "no-store"})

    @app.get("/api/camaras/{cam_id}/captura")
    def captura(cam_id: str) -> Response:
        """Cuadro original sin anotaciones (lo usa el editor de zonas)."""
        jpeg = pipeline(cam_id).hub.raw_jpeg()
        if jpeg is None:
            raise HTTPException(503, "La cámara todavía no tiene imagen")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/camaras/{cam_id}/video")
    async def video(cam_id: str, request: Request) -> StreamingResponse:
        """Video MJPEG, útil para verlo en VLC o incrustarlo en otra página."""
        cam = pipeline(cam_id)

        async def frames():
            last = 0
            while not system.stopping.is_set():
                if await request.is_disconnected():
                    break
                seq, jpeg = cam.hub.latest()
                if jpeg is not None and seq != last:
                    last = seq
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
                await asyncio.sleep(0.04)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame", headers={"Cache-Control": "no-store"})

    @app.put("/api/camaras/{cam_id}/geometria")
    def geometria(cam_id: str, body: GeometryIn) -> dict:
        pipeline(cam_id)
        try:
            zones = [Zone(z.nombre, _points(z.puntos, 3)) for z in body.zonas]
            lines = [Line(ln.nombre, *_points(ln.puntos, 2, exact=2)) for ln in body.lineas]
            return system.update_geometry(cam_id, zones, lines)
        except (ValueError, ConfigError) as exc:
            raise HTTPException(400, str(exc)) from None

    # --- alertas ------------------------------------------------------------------------------
    @app.get("/api/alertas")
    def alertas(
        limite: int = Query(50, ge=1, le=500),
        antes_de: int | None = None,
        camara: str | None = None,
        severidad_min: int | None = Query(None, ge=1, le=4),
        pendientes: bool = False,
    ) -> dict:
        items = system.store.list(limite, antes_de, camara, severidad_min, pendientes)
        return {"alertas": [alert_dict(a) for a in items], "resumen": system.store.summary(time.time() - 86400)}

    @app.get("/api/alertas/{alert_id}")
    def alerta(alert_id: int) -> dict:
        found = system.store.get(alert_id)
        if found is None:
            raise HTTPException(404, "No existe la alerta")
        return alert_dict(found)

    @app.post("/api/alertas/{alert_id}/reconocer")
    def reconocer(alert_id: int) -> dict:
        if system.store.get(alert_id) is None:
            raise HTTPException(404, "No existe la alerta")
        system.alerts.acknowledge(alert_id)
        return alert_dict(system.store.get(alert_id))

    @app.post("/api/alertas/reconocer-todas")
    def reconocer_todas() -> dict:
        return {"reconocidas": system.alerts.acknowledge_all()}

    @app.post("/api/prueba")
    def prueba() -> dict:
        return alert_dict(system.test_alert())

    # --- eventos en vivo (Server-Sent Events) -------------------------------------------
    @app.get("/api/eventos")
    async def eventos(request: Request) -> StreamingResponse:
        queue = system.broadcaster.subscribe()

        async def stream():
            try:
                yield "retry: 3000\n\n"
                yield _sse({"tipo": "estado", "estado": system.status()})
                while not system.stopping.is_set():
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=10)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield _sse(event)
            finally:
                system.broadcaster.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/media/capturas", StaticFiles(directory=system.data_dir / "capturas"), name="capturas")
    app.mount("/media/clips", StaticFiles(directory=system.data_dir / "clips"), name="clips")
    return app
