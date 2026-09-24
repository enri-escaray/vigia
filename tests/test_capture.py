"""Captura: reconexión cuando una transmisión deja de entregar imagen."""

import time

import numpy as np

from vigia.capture import VideoSource


class _FreezingCapture:
    """Entrega unos cuadros y después cada lectura espera hasta su tiempo límite
    y falla, como una transmisión HLS que se congeló (caso real de Caltrans)."""

    def __init__(self, frames: int, read_block: float) -> None:
        self.frames = frames
        self.read_block = read_block

    def read(self):
        if self.frames > 0:
            self.frames -= 1
            return True, np.zeros((48, 64, 3), np.uint8)
        time.sleep(self.read_block)
        return False, None

    def get(self, prop):
        return 15.0

    def set(self, prop, value):
        return True

    def release(self):
        pass


def test_frozen_stream_reconnects_by_time_without_image(monkeypatch):
    """Antes se reconectaba tras 10 lecturas fallidas seguidas: con lecturas que
    tardan 8 s en fallar, eso eran 80 s sin imagen."""
    opens: list[float] = []
    src = VideoSource("https://ejemplo.org/camara.m3u8", name="prueba", reconnect_delay=0.05)
    src.stall_after = 0.5  # en la realidad, 9 s

    def fake_open():
        opens.append(time.monotonic())
        return _FreezingCapture(frames=3, read_block=0.2)  # cada lectura fallida tarda 0,2 s

    monkeypatch.setattr(src, "_open", fake_open)
    src.start()
    try:
        deadline = time.monotonic() + 5
        while len(opens) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        src.stop()
    assert len(opens) >= 2
    assert opens[1] - opens[0] < 1.6  # con el criterio anterior: 10 × 0,25 s = 2,5 s
