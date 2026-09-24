"""Línea de comandos: ``python -m vigia <comando>``."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Antes de importar OpenCV: menos mensajes internos en la consola (los errores de
# decodificación de FFmpeg al unirse a una transmisión en curso son normales).
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "8")  # 8 = solo errores fatales
# Antes de importar PyTorch: que los hilos de cálculo duerman al terminar cada
# inferencia en vez de quedarse girando (con varias cámaras eso consume mucha CPU).
os.environ.setdefault("KMP_BLOCKTIME", "1")
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")

from vigia import __version__  # noqa: E402
from vigia.config import Config, ConfigError, load_config  # noqa: E402

log = logging.getLogger("vigia")


def _setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(file_handler)
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "ultralytics", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _panel_url(cfg: Config) -> str:
    host = cfg.web.host
    if host in ("0.0.0.0", "::", "127.0.0.1", "::1"):
        host = "localhost"
    return f"http://{host}:{cfg.web.port}"


def _run_system(cfg: Config, open_browser: bool = False, mode: str | None = None) -> int:
    from vigia.system import VigiaSystem

    system = VigiaSystem(cfg)
    if mode:
        try:
            system.modes.select(mode)
        except ValueError as exc:
            system.stop()
            raise ConfigError(str(exc)) from None
    system.start()
    try:
        if cfg.web.enabled:
            import uvicorn

            from vigia.web.server import create_app

            url = _panel_url(cfg)
            if cfg.web.host not in ("127.0.0.1", "localhost", "::1") and not cfg.web.user:
                log.warning("El panel queda accesible desde la red SIN contraseña: defina web.usuario y web.clave")
            log.info("Panel web: %s  (Ctrl+C para detener)", url)
            if open_browser:
                threading.Timer(1.5, webbrowser.open, args=(url,)).start()
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(system),
                    host=cfg.web.host,
                    port=cfg.web.port,
                    log_level="warning",
                    access_log=False,
                    timeout_graceful_shutdown=3,
                )
            )
            server.run()
        else:
            log.info("Panel web desactivado. Ctrl+C para detener.")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        system.stop()
    return 0


# --------------------------------------------------------------------------- comandos
def cmd_iniciar(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if not path.is_file():
        print(
            f"No existe {path}. Use el config.yaml de ejemplo del proyecto, indique otro con -c, "
            "o pruebe la demostración: python -m vigia demo",
            file=sys.stderr,
        )
        return 2
    cfg = load_config(path)
    if args.host:
        cfg.web.host = args.host
    if args.puerto:
        cfg.web.port = args.puerto
    _setup_logging(cfg.system.log_level, cfg.data_dir / "vigia.log")
    return _run_system(cfg, open_browser=args.abrir, mode=args.modalidad)


def cmd_demo(args: argparse.Namespace) -> int:
    from vigia.demo import prepare_demo

    _setup_logging("INFO")
    folder = Path(args.carpeta)
    log.info("Preparando la demostración en %s ...", folder)
    cfg = load_config(prepare_demo(folder, port=args.puerto or 8080))
    if args.puerto:
        cfg.web.port = args.puerto
    _setup_logging(cfg.system.log_level, cfg.data_dir / "vigia.log")
    return _run_system(cfg, open_browser=args.abrir)


def cmd_camaras(args: argparse.Namespace) -> int:
    from vigia.capture import probe_webcams

    print("Buscando webcams...")
    found = probe_webcams(args.max)
    if not found:
        print("No se encontraron webcams. Para cámaras IP use su URL RTSP en 'fuente'.")
        return 1
    for cam in found:
        print(f"  fuente: {cam['indice']}   ({cam['resolucion']})")
    return 0


def cmd_validar(args: argparse.Namespace) -> int:
    from vigia.alerts.notifiers import build_notifiers
    from vigia.rules import RuleEngine

    cfg = load_config(args.config)
    build_notifiers(cfg.notifications)  # valida las opciones de cada canal
    print(f"✔ {Path(args.config).name} es válido — {cfg.system.name}")
    print(f"\nDetector: {cfg.detector.kind}" + (f" (modelo {cfg.detector.model})" if cfg.detector.kind in ("auto", "yolo") else ""))
    print(f"Datos: {cfg.data_dir}")
    print("\nCámaras:")
    for cam in cfg.cameras:
        state = "" if cam.enabled else " [desactivada]"
        fixed = f" [modalidad fija: {cam.mode}]" if cam.mode else ""
        print(f"  • {cam.id} «{cam.name}»{state}{fixed}: {cam.safe_source}")
        print(f"      zonas: {', '.join(z.name for z in cam.zones) or '—'} | líneas: {', '.join(ln.name for ln in cam.lines) or '—'}")
    print("\nModalidades:")
    for mode in cfg.modes.definitions.values():
        print(f"  • {mode.name} «{mode.label}»: {mode.description}")
        for cam in cfg.cameras:
            if cam.mode:
                if cam.mode != mode.name:
                    continue  # la cámara usa siempre otra modalidad
            elif mode.name in cfg.modes.camera_only:
                continue  # modalidad reservada para cámaras que la fijan
            engine = RuleEngine(mode.name, mode.rules, cam.id, {z.name: z for z in cam.zones}, {ln.name: ln for ln in cam.lines})
            rules = ", ".join(r.id.split(".", 1)[1] for r in engine.rules) or "ninguna regla aplica"
            print(f"      {cam.id}: {rules}")
    if cfg.modes.schedule:
        print("\nHorario:")
        for entry in cfg.modes.schedule:
            d = entry.to_dict()
            print(f"  • {d['desde']}-{d['hasta']} ({', '.join(d['dias'])}): {entry.mode}")
    print(f"  (fuera de horario: {cfg.modes.default}; al iniciar: {cfg.modes.initial})")
    channels = [name for name, opts in cfg.notifications.channels.items() if opts.get("activo")]
    print(f"\nNotificaciones: {', '.join(channels) or 'solo panel web'}")
    if cfg.warnings:
        print("\nAdvertencias:")
        for warning in cfg.warnings:
            print(f"  ⚠ {warning}")
    return 0


def cmd_probar_alerta(args: argparse.Namespace) -> int:
    import cv2

    from vigia.alerts.model import Alert
    from vigia.alerts.notifiers import build_notifiers, send_all
    from vigia.annotate import placeholder
    from vigia.types import Severity

    cfg = load_config(args.config)
    notifiers = build_notifiers(cfg.notifications)
    if not notifiers:
        print("No hay canales de notificación activos en 'notificaciones'.")
        return 1
    snapshot = cfg.data_dir / "capturas" / "prueba.jpg"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", placeholder(640, 360, "PRUEBA DE VIGIA", "Si ve esta imagen, las fotos llegan bien"))
    if ok:
        snapshot.write_bytes(buf.tobytes())
    alert = Alert(
        ts=time.time(), camera_id="prueba", camera_name="Prueba", mode="prueba", rule_id="prueba",
        rule_type="prueba", title="Alerta de prueba", message="Si recibió este mensaje, las notificaciones funcionan.",
        severity=Severity.CRITICA,
    )  # fmt: skip
    for name, result in send_all(notifiers, alert, snapshot).items():
        print(f"  {name}: {result}")
    return 0


def cmd_publicas(args: argparse.Namespace) -> int:
    from vigia.publicas import DISTRICTS, config_yaml, find_live

    path = Path(args.crear or "config.publico.yaml")
    if args.iniciar and path.is_file() and not args.forzar:
        print(f"Se usará {path} tal como está (use --forzar para buscar cámaras nuevas).")
    else:
        region = DISTRICTS.get(args.distrito, "?")
        filtro = f", que coincidan con «{args.buscar}»" if args.buscar else ""
        print(f"Buscando cámaras de Caltrans en vivo, distrito {args.distrito} ({region}){filtro}...")
        try:
            cameras = find_live(args.distrito, args.cantidad, args.buscar)
        except Exception as exc:  # noqa: BLE001 - red caída, API cambiada, etc.
            print(f"No se pudo consultar Caltrans: {exc}", file=sys.stderr)
            return 1
        if not cameras:
            print("No hay transmisiones activas con ese criterio. Pruebe otro --distrito o quite --buscar.")
            return 1
        for cam in cameras:
            print(f"  • {cam.name} — {cam.place}\n    {cam.stream}")
        if not (args.crear or args.iniciar):
            print("\nPara crear una configuración con estas cámaras agregue: --crear config.publico.yaml")
            return 0
        if path.is_file() and not args.forzar and not args.iniciar:
            print(f"\n{path} ya existe; use --forzar para reemplazarlo.")
            return 1
        path.write_text(config_yaml(cameras, args.puerto or 8080), encoding="utf-8")
        print(f"\n✔ Configuración creada: {path}")
        if not args.iniciar:
            print(f"  Iníciela con: python -m vigia iniciar -c {path} --abrir")
            return 0
    cfg = load_config(path)
    if args.puerto:
        cfg.web.port = args.puerto
    _setup_logging(cfg.system.log_level, cfg.data_dir / "vigia.log")
    return _run_system(cfg, open_browser=args.abrir)


def cmd_modelo(args: argparse.Namespace) -> int:
    import numpy as np

    from vigia.vision.detectors import YoloDetector

    _setup_logging("INFO")
    cfg = load_config(args.config)
    d = cfg.detector
    print(f"Cargando {d.model} (se descarga la primera vez)...")
    detector = YoloDetector(d.model, d.confidence, d.image_size, d.device, d.categories, d.confidence_by_category, cfg.base_dir, d.threads)
    detector.detect(np.zeros((360, 640, 3), np.uint8))
    print(f"✔ Modelo listo: {detector.model_path}")
    print(f"  Categorías disponibles: {', '.join(sorted(detector.categories))}")
    return 0


# --------------------------------------------------------------------------- argumentos
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m vigia", description="Vigía: videovigilancia inteligente con alertas por modalidades.")
    parser.add_argument("--version", action="version", version=f"Vigía {__version__}")
    sub = parser.add_subparsers(dest="comando", metavar="comando")

    p = sub.add_parser("iniciar", help="inicia la vigilancia y el panel web (comando por defecto)")
    p.add_argument("-c", "--config", default="config.yaml", help="archivo de configuración (config.yaml)")
    p.add_argument("--abrir", action="store_true", help="abre el panel en el navegador")
    p.add_argument("--host", help="interfaz de red del panel (p. ej. 0.0.0.0 para verlo desde otros equipos)")
    p.add_argument("--puerto", type=int, help="puerto del panel web")
    p.add_argument("--modalidad", help="fuerza una modalidad al iniciar (o 'auto')")
    p.set_defaults(func=cmd_iniciar)

    p = sub.add_parser("demo", help="demostración con un video sintético, sin cámaras")
    p.add_argument("--carpeta", default="datos/demo", help="dónde crear el video y la configuración de la demo")
    p.add_argument("--abrir", action="store_true", help="abre el panel en el navegador")
    p.add_argument("--puerto", type=int, help="puerto del panel web")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("publicas", help="usa cámaras públicas de autopista en vivo (Caltrans) para probar el sistema")
    p.add_argument("--distrito", type=int, default=4, help="distrito de Caltrans: 4 = San Francisco, 7 = Los Ángeles, 11 = San Diego, 12 = Orange...")
    p.add_argument("--buscar", help="texto a buscar en el nombre o lugar de la cámara (p. ej. 'San Francisco')")
    p.add_argument("--cantidad", type=int, default=4, help="cuántas cámaras en vivo usar")
    p.add_argument("--crear", metavar="ARCHIVO", help="escribe una configuración con esas cámaras (p. ej. config.publico.yaml)")
    p.add_argument("--iniciar", action="store_true", help="inicia Vigía con esa configuración")
    p.add_argument("--forzar", action="store_true", help="reemplaza la configuración si ya existe")
    p.add_argument("--abrir", action="store_true", help="abre el panel en el navegador")
    p.add_argument("--puerto", type=int, help="puerto del panel web")
    p.set_defaults(func=cmd_publicas)

    p = sub.add_parser("camaras", help="busca webcams conectadas a este equipo")
    p.add_argument("--max", type=int, default=5, help="índice máximo a probar")
    p.set_defaults(func=cmd_camaras)

    p = sub.add_parser("validar", help="revisa la configuración y muestra qué reglas aplican en cada cámara")
    p.add_argument("-c", "--config", default="config.yaml")
    p.set_defaults(func=cmd_validar)

    p = sub.add_parser("probar-alerta", help="envía una alerta de prueba por los canales configurados")
    p.add_argument("-c", "--config", default="config.yaml")
    p.set_defaults(func=cmd_probar_alerta)

    p = sub.add_parser("modelo", help="descarga y verifica el modelo YOLO")
    p.add_argument("-c", "--config", default="config.yaml")
    p.set_defaults(func=cmd_modelo)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # consolas sin UTF-8 no deben romper el programa
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv = ["iniciar", *argv]
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"\nError de configuración: {exc}\n", file=sys.stderr)
        return 2
