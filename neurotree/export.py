"""Store -> graph.json for the viewer: scored nodes with level-of-detail flags, edges, recent events."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .scoring import WEIGHTS


def export(store, out_path, max_files: int = 6000, event_days: int = 120, now: float | None = None) -> dict:
    now = now or time.time()
    out_path = Path(out_path)
    scores, nodes = store.scores(), store.nodes()
    imp = lambda n: scores.get(n["id"], {}).get("importance") or 0.0  # noqa: E731
    default_files = {n["id"] for n in sorted((n for n in nodes if n["level"] == 3), key=imp, reverse=True)[:max_files]}
    events = store.events()
    last = {}
    for e in events:
        if e["ts"] > last.get(e["item"], 0):
            last[e["item"]] = e["ts"]
    out_nodes = []
    for n in nodes:
        s = scores.get(n["id"], {})
        lod = 0 if n["level"] <= 2 or n["id"] in default_files else (1 if n["level"] == 3 else 2)
        out_nodes.append({"id": n["id"], "kind": n["kind"], "level": n["level"], "parent": n["parent"],
                          "label": n["label"], "path": n["path"], "project": n["project"], "lod": lod,
                          "color": n["meta"].get("color"), "type": n["meta"].get("kind_detail"),
                          "importance": s.get("importance", 0.0), "attentionRank": s.get("attention_rank", 0.0),
                          "signals": {k: s.get(k) for k in WEIGHTS}, "noise": s.get("noise", 1.0),
                          "evidence": s.get("evidence", []), "lastActivity": last.get(n["id"])})
    ids = {n["id"] for n in out_nodes}
    edges = [{"source": e["src"], "target": e["dst"], "kind": e["kind"], "weight": e["weight"]}
             for e in store.edges() if e["src"] in ids and e["dst"] in ids]
    cutoff = now - event_days * 86400
    recent = sorted(([e["item"], round(e["ts"], 1), e["kind"], round(e["weight"], 2)] for e in events
                     if e["ts"] >= cutoff and e["item"] in ids), key=lambda r: r[1])
    counts = {"nodes": len(out_nodes), "edges": len(edges), "events": len(recent),
              "projects": sum(n["level"] == 2 for n in out_nodes), "files": sum(n["level"] == 3 for n in out_nodes)}
    data = {"meta": {"generated_at": now, "weights": WEIGHTS, "counts": counts, "status": store.get_meta("status", {})},
            "nodes": out_nodes, "edges": edges, "events": recent}
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out_path)
    return counts
