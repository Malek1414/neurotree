"""Attention over the tree, after "Attention Is All You Need" (Vaswani et al., 2017).

A question attends to every node at once, the way a query attends to every position: each head scores the nodes,
a softmax turns its scores into weights, and the heads' weights are mixed. What carried over from the paper, what
did not, and the measurements behind each choice: docs/attention.md.

  lexical head   BM25 over the node keys (names, path words, first lines, functions, memory titles)
  semantic head  cosine of static code embeddings; a softmax temperature does the paper's 1/sqrt(d) job
  bias           added to the logits: the log of the node's prior (what you work on, at several time scales)
                 and the project you are in. A bias reorders matches; it never brings in a node nothing matched.
  graph step     one round of attention along imports and edited-together links, with a residual

Reads graph.db, writes nothing."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .keys import load_model, words

DAY = 86400.0
ATTENTION_SOURCES = ("claude_sessions", "claude_mem")
STOP = set("""a an and are as at be but by can could did do does doing for from had has have how i if in into is it its
just me my no not now of on or our please so some such than that the their them then there these they this to too up
us was we what when where which who why will with would you your yes ok okay hey bro go make let lets want need get
also all any more most very much again still like use using used way thing things something etc via about over
der die das und ist ich du er sie es wir ihr mit von zu den dem des ein eine einen einem einer auf für fur nicht
noch auch wie was wenn dann aber oder bei aus nach so mal ja nein bitte mach machen kannst kann soll sollte""".split())

DEFAULT = {
    "lexical": 1.0, "semantic": 1.0,     # head weights (the paper's W_O, fixed and equal instead of learned)
    "tau_lex": 0.08, "tau_sem": 0.04,    # softmax temperatures
    "prior": 1.0, "eps": 0.05,           # logit bias: prior * log(eps + prior)
    "cwd": 1.5,                          # logit bias for nodes in the project you are in
    "graph": 0.15, "hops": 1,            # share of attention that flows along edges per round
    "rel_import": 1.0, "rel_together": 0.0,  # edited-together links measured slightly worse for ranking
    "scales": (1, 7, 30, 120),           # attention half-lives in days (one per "frequency")
    "limit": 400, "seeds": 40, "hub": 40,
}


@dataclass
class Index:
    db: sqlite3.Connection
    path: str
    built_at: float
    ids: list
    label: list
    paths: list
    summary: dict
    kind: np.ndarray
    level: np.ndarray
    parent: np.ndarray
    project: np.ndarray
    unit: np.ndarray
    fact_ts: np.ndarray
    noise: np.ndarray
    structure: np.ndarray
    rowid: dict
    by_id: dict
    ev_item: np.ndarray
    ev_ts: np.ndarray
    ev_w: np.ndarray
    ev_att: np.ndarray
    first_ts: np.ndarray
    imports: dict
    file_sessions: dict
    session_files: dict
    project_roots: list
    copy_of: dict = field(default_factory=dict)
    vecs: np.ndarray | None = None
    model: object = field(default=None, repr=False)

    def encode(self, text: str) -> np.ndarray | None:
        if self.vecs is None:
            return None
        if self.model is None:
            self.model = load_model()
            if self.model is None:
                self.vecs = None
                return None
        v = np.asarray(self.model.encode([text])[0], dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


class NoKeys(RuntimeError):
    """graph.db was built before attention keys existed (or by a run whose keys step failed)."""


def has_keys(db_path) -> bool:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return _has_table(db, "keys")
    finally:
        db.close()


def load(db_path, vectors: bool = True) -> Index:
    """Everything a query needs, in arrays (about a second for 50k nodes)."""
    if not has_keys(db_path):
        raise NoKeys(f"{db_path} has no attention keys yet: run `python -m neurotree all` once (or let the app reindex)")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = db.execute("SELECT rowid, id, kind, level, parent, label, path, project, json_extract(meta, '$.summary'), "
                      "json_extract(meta, '$.ts'), json_extract(meta, '$.noise'), json_extract(meta, '$.rel') "
                      "FROM nodes").fetchall()
    n = len(rows)
    by_id = {r[1]: i for i, r in enumerate(rows)}
    rowid = {r[0]: i for i, r in enumerate(rows)}
    kind = np.array([r[2] for r in rows], dtype=object)
    level = np.array([r[3] for r in rows], dtype=np.int8)
    parent = np.array([by_id.get(r[4], -1) for r in rows], dtype=np.int32)
    project = np.array([by_id.get(r[7], -1) if r[2] != "project" else i for i, r in enumerate(rows)], dtype=np.int32)
    unit = np.full(n, -1, dtype=np.int32)
    for i, r in enumerate(rows):
        if r[2] in ("file", "project"):
            unit[i] = i
        elif r[2] in ("function", "fact") and parent[i] >= 0 and rows[parent[i]][2] in ("file", "project"):
            unit[i] = parent[i]
    fact_ts = np.array([float(r[9]) if r[2] == "fact" and r[9] is not None else -np.inf for r in rows])
    noise = np.array([float(r[10]) if r[10] is not None else 1.0 for r in rows])
    structure = np.full(n, np.nan)
    for i, v in db.execute("SELECT id, structure FROM scores WHERE structure IS NOT NULL"):
        if i in by_id:
            structure[by_id[i]] = v
    ev = db.execute("SELECT item, ts, weight, source, session FROM events").fetchall()
    keep = [(by_id[e[0]], e[1], e[2], e[3] in ATTENTION_SOURCES, e[4]) for e in ev if e[0] in by_id]
    ev_item = np.array([e[0] for e in keep], dtype=np.int32)
    ev_ts = np.array([e[1] for e in keep], dtype=np.float64)
    first_ts = np.full(n, np.inf)
    np.minimum.at(first_ts, ev_item, ev_ts)
    file_sessions, session_files = defaultdict(dict), defaultdict(dict)
    for item, ts, _, att, sess in keep:
        if att and sess and kind[item] == "file":
            if ts < session_files[sess].get(item, np.inf):
                session_files[sess][item] = ts
                file_sessions[item][sess] = min(ts, file_sessions[item].get(sess, np.inf))
    imports = defaultdict(set)
    for s, d in db.execute("SELECT src, dst FROM edges WHERE kind='import'"):
        if s in by_id and d in by_id:
            imports[by_id[s]].add(by_id[d])
            imports[by_id[d]].add(by_id[s])
    vecs = None
    if vectors:
        blobs = db.execute("SELECT id, v FROM vecs ORDER BY id").fetchall() if _has_table(db, "vecs") else []
        if blobs and len(blobs) == n:
            order = np.array([rowid[b[0]] for b in blobs])
            mat = np.frombuffer(b"".join(b[1] for b in blobs), dtype=np.float16).reshape(n, -1)
            vecs = np.empty(mat.shape, dtype=np.float32)
            vecs[order] = mat
    roots = sorted(((r[6], i) for i, r in enumerate(rows) if r[2] == "project" and r[6]), key=lambda x: -len(x[0]))
    copies = defaultdict(list)  # the same file in sibling repos (a copied project): same relative path and first line
    for i, r in enumerate(rows):
        if r[2] == "file" and r[11] and r[8]:
            copies[(r[11], r[8])].append(i)
    copy_of = {i: group for group in copies.values() if len(group) > 1 for i in group}
    built = os.path.getmtime(db_path)
    return Index(db=db, path=str(db_path), built_at=built, ids=[r[1] for r in rows], label=[r[5] for r in rows],
                 paths=[r[6] for r in rows], summary={i: r[8] for i, r in enumerate(rows) if r[8]}, kind=kind,
                 level=level, parent=parent, project=project, unit=unit, fact_ts=fact_ts, noise=noise,
                 structure=structure, rowid=rowid, by_id=by_id, ev_item=ev_item, ev_ts=ev_ts,
                 ev_w=np.array([e[2] for e in keep], dtype=np.float64), ev_att=np.array([e[3] for e in keep], dtype=bool),
                 first_ts=first_ts, imports=dict(imports), file_sessions=dict(file_sessions),
                 session_files=dict(session_files), project_roots=roots, copy_of=copy_of, vecs=vecs)


def _has_table(db, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


# ---- prior: what you work on, at several time scales --------------------------------------------------------------

def _rollup(ix: Index, arr: np.ndarray) -> np.ndarray:
    for lvl in (4, 3, 2, 1):
        m = (ix.level == lvl) & (ix.parent >= 0)
        np.add.at(arr, ix.parent[m], arr[m])
    return arr


def _pct(ix: Index, arr: np.ndarray) -> np.ndarray:
    """Percentile among the positive values of the same level, like scoring.percentile_ranks (a per-level norm)."""
    out = np.zeros_like(arr, dtype=np.float64)
    for lvl in np.unique(ix.level):
        m = ix.level == lvl
        vals = arr[m]
        pos = np.sort(vals[vals > 0])
        if len(pos):
            out[m] = np.where(vals > 0, np.searchsorted(pos, vals, side="right") / len(pos), 0.0)
    return out


def decayed(ix: Index, as_of: float, half_life: float, attention: bool) -> np.ndarray:
    m = (ix.ev_ts < as_of) & (ix.ev_att == attention)
    age = (as_of - ix.ev_ts[m]) / DAY
    arr = np.bincount(ix.ev_item[m], weights=ix.ev_w[m] * 0.5 ** (age / half_life), minlength=len(ix.ids))
    return _rollup(ix, arr)


def prior(ix: Index, as_of: float | None = None, scales=DEFAULT["scales"]) -> np.ndarray:
    """Importance in [0, 1] as of a moment: attention (mean percentile over the half-lives in `scales`) 45 %,
    history 25 %, structure 20 % where a module has imports, times noise; the same mix as scoring.py."""
    as_of = as_of or time.time()
    att = np.mean([_pct(ix, decayed(ix, as_of, h, True)) for h in scales], axis=0)
    his = _pct(ix, decayed(ix, as_of, 90.0, False))
    st = np.where(np.isnan(ix.structure), 0.0, ix.structure)
    st_pct = _pct(ix, st)
    has = ~np.isnan(ix.structure)
    value = (0.45 * att + 0.25 * his + np.where(has, 0.20 * st_pct, 0.0)) / (0.70 + np.where(has, 0.20, 0.0))
    value = np.where(has, np.maximum(value, 0.15 * st_pct), value)
    return value * ix.noise


# ---- heads ---------------------------------------------------------------------------------------------------------

def query_terms(query: str) -> list[str]:
    seen = []
    for t in words(query).split():
        if len(t) > 1 and t not in STOP and not t.isdigit() and t not in seen:
            seen.append(t)
    return seen[:32]


def lexical(ix: Index, query: str, limit: int) -> tuple[np.ndarray, np.ndarray]:
    terms = query_terms(query)
    if not terms:
        return np.empty(0, dtype=np.int64), np.empty(0)
    expr = " OR ".join(f'"{t}"' for t in terms)
    try:
        rows = ix.db.execute("SELECT rowid, -bm25(keys, 4.0, 1.5, 1.0) AS s FROM keys WHERE keys MATCH ? "
                             "ORDER BY s DESC LIMIT ?", (expr, limit)).fetchall()
    except sqlite3.OperationalError:
        return np.empty(0, dtype=np.int64), np.empty(0)
    idx = np.array([ix.rowid[r[0]] for r in rows if r[0] in ix.rowid], dtype=np.int64)
    s = np.array([r[1] for r in rows if r[0] in ix.rowid], dtype=np.float64)
    return idx, (s / s.max() if len(s) and s.max() > 0 else s)


def semantic(ix: Index, qv: np.ndarray | None, limit: int) -> tuple[np.ndarray, np.ndarray]:
    if qv is None or ix.vecs is None:
        return np.empty(0, dtype=np.int64), np.empty(0)
    sims = ix.vecs @ qv
    idx = np.argpartition(-sims, min(limit, len(sims) - 1))[:limit]
    return idx, sims[idx].astype(np.float64)


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


# ---- the graph step: attention flows along edges ------------------------------------------------------------------

def together(ix: Index, f: int, as_of: float, hub: int) -> dict:
    """Files edited or read in the same Claude sessions as f before as_of, weight 1/(files in the session - 1)."""
    out = defaultdict(float)
    for sess, ts in ix.file_sessions.get(f, {}).items():
        if ts >= as_of:
            continue
        files = [g for g, t in ix.session_files[sess].items() if t < as_of and g != f]
        if 1 <= len(files) < hub:
            for g in files:
                out[g] += 1.0 / len(files)
    return out


def neighbors(ix: Index, f: int, as_of: float, cfg: dict) -> dict:
    rel = defaultdict(float)
    if cfg["rel_import"]:
        imp = ix.imports.get(f, ())
        for g in imp:
            rel[g] += cfg["rel_import"] / len(imp)
    if cfg["rel_together"]:
        for g, w in together(ix, f, as_of, cfg["hub"]).items():
            rel[g] += cfg["rel_together"] * w
    return rel


def graph_step(ix: Index, mass: dict, as_of: float, cfg: dict, valid: np.ndarray, via: dict) -> dict:
    """mass' = (1 - g) mass + g * (mass sent along normalized edges), for the top seeds; a seed with no edges keeps
    its mass (the residual). Mass is conserved, so the result is still a distribution."""
    g = cfg["graph"]
    out = dict(mass)
    seeds = sorted((u for u in mass if ix.kind[u] == "file"), key=lambda u: -mass[u])[:cfg["seeds"]]
    for s in seeds:
        rel = {u: w for u, w in neighbors(ix, s, as_of, cfg).items() if valid[u] and ix.kind[u] == "file"}
        total = sum(rel.values())
        if not total:
            continue
        send = g * mass[s]
        out[s] -= send
        for u, w in rel.items():
            share = send * w / total
            out[u] = out.get(u, 0.0) + share
            if share > via.get(u, (None, 0.0))[1]:
                via[u] = (s, share)
    return out


# ---- attend --------------------------------------------------------------------------------------------------------

def project_of(ix: Index, path: str | None) -> int:
    if not path:
        return -1
    path = os.path.normpath(os.path.expanduser(path))
    for root, i in ix.project_roots:
        if path == root or path.startswith(root.rstrip("/") + "/"):
            return i
    return -1


def attend(ix: Index, query: str, cwd: str | None = None, as_of: float | None = None, cfg: dict | None = None,
           qv: np.ndarray | None = None, explain: bool = True, memo: dict | None = None) -> dict:
    """-> {"units": [(node index, mass)] best first, "heads": per-head mass per unit, "via": graph links,
    "best": the node that carried each unit's mass, "cwd_project": index or -1}. Mass sums to 1."""
    cfg = {**DEFAULT, **(cfg or {})}
    live = as_of is None
    as_of = as_of or time.time()
    n = len(ix.ids)
    valid = np.ones(n, dtype=bool) if live else _valid(ix, as_of)
    memo = {} if memo is None else memo  # the benchmark reuses priors and head scores across settings
    key = ("prior", as_of, tuple(cfg["scales"]))
    if cfg["prior"] and key not in memo:
        memo[key] = prior(ix, as_of, cfg["scales"])
    pr = memo[key] if cfg["prior"] else np.zeros(n)
    here = project_of(ix, cwd)
    unit_safe = np.where(ix.unit >= 0, ix.unit, 0)
    bias = cfg["prior"] * np.log(cfg["eps"] + pr[unit_safe]) if cfg["prior"] else np.zeros(n)
    if here >= 0 and cfg["cwd"]:
        bias = bias + cfg["cwd"] * (ix.project[unit_safe] == here)
    heads = {}
    if cfg["lexical"]:
        if ("lex", query) not in memo:
            memo[("lex", query)] = lexical(ix, query, cfg["limit"])
        heads["lexical"] = (cfg["lexical"], *memo[("lex", query)], cfg["tau_lex"])
    if cfg["semantic"]:
        if ("sem", query) not in memo:
            memo[("sem", query)] = semantic(ix, ix.encode(query) if qv is None else qv, cfg["limit"])
        heads["semantic"] = (cfg["semantic"], *memo[("sem", query)], cfg["tau_sem"])
    node_mass = np.zeros(n)
    per_head = {}
    total_w = 0.0
    for name, (w, idx, s, tau) in heads.items():
        ok = valid[idx] & (ix.unit[idx] >= 0)
        idx, s = idx[ok], s[ok]
        if not len(idx):
            continue
        p = _softmax(s / tau + bias[idx])
        per_head[name] = (idx, p)
        node_mass[idx] += w * p
        total_w += w
    if not total_w:
        return {"units": [], "heads": {}, "via": {}, "best": {}, "cwd_project": here, "prior": pr}
    node_mass /= total_w
    cand = np.nonzero(node_mass)[0]
    mass = defaultdict(float)
    best = {}
    for i in cand:
        u = int(ix.unit[i])
        mass[u] += node_mass[i]
        if node_mass[i] > best.get(u, (None, 0.0))[1]:
            best[u] = (int(i), node_mass[i])
    via = {}
    mass = dict(mass)
    for _ in range(cfg["hops"] if cfg["graph"] else 0):
        mass = graph_step(ix, mass, as_of, cfg, valid, via)
    copies = {}
    for u in list(mass):  # copies of one file in sibling repos: the best one keeps their summed attention
        group = ix.copy_of.get(u)
        if group:
            keep = max((g for g in group if g in mass), key=lambda g: mass[g])
            if keep != u:
                mass[keep] += mass.pop(u)
                copies.setdefault(keep, []).append(u)
    units = sorted(mass.items(), key=lambda kv: -kv[1])
    out = {"units": units, "via": via, "best": best, "cwd_project": here, "prior": pr, "copies": copies}
    if explain:
        hm = {}
        for name, (idx, p) in per_head.items():
            acc = defaultdict(float)
            for i, v in zip(idx, p):
                acc[int(ix.unit[i])] += v
            hm[name] = acc
        out["heads"] = hm
    return out


def _valid(ix: Index, as_of: float) -> np.ndarray:
    """Nodes that existed at as_of (for replaying the past): no events only after as_of, facts written before."""
    existed = ~((ix.first_ts >= as_of) & np.isfinite(ix.first_ts))
    u = np.where(ix.unit >= 0, ix.unit, np.arange(len(ix.ids)))
    return existed[u] & (ix.fact_ts < as_of)


# ---- reading the result ------------------------------------------------------------------------------------------

def touches(ix: Index, i: int, as_of: float, days: float) -> int:
    m = (ix.ev_item == i) & ix.ev_att & (ix.ev_ts < as_of) & (ix.ev_ts >= as_of - days * DAY)
    return int(m.sum())


def explain(ix: Index, res: dict, u: int, as_of: float) -> list[str]:
    why = []
    heads = res.get("heads", {})
    lex, sem = heads.get("lexical", {}).get(u, 0.0), heads.get("semantic", {}).get(u, 0.0)
    if lex or sem:
        best = res["best"].get(u, (u, 0))[0]
        what = "words" if lex >= sem else "meaning"
        if best != u and ix.kind[best] == "function":
            why.append(f"{what} match in {ix.label[best]}()")
        elif best != u and ix.kind[best] == "fact":
            why.append(f"memory: \"{ix.label[best][:90]}\"")
        else:
            why.append(f"{what} match")
    if u in res.get("via", {}):
        s = res["via"][u][0]
        rel = "imports/imported by" if s in ix.imports.get(u, ()) else "worked on together with"
        why.append(f"{rel} {ix.label[s]}")
    if res.get("cwd_project", -1) >= 0 and ix.project[u] == res["cwd_project"]:
        why.append("in the project you're in")
    if res.get("copies", {}).get(u):
        others = sorted({ix.label[ix.project[c]] for c in res["copies"][u] if ix.project[c] >= 0})
        why.append("same file also in " + ", ".join(others[:3]))
    t7, t30 = touches(ix, u, as_of, 7), touches(ix, u, as_of, 30)
    if t30:
        why.append(f"Claude touched it {t7}x in 7 days, {t30}x in 30" if t7 else f"Claude touched it {t30}x in 30 days")
    return why


def ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min"
    if seconds < 2 * DAY:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / DAY:.0f} days"


def short(path: str | None) -> str:
    home = os.path.expanduser("~")
    return ("~" + path[len(home):]) if path and path.startswith(home) else (path or "")


def report(ix: Index, query: str, cwd: str | None = None, k: int = 12, mass: float = 0.8, cfg: dict | None = None,
           as_json: bool = False) -> str:
    """The answer Claude reads: projects and files holding most of the attention, each with why."""
    now = time.time()
    res = attend(ix, query, cwd, cfg=cfg)
    files = [(u, m) for u, m in res["units"] if ix.kind[u] == "file"]
    proj = defaultdict(float)
    for u, m in res["units"]:
        p = u if ix.kind[u] == "project" else int(ix.project[u])
        if p >= 0:
            proj[p] += m
    projects = sorted(proj.items(), key=lambda kv: -kv[1])[:4]
    chosen, acc = [], 0.0
    for u, m in files:  # stop once the chosen files hold `mass` of the attention (sharp questions -> fewer files)
        if len(chosen) >= k or (acc >= mass * sum(m for _, m in files) and len(chosen) >= 3):
            break
        chosen.append((u, m))
        acc += m
    facts = []
    for u, _ in chosen + [(p, m) for p, m in projects if m >= 0.1]:
        b = res["best"].get(u, (None,))[0]
        if b is not None and ix.kind[b] == "fact":
            facts.append(b)
    if as_json:
        return json.dumps({
            "query": query, "index_age_s": round(now - ix.built_at),
            "projects": [{"id": ix.ids[p], "label": ix.label[p], "path": ix.paths[p], "summary": ix.summary.get(p),
                          "attention": round(m, 4)} for p, m in projects],
            "files": [{"id": ix.ids[u], "path": ix.paths[u], "project": ix.label[ix.project[u]] if ix.project[u] >= 0 else None,
                       "summary": ix.summary.get(u), "attention": round(m, 4), "why": explain(ix, res, u, now)}
                      for u, m in chosen]}, ensure_ascii=False, indent=1)
    lines = [f"Neurotree · indexed {ago(now - ix.built_at)} ago · {len(ix.ids):,} nodes · query: {query!r}"
             + (f" · you are in {ix.label[res['cwd_project']]}" if res["cwd_project"] >= 0 else "")]
    if not res["units"]:
        lines.append("Nothing in the tree matches. Try other words (names, project names), or `neurotree now`.")
        return "\n".join(lines)
    lines.append("\nProjects")
    for p, m in projects:
        s = ix.summary.get(p)
        lines.append(f"  {ix.label[p]}  {short(ix.paths[p])}  [{m:.0%}]" + (f"  {s[:120]}" if s else ""))
    lines.append(f"\nFiles ({acc:.0%} of the attention in {len(chosen)} files, best first)")
    for rank, (u, m) in enumerate(chosen, 1):
        s = ix.summary.get(u)
        lines.append(f"  {rank}. {ix.paths[u]}  [{m:.0%}]" + (f"\n     {s[:140]}" if s else ""))
        why = explain(ix, res, u, now)
        if why:
            lines.append("     why: " + " · ".join(why))
    if facts:
        lines.append("\nMemory (claude-mem titles)")
        for f in dict.fromkeys(facts):
            when = time.strftime("%Y-%m-%d", time.localtime(ix.fact_ts[f])) if math.isfinite(ix.fact_ts[f]) else ""
            lines.append(f"  - {when} \"{ix.label[f]}\"  -> {ix.label[ix.unit[f]]}")
    return "\n".join(lines)


def now_report(ix: Index, k: int = 8) -> str:
    """What has your attention right now, at three time scales."""
    t = time.time()
    lines = [f"Neurotree · indexed {ago(t - ix.built_at)} ago · what you and Claude worked on"]
    for days, title in ((1, "last 24 hours"), (7, "last 7 days"), (30, "last 30 days")):
        arr = decayed(ix, t, days / 2, True)
        lines.append(f"\n{title}")
        for kind, n in (("project", 5), ("file", k)):
            m = np.nonzero((ix.kind == kind) & (arr > 0))[0]
            top = m[np.argsort(-arr[m])][:n]
            if not len(top):
                continue
            names = [f"{ix.label[i]}" if kind == "project" else short(ix.paths[i]) for i in top]
            lines.append(f"  {kind}s: " + ", ".join(names))
    return "\n".join(lines)


def node_report(ix: Index, target: str) -> str:
    """One file or project: what it is, what it imports and is imported by, what it is worked on together with."""
    path = os.path.normpath(os.path.expanduser(target))
    i = ix.by_id.get(target, ix.by_id.get(f"file:{path}"))
    if i is None:
        p = project_of(ix, path)
        i = p if p >= 0 and ix.paths[p] == path else None
    if i is None:
        return f"{target}: not in the tree (not a known file or project path)."
    t = time.time()
    lines = [f"{ix.label[i]} ({ix.kind[i]})  {ix.paths[i] or ''}"]
    if ix.summary.get(i):
        lines.append(f"  {ix.summary[i]}")
    if ix.project[i] >= 0 and ix.project[i] != i:
        lines.append(f"  project: {ix.label[ix.project[i]]}  {short(ix.paths[ix.project[i]])}")
    t7, t30 = touches(ix, i, t, 7), touches(ix, i, t, 30)
    lines.append(f"  Claude touched it {t7}x in 7 days, {t30}x in 30 days")
    kids = [j for j in np.nonzero(ix.parent == i)[0] if ix.kind[j] in ("function", "fact")]
    fns = [ix.label[j] for j in kids if ix.kind[j] == "function"]
    if fns:
        lines.append("  defines: " + ", ".join(fns[:40]))
    for j in [j for j in kids if ix.kind[j] == "fact"][:5]:
        lines.append(f"  memory: \"{ix.label[j]}\"")
    imp = sorted(ix.imports.get(i, ()), key=lambda j: ix.paths[j] or "")
    if imp:
        lines.append("  imports / imported by: " + ", ".join(short(ix.paths[j]) for j in imp[:25]))
    tog = sorted(together(ix, i, t, DEFAULT["hub"]).items(), key=lambda kv: -kv[1])[:12]
    if tog:
        lines.append("  worked on together with: " + ", ".join(short(ix.paths[j]) for j, _ in tog))
    return "\n".join(lines)
