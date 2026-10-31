"""Documents and Desktop files outside git repos -> folder projects, document nodes, edit-time events,
and 'mention' links from notes to the repos they name."""
from __future__ import annotations

import os
import re
import time
from collections import Counter
from pathlib import Path

from ..kinds import classify
from ..privacy import SKIP_DIRS, is_excluded

BIG_FOLDER = 150          # a top-level folder holding more documents than this splits into its subfolders
MAX_BYTES = 50_000_000
MENTION_BYTES = 200_000


def walk_documents(roots, repo_roots: set, extra=()) -> list[tuple[str, str]]:
    found = []
    for root in roots:
        root = str(Path(root).expanduser())
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath in repo_roots or is_excluded(dirpath, extra):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
                           and not d.endswith((".app", ".photoslibrary", ".bundle"))
                           and os.path.join(dirpath, d) not in repo_roots]
            for name in filenames:
                p = os.path.join(dirpath, name)
                if name.startswith(".") or classify(p) != "doc" or is_excluded(p, extra):
                    continue
                try:
                    if os.path.getsize(p) > MAX_BYTES:
                        continue
                except OSError:
                    continue
                found.append((root, p))
    return found


def folder_project(root: str, path: str, counts: Counter) -> tuple[str | None, str]:
    """-> (project folder, label); loose files directly in a root get (None, '<root> (loose files)')."""
    parts = Path(path).relative_to(root).parts[:-1]
    if not parts:
        return None, f"{Path(root).name} (loose files)"
    first = os.path.join(root, parts[0])
    if len(parts) >= 2 and counts[first] > BIG_FOLDER:
        return os.path.join(first, parts[1]), parts[1]
    return first, parts[0]


def index_files(store, roots, repo_roots: set, since_days: int, extra=(), now: float | None = None) -> dict:
    now = now or time.time()
    docs = walk_documents(roots, set(repo_roots), extra)
    counts = Counter()
    for root, p in docs:
        parts = Path(p).relative_to(root).parts
        if len(parts) > 1:
            counts[os.path.join(root, parts[0])] += 1
    names = {n["label"].lower(): n["id"] for n in store.nodes("project") if len(n["label"]) >= 4}
    mention = (re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\b", re.I)
               if names else None)
    events, mentions = [], 0
    for root, p in docs:
        folder, label = folder_project(root, p, counts)
        pid = f"project:dir:{folder}" if folder else f"project:loose:{root}"
        store.upsert_node(pid, "project", 2, label, path=folder, source="files")
        fid = f"file:{p}"
        store.upsert_node(fid, "file", 3, os.path.basename(p), parent=pid, path=p, project=pid, kind_detail="doc",
                          rel=os.path.relpath(p, folder or root), noise=1.0)
        store.add_edge(pid, fid, "tree")
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        if now - mtime <= since_days * 86400:
            events.append((fid, mtime, "modified", 0.5, "fs", None))
        if mention and p.lower().endswith((".md", ".txt")):
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read(MENTION_BYTES)
            except OSError:
                continue
            for hit in {m.lower() for m in mention.findall(text)}:
                target = names.get(hit)
                if target and target != pid:
                    store.add_edge(fid, target, "mention")
                    mentions += 1
    store.add_events(events)
    return {"ok": len(docs), "events": len(events), "mentions": mentions}
