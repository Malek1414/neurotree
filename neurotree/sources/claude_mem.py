"""claude-mem (~/.claude-mem/claude-mem.db, opened read-only) -> attention events and fact nodes.
Only titles, projects, file lists and times are kept."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict


def _cols(db, table: str) -> set:
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def _col(name: str, alias: str, cols: set) -> str:
    return f"{alias}.{name}" if name in cols else "NULL"


def index_memory(store, index, db_path: str, facts_per_node: int = 5) -> dict:
    if not os.path.exists(db_path):
        return {"ok": 0, "errors": 0, "skipped": "no database"}
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        oc, sc = _cols(db, "observations"), _cols(db, "sdk_sessions")
        join = "LEFT JOIN sdk_sessions s ON s.memory_session_id = o.memory_session_id" if sc else ""
        sc = sc if sc else set()
        rows = db.execute(
            f"SELECT o.id, {_col('title', 'o', oc)}, {_col('project', 'o', oc)}, {_col('created_at_epoch', 'o', oc)}, "
            f"{_col('files_read', 'o', oc)}, {_col('files_modified', 'o', oc)}, {_col('cwd', 's', sc)}, "
            f"{_col('content_session_id', 's', sc)} FROM observations o {join}").fetchall()
    finally:
        db.close()
    events, facts = [], defaultdict(list)
    ok = bad = 0
    for oid, title, project, epoch, files_read, files_modified, cwd, sid in rows:
        try:
            ts = float(epoch)
            ts = ts / 1000 if ts > 1e11 else ts
            cwd = cwd or index.root_by_name(project)
            touched = []
            for raw_list, kind, weight in ((files_modified, "modified", 3.0), (files_read, "read", 1.0)):
                for raw in json.loads(raw_list or "[]"):
                    node = index.resolve(raw, cwd)
                    if node:
                        events.append((node, ts, kind, weight, "claude_mem", sid))
                        touched.append(node)
            if title and touched:
                facts[touched[0]].append((ts, oid, title))
            ok += 1
        except (TypeError, ValueError):
            bad += 1
    store.add_events(events)
    for node_id, items in facts.items():
        owner = store.node(node_id)
        project = node_id if owner["kind"] == "project" else owner["project"]
        for ts, oid, title in sorted(items, reverse=True)[:facts_per_node]:
            store.upsert_node(f"fact:{oid}", "fact", 4, str(title)[:160], parent=node_id, project=project, ts=ts)
    return {"ok": ok, "errors": bad, "events": len(events), "facts": sum(min(len(v), facts_per_node) for v in facts.values())}
