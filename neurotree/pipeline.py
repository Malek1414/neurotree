"""One full index run: sources -> areas -> scores -> export. Each step is isolated (a failure is recorded in
status.json, the rest still runs) and the new database replaces the old one only when it is complete."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .areas import assign_areas
from .config import Settings, data_dir, load_settings
from .export import export
from .paths import PathIndex
from .scoring import score
from .sources.claude_mem import index_memory
from .sources.claude_sessions import index_sessions
from .sources.files import index_files
from .sources.git_repos import find_repos, index_repo, shared_code_edges
from .sources.github import index_github
from .store import Store
from .structure import add_structure


def run_all(settings: Settings | None = None, home=None, now: float | None = None, log=print) -> dict:
    settings = settings or load_settings()
    home = Path(home) if home else data_dir()
    home.mkdir(parents=True, exist_ok=True)
    building = home / "graph.db.building"
    for stale in (building, Path(f"{building}-wal"), Path(f"{building}-shm")):
        stale.unlink(missing_ok=True)
    store = Store(building, wal=False)
    extra, on = tuple(settings.exclude), settings.sources
    status, repos = {}, []

    def step(name, fn):
        t0 = time.time()
        try:
            result = fn() or {}
        except Exception as e:  # noqa: BLE001 - one broken source must not stop the others
            result = {"ok": 0, "errors": 1, "error": f"{type(e).__name__}: {e}"[:300]}
        result["seconds"] = round(time.time() - t0, 1)
        status[name] = result
        store.commit()
        log(f"[{name}] " + ", ".join(f"{k} {v}" for k, v in result.items()))

    def git_step():
        repos.extend(find_repos(settings.repo_roots, extra=extra))
        blobs, files, imports, functions = {}, 0, 0, 0
        for repo in repos:
            info = index_repo(store, repo, settings.history_days, extra, now)
            st = add_structure(store, repo, info["kept"])
            files, imports, functions = files + info["files"], imports + st["imports"], functions + st["functions"]
            blobs.update(info["blobs"])
        return {"ok": len(repos), "files": files, "imports": imports, "functions": functions,
                "shared_code": shared_code_edges(store, blobs)}

    if on.get("git", True):
        step("git", git_step)
    if on.get("github", True):
        step("github", lambda: index_github(store))
    if on.get("files", True):
        step("files", lambda: index_files(store, settings.doc_roots, {str(r) for r in repos},
                                          settings.history_days, extra, now))
    index = PathIndex(store, extra)
    if on.get("claude_sessions", True):
        step("claude_sessions", lambda: index_sessions(store, index, settings.claude_projects))
    if on.get("claude_mem", True):
        step("claude_mem", lambda: index_memory(store, index, settings.claude_mem_db))
    step("areas", lambda: assign_areas(store, home / "areas.json", owner=settings.owner_name))
    step("score", lambda: {"ok": score(store, now)})
    store.set_meta("status", status)
    step("export", lambda: export(store, home / "graph.json", now=now))
    store.close()
    os.replace(building, home / "graph.db")
    for stale in (home / "graph.db-wal", home / "graph.db-shm"):
        stale.unlink(missing_ok=True)
    (home / "status.json").write_text(json.dumps({"finished_at": time.time(), "steps": status}, indent=1))
    return status
