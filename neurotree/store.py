"""SQLite store for the graph: nodes, edges, events, scores, meta. Rebuilt from scratch on every full index."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, kind TEXT NOT NULL, level INTEGER NOT NULL, parent TEXT,
  label TEXT NOT NULL, path TEXT, project TEXT, meta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS edges(src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1, PRIMARY KEY(src, dst, kind));
CREATE TABLE IF NOT EXISTS events(item TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, weight REAL NOT NULL,
  source TEXT NOT NULL, session TEXT);
CREATE INDEX IF NOT EXISTS ix_events_item ON events(item);
CREATE TABLE IF NOT EXISTS scores(id TEXT PRIMARY KEY, attention REAL, history REAL, structure REAL, runtime REAL,
  noise REAL, importance REAL, attention_rank REAL, evidence TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
EVENT_COLS = "item, ts, kind, weight, source, session"


class Store:
    def __init__(self, path, wal: bool = True):
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute(f"PRAGMA journal_mode={'WAL' if wal else 'DELETE'}")
        self.db.executescript(SCHEMA)

    # nodes
    def upsert_node(self, id: str, kind: str, level: int, label: str, parent: str | None = None,
                    path: str | None = None, project: str | None = None, **meta) -> None:
        row = self.db.execute("SELECT meta FROM nodes WHERE id=?", (id,)).fetchone()
        if row:
            merged = {**json.loads(row["meta"]), **meta}
            self.db.execute("UPDATE nodes SET kind=?, level=?, label=?, parent=COALESCE(?, parent), "
                            "path=COALESCE(?, path), project=COALESCE(?, project), meta=? WHERE id=?",
                            (kind, level, label, parent, path, project, json.dumps(merged), id))
        else:
            self.db.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                            (id, kind, level, parent, label, path, project, json.dumps(meta)))

    def set_parent(self, id: str, parent: str) -> None:
        self.db.execute("UPDATE nodes SET parent=? WHERE id=?", (parent, id))

    @staticmethod
    def _node(row) -> dict:
        d = dict(row)
        d["meta"] = json.loads(d["meta"])
        return d

    def node(self, id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM nodes WHERE id=?", (id,)).fetchone()
        return self._node(row) if row else None

    def nodes(self, kind: str | None = None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM nodes WHERE kind=?", (kind,)) if kind else self.db.execute("SELECT * FROM nodes")
        return [self._node(r) for r in rows]

    # edges
    def add_edge(self, src: str, dst: str, kind: str, weight: float = 1.0) -> None:
        self.db.execute("INSERT INTO edges VALUES(?,?,?,?) ON CONFLICT(src, dst, kind) "
                        "DO UPDATE SET weight = weight + excluded.weight", (src, dst, kind, weight))

    def edges(self, kind: str | None = None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM edges WHERE kind=?", (kind,)) if kind else self.db.execute("SELECT * FROM edges")
        return [dict(r) for r in rows]

    # events
    def add_events(self, rows: Iterable[tuple]) -> int:
        rows = list(rows)
        self.db.executemany(f"INSERT INTO events({EVENT_COLS}) VALUES(?,?,?,?,?,?)", rows)
        return len(rows)

    def events(self, sources: Iterable[str] | None = None) -> list[dict]:
        if sources:
            sources = tuple(sources)
            q = f"SELECT {EVENT_COLS} FROM events WHERE source IN ({','.join('?' * len(sources))})"
            return [dict(r) for r in self.db.execute(q, sources)]
        return [dict(r) for r in self.db.execute(f"SELECT {EVENT_COLS} FROM events")]

    # scores
    def write_scores(self, rows: Iterable[dict]) -> None:
        self.db.execute("DELETE FROM scores")
        self.db.executemany("INSERT INTO scores VALUES(?,?,?,?,?,?,?,?,?)",
                            [(r["id"], r["attention"], r["history"], r["structure"], r["runtime"], r["noise"],
                              r["importance"], r["attention_rank"], json.dumps(r["evidence"])) for r in rows])

    def scores(self) -> dict[str, dict]:
        out = {}
        for r in self.db.execute("SELECT * FROM scores"):
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"])
            out[d["id"]] = d
        return out

    # meta
    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT INTO meta VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value)))

    def get_meta(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()
