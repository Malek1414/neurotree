"""Local git repos -> project and file nodes, commit and edit events (history), blob ids for shared-code links."""
from __future__ import annotations

import hashlib
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


def git_dir(repo: Path) -> Path | None:
    """<repo>/.git, or the folder a worktree's or submodule's .git file points to."""
    d = Path(repo) / ".git"
    if d.is_dir():
        return d
    try:
        line = d.read_text(errors="replace").strip()
    except OSError:
        return None
    if line.startswith("gitdir:"):
        g = Path(line[7:].strip())
        return g if g.is_absolute() else (Path(repo) / g).resolve()
    return None


def _common(gd: Path) -> Path:
    try:
        rel = (gd / "commondir").read_text().strip()
        return (gd / rel).resolve()
    except OSError:
        return gd


def head_sha(repo: Path) -> str:
    """HEAD's commit read from the files in .git (no git process); falls back to `git rev-parse`."""
    gd = git_dir(repo)
    try:
        head = (gd / "HEAD").read_text().strip() if gd else ""
    except OSError:
        head = ""
    if head and not head.startswith("ref:"):
        return head
    if head.startswith("ref:"):
        ref = head[4:].strip()
        for base in (gd, _common(gd)):
            try:
                return (base / ref).read_text().strip()
            except OSError:
                pass
        try:
            for line in (_common(gd) / "packed-refs").read_text().splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
        except OSError:
            pass
    return git(repo, "rev-parse", "HEAD").strip()


def origin_url(repo: Path) -> str:
    """remote.origin.url from .git/config (no git process); falls back to `git config`."""
    gd = git_dir(repo)
    try:
        text = (_common(gd) / "config").read_text(errors="replace") if gd else None
    except OSError:
        text = None
    if text is None:
        return git(repo, "config", "--get", "remote.origin.url").strip()
    m = re.search(r'^\s*\[remote "origin"\][^\[]*?^\s*url\s*=\s*(\S+)', text, re.M | re.S)
    return m.group(1) if m else ""


def project_id(repo: Path) -> tuple[str, str]:
    remote = origin_url(repo)
    key = normalize_remote(remote) if remote else None
    return (f"project:{key}" if key else f"project:dir:{repo}"), remote


def head_time(repo: Path, cache=None) -> float:
    """Time of the newest commit, remembered per HEAD commit (asked for every clone on every run)."""
    sha = head_sha(repo) if cache else ""
    if sha:
        hit = cache.get("headtime", str(repo), sha)
        if hit is not None:
            return hit
    out = git(repo, "log", "-1", "--format=%ct").strip()
    t = float(out) if out else 0.0
    if sha:
        cache.put("headtime", str(repo), sha, t)
    return t


def pick_clones(repos: list[Path], cache=None) -> tuple[list[Path], dict[Path, list[Path]]]:
    """Several local clones of one GitHub repo -> index only the one with the newest commit; the others become
    aliases, so their paths still count for the project without duplicating its files."""
    by_project = defaultdict(list)
    for r in repos:
        by_project[project_id(r)[0]].append(r)
    canonical, clones = [], {}
    for rs in by_project.values():
        best = max(rs, key=lambda r: (head_time(r, cache) if len(rs) > 1 else 0.0, -len(str(r))))
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


STAMP_VERSION = "1"  # bump when index_repo or add_structure change what they write


def repo_stamp(repo: Path, since_days: int, extra=()) -> str | None:
    """What a repo's index depends on: HEAD, the tracked files that differ from it (with their mtimes) and the
    settings. Unchanged stamp -> the cached nodes, edges and events are still right. ~10-50 ms per repo instead of
    a full index (ls-files, stat, a year of log, parsing every module). --no-optional-locks: never rewrite the
    index (an index rewrite would change nothing here but wake other tools)."""
    head = head_sha(repo)
    status = git(repo, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=no")
    if not head and not status:
        return None
    h = hashlib.sha1(f"{STAMP_VERSION}|{head}|{since_days}|{sorted(map(str, extra))}|".encode())
    h.update(status.encode("utf-8", "replace"))
    for entry in status.split("\0"):
        rel = entry[3:] if len(entry) > 3 else ""
        if rel:
            try:
                h.update(f"|{rel}:{(repo / rel).stat().st_mtime_ns}".encode("utf-8", "replace"))
            except OSError:
                h.update(f"|{rel}:gone".encode("utf-8", "replace"))
    return h.hexdigest()


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
