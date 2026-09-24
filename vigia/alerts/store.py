"""Historial de alertas en SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from vigia.alerts.model import Alert
from vigia.types import Severity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    camara_id TEXT NOT NULL,
    camara TEXT NOT NULL,
    modalidad TEXT NOT NULL,
    regla TEXT NOT NULL,
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    mensaje TEXT NOT NULL,
    severidad INTEGER NOT NULL,
    zona TEXT,
    objetos TEXT NOT NULL DEFAULT '[]',
    categorias TEXT NOT NULL DEFAULT '[]',
    captura TEXT,
    clip TEXT,
    reconocida INTEGER NOT NULL DEFAULT 0,
    reconocida_ts REAL,
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_alertas_ts ON alertas (ts);
CREATE INDEX IF NOT EXISTS idx_alertas_pendientes ON alertas (reconocida, severidad);
"""

_COLUMNS = (
    "id, ts, camara_id, camara, modalidad, regla, tipo, titulo, mensaje, severidad, zona, "
    "objetos, categorias, captura, clip, reconocida, reconocida_ts, extra"
)


class AlertStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add(self, alert: Alert) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO alertas (ts, camara_id, camara, modalidad, regla, tipo, titulo, mensaje, severidad, "
                "zona, objetos, categorias, captura, clip, reconocida, reconocida_ts, extra) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alert.ts, alert.camera_id, alert.camera_name, alert.mode, alert.rule_id, alert.rule_type,
                    alert.title, alert.message, int(alert.severity), alert.zone, json.dumps(alert.track_ids),
                    json.dumps(alert.categories), alert.snapshot, alert.clip, int(alert.acknowledged),
                    alert.acknowledged_at, json.dumps(alert.extra, default=str),
                ),
            )  # fmt: skip
            self._conn.commit()
            alert.id = int(cur.lastrowid)
            return alert.id

    def get(self, alert_id: int) -> Alert | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM alertas WHERE id = ?", (alert_id,)).fetchone()
        return _from_row(row) if row else None

    def list(
        self,
        limit: int = 50,
        before_id: int | None = None,
        camera_id: str | None = None,
        min_severity: int | None = None,
        pending_only: bool = False,
    ) -> list[Alert]:
        where, args = [], []
        if before_id is not None:
            where.append("id < ?")
            args.append(before_id)
        if camera_id:
            where.append("camara_id = ?")
            args.append(camera_id)
        if min_severity:
            where.append("severidad >= ?")
            args.append(int(min_severity))
        if pending_only:
            where.append("reconocida = 0")
        sql = f"SELECT {_COLUMNS} FROM alertas"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [_from_row(r) for r in rows]

    def acknowledge(self, alert_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE alertas SET reconocida = 1, reconocida_ts = ? WHERE id = ? AND reconocida = 0",
                (time.time(), alert_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def acknowledge_all(self) -> int:
        with self._lock:
            cur = self._conn.execute("UPDATE alertas SET reconocida = 1, reconocida_ts = ? WHERE reconocida = 0", (time.time(),))
            self._conn.commit()
            return cur.rowcount

    def summary(self, since: float) -> dict:
        with self._lock:
            pending = self._conn.execute("SELECT COUNT(*) FROM alertas WHERE reconocida = 0").fetchone()[0]
            rows = self._conn.execute("SELECT severidad, COUNT(*) FROM alertas WHERE ts >= ? GROUP BY severidad", (since,)).fetchall()
        by_severity = {s.label: 0 for s in Severity}
        for sev, count in rows:
            by_severity[Severity(sev).label] = count
        return {"pendientes": pending, "ultimas_24h": by_severity}

    def purge_older_than(self, ts: float) -> list[str]:
        """Borra alertas antiguas y devuelve las rutas de sus evidencias."""
        with self._lock:
            rows = self._conn.execute("SELECT captura, clip FROM alertas WHERE ts < ?", (ts,)).fetchall()
            self._conn.execute("DELETE FROM alertas WHERE ts < ?", (ts,))
            self._conn.commit()
        return [p for row in rows for p in row if p]


def _from_row(row: tuple) -> Alert:
    (aid, ts, cam_id, cam, mode, rule, rtype, title, msg, sev, zone, objs, cats, snap, clip, ack, ack_ts, extra) = row
    return Alert(
        id=aid, ts=ts, camera_id=cam_id, camera_name=cam, mode=mode, rule_id=rule, rule_type=rtype, title=title,
        message=msg, severity=Severity(sev), zone=zone, track_ids=json.loads(objs), categories=json.loads(cats),
        snapshot=snap, clip=clip, acknowledged=bool(ack), acknowledged_at=ack_ts, extra=json.loads(extra),
    )  # fmt: skip
