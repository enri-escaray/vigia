"""Captura el panel de Vigía en vivo (instancia de linkedin/config.linkedin.yaml).

Uso: python linkedin/herramientas/capturar_panel.py [SEGUNDOS_DE_CUADROS]

Guarda en linkedin/capturas/:
  panel_4_camaras.png        el panel completo (2x)
  panel_alerta.png           el detalle de una alerta con su evidencia
  panel_movil.png            el panel en un celular
  cuadros/<camara>_NN.jpg    cuadros anotados cada 4 s, para elegir los mejores
"""

import asyncio
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser, Page  # noqa: E402

BASE = "http://127.0.0.1:8091"
OUT = Path(__file__).resolve().parents[1] / "capturas"
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 60


def grab_frames() -> None:
    import json

    cams = [c["id"] for c in json.loads(urllib.request.urlopen(f"{BASE}/api/camaras").read())]
    t0 = time.time()
    n = 0
    while time.time() - t0 < SECONDS:
        for cam in cams:
            for kind in ("cuadro", "captura"):
                data = urllib.request.urlopen(f"{BASE}/api/camaras/{cam}/{kind}", timeout=10).read()
                path = OUT / "cuadros" / f"{cam}_{n:02d}_{kind}.jpg"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        n += 1
        time.sleep(4)
    print(f"{n} tandas de cuadros guardadas")


async def main() -> None:
    with Browser() as browser:
        page = await Page.open(browser.page_ws())

        # 1) Panel completo, 2 columnas de cámaras + alertas.
        await page.viewport(1440, 900)
        await page.goto(BASE + "/", settle=8)
        height = await page.eval(
            "Math.ceil(document.querySelector('.layout').getBoundingClientRect().bottom)"
        )
        await page.viewport(1440, int(height))
        await asyncio.sleep(4)
        await page.shot(OUT / "panel_4_camaras.png")
        print("panel_4_camaras.png", height)

        # 2) Detalle de la primera alerta (captura + datos).
        clicked = await page.eval(
            "(() => { const a = document.querySelector('.alert'); if (a) { a.click(); return true } return false })()"
        )
        if clicked:
            await asyncio.sleep(3)
            await page.shot(OUT / "panel_alerta.png")
            print("panel_alerta.png")
            await page.eval("document.querySelector('#dlg-alert').close()")

        # 3) Versión celular.
        await page.viewport(390, 844, scale=3)
        await page.goto(BASE + "/", settle=8)
        await page.shot(OUT / "panel_movil.png")
        print("panel_movil.png")
        await page.close()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(main())
    grab_frames()
