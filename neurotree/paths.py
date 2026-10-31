"""Map file paths seen in Claude sessions and memory onto graph nodes."""
from __future__ import annotations

import os

from .kinds import classify, noise_factor
from .privacy import is_excluded


class PathIndex:
    def __init__(self, store, extra=()):
        self.store = store
        self.extra = tuple(extra)
        self.files = {n["path"]: n["id"] for n in store.nodes("file") if n["path"]}
        roots = [(n["path"], n["id"]) for n in store.nodes("project") if n["path"]]
        self.roots = sorted(roots, key=lambda r: -len(r[0]))
        self.by_name = {os.path.basename(r).lower(): r for r, _ in reversed(self.roots)}

    def project_of(self, path: str) -> str | None:
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
        p = os.path.normpath(p)
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
