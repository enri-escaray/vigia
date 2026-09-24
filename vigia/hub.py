"""Canales de distribución: último cuadro de cada cámara y eventos en vivo."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import cv2
import numpy as np

log = logging.getLogger(__name__)


class FrameHub:
    """Guarda el último cuadro anotado (JPEG) y el último cuadro original de una cámara."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._raw: np.ndarray | None = None
        self._seq = 0

    def publish(self, jpeg: bytes, raw: np.ndarray | None = None) -> None:
        with self._lock:
            self._jpeg = jpeg
            if raw is not None:
                self._raw = raw
            self._seq += 1

    def latest(self) -> tuple[int, bytes | None]:
        with self._lock:
            return self._seq, self._jpeg

    def raw_jpeg(self, quality: int = 90) -> bytes | None:
        with self._lock:
            raw = self._raw
        if raw is None:
            return None
        ok, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None


class EventBroadcaster:
    """Reparte eventos (dict) desde los hilos de las cámaras a los clientes web
    conectados por Server-Sent Events, que viven en el bucle asyncio."""

    def __init__(self, queue_size: int = 200) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._queue_size = queue_size

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [(lp, q) for lp, q in self._subscribers if q is not queue]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(_offer, queue, event)
            except RuntimeError:  # el bucle ya se cerró
                self.unsubscribe(queue)


def _offer(queue: asyncio.Queue, event: dict[str, Any]) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass  # cliente lento: se descarta el evento
