"""Signals -> importance. Attention 45 %, history 25 %, structure 20 %, runtime 10 %, times noise.
Each signal is a percentile rank among nodes of the same level; parents roll up their children's raw signals."""
from __future__ import annotations

import bisect
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

from .structure import pagerank_by_project

WEIGHTS = {"attention": 0.45, "history": 0.25, "structure": 0.20, "runtime": 0.10}
HALF_LIFE_DAYS = {"attention": 30.0, "history": 90.0}
SIGNAL_OF_SOURCE = {"claude_sessions": "attention", "claude_mem": "attention", "git": "history", "fs": "history",
                    "github": "history"}
STRUCTURE_FLOOR = 0.15
DAY = 86400.0


def decay(weight: float, ts: float, now: float, half_life_days: float) -> float:
    return weight * 0.5 ** (max(0.0, now - ts) / DAY / half_life_days)


def percentile_ranks(values: dict) -> dict:
    """Rank in (0, 1] among positive values (ties share the higher rank); zero stays 0."""
    pos = sorted(v for v in values.values() if v > 0)
    if not pos:
        return {k: 0.0 for k in values}
    return {k: (bisect.bisect_right(pos, v) / len(pos) if v > 0 else 0.0) for k, v in values.items()}


def combine(signals: dict, noise: float = 1.0) -> float:
    present = {k: w for k, w in WEIGHTS.items() if signals.get(k) is not None}
    total = sum(present.values())
    if not total:
        return 0.0
    value = sum(w / total * signals[k] for k, w in present.items())
    if signals.get("structure") is not None:  # essential but untouched code never goes fully dark
        value = max(value, STRUCTURE_FLOOR * signals["structure"])
    return round(value * noise, 6)


def runtime_shares(store) -> dict:
    """repograph traces (docs/graph/graph.json + trace.json in a repo) -> {file id: share of recorded time}."""
    out = {}
    for n in store.nodes("project"):
        if not n["path"]:
            continue
        g, t = Path(n["path"], "docs/graph/graph.json"), Path(n["path"], "docs/graph/trace.json")
        if not (g.exists() and t.exists()):
            continue
        try:
            graph, trace = json.loads(g.read_text()), json.loads(t.read_text())
        except (OSError, ValueError):
            continue
        path_of = {m["id"]: m.get("path") for m in graph.get("nodes", [])}
        ms = {mid: (v or {}).get("ms", 0) or 0 for mid, v in trace.get("nodes", {}).items()}
        total = sum(ms.values()) or 1
        for mid, v in ms.items():
            if path_of.get(mid):
                out[f"file:{os.path.join(n['path'], path_of[mid])}"] = round(v / total, 6)
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 'es' if word.endswith('ch') else 's'}"


def evidence(counts: dict, imported_by: int, runtime: float | None, noise: float) -> list[str]:
    out = []
    if counts.get("claude30"):
        out.append(f"{_plural(counts['claude30'], 'Claude touch')} in 30 days")
    if counts.get("commits90"):
        out.append(f"{_plural(counts['commits90'], 'commit')} in 90 days")
    if imported_by:
        out.append(f"imported by {_plural(imported_by, 'module')}")
    if runtime:
        out.append(f"{runtime * 100:.0f} % of recorded run time")
    if noise <= 0.1:
        out.append("dimmed: lockfile, generated or vendored")
    elif noise < 1:
        out.append("dimmed: test code")
    return out


def score(store, now: float | None = None) -> int:
    now = now or time.time()
    nodes = {n["id"]: n for n in store.nodes()}
    raw = {"attention": defaultdict(float), "history": defaultdict(float)}
    counts = defaultdict(Counter)
    for e in store.events():
        sig = SIGNAL_OF_SOURCE.get(e["source"])
        if not sig or e["item"] not in nodes:
            continue
        raw[sig][e["item"]] += decay(e["weight"], e["ts"], now, HALF_LIFE_DAYS[sig])
        age_days = (now - e["ts"]) / DAY
        if sig == "attention" and age_days <= 30:
            counts[e["item"]]["claude30"] += 1
        if e["kind"] == "commit" and age_days <= 90:
            counts[e["item"]]["commits90"] += 1
    for level in (4, 3, 2, 1):  # roll children up into parents, deepest first
        for nid, n in nodes.items():
            if n["level"] == level and n["parent"] in nodes:
                for sig in raw:
                    raw[sig][n["parent"]] += raw[sig].get(nid, 0.0)
                counts[n["parent"]].update(counts.get(nid, Counter()))
    structure, runtime = pagerank_by_project(store), runtime_shares(store)
    imported_by = Counter(e["dst"] for e in store.edges("import"))
    by_level = defaultdict(list)
    for nid, n in nodes.items():
        by_level[n["level"]].append(nid)
    ranks = {}
    for ids in by_level.values():
        att = percentile_ranks({i: raw["attention"].get(i, 0.0) for i in ids})
        his = percentile_ranks({i: raw["history"].get(i, 0.0) for i in ids})
        st = percentile_ranks({i: structure[i] for i in ids if i in structure})
        rt = percentile_ranks({i: runtime[i] for i in ids if i in runtime})
        for i in ids:
            ranks[i] = {"attention": att[i], "history": his[i], "structure": st.get(i), "runtime": rt.get(i)}
    rows = []
    for nid, n in nodes.items():
        r, noise = ranks[nid], float(n["meta"].get("noise", 1.0))
        is_self = n["kind"] == "self"
        rows.append({"id": nid, **r, "noise": noise, "importance": 1.0 if is_self else combine(r, noise),
                     "attention_rank": 1.0 if is_self else r["attention"],
                     "evidence": evidence(counts.get(nid, {}), imported_by.get(nid, 0), runtime.get(nid), noise)})
    store.write_scores(rows)
    return len(rows)
