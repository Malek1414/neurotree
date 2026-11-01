"""Map file paths seen in Claude sessions and memory onto graph nodes."""
from __future__ import annotations

import os
import re

from .kinds import classify, noise_factor
from .privacy import is_excluded

WORKTREE = re.compile(r"^(.*?)/\.claude/worktrees/[^/]+(/.*)?$")
GENERIC = {"src", "app", "apps", "lib", "libs", "test", "tests", "docs", "web", "server", "client", "core", "main",
           "scripts", "packages", "projects", "desktop", "documents", "code", "repo", "repos", "tmp", "build"}


class Translator:
    """Old or indirect paths -> where that file lives in the indexed tree now:
    a second clone -> the indexed clone; an agent worktree (<repo>/.claude/worktrees/<name>/x) -> <repo>/x;
    a project that has moved since (~/Desktop/simulacrum/x -> ~/Desktop/Projects/simulacrum/x), so Claude's
    attention from before a move still counts."""

    def __init__(self, roots, aliases=()):
        self.roots = sorted(roots, key=lambda r: -len(r))
        self.aliases = sorted(((a, c) for a, c in aliases or []), key=lambda x: -len(x[0]))
        self.by_name: dict[str, list] = {}
        for r in self.roots:
            name = os.path.basename(r).lower()
            if len(name) >= 4 and name not in GENERIC:
                self.by_name.setdefault(name, []).append(r)
        self.memo: dict[str, str] = {}

    def inside(self, path: str) -> bool:
        return any(path == r or path.startswith(r.rstrip("/") + "/") for r in self.roots)

    def __call__(self, path: str) -> str:
        if path in self.memo:
            return self.memo[path]
        p = path
        m = WORKTREE.match(p)
        if m:
            p = m.group(1) + (m.group(2) or "")
        for alias, canonical in self.aliases:
            if p == alias or p.startswith(alias.rstrip("/") + "/"):
                p = canonical + p[len(alias):]
                break
        if not self.inside(p) and not os.path.exists(p):
            p = self.relocate(p) or p
        self.memo[path] = p
        return p

    def relocate(self, path: str) -> str | None:
        """The deepest folder of `path` named like a known project whose remaining path exists there (or at least
        starts with a folder that exists there, for a file deleted since)."""
        parts = path.split("/")
        for i in range(len(parts) - 1, 0, -1):
            for root in self.by_name.get(parts[i].lower(), ()):
                rest = parts[i + 1:]
                cand = os.path.join(root, *rest) if rest else root
                if os.path.exists(cand) or (len(rest) > 1 and os.path.isdir(os.path.join(root, rest[0]))):
                    return cand
        return None


class PathIndex:
    def __init__(self, store, extra=()):
        self.store = store
        self.extra = tuple(extra)
        self.files = {n["path"]: n["id"] for n in store.nodes("file") if n["path"]}
        roots = [(n["path"], n["id"]) for n in store.nodes("project") if n["path"]]
        self.roots = sorted(roots, key=lambda r: -len(r[0]))
        self.by_name = {os.path.basename(r).lower(): r for r, _ in reversed(self.roots)}
        self.translate = Translator([r for r, _ in self.roots], store.get_meta("aliases", []))

    def project_of(self, path: str) -> str | None:
        path = self.translate(path)
        for root, pid in self.roots:
            if path == root or path.startswith(root.rstrip("/") + "/"):
                return pid
        return None

    def root_by_name(self, name: str | None) -> str | None:
        return self.by_name.get((name or "").lower())

    def resolve(self, raw, cwd: str | None = None) -> str | None:
        """Absolute, ~ or cwd-relative path -> file node (created for real files inside a project), else project,
        else None (outside every known project, excluded, or unparseable)."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        raw = raw.strip().strip("'\"")
        if raw.startswith("~"):
            p = os.path.expanduser(raw)
        elif os.path.isabs(raw):
            p = raw
        elif cwd:
            p = os.path.join(cwd, raw)
        else:
            return None
        p = self.translate(os.path.normpath(p))
        if p in self.files:
            return self.files[p]
        if is_excluded(p, self.extra):
            return None
        pid = self.project_of(p)
        if not pid:
            return None
        kind = classify(p)
        if kind and os.path.isfile(p):
            root = next(r for r, i in self.roots if i == pid)
            fid = f"file:{p}"
            self.store.upsert_node(fid, "file", 3, os.path.basename(p), parent=pid, path=p, project=pid,
                                   kind_detail=kind, rel=os.path.relpath(p, root),
                                   noise=noise_factor(os.path.relpath(p, root)), untracked=True)
            self.store.add_edge(pid, fid, "tree")
            self.files[p] = fid
            return fid
        return pid
