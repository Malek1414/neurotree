"""Local git repos -> project and file nodes, commit and edit events (history), blob ids for shared-code links."""
from __future__ import annotations

import math
import os
import re
import subprocess
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from ..kinds import classify, noise_factor
from ..privacy import SKIP_DIRS, is_excluded

GITHUB = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.I)
RENAME = re.compile(r"\{([^{}]*) => ([^{}]*)\}")
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
BOILERPLATE = {"license", "license.md", "license.txt", ".gitignore", "readme.md", "__init__.py", "package.json",
               "tsconfig.json", "requirements.txt", ".prettierrc", ".eslintrc.json", "next-env.d.ts"}


def git(repo, *args, timeout: int = 120) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                           errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def find_repos(roots, max_depth: int = 4, extra=()) -> list[Path]:
    """Git working trees under the roots (a root itself is never a project; nested repos are not entered)."""
    found = []
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if here != root and (".git" in dirnames or ".git" in filenames):
                found.append(here)
                dirnames[:] = []
                continue
            if len(here.relative_to(root).parts) >= max_depth or is_excluded(here, extra):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                           and not d.endswith((".app", ".photoslibrary", ".bundle"))]
    return sorted(set(found))


def normalize_remote(url: str) -> str | None:
    m = GITHUB.search(url.strip())
    return f"gh:{m.group(1).lower()}/{m.group(2).lower()}" if m else None


def numstat_path(p: str) -> str:
    """'src/{a.py => b.py}' or 'a.py => b.py' -> the path after the rename."""
    if "=>" not in p:
        return p
    if "{" in p:
        return re.sub(r"/{2,}", "/", RENAME.sub(lambda m: m.group(2), p)).strip("/")
    return p.split("=>", 1)[1].strip()


def project_id(repo: Path) -> tuple[str, str]:
    remote = git(repo, "config", "--get", "remote.origin.url").strip()
    key = normalize_remote(remote) if remote else None
    return (f"project:{key}" if key else f"project:dir:{repo}"), remote


def head_time(repo: Path) -> float:
    out = git(repo, "log", "-1", "--format=%ct").strip()
    return float(out) if out else 0.0


def pick_clones(repos: list[Path]) -> tuple[list[Path], dict[Path, list[Path]]]:
    """Several local clones of one GitHub repo -> index only the one with the newest commit; the others become
    aliases, so their paths still count for the project without duplicating its files."""
    by_project = defaultdict(list)
    for r in repos:
        by_project[project_id(r)[0]].append(r)
    canonical, clones = [], {}
    for rs in by_project.values():
        best = max(rs, key=lambda r: (head_time(r), -len(str(r))))
        canonical.append(best)
        clones[best] = sorted(r for r in rs if r != best)
    return sorted(canonical), clones


def index_repo(store, repo: Path, since_days: int, extra=(), now: float | None = None) -> dict:
    now = now or time.time()
    repo = Path(repo)
    pid, remote = project_id(repo)
    store.upsert_node(pid, "project", 2, repo.name, path=str(repo), remote=remote or None, source="git")
    kept, blobs, events = {}, {}, []
    for entry in git(repo, "ls-files", "-s", "-z").split("\0"):
        head, _, rel = entry.partition("\t")
        parts = head.split()
        if not rel or len(parts) < 2:
            continue
        p = repo / rel
        kind = classify(rel)
        if not kind or is_excluded(p, extra) or not p.is_file():
            continue
        fid = f"file:{p}"
        store.upsert_node(fid, "file", 3, p.name, parent=pid, path=str(p), project=pid, kind_detail=kind,
                          rel=rel, noise=noise_factor(rel))
        store.add_edge(pid, fid, "tree")
        kept[rel], blobs[fid] = fid, parts[1]
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if now - mtime <= since_days * 86400:
            events.append((fid, mtime, "modified", 0.5, "fs", None))
    ts = None
    log = git(repo, "log", f"--since={since_days}.days", "--max-count=3000", "--no-renames", "--numstat", "--format=@%ct",
                     timeout=180)
    for line in log.splitlines():
        if line.startswith("@"):
            ts = float(line[1:])
            continue
        cols = line.split("\t")
        if len(cols) == 3 and ts is not None:
            fid = kept.get(numstat_path(cols[2]))
            if fid:
                changed = sum(int(c) for c in cols[:2] if c.isdigit())
                events.append((fid, ts, "commit", math.log2(1 + changed) or 0.5, "git", None))
    store.add_events(events)
    return {"project": pid, "files": len(kept), "events": len(events), "kept": kept, "blobs": blobs}


def shared_code_edges(store, blobs: dict[str, str]) -> int:
    """Projects that contain the very same file (same git blob) get a 'shared_code' arc, weight = files shared."""
    files = {n["id"]: n for n in store.nodes("file")}
    by_blob = defaultdict(set)
    for fid, blob in blobs.items():
        n = files.get(fid)
        if not n or blob == EMPTY_BLOB or n["label"].lower() in BOILERPLATE or n["meta"].get("noise", 1) < 1:
            continue
        by_blob[blob].add(n["project"])
    pairs = Counter()
    for projects in by_blob.values():
        if 2 <= len(projects) <= 10:
            for a, b in combinations(sorted(projects), 2):
                pairs[(a, b)] += 1
    for (a, b), w in pairs.items():
        store.add_edge(a, b, "shared_code", float(w))
    return len(pairs)
