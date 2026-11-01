"""python -m neurotree <command>
  all                  index everything, score, export graph.json (runs at background priority)
  ask "question"       the projects, files and memory that hold the attention for a question
  now                  what you and Claude worked on in the last 24 hours, 7 and 30 days
  node PATH            one file or project: what it defines, imports, and is worked on together with
  bench                measure the attention settings on your past sessions (numbers only)"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .config import data_dir, load_settings


def background() -> None:
    """Darwin background priority: low CPU priority on the efficiency cores, throttled disk I/O; children
    (git, gh) inherit it. A full index then barely touches battery or fans."""
    if hasattr(os, "PRIO_DARWIN_BG"):
        try:
            os.setpriority(os.PRIO_DARWIN_PROCESS, 0, os.PRIO_DARWIN_BG)
        except OSError:
            pass


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="neurotree", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["all", "ask", "now", "node", "bench"])
    ap.add_argument("text", nargs="*", help="the question (ask) or a path (node)")
    ap.add_argument("--home", help="data folder (default: ~/Library/Application Support/Neurotree)")
    ap.add_argument("--settings", help="settings.json to use")
    ap.add_argument("--cwd", help="the folder you are working in (ask): its project gets a bias")
    ap.add_argument("-k", type=int, default=12, help="at most this many files (ask)")
    ap.add_argument("--json", action="store_true", help="JSON instead of text (ask)")
    ap.add_argument("--fresh", type=float, default=0, metavar="HOURS",
                    help="reindex first if the index is older than this (ask, now, node)")
    ap.add_argument("--foreground", action="store_true", help="index at normal priority (all)")
    a = ap.parse_args(argv)
    home = Path(a.home) if a.home else data_dir()
    if a.command == "all":
        if not a.foreground:
            background()
        from .pipeline import run_all
        status = run_all(load_settings(Path(a.settings)) if a.settings else None, home=home)
        failed = [k for k, v in status.items() if "error" in v]
        print(f"[neurotree] done, {len(status) - len(failed)} steps ok" + (f", failed: {', '.join(failed)}" if failed else ""))
        return
    db = home / "graph.db"
    from . import attend
    stale = not db.exists() or time.time() - db.stat().st_mtime > a.fresh * 3600 or not attend.has_keys(db)
    if a.command != "bench" and a.fresh and stale:
        background()
        from .pipeline import run_all
        print("[neurotree] index is stale, reindexing first ...", file=sys.stderr)
        run_all(load_settings(Path(a.settings)) if a.settings else None, home=home, log=lambda *_: None)
    if not db.exists():
        sys.exit("No index yet: run `python -m neurotree all` (about 2 minutes the first time).")
    if a.command == "bench":
        from .bench import main as bench
        bench(db, load_settings(Path(a.settings) if a.settings else None).claude_projects)
        return
    try:
        ix = attend.load(db, vectors=a.command == "ask")
    except attend.NoKeys as e:
        sys.exit(str(e))
    if a.command == "ask":
        if not a.text:
            sys.exit('ask needs a question: python -m neurotree ask "how does scoring work"')
        print(attend.report(ix, " ".join(a.text), a.cwd or os.getcwd(), k=a.k, as_json=a.json))
    elif a.command == "now":
        print(attend.now_report(ix))
    else:
        if not a.text:
            sys.exit("node needs a file or project path")
        print(attend.node_report(ix, " ".join(a.text)))


if __name__ == "__main__":
    main()
