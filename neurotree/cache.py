"""cache.db: what earlier index runs already worked out, so the next run only redoes what changed.
It lives next to graph.db but is never replaced; deleting it only makes the next run slower."""
from __future__ import annotations

import json
import sqlite3
import zlib
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(ns TEXT NOT NULL, key TEXT NOT NULL, stamp TEXT NOT NULL, value BLOB NOT NULL,
  PRIMARY KEY(ns, key));
"""


class Cache:
    """(namespace, key) -> value, valid while its stamp (size and mtime, a git state, ...) is unchanged."""

    def __init__(self, path):
        self.db = sqlite3.connect(str(Path(path)))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.hits = self.misses = 0
        self.used: dict[str, set] = {}
        self.mem: dict[str, dict] = {}

    def preload(self, ns: str) -> None:
        """Reads a whole namespace in one query (for namespaces asked about thousands of times a run)."""
        self.mem[ns] = {k: (st, v) for k, st, v in self.db.execute("SELECT key, stamp, value FROM entries WHERE ns=?", (ns,))}

    def get(self, ns: str, key: str, stamp: str):
        self.used.setdefault(ns, set()).add(key)
        row = (self.mem[ns].get(key) if ns in self.mem else
               self.db.execute("SELECT stamp, value FROM entries WHERE ns=? AND key=?", (ns, key)).fetchone())
        if row and row[0] == stamp:
            self.hits += 1
            return json.loads(zlib.decompress(row[1]))
        self.misses += 1
        return None

    def put(self, ns: str, key: str, stamp: str, value) -> None:
        self.used.setdefault(ns, set()).add(key)
        blob = zlib.compress(json.dumps(value, separators=(",", ":")).encode(), 3)
        self.db.execute("INSERT INTO entries VALUES(?,?,?,?) ON CONFLICT(ns, key) DO UPDATE SET stamp=excluded.stamp, "
                        "value=excluded.value", (ns, key, stamp, blob))

    def prune(self, ns: str) -> int:
        """Drops the entries of `ns` this run never asked for (deleted files, removed repos)."""
        keep = self.used.get(ns, set())
        gone = [k for (k,) in self.db.execute("SELECT key FROM entries WHERE ns=?", (ns,)) if k not in keep]
        self.db.executemany("DELETE FROM entries WHERE ns=? AND key=?", [(ns, k) for k in gone])
        return len(gone)

    def close(self) -> None:
        self.db.commit()
        self.db.close()


def file_stamp(path) -> str | None:
    """size:mtime_ns, or None when the file is gone."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return f"{st.st_size}:{st.st_mtime_ns}"


class Recorder:
    """Stands in for the store while one source runs: everything is forwarded and the writes are noted down, so a
    later run with the same stamp can replay them instead of redoing the work."""

    def __init__(self, store):
        self.store, self.ops = store, []

    def upsert_node(self, *args, **meta) -> None:
        self.ops.append(["n", list(args), meta])
        self.store.upsert_node(*args, **meta)

    def add_edge(self, *args) -> None:
        self.ops.append(["e", list(args)])
        self.store.add_edge(*args)

    def add_events(self, rows) -> int:
        rows = [list(r) for r in rows]
        self.ops.append(["v", rows])
        return self.store.add_events(rows)

    def __getattr__(self, name):
        return getattr(self.store, name)


def replay(store, ops) -> None:
    for op in ops:
        if op[0] == "n":
            store.upsert_node(*op[1], **op[2])
        elif op[0] == "e":
            store.add_edge(*op[1])
        else:
            store.add_events(op[1])
