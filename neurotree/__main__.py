"""python -m neurotree all [--home DIR] [--settings FILE]: index everything, score, export graph.json."""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_settings
from .pipeline import run_all


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="neurotree")
    ap.add_argument("command", choices=["all"])
    ap.add_argument("--home", help="data folder (default: ~/Library/Application Support/Neurotree)")
    ap.add_argument("--settings", help="settings.json to use")
    a = ap.parse_args(argv)
    status = run_all(load_settings(Path(a.settings)) if a.settings else None, home=a.home)
    failed = [k for k, v in status.items() if "error" in v]
    print(f"[neurotree] done, {len(status) - len(failed)} steps ok" + (f", failed: {', '.join(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
