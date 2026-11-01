"""python -m neurotree bench: measures the attention settings on your own past. Prints numbers only.

Each earlier Claude Code session is one question: its first prompt (read in memory here, never written or printed)
and the folder it ran in. The answer is the set of files Claude then edited that already existed. Every setting is
replayed as of the moment the session started, so it sees neither the session's own touches nor memory written
later. The older 60 % of the sessions tune the settings, the newer 40 % only test them."""
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime

import numpy as np

from . import attend as A
from .paths import Translator
from .sources.claude_sessions import EDIT_TOOLS, PATH_KEYS, session_files

SKIP = ("claude-mem-observer", "-private-tmp-", "-private-var-", "/subagents/")
ABS = re.compile(r"(?:~|/Users|/private|/tmp|/opt|/var)/[^\s'\"`),;]+")


def _ts(v) -> float | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _prompt(m: dict) -> str | None:
    if m.get("type") != "user" or m.get("isSidechain") or m.get("isMeta"):
        return None
    c = (m.get("message") or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return None
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    if not isinstance(c, str):
        return None
    c = c.strip()
    if not c or c.startswith(("<", "Caveat:", "This session is being continued", "[Request interrupted")):
        return None
    return c


def resolve(ix: A.Index, tr: Translator, raw: str, cwd: str | None) -> int | None:
    p = raw if os.path.isabs(raw) else os.path.join(cwd or "/", raw)
    return ix.by_id.get(f"file:{tr(os.path.normpath(os.path.expanduser(p)))}")


def questions(ix: A.Index, root: str, min_words: int = 6, per_session: int = 8) -> list[dict]:
    """Every prompt of a main session that led to edits is a question; its answer is the files edited before the
    next prompt that existed when it was asked."""
    row = ix.db.execute("SELECT value FROM meta WHERE key='aliases'").fetchone()
    tr = Translator([r for r, _ in ix.project_roots], json.loads(row[0]) if row else [])
    out, seen = [], set()
    for f in session_files(root):
        if any(s in f for s in SKIP):
            continue
        asked = []
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"user"' in line and '"tool_result"' not in line:
                        try:
                            m = json.loads(line)
                        except ValueError:
                            continue
                        text = _prompt(m)
                        if text and len(text.split()) >= min_words and text not in seen:
                            seen.add(text)
                            asked.append({"q": text[:1000], "as_of": _ts(m.get("timestamp")), "cwd": m.get("cwd"),
                                          "edited": [], "session": f})
                    elif asked and '"tool_use"' in line:
                        try:
                            m = json.loads(line)
                        except ValueError:
                            continue
                        for c in (m.get("message") or {}).get("content") or []:
                            if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in EDIT_TOOLS:
                                raw = next((c["input"][k] for k in PATH_KEYS if isinstance((c.get("input") or {}).get(k), str)), None)
                                if raw:
                                    asked[-1]["edited"].append(raw)
        except OSError:
            continue
        kept = 0
        for x in asked:
            if not x["as_of"] or kept >= per_session:
                continue
            truth = {i for i in (resolve(ix, tr, raw, x["cwd"]) for raw in x.pop("edited")) if i is not None
                     and ix.first_ts[i] < x["as_of"]}
            if truth:
                out.append({**x, "truth": truth})
                kept += 1
    return sorted(out, key=lambda x: x["as_of"])


def agent_questions(ix: A.Index, root: str, min_words: int = 6) -> list[dict]:
    """Subagent prompts are questions written the way Claude will ask Neurotree. Paths are cut out of the prompt
    (else the answer is in the question); the answer is the files the agent edited, else the files it read."""
    row = ix.db.execute("SELECT value FROM meta WHERE key='aliases'").fetchone()
    tr = Translator([r for r, _ in ix.project_roots], json.loads(row[0]) if row else [])
    out = []
    for f in session_files(root):
        if "/subagents/" not in f or any(s in f for s in SKIP[:3]):
            continue
        q = as_of = cwd = None
        edited, read = [], []
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if q is None and '"user"' in line:
                        try:
                            m = json.loads(line)
                        except ValueError:
                            continue
                        c = (m.get("message") or {}).get("content")
                        if isinstance(c, list):
                            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                        if isinstance(c, str) and c.strip():
                            q, as_of, cwd = ABS.sub(" ", c)[:1500], _ts(m.get("timestamp")), m.get("cwd")
                    elif q is not None and '"tool_use"' in line:
                        try:
                            m = json.loads(line)
                        except ValueError:
                            continue
                        for c in (m.get("message") or {}).get("content") or []:
                            if isinstance(c, dict) and c.get("type") == "tool_use":
                                raw = next((c["input"][k] for k in PATH_KEYS if isinstance((c.get("input") or {}).get(k), str)), None)
                                if raw and c.get("name") in EDIT_TOOLS:
                                    edited.append(raw)
                                elif raw and c.get("name") in ("Read", "mcp__plugin_mozcode_mozcode__code_read"):
                                    read.append(raw)
        except OSError:
            continue
        if not q or not as_of or len(q.split()) < min_words:
            continue
        for paths in (edited, read):
            truth = {i for i in (resolve(ix, tr, raw, cwd) for raw in paths) if i is not None and ix.first_ts[i] < as_of}
            if truth:
                out.append({"q": q, "as_of": as_of, "cwd": cwd, "truth": truth, "session": f})
                break
    return sorted(out, key=lambda x: x["as_of"])


def score_projects(ranked: list, truth_projects: set) -> dict:
    return {"P@1": float(bool(set(ranked[:1]) & truth_projects)), "P@3": float(bool(set(ranked[:3]) & truth_projects))}


def score_ranking(ranked: list, truth: set) -> dict:
    hits = [1.0 if u in truth else 0.0 for u in ranked[:10]]
    first = next((r for r, u in enumerate(ranked, 1) if u in truth), None)
    dcg = sum(h / math.log2(r + 2) for r, h in enumerate(hits))
    ideal = sum(1 / math.log2(r + 2) for r in range(min(10, len(truth))))
    return {"R@5": len(truth & set(ranked[:5])) / len(truth), "R@10": len(truth & set(ranked[:10])) / len(truth),
            "MRR": 1.0 / first if first else 0.0, "nDCG@10": dcg / ideal}


def run(ix: A.Index, qs: list, cfg: dict | None, memo: dict, mode: str = "attend") -> dict:
    """Mean metrics over the questions; for attend also how many files the 80 % mass cut keeps and its recall."""
    rows, kept, kept_recall = [], [], []
    files = np.nonzero(ix.kind == "file")[0]
    for x in qs:
        if mode == "attend":
            res = A.attend(ix, x["q"], x["cwd"], as_of=x["as_of"], cfg=cfg, explain=False, memo=memo)
            ranked = [u for u, _ in res["units"] if ix.kind[u] == "file"]
            masses = [m for u, m in res["units"] if ix.kind[u] == "file"]
            total, acc, cut = sum(masses), 0.0, []
            for u, m in zip(ranked, masses):
                if len(cut) >= 12 or (acc >= 0.8 * total and len(cut) >= 3):
                    break
                cut.append(u)
                acc += m
            kept.append(len(cut))
            kept_recall.append(len(x["truth"] & set(cut)) / len(x["truth"]))
            proj = {}
            for u, m in res["units"]:
                p = u if ix.kind[u] == "project" else int(ix.project[u])
                proj[p] = proj.get(p, 0.0) + m
            pranked = sorted(proj, key=lambda p: -proj[p])
        else:
            key = ("prior", x["as_of"], tuple(A.DEFAULT["scales"]))
            if key not in memo:
                memo[key] = A.prior(ix, x["as_of"])
            pr = memo[key][files]
            if mode == "prior_cwd":
                here = A.project_of(ix, x["cwd"])
                pr = pr + 10.0 * (ix.project[files] == here) if here >= 0 else pr
            ranked = [int(files[j]) for j in np.argsort(-pr)[:50]]
            pranked = list(dict.fromkeys(int(ix.project[u]) for u in ranked))
        tp = {int(ix.project[t]) for t in x["truth"]}
        canon = lambda u: min(ix.copy_of[u]) if u in ix.copy_of else u  # noqa: E731 - a copy of the answer counts
        ranked_c = list(dict.fromkeys(canon(u) for u in ranked))
        rows.append({**score_ranking(ranked_c, {canon(t) for t in x["truth"]}), **score_projects(pranked, tp)})
    out = {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}
    out["±"] = 1.96 * float(np.std([r["nDCG@10"] for r in rows], ddof=1)) / math.sqrt(len(rows)) if len(rows) > 1 else 0.0
    if kept:
        out["files@80%"] = float(np.mean(kept))
        out["R@80%"] = float(np.mean(kept_recall))
    return out


LADDER = [
    ("today: importance ranking, no question", None, "prior"),
    ("today + the project you are in", None, "prior_cwd"),
    ("lexical head only", {"semantic": 0, "prior": 0, "cwd": 0, "graph": 0}, "attend"),
    ("semantic head only", {"lexical": 0, "prior": 0, "cwd": 0, "graph": 0}, "attend"),
    ("both heads (multi-head)", {"prior": 0, "cwd": 0, "graph": 0}, "attend"),
    ("+ prior bias, one 30-day scale", {"cwd": 0, "graph": 0, "scales": (30,)}, "attend"),
    ("+ prior bias, four scales 1/7/30/120 d", {"cwd": 0, "graph": 0}, "attend"),
    ("+ project bias", {"graph": 0}, "attend"),
    ("+ graph step, 1 round (all of the above)", {}, "attend"),
    ("graph step: imports only", {"rel_together": 0}, "attend"),
    ("graph step: worked-on-together only", {"rel_import": 0}, "attend"),
    ("graph step: 2 rounds", {"hops": 2}, "attend"),
    ("graph step: 3 rounds", {"hops": 3}, "attend"),
]
GRID = {"tau_lex": [0.04, 0.08, 0.15, 0.3], "semantic": [0.0, 0.25, 0.5, 1.0], "tau_sem": [0.02, 0.04, 0.08],
        "prior": [0.5, 1.0, 1.5, 2.0], "cwd": [0.0, 0.75, 1.5, 3.0], "graph": [0.0, 0.08, 0.15, 0.3],
        "rel_together": [0.0, 0.5, 1.0, 2.0]}


def tune(ix: A.Index, qs: list, memo: dict, start: dict, passes: int = 2, log=print) -> dict:
    """Coordinate ascent on nDCG@10: one setting at a time, keep the best value, two passes."""
    best = dict(start)
    best_score = run(ix, qs, best, memo)["nDCG@10"]
    for _ in range(passes):
        for key, values in GRID.items():
            for v in values:
                if best.get(key, A.DEFAULT[key]) == v:
                    continue
                s = run(ix, qs, {**best, key: v}, memo)["nDCG@10"]
                if s > best_score + 1e-4:
                    best, best_score = {**best, key: v}, s
        log(f"  tuned: nDCG@10 {best_score:.3f} with {best}")
    return best


def table(rows: list) -> str:
    cols = ["P@1", "P@3", "R@5", "R@10", "MRR", "nDCG@10", "±", "files@80%", "R@80%"]
    w = max(len(r[0]) for r in rows)
    out = ["  " + "".ljust(w) + "".join(c.rjust(10) for c in cols)]
    for name, m in rows:
        out.append("  " + name.ljust(w) + "".join((f"{m[c]:.3f}" if c in m and c != "files@80%" else
                                                     f"{m[c]:.1f}" if c in m else "–").rjust(10) for c in cols))
    return "\n".join(out)


def main(db_path, root: str, log=print, do_tune: bool = True, track: str = "all") -> dict:
    t0 = time.time()
    ix = A.load(db_path)
    qs = {"prompts": questions, "agents": agent_questions}.get(track, lambda i, r: questions(i, r) + agent_questions(i, r))(ix, root)
    qs.sort(key=lambda x: x["as_of"])
    starts = sorted({min(q["as_of"] for q in qs if q["session"] == s) for s in {q["session"] for q in qs}})
    split_at = starts[int(len(starts) * 0.6)] if starts else 0  # whole sessions on one side of the split
    first = {s: min(q["as_of"] for q in qs if q["session"] == s) for s in {q["session"] for q in qs}}
    train = [q for q in qs if first[q["session"]] < split_at]
    test = [q for q in qs if first[q["session"]] >= split_at]
    log(f"[bench] {track}: {len(qs)} past questions with known answers, from {len(first)} sessions ({len(train)} tune, {len(test)} test, "
        f"{sum(len(q['truth']) for q in qs) / max(1, len(qs)):.1f} files per answer)")
    memo = {}
    result = {}
    for split, data in (("tune", train), ("test", test)):
        rows = [(name, run(ix, data, cfg, memo, mode)) for name, cfg, mode in LADDER]
        log(f"\n[bench] each step adds one idea ({split} sessions)\n" + table(rows))
        result[split] = rows
    if do_tune:
        log("\n[bench] tuning on the older sessions")
        best = tune(ix, train, memo, {}, log=log)
        rows = [("defaults", run(ix, test, {}, memo)), ("tuned", run(ix, test, best, memo))]
        log("\n[bench] defaults vs tuned, on the newer sessions only\n" + table(rows))
        result["tuned"] = best
    log(f"\n[bench] {time.time() - t0:.0f} s")
    return result
