"""Capturas en alta resolución con Chrome headless (protocolo DevTools).

Se usa para dos cosas:
  - capturar el panel de Vigía en vivo (http://127.0.0.1:8091), y
  - convertir las diapositivas HTML del carrusel en PNG.

Solo depende de `websockets` (ya viene con uvicorn[standard]).
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).is_file():
            return c
    found = shutil.which("chrome") or shutil.which("msedge")
    if not found:
        raise RuntimeError("No se encontró Chrome ni Edge")
    return found


class Browser:
    def __init__(self, port: int = 9337):
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.profile = tempfile.mkdtemp(prefix="vigia-cdp-")

    def __enter__(self) -> "Browser":
        self.proc = subprocess.Popen(
            [
                find_chrome(),
                "--headless=new",
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--hide-scrollbars",
                "--mute-audio",
                "--disable-extensions",
                "--force-color-profile=srgb",
                "--font-render-hinting=none",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        return self

    def __exit__(self, *exc) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    def page_ws(self) -> str:
        tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read())
        page = next(t for t in tabs if t["type"] == "page")
        return page["webSocketDebuggerUrl"]


class Page:
    """Conexión CDP a una pestaña."""

    def __init__(self, ws):
        self.ws = ws
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future] = {}
        self.reader = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, ws_url: str) -> "Page":
        ws = await websockets.connect(ws_url, max_size=512 * 1024 * 1024)
        page = cls(ws)
        await page.send("Page.enable")
        await page.send("Runtime.enable")
        return page

    async def _read(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            fut = self.pending.pop(msg.get("id"), None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(msg["error"]))
                else:
                    fut.set_result(msg.get("result", {}))

    async def send(self, method: str, **params):
        mid = next(self.ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        return await asyncio.wait_for(fut, 120)

    async def eval(self, expr: str, await_promise: bool = True):
        res = await self.send(
            "Runtime.evaluate", expression=expr, awaitPromise=await_promise, returnByValue=True
        )
        if "exceptionDetails" in res:
            raise RuntimeError(res["exceptionDetails"])
        return res.get("result", {}).get("value")

    async def viewport(self, width: int, height: int, scale: float = 2) -> None:
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            width=width, height=height, deviceScaleFactor=scale, mobile=False,
        )

    async def goto(self, url: str, settle: float = 1.0) -> None:
        await self.send("Page.navigate", url=url)
        for _ in range(200):
            if await self.eval("document.readyState") == "complete":
                break
            await asyncio.sleep(0.1)
        await self.eval("document.fonts ? document.fonts.ready.then(() => true) : true")
        await asyncio.sleep(settle)

    async def shot(self, path: Path, clip: dict | None = None, fmt: str = "png") -> None:
        params = {"format": fmt, "captureBeyondViewport": clip is not None}
        if fmt == "jpeg":
            params["quality"] = 92
        if clip:
            params["clip"] = {**clip, "scale": 1}
        res = await self.send("Page.captureScreenshot", **params)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(res["data"]))

    async def close(self) -> None:
        self.reader.cancel()
        await self.ws.close()
