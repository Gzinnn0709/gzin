"""Armazenamento dos cortes em SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    channel TEXT NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    peak_score REAL,
    ai_score INTEGER,
    category TEXT,
    title TEXT,
    description TEXT,
    hashtags TEXT,
    reason TEXT,
    by_ai INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pendente',
    file_h TEXT,
    file_v TEXT,
    thumb TEXT,
    transcript TEXT,
    stats TEXT
);
CREATE INDEX IF NOT EXISTS idx_clips_created ON clips(created_at DESC);
"""

STATUSES = ("na fila", "processando", "pendente", "aprovado", "rejeitado", "postado", "descartado", "erro")


class ClipDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def insert(self, **fields) -> int:
        fields.setdefault("created_at", time.time())
        for k in ("hashtags", "stats"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        cols = ",".join(fields)
        q = ",".join("?" for _ in fields)
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO clips ({cols}) VALUES ({q})", tuple(fields.values()))
            self._conn.commit()
            return int(cur.lastrowid)

    def update(self, clip_id: int, **fields) -> None:
        sets = ",".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE clips SET {sets} WHERE id=?", (*fields.values(), clip_id))
            self._conn.commit()

    def get(self, clip_id: int) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        return _row(r) if r else None

    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        q, args = "SELECT * FROM clips", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [_row(r) for r in self._conn.execute(q, args).fetchall()]


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("hashtags", "stats"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d
