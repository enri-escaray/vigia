"""Exporta linkedin/carrusel/carrusel.html a PNG (2160×2700) y a un PDF listo para LinkedIn.

  python linkedin/herramientas/exportar_carrusel.py

Salida:
  linkedin/carrusel/png/01.png ... 11.png
  linkedin/Vigia_Opus55_carrusel.pdf
"""

import asyncio
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser, Page  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "carrusel" / "carrusel.html"
PNG_DIR = ROOT / "carrusel" / "png"
PDF = ROOT / "Vigia_Opus55_carrusel.pdf"
W, H = 1080, 1350


async def render() -> list[Path]:
    with Browser(port=9338) as browser:
        page = await Page.open(browser.page_ws())
        await page.viewport(W, H, scale=2)
        await page.goto(HTML.as_uri(), settle=1.5)
        await page.eval("Promise.all([...document.images].map(i => i.decode().catch(() => null))).then(() => true)")
        count = await page.eval("document.querySelectorAll('.slide').length")
        # Diagnóstico: contenido que se sale del cuadro de cada diapositiva.
        overflow = await page.eval(
            """[...document.querySelectorAll('.slide')].map((s, i) => {
                const r = s.getBoundingClientRect();
                const bad = [...s.querySelectorAll('*')].filter(e => {
                    const b = e.getBoundingClientRect();
                    return b.width && (b.right > r.right + 0.5 || b.bottom > r.bottom + 0.5);
                }).length;
                const body = s.firstElementChild.children[1];
                return {slide: i + 1, fuera: bad, hueco: Math.round(body.clientHeight - body.scrollHeight)};
            })"""
        )
        for o in overflow:
            print(o)
        paths = []
        PNG_DIR.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            path = PNG_DIR / f"{i + 1:02d}.png"
            await page.shot(path, clip={"x": 0, "y": i * H, "width": W, "height": H})
            paths.append(path)
        await page.close()
    return paths


def make_pdf(pngs: list[Path]) -> None:
    pages = [Image.open(p).convert("RGB") for p in pngs]
    pages[0].save(PDF, "PDF", save_all=True, append_images=pages[1:], resolution=144.0, quality=90)
    print(f"{PDF.name}: {len(pages)} páginas, {PDF.stat().st_size // 1024} KB")


if __name__ == "__main__":
    pngs = asyncio.run(render())
    print(f"{len(pngs)} PNG en {PNG_DIR}")
    make_pdf(pngs)
