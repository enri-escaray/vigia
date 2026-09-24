"""Canales de notificación: sonido local, webhook (Slack/Discord/n8n...), Telegram y correo."""

from __future__ import annotations

import html
import logging
import queue
import smtplib
import sys
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

from vigia.alerts.model import Alert
from vigia.config import ConfigError, NotificationsConfig
from vigia.types import Severity

log = logging.getLogger(__name__)

_SEVERITY_ICONS = {Severity.BAJA: "🔵", Severity.MEDIA: "🟡", Severity.ALTA: "🟠", Severity.CRITICA: "🔴"}


class Notifier:
    name = "base"
    OPTIONS: set[str] = set()

    def __init__(self, options: dict[str, Any]) -> None:
        unknown = set(options) - self.OPTIONS - {"activo", "severidad_minima"}
        if unknown:
            raise ConfigError(
                f"notificaciones.{self.name}: opción(es) desconocida(s): {', '.join(sorted(unknown))}. "
                f"Válidas: {', '.join(sorted(self.OPTIONS | {'activo', 'severidad_minima'}))}"
            )
        self.min_severity: Severity = options.get("severidad_minima", Severity.MEDIA)

    def accepts(self, alert: Alert) -> bool:
        return alert.severity >= self.min_severity

    def send(self, alert: Alert, snapshot: Path | None) -> None:
        raise NotImplementedError


class SoundNotifier(Notifier):
    """Pitidos en el equipo donde corre Vigía (patrón según la severidad)."""

    name = "sonido"
    PATTERNS = {
        Severity.BAJA: [(880, 150)],
        Severity.MEDIA: [(880, 250), (0, 120), (880, 250)],
        Severity.ALTA: [(1200, 300), (0, 100)] * 3,
        Severity.CRITICA: [(1500, 250), (900, 250)] * 4,
    }

    def send(self, alert: Alert, snapshot: Path | None) -> None:
        pattern = self.PATTERNS[alert.severity]
        if sys.platform == "win32":
            import winsound

            for freq, ms in pattern:
                if freq:
                    winsound.Beep(freq, ms)
                else:
                    time.sleep(ms / 1000)
        else:
            sys.stdout.write("\a" * sum(1 for f, _ in pattern if f))
            sys.stdout.flush()


class WebhookNotifier(Notifier):
    """POST con JSON. Incluye 'text' y 'content' para funcionar directo con Slack y Discord."""

    name = "webhook"
    OPTIONS = {"url", "cabeceras", "url_panel"}

    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__(options)
        self.url = str(options.get("url") or "").strip()
        if not self.url:
            raise ConfigError("notificaciones.webhook: falta 'url'")
        self.headers = {str(k): str(v) for k, v in (options.get("cabeceras") or {}).items()}
        self.panel_url = str(options.get("url_panel") or "").rstrip("/")

    def send(self, alert: Alert, snapshot: Path | None) -> None:
        payload = alert.to_dict()
        text = alert.text()
        payload["text"] = text
        payload["content"] = text
        if self.panel_url:
            for key in ("captura", "clip"):
                if payload.get(key):
                    payload[key] = self.panel_url + payload[key]
        try:
            response = requests.post(self.url, json=payload, headers=self.headers, timeout=10)
        except requests.RequestException as exc:
            raise RuntimeError(f"error de conexión ({type(exc).__name__})") from None
        if not response.ok:
            raise RuntimeError(f"el webhook respondió {response.status_code}: {response.text[:200]}")


class TelegramNotifier(Notifier):
    name = "telegram"
    OPTIONS = {"token", "chat_id", "enviar_foto"}

    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__(options)
        self.token = str(options.get("token") or "").strip()
        self.chat_id = str(options.get("chat_id") or "").strip()
        if not self.token or not self.chat_id:
            raise ConfigError("notificaciones.telegram: faltan 'token' y/o 'chat_id'")
        self.send_photo = bool(options.get("enviar_foto", True))

    def send(self, alert: Alert, snapshot: Path | None) -> None:
        text = (
            f"{_SEVERITY_ICONS[alert.severity]} <b>{html.escape(alert.title)}</b>\n"
            f"📷 {html.escape(alert.camera_name)} · {alert.when:%d/%m/%Y %H:%M:%S}\n"
            f"{html.escape(alert.message)}\n"
            f"Modalidad: {html.escape(alert.mode)} · Severidad: {alert.severity.display}"
        )
        base = f"https://api.telegram.org/bot{self.token}"
        try:
            if self.send_photo and snapshot is not None and snapshot.is_file():
                with snapshot.open("rb") as fh:
                    response = requests.post(
                        f"{base}/sendPhoto",
                        data={"chat_id": self.chat_id, "caption": text[:1024], "parse_mode": "HTML"},
                        files={"photo": (snapshot.name, fh, "image/jpeg")},
                        timeout=20,
                    )
            else:
                response = requests.post(
                    f"{base}/sendMessage",
                    data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=15,
                )
        except requests.RequestException as exc:
            # No se incluye la URL en el error: contiene el token del bot.
            raise RuntimeError(f"error de conexión con Telegram ({type(exc).__name__})") from None
        if not response.ok:
            raise RuntimeError(f"Telegram respondió {response.status_code}: {response.text[:200]}")


class EmailNotifier(Notifier):
    name = "correo"
    OPTIONS = {"servidor", "puerto", "usuario", "clave", "remitente", "destinatarios", "tls"}

    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__(options)
        self.host = str(options.get("servidor") or "").strip()
        self.port = int(options.get("puerto") or 587)
        self.user = str(options.get("usuario") or "")
        self.password = str(options.get("clave") or "")
        self.sender = str(options.get("remitente") or self.user)
        recipients = options.get("destinatarios") or []
        if isinstance(recipients, str):
            recipients = [r.strip() for r in recipients.split(",")]
        self.recipients = [str(r) for r in recipients if str(r).strip()]
        self.tls = bool(options.get("tls", True))
        if not self.host or not self.sender or not self.recipients:
            raise ConfigError("notificaciones.correo: defina 'servidor', 'remitente' (o 'usuario') y 'destinatarios'")

    def send(self, alert: Alert, snapshot: Path | None) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[Vigía] {alert.severity.display.upper()}: {alert.title} — {alert.camera_name}"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(
            f"{alert.title}\n\n{alert.message}\n\n"
            f"Cámara: {alert.camera_name}\nFecha: {alert.when:%d/%m/%Y %H:%M:%S}\n"
            f"Modalidad: {alert.mode}\nSeveridad: {alert.severity.display}\nRegla: {alert.rule_id}\n"
        )
        if snapshot is not None and snapshot.is_file():
            msg.add_attachment(snapshot.read_bytes(), maintype="image", subtype="jpeg", filename=snapshot.name)
        if self.port == 465:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=20)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=20)
        with smtp:
            if self.tls and self.port != 465:
                smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


NOTIFIER_TYPES: dict[str, type[Notifier]] = {
    cls.name: cls for cls in (SoundNotifier, WebhookNotifier, TelegramNotifier, EmailNotifier)
}


def build_notifiers(cfg: NotificationsConfig) -> list[Notifier]:
    return [NOTIFIER_TYPES[name](opts) for name, opts in cfg.channels.items() if opts.get("activo")]


def send_all(notifiers: list[Notifier], alert: Alert, snapshot: Path | None) -> dict[str, str]:
    """Envío sincrónico por todos los canales (para probar la configuración)."""
    results: dict[str, str] = {}
    for notifier in notifiers:
        try:
            notifier.send(alert, snapshot)
            results[notifier.name] = "ok"
        except Exception as exc:  # noqa: BLE001
            results[notifier.name] = f"error: {exc}"
    return results


class NotifierHub:
    """Envía las alertas por todos los canales, cada uno en su propio hilo para
    que un canal lento (correo, red caída) no retrase a los demás."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers
        self._workers: list[tuple[Notifier, queue.Queue, threading.Thread]] = []
        for notifier in notifiers:
            q: queue.Queue = queue.Queue(maxsize=100)
            th = threading.Thread(target=self._run, args=(notifier, q), name=f"notif-{notifier.name}", daemon=True)
            th.start()
            self._workers.append((notifier, q, th))

    def dispatch(self, alert: Alert, snapshot: Path | None) -> None:
        for notifier, q, _ in self._workers:
            if not notifier.accepts(alert):
                continue
            try:
                q.put_nowait((alert, snapshot))
            except queue.Full:
                log.warning("Cola de %s llena: se descarta la notificación de la alerta %s", notifier.name, alert.id)

    def close(self) -> None:
        for _, q, _ in self._workers:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
        for _, _, th in self._workers:
            th.join(timeout=5)

    @staticmethod
    def _run(notifier: Notifier, q: queue.Queue) -> None:
        while True:
            item = q.get()
            if item is None:
                return
            alert, snapshot = item
            try:
                notifier.send(alert, snapshot)
            except Exception as exc:  # noqa: BLE001
                log.error("No se pudo notificar la alerta %s por %s: %s", alert.id, notifier.name, exc)
