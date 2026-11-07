"""python -m neurotree.evaljson --split tune|test: the shipped settings on one side of bench's time split, as JSON.

For optimisers (heartgold's metric mode). Prints numbers only, never a prompt. Inputs are frozen so every candidate
is scored on exactly the same questions: the index is a copy (NEUROTREE_EVAL_DB, default ~/.neurotree-eval/graph.db)
and only sessions that started before the index was built (NEUROTREE_EVAL_CUTOFF, default: the index's build time)
count. The split is bench's own: whole sessions, the older 60 % tune, the newer 40 % test.

The test split is held out: it is printed only when HEARTGOLD_HOLDOUT=1, which the optimiser sets for its own
checks and never for the agents it runs. Agents tune on the tune split.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import attend as A
from .bench import agent_questions, questions, run


def split(qs: list) -> tuple[list, list]:
    first: dict = {}
    for q in qs:
        first[q["session"]] = min(first.get(q["session"], q["as_of"]), q["as_of"])
    starts = sorted(set(first.values()))
    at = starts[int(len(starts) * 0.6)] if starts else 0
    return [q for q in qs if first[q["session"]] < at], [q for q in qs if first[q["session"]] >= at]


def per_question(ix, qs, cfg, memo) -> list[dict]:
    return [run(ix, [q], cfg, memo) for q in qs]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="neurotree.evaljson")
    ap.add_argument("--split", choices=["tune", "test"], default="tune")
    ap.add_argument("--settings", help="settings JSON (attend cfg overrides); default: the shipped settings")
    a = ap.parse_args(argv)
    if a.split == "test" and os.environ.get("HEARTGOLD_HOLDOUT") != "1":
        sys.exit("the test split is held out; tune on --split tune")
    db = os.environ.get("NEUROTREE_EVAL_DB", str(Path.home() / ".neurotree-eval" / "graph.db"))
    root = os.environ.get("NEUROTREE_EVAL_SESSIONS", str(Path.home() / ".claude" / "projects"))
    ix = A.load(db)
    cutoff = float(os.environ.get("NEUROTREE_EVAL_CUTOFF") or os.path.getmtime(db))
    qs = [q for q in questions(ix, root) + agent_questions(ix, root) if q["as_of"] and q["as_of"] < cutoff]
    qs.sort(key=lambda x: x["as_of"])
    tune, test = split(qs)
    data = tune if a.split == "tune" else test
    cfg = json.loads(Path(a.settings).read_text()) if a.settings else {}
    memo: dict = {}
    rows = per_question(ix, data, cfg, memo)
    keys = ["P@1", "P@3", "R@10", "MRR", "nDCG@10"]
    out = {"split": a.split, "n": len(rows), **{k: sum(r[k] for r in rows) / max(1, len(rows)) for k in keys},
           # per-question scores, in a fixed order, so a caller can run a paired bootstrap; no text, no paths
           "per_question": {k: [round(r[k], 6) for r in rows] for k in ("P@1", "nDCG@10")}}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
