# Neurotree Indexer Implementation Plan (plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python indexer that reads Malek's repos, Claude Code sessions, claude-mem memory and documents (read-only), scores every node (attention first), groups projects into life areas and exports `graph.json` for the 3D viewer.

**Architecture:** One package `neurotree`: source adapters write nodes/edges/events into a fresh SQLite file; `areas` clusters projects by co-attention; `scoring` turns events into percentile-ranked signals and importance; `export` writes the viewer JSON. `pipeline.run_all` runs every step isolated and swaps the finished database in atomically.

**Tech Stack:** Python 3.13 (uv venv), stdlib `sqlite3`/`ast`/`json`, `networkx` (Louvain), `pytest`.

## Global Constraints
- Read-only towards every source; claude-mem is opened with `mode=ro`.
- Never read: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.kube`, `~/.docker`, `~/Library`; names `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.kdbx`, `*.keychain*`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*`, `*credential*`, `*secret*`, `.netrc`, `.npmrc`, `.pypirc`.
- From Claude sessions keep only file paths, times, session ids (never message text). From claude-mem keep only title, project, file lists, time.
- Weights: attention 0.45, history 0.25, structure 0.20, runtime 0.10. Half-lives: attention 30 d, history 90 d. Noise: lockfile/generated/vendored/minified/fixtures 0.1, tests 0.5. Structure floor 0.15. Area confidence < 0.4 -> Unsorted.
- Data lives in `~/Library/Application Support/Neurotree/` (override with `NEUROTREE_HOME`). No personal data in git.
- Spec deviations (decided while planning, also written into the spec): `.key` is treated as a secret (Keynote decks are skipped); parents roll up their children's raw attention/history and are ranked within their own level; CLI is `python -m neurotree all`; `areas.json` has a `reviewed` flag and stays a fresh proposal until Malek reviews it.

## File Structure
```
pyproject.toml                    deps + pytest config
neurotree/__init__.py             version
neurotree/__main__.py             CLI: python -m neurotree all
neurotree/config.py               Settings, data_dir()
neurotree/privacy.py              is_excluded(), SKIP_DIRS
neurotree/kinds.py                classify(), noise_factor()
neurotree/store.py                Store (SQLite)
neurotree/paths.py                PathIndex: raw path -> node id
neurotree/structure.py            import edges, function nodes, PageRank
neurotree/sources/__init__.py
neurotree/sources/git_repos.py    repos, files, commits, shared code
neurotree/sources/github.py       GitHub-only repos via gh
neurotree/sources/files.py        documents outside repos, mentions
neurotree/sources/claude_sessions.py
neurotree/sources/claude_mem.py
neurotree/areas.py                co-attention, Louvain, areas.json
neurotree/scoring.py              signals -> importance
neurotree/export.py               graph.json
neurotree/pipeline.py             run_all()
tests/conftest.py                 fixture world (2 repos, docs, session, memory db)
tests/test_*.py
```

---

### Task 1: Project skeleton, settings, privacy, kinds

**Files:** Create `pyproject.toml`, `neurotree/__init__.py`, `neurotree/config.py`, `neurotree/privacy.py`, `neurotree/kinds.py`, `tests/test_privacy.py`

**Interfaces:**
- Produces: `Settings` dataclass (fields `repo_roots, doc_roots, claude_projects, claude_mem_db, sources, exclude, history_days, owner_name`), `load_settings(path=None) -> Settings`, `data_dir() -> Path`; `is_excluded(path, extra=()) -> bool`, `is_secret_name(name) -> bool`, `SKIP_DIRS: set[str]`; `classify(path) -> "code"|"doc"|None`, `noise_factor(rel) -> float`.

- [ ] **Step 1: Write the failing test**

**File:** `tests/test_privacy.py`
```python
from pathlib import Path

from neurotree.kinds import classify, noise_factor
from neurotree.privacy import is_excluded, is_secret_name

HOME = Path.home()


def test_secret_names():
    for name in [".env", ".env.local", "server.pem", "id_rsa", "id_ed25519.pub", "aws-credentials.json",
                 "secret-plan.md", "talk.key", ".npmrc"]:
        assert is_secret_name(name), name
    for name in ["core.py", "id_mapping.py", "notes.md", "keynote.pdf"]:
        assert not is_secret_name(name), name


def test_excluded_paths():
    assert is_excluded(HOME / ".ssh" / "config")
    assert is_excluded(HOME / "Library" / "Mobile Documents" / "x.md")
    assert is_excluded("/repo/node_modules/react/index.js")
    assert is_excluded("/repo/.venv/lib/site.py")
    assert is_excluded("/repo/app/.env")
    assert is_excluded("/private/stuff/plan.md", extra=["/private/stuff"])
    assert not is_excluded("/repo/app/main.py")
    assert not is_excluded(HOME / ".claude" / "projects" / "-x" / "s.jsonl")


def test_classify_and_noise():
    assert classify("a/b.py") == "code" and classify("x.TSX") == "code" and classify("pyproject.toml") == "code"
    assert classify("notes.md") == "doc" and classify("slides.pptx") == "doc"
    assert classify("photo.jpg") is None and classify("talk.key") is None
    assert noise_factor("package-lock.json") == 0.1
    assert noise_factor("web/vendor/lib.js") == 0.1
    assert noise_factor("dist2/app.min.js") == 0.1
    assert noise_factor("tests/test_core.py") == 0.5
    assert noise_factor("src/app.test.ts") == 0.5
    assert noise_factor("pkg/core.py") == 1.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_privacy.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'neurotree'`)

- [ ] **Step 3: Implement**

**File:** `pyproject.toml`
```toml
[project]
name = "neurotree"
version = "0.1.0"
description = "A 3D attention tree of everything on this Mac"
requires-python = ">=3.12"
dependencies = ["networkx>=3.3"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

**File:** `neurotree/__init__.py`
```python
"""Neurotree: a 3D attention tree of Malek's Mac (indexer)."""
__version__ = "0.1.0"
```

**File:** `neurotree/config.py`
```python
"""Settings and the one folder Neurotree writes to."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()


def data_dir() -> Path:
    d = Path(os.environ.get("NEUROTREE_HOME", HOME / "Library" / "Application Support" / "Neurotree"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Settings:
    repo_roots: list = field(default_factory=lambda: [str(HOME / "Desktop"), str(HOME / "Documents")])
    doc_roots: list = field(default_factory=lambda: [str(HOME / "Desktop"), str(HOME / "Documents")])
    claude_projects: str = str(HOME / ".claude" / "projects")
    claude_mem_db: str = str(HOME / ".claude-mem" / "claude-mem.db")
    sources: dict = field(default_factory=lambda: {"git": True, "github": True, "files": True,
                                                   "claude_sessions": True, "claude_mem": True})
    exclude: list = field(default_factory=list)
    history_days: int = 365
    owner_name: str = "Malek"


def load_settings(path: Path | None = None) -> Settings:
    """settings.json in the data folder overrides the defaults key by key."""
    path = Path(path) if path else data_dir() / "settings.json"
    s = Settings()
    if path.exists():
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            if hasattr(s, key):
                setattr(s, key, value)
    return s
```

**File:** `neurotree/privacy.py`
```python
"""Hard exclusions: paths Neurotree never reads, whatever the settings say."""
from __future__ import annotations

import fnmatch
from pathlib import Path

HOME = Path.home()
NEVER_DIRS = tuple(HOME / d for d in (".ssh", ".gnupg", ".aws", ".kube", ".docker", "Library"))
SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx", "*.keychain", "*.keychain-db",
                   "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "*credential*", "*secret*",
                   ".netrc", ".npmrc", ".pypirc")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache", "target",
             ".tox", ".mypy_cache", ".pytest_cache", "Pods", "DerivedData", ".gradle", ".idea", ".turbo"}


def is_secret_name(name: str) -> bool:
    n = name.lower()
    return any(fnmatch.fnmatch(n, p) for p in SECRET_PATTERNS)


def is_excluded(path, extra=()) -> bool:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.absolute()
    for d in NEVER_DIRS:
        if p == d or d in p.parents:
            return True
    if is_secret_name(p.name) or any(part in SKIP_DIRS for part in p.parts):
        return True
    s = str(p)
    for pattern in extra:
        e = str(Path(pattern).expanduser())
        if s == e or s.startswith(e.rstrip("/") + "/") or fnmatch.fnmatch(s, e):
            return True
    return False
```

**File:** `neurotree/kinds.py`
```python
"""What a file is (code or document) and how much of it is noise."""
from __future__ import annotations

import os

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".swift", ".rs", ".go", ".java", ".kt", ".rb",
            ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".sql", ".sh", ".html", ".css", ".scss", ".vue", ".svelte",
            ".ipynb", ".json", ".yaml", ".yml", ".toml"}
DOC_EXT = {".md", ".txt", ".pdf", ".docx", ".pptx", ".pages", ".xlsx", ".csv", ".rtf"}
LOCK_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "uv.lock",
              "pipfile.lock", "gemfile.lock", "composer.lock", "bun.lockb"}
NOISE_DIRS = ("vendor", "third_party", "generated", "fixtures", "__snapshots__")


def classify(path) -> str | None:
    ext = os.path.splitext(str(path))[1].lower()
    if ext in CODE_EXT:
        return "code"
    if ext in DOC_EXT:
        return "doc"
    return None


def noise_factor(rel: str) -> float:
    """0.1 for lockfiles, minified, vendored, generated and fixture files; 0.5 for tests; else 1."""
    low = "/" + rel.lower().strip("/")
    name = low.rsplit("/", 1)[-1]
    if name in LOCK_NAMES or name.endswith(".lock") or ".min." in name or any(f"/{d}/" in low for d in NOISE_DIRS):
        return 0.1
    if "/tests/" in low or "/test/" in low or "/__tests__/" in low or name.startswith("test_") \
            or ".test." in name or ".spec." in name or name.endswith("_test.py"):
        return 0.5
    return 1.0
```

- [ ] **Step 4: Run the tests**

Run: `uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python networkx pytest && .venv/bin/python -m pytest tests/test_privacy.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit** `git add -A && git commit -m "Indexer skeleton: settings, privacy exclusions, file kinds"`

---

### Task 2: SQLite store

**Files:** Create `neurotree/store.py`, `tests/test_store.py`

**Interfaces:**
- Produces: `Store(path, wal=True)` with `upsert_node(id, kind, level, label, parent=None, path=None, project=None, **meta)`, `set_parent(id, parent)`, `node(id) -> dict|None`, `nodes(kind=None) -> list[dict]` (dict keys `id kind level parent label path project meta`), `add_edge(src, dst, kind, weight=1.0)` (weights add up), `edges(kind=None) -> list[dict]` (`src dst kind weight`), `add_events(rows) -> int` (rows `(item, ts, kind, weight, source, session)`), `events(sources=None) -> list[dict]`, `write_scores(rows)`, `scores() -> dict[id, dict]`, `set_meta(key, value)`, `get_meta(key, default)`, `commit()`, `close()`.

- [ ] **Step 1: Write the failing test**

**File:** `tests/test_store.py`
```python
from neurotree.store import Store


def test_nodes_edges_events_scores(tmp_path):
    s = Store(tmp_path / "g.db")
    s.upsert_node("project:a", "project", 2, "a", path="/x/a", source="git")
    s.upsert_node("project:a", "project", 2, "A", url="https://github.com/m/a")  # merge, keep path and meta
    n = s.node("project:a")
    assert n["label"] == "A" and n["path"] == "/x/a" and n["meta"] == {"source": "git", "url": "https://github.com/m/a"}
    s.upsert_node("file:/x/a/m.py", "file", 3, "m.py", parent="project:a", path="/x/a/m.py", project="project:a")
    s.set_parent("project:a", "area:x")
    assert s.node("project:a")["parent"] == "area:x"
    s.add_edge("a", "b", "co_attention", 1.0)
    s.add_edge("a", "b", "co_attention", 0.5)
    assert s.edges("co_attention") == [{"src": "a", "dst": "b", "kind": "co_attention", "weight": 1.5}]
    assert s.add_events([("file:/x/a/m.py", 10.0, "edit", 3.0, "claude_sessions", "s1"),
                         ("file:/x/a/m.py", 11.0, "commit", 1.0, "git", None)]) == 2
    assert [e["kind"] for e in s.events(("git",))] == ["commit"]
    s.write_scores([{"id": "project:a", "attention": 1, "history": 0.5, "structure": None, "runtime": None,
                     "noise": 1, "importance": 0.8, "attention_rank": 1, "evidence": ["3 Claude touches in 30 days"]}])
    assert s.scores()["project:a"]["evidence"] == ["3 Claude touches in 30 days"]
    s.set_meta("status", {"git": {"ok": 2}})
    assert s.get_meta("status") == {"git": {"ok": 2}} and s.get_meta("missing", 7) == 7
    assert len(s.nodes("file")) == 1 and len(s.nodes()) == 2
    s.commit()
    s.close()
```

- [ ] **Step 2: Run it to verify it fails** — `.venv/bin/python -m pytest tests/test_store.py -q` -> FAIL (no module `neurotree.store`)

- [ ] **Step 3: Implement**

**File:** `neurotree/store.py`
```python
"""SQLite store for the graph: nodes, edges, events, scores, meta. Rebuilt from scratch on every full index."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, kind TEXT NOT NULL, level INTEGER NOT NULL, parent TEXT,
  label TEXT NOT NULL, path TEXT, project TEXT, meta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS edges(src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1, PRIMARY KEY(src, dst, kind));
CREATE TABLE IF NOT EXISTS events(item TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, weight REAL NOT NULL,
  source TEXT NOT NULL, session TEXT);
CREATE INDEX IF NOT EXISTS ix_events_item ON events(item);
CREATE TABLE IF NOT EXISTS scores(id TEXT PRIMARY KEY, attention REAL, history REAL, structure REAL, runtime REAL,
  noise REAL, importance REAL, attention_rank REAL, evidence TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
EVENT_COLS = "item, ts, kind, weight, source, session"


class Store:
    def __init__(self, path, wal: bool = True):
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute(f"PRAGMA journal_mode={'WAL' if wal else 'DELETE'}")
        self.db.executescript(SCHEMA)

    # nodes
    def upsert_node(self, id: str, kind: str, level: int, label: str, parent: str | None = None,
                    path: str | None = None, project: str | None = None, **meta) -> None:
        row = self.db.execute("SELECT meta FROM nodes WHERE id=?", (id,)).fetchone()
        if row:
            merged = {**json.loads(row["meta"]), **meta}
            self.db.execute("UPDATE nodes SET kind=?, level=?, label=?, parent=COALESCE(?, parent), "
                            "path=COALESCE(?, path), project=COALESCE(?, project), meta=? WHERE id=?",
                            (kind, level, label, parent, path, project, json.dumps(merged), id))
        else:
            self.db.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                            (id, kind, level, parent, label, path, project, json.dumps(meta)))

    def set_parent(self, id: str, parent: str) -> None:
        self.db.execute("UPDATE nodes SET parent=? WHERE id=?", (parent, id))

    @staticmethod
    def _node(row) -> dict:
        d = dict(row)
        d["meta"] = json.loads(d["meta"])
        return d

    def node(self, id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM nodes WHERE id=?", (id,)).fetchone()
        return self._node(row) if row else None

    def nodes(self, kind: str | None = None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM nodes WHERE kind=?", (kind,)) if kind else self.db.execute("SELECT * FROM nodes")
        return [self._node(r) for r in rows]

    # edges
    def add_edge(self, src: str, dst: str, kind: str, weight: float = 1.0) -> None:
        self.db.execute("INSERT INTO edges VALUES(?,?,?,?) ON CONFLICT(src, dst, kind) "
                        "DO UPDATE SET weight = weight + excluded.weight", (src, dst, kind, weight))

    def edges(self, kind: str | None = None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM edges WHERE kind=?", (kind,)) if kind else self.db.execute("SELECT * FROM edges")
        return [dict(r) for r in rows]

    # events
    def add_events(self, rows: Iterable[tuple]) -> int:
        rows = list(rows)
        self.db.executemany(f"INSERT INTO events({EVENT_COLS}) VALUES(?,?,?,?,?,?)", rows)
        return len(rows)

    def events(self, sources: Iterable[str] | None = None) -> list[dict]:
        if sources:
            sources = tuple(sources)
            q = f"SELECT {EVENT_COLS} FROM events WHERE source IN ({','.join('?' * len(sources))})"
            return [dict(r) for r in self.db.execute(q, sources)]
        return [dict(r) for r in self.db.execute(f"SELECT {EVENT_COLS} FROM events")]

    # scores
    def write_scores(self, rows: Iterable[dict]) -> None:
        self.db.execute("DELETE FROM scores")
        self.db.executemany("INSERT INTO scores VALUES(?,?,?,?,?,?,?,?,?)",
                            [(r["id"], r["attention"], r["history"], r["structure"], r["runtime"], r["noise"],
                              r["importance"], r["attention_rank"], json.dumps(r["evidence"])) for r in rows])

    def scores(self) -> dict[str, dict]:
        out = {}
        for r in self.db.execute("SELECT * FROM scores"):
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"])
            out[d["id"]] = d
        return out

    # meta
    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT INTO meta VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value)))

    def get_meta(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_store.py -q` -> `1 passed`
- [ ] **Step 5: Commit** `git add -A && git commit -m "SQLite store"`

---

### Task 3: Fixture world, git repos, structure

**Files:** Create `tests/conftest.py`, `neurotree/sources/__init__.py`, `neurotree/sources/git_repos.py`, `neurotree/structure.py`, `tests/test_git.py`

**Interfaces:**
- Consumes: `Store`, `classify`, `noise_factor`, `is_excluded`, `SKIP_DIRS`.
- Produces: `find_repos(roots, max_depth=4, extra=()) -> list[Path]`; `normalize_remote(url) -> str|None` (`"gh:owner/name"` lowercase); `numstat_path(p) -> str`; `index_repo(store, repo, since_days, extra=(), now=None) -> dict` with keys `project, files, events, kept (rel->file id), blobs (file id->blob sha)`; `shared_code_edges(store, blobs) -> int`; `add_structure(store, repo, kept) -> dict` (`imports`, `functions`); `pagerank_by_project(store) -> dict[file id, float]` (top module per project = 1.0). Node ids: `project:gh:<owner>/<name>` or `project:dir:<abs path>`; `file:<abs path>`; `fn:<abs path>#<name>`.
- Fixture `world` (dict): `root, alpha, beta, docs, sessions, mem, settings, home, now`.

- [ ] **Step 1: Write the fixture and failing tests**

**File:** `tests/conftest.py`
```python
"""A tiny fake Mac: two repos (alpha with imports and history, beta with a GitHub remote), documents,
one Claude session touching both repos, a claude-mem database, plus secret files that must never be read."""
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone

import pytest

from neurotree.config import Settings

DAY = 86400.0
NOW = 1_793_500_000.0


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _git(repo, *args, when=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(when)} +0000"
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture()
def world(tmp_path):
    desk, docs = tmp_path / "Desktop", tmp_path / "Documents"
    a = desk / "alpha"
    _write(a / "pkg/__init__.py", "")
    _write(a / "pkg/core.py", '"""core."""\ndef run():\n    return 1\n\nclass Engine:\n    pass\n')
    _write(a / "pkg/b.py", "from .core import run\n")
    _write(a / "pkg/c.py", "from pkg import core\n")
    _write(a / "tests/test_core.py", "from pkg.core import run\n")
    _write(a / "package-lock.json", "{}")
    _write(a / "credentials.json", '{"token": "x"}')
    _write(a / ".env", "SECRET=1")
    _write(a / "web/app.ts", "import { x } from './util'\n")
    _write(a / "web/util.ts", "export const x = 1\n")
    _git(a, "init", "-q", "-b", "main")
    _git(a, "add", "-A")
    _git(a, "commit", "-qm", "one", when=NOW - 100 * DAY)
    _write(a / "pkg/core.py", '"""core."""\ndef run():\n    return 2\n\nclass Engine:\n    pass\n')
    _git(a, "commit", "-qam", "two", when=NOW - 2 * DAY)
    b = desk / "beta"
    _write(b / "main.py", "print(1)\n")
    _write(b / "LICENSE", "MIT")
    _git(b, "init", "-q", "-b", "main")
    _git(b, "add", "-A")
    _git(b, "commit", "-qm", "init", when=NOW - 10 * DAY)
    _git(b, "remote", "add", "origin", "git@github.com:Malek1414/Beta.git")
    _write(docs / "School/notes.md", "Ideas for alpha and the beta launch.")
    _write(docs / "School/report.pdf", "%PDF-1.4 fake")
    _write(docs / "School/secret-plan.md", "never read me")
    sessions = tmp_path / "claude_projects"
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": str(a), "timestamp": iso(NOW - DAY),
         "message": {"role": "user", "content": "TOP-SECRET-PROMPT-TEXT"}},
        {"type": "assistant", "sessionId": "s1", "cwd": str(a), "timestamp": iso(NOW - DAY),
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Edit", "input": {"file_path": str(a / "pkg/core.py")}},
             {"type": "tool_use", "name": "Read", "input": {"file_path": str(b / "main.py")}},
             {"type": "tool_use", "name": "Bash", "input": {"command": f"cat {a}/pkg/b.py | head"}},
             {"type": "tool_use", "name": "Read", "input": {"file_path": str(a / ".env")}}]}},
    ]
    _write(sessions / "-proj/s1.jsonl", "\n".join(json.dumps(x) for x in lines) + "\n")
    _write(sessions / "-proj/broken.jsonl", "{not json\n")
    mem = tmp_path / "claude-mem.db"
    db = sqlite3.connect(mem)
    db.executescript("""
      CREATE TABLE sdk_sessions(id INTEGER PRIMARY KEY, content_session_id TEXT, memory_session_id TEXT,
        project TEXT, cwd TEXT);
      CREATE TABLE observations(id INTEGER PRIMARY KEY, memory_session_id TEXT, project TEXT, title TEXT,
        type TEXT, files_read TEXT, files_modified TEXT, created_at_epoch INTEGER);""")
    db.execute("INSERT INTO sdk_sessions VALUES(1, 's1', 'm1', 'alpha', ?)", (str(a),))
    db.execute("INSERT INTO observations VALUES(1, 'm1', 'alpha', 'Core returns 2 now', 'change', ?, ?, ?)",
               (json.dumps(["pkg/c.py"]), json.dumps(["pkg/core.py"]), int((NOW - DAY) * 1000)))
    db.execute("INSERT INTO observations VALUES(2, 'm2', 'beta', 'Beta prints', 'change', ?, '[]', ?)",
               (json.dumps(["main.py"]), int((NOW - 3 * DAY) * 1000)))
    db.commit()
    db.close()
    settings = Settings(repo_roots=[str(desk), str(docs)], doc_roots=[str(desk), str(docs)],
                        claude_projects=str(sessions), claude_mem_db=str(mem),
                        sources={"git": True, "github": False, "files": True, "claude_sessions": True,
                                 "claude_mem": True}, history_days=365)
    return {"root": tmp_path, "alpha": a, "beta": b, "docs": docs, "sessions": sessions, "mem": mem,
            "settings": settings, "home": tmp_path / "nt", "now": NOW}
```

**File:** `tests/test_git.py`
```python
from neurotree.sources.git_repos import (find_repos, index_repo, normalize_remote, numstat_path,
                                         shared_code_edges)
from neurotree.store import Store
from neurotree.structure import add_structure, pagerank_by_project


def test_helpers():
    assert normalize_remote("git@github.com:Malek1414/Beta.git") == "gh:malek1414/beta"
    assert normalize_remote("https://github.com/sami-mag07/searchify/") == "gh:sami-mag07/searchify"
    assert normalize_remote("https://gitlab.com/x/y") is None
    assert numstat_path("src/{a.py => b.py}") == "src/b.py"
    assert numstat_path("{old => new}/f.py") == "new/f.py"
    assert numstat_path("src/{ => sub}/f.py") == "src/sub/f.py"
    assert numstat_path("a.py => b.py") == "b.py"
    assert numstat_path("plain.py") == "plain.py"


def test_index_repo_and_structure(world, tmp_path):
    repos = find_repos(world["settings"].repo_roots)
    assert [r.name for r in repos] == ["alpha", "beta"]
    s = Store(tmp_path / "g.db")
    info = index_repo(s, world["alpha"], 365, now=world["now"])
    a = world["alpha"]
    assert info["project"] == f"project:dir:{a}"
    rels = set(info["kept"])
    assert {"pkg/core.py", "pkg/b.py", "web/app.ts", "package-lock.json", "tests/test_core.py"} <= rels
    assert "credentials.json" not in rels and ".env" not in rels
    commits = [e for e in s.events(("git",)) if e["item"] == f"file:{a}/pkg/core.py"]
    assert len(commits) == 2 and all(e["kind"] == "commit" for e in commits)
    assert s.node(f"file:{a}/package-lock.json")["meta"]["noise"] == 0.1
    beta = index_repo(s, world["beta"], 365, now=world["now"])
    assert beta["project"] == "project:gh:malek1414/beta"
    st = add_structure(s, a, info["kept"])
    assert st["imports"] >= 4 and st["functions"] >= 2
    imports = {(e["src"].rsplit("/", 1)[-1], e["dst"].rsplit("/", 1)[-1]) for e in s.edges("import")}
    assert ("b.py", "core.py") in imports and ("c.py", "core.py") in imports and ("app.ts", "util.ts") in imports
    assert s.node(f"fn:{a}/pkg/core.py#Engine")["parent"] == f"file:{a}/pkg/core.py"
    pr = pagerank_by_project(s)
    assert max(pr, key=pr.get) == f"file:{a}/pkg/core.py" and pr[f"file:{a}/pkg/core.py"] == 1.0


def test_shared_code(tmp_path):
    s = Store(tmp_path / "g.db")
    for p in ("project:a", "project:b"):
        s.upsert_node(p, "project", 2, p)
    s.upsert_node("file:/a/util.py", "file", 3, "util.py", project="project:a", noise=1.0)
    s.upsert_node("file:/b/util.py", "file", 3, "util.py", project="project:b", noise=1.0)
    s.upsert_node("file:/a/LICENSE.md", "file", 3, "LICENSE.md", project="project:a", noise=1.0)
    s.upsert_node("file:/b/LICENSE.md", "file", 3, "LICENSE.md", project="project:b", noise=1.0)
    n = shared_code_edges(s, {"file:/a/util.py": "abc", "file:/b/util.py": "abc",
                              "file:/a/LICENSE.md": "lic", "file:/b/LICENSE.md": "lic"})
    assert n == 1 and s.edges("shared_code")[0]["weight"] == 1.0
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_git.py -q` -> FAIL (no module `neurotree.sources`)

- [ ] **Step 3: Implement**

**File:** `neurotree/sources/__init__.py`
```python
"""Source adapters. Each one only reads its source and writes nodes, edges and events into the store."""
```

**File:** `neurotree/sources/git_repos.py`
```python
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
```

**File:** `neurotree/structure.py`
```python
"""Structure inside one repo: import edges (Python via AST, JS/TS via import/require), top-level functions and
classes as detail nodes, and PageRank over each project's import graph."""
from __future__ import annotations

import ast
import os
import re
from collections import defaultdict
from pathlib import Path

JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
JS_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"](\.{1,2}/[^'"]+)['"]""",
                       re.M)
MAX_BYTES = 1_000_000


def _py_module(rel: str) -> str:
    mod = rel[:-3].replace("/", ".")
    return mod[: -len(".__init__")] if mod.endswith(".__init__") else mod


def _longest(name: str, mods: dict) -> str | None:
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        hit = mods.get(".".join(parts[:i]))
        if hit:
            return hit
    return None


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def scan_python(repo: Path, kept: dict[str, str]) -> tuple[set, list]:
    mods = {}
    for rel, fid in kept.items():
        if rel.endswith(".py"):
            m = _py_module(rel)
            mods[m] = fid
            if m.startswith("src."):
                mods.setdefault(m[4:], fid)
    edges, defs = set(), []
    for rel, fid in kept.items():
        if not rel.endswith(".py"):
            continue
        text = _read(repo / rel)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        cur, is_pkg = _py_module(rel), rel.endswith("__init__.py")
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs.append((fid, node.name, node.lineno))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = cur.split(".") if is_pkg else cur.split(".")[:-1]
                    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                    prefix = ".".join(base + ([node.module] if node.module else []))
                else:
                    prefix = node.module or ""
                targets = [f"{prefix}.{a.name}" if prefix else a.name for a in node.names] + ([prefix] if prefix else [])
            else:
                continue
            for t in targets:
                hit = _longest(t, mods)
                if hit and hit != fid:
                    edges.add((fid, hit))
    return edges, defs


def scan_js(repo: Path, kept: dict[str, str]) -> set:
    edges = set()
    for rel, fid in kept.items():
        if not rel.endswith(JS_EXT):
            continue
        text = _read(repo / rel)
        if text is None:
            continue
        base = os.path.dirname(rel)
        for spec in JS_IMPORT.findall(text):
            target = os.path.normpath(os.path.join(base, spec))
            for cand in (target, *(target + e for e in JS_EXT), *(f"{target}/index{e}" for e in JS_EXT)):
                if cand in kept:
                    if kept[cand] != fid:
                        edges.add((fid, kept[cand]))
                    break
    return edges


def add_structure(store, repo, kept: dict[str, str]) -> dict:
    repo = Path(repo)
    py_edges, defs = scan_python(repo, kept)
    edges = py_edges | scan_js(repo, kept)
    for a, b in edges:
        store.add_edge(a, b, "import")
    for fid, name, line in defs:
        path = fid[len("file:"):]
        store.upsert_node(f"fn:{path}#{name}", "function", 4, name, parent=fid, path=path,
                          project=store.node(fid)["project"], line=line)
    return {"imports": len(edges), "functions": len(defs)}


def _pagerank(nodes: list, succ: dict, alpha: float = 0.85, iters: int = 60) -> dict:
    n = len(nodes)
    pr = dict.fromkeys(nodes, 1.0 / n)
    for _ in range(iters):
        dangling = alpha * sum(pr[u] for u in nodes if not succ.get(u)) / n
        new = dict.fromkeys(nodes, (1 - alpha) / n + dangling)
        for u in nodes:
            out = succ.get(u)
            if out:
                share = alpha * pr[u] / len(out)
                for v in out:
                    new[v] += share
        pr = new
    return pr


def pagerank_by_project(store) -> dict[str, float]:
    """PageRank inside each project's import graph, scaled so each project's top module is 1.0."""
    project = {n["id"]: n["project"] for n in store.nodes("file")}
    graphs = defaultdict(lambda: defaultdict(set))
    for e in store.edges("import"):
        p = project.get(e["src"])
        if p and project.get(e["dst"]) == p:
            graphs[p][e["src"]].add(e["dst"])
    out = {}
    for succ in graphs.values():
        nodes = sorted(set(succ) | {v for vs in succ.values() for v in vs})
        pr = _pagerank(nodes, succ)
        top = max(pr.values())
        out.update({k: round(v / top, 6) for k, v in pr.items()})
    return out
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_git.py -q` -> `3 passed`
- [ ] **Step 5: Commit** `git add -A && git commit -m "Git repos, imports, functions, shared code, PageRank"`

---

### Task 4: Path index and Claude Code sessions

**Files:** Create `neurotree/paths.py`, `neurotree/sources/claude_sessions.py`, `tests/test_sessions.py`

**Interfaces:**
- Consumes: Task 3 nodes.
- Produces: `PathIndex(store, extra=())` with `project_of(path) -> str|None`, `resolve(raw, cwd=None) -> str|None` (creates file nodes for untracked files inside project roots), `root_by_name(name) -> str|None`; `parse_session(path) -> dict` (`session, cwd, start, touches[(raw, ts, kind, weight)]`); `index_sessions(store, index, root) -> dict` (`ok, errors, events`). Event weights: edit 3, read 1, search 0.5, mention 0.5, session (project via cwd) 2.

- [ ] **Step 1: Write the failing test**

**File:** `tests/test_sessions.py`
```python
from neurotree.paths import PathIndex
from neurotree.sources.claude_sessions import index_sessions, parse_session
from neurotree.sources.git_repos import index_repo
from neurotree.store import Store


def _store(world, tmp_path):
    s = Store(tmp_path / "g.db")
    index_repo(s, world["alpha"], 365, now=world["now"])
    index_repo(s, world["beta"], 365, now=world["now"])
    return s


def test_parse_session_keeps_paths_only(world):
    sess = parse_session(str(world["sessions"] / "-proj/s1.jsonl"))
    assert sess["session"] == "s1" and sess["cwd"] == str(world["alpha"])
    kinds = sorted((k, w) for _, _, k, w in sess["touches"])
    assert kinds == [("edit", 3.0), ("mention", 0.5), ("read", 1.0), ("read", 1.0)]
    assert "TOP-SECRET-PROMPT-TEXT" not in repr(sess)


def test_index_sessions(world, tmp_path):
    s = _store(world, tmp_path)
    res = index_sessions(s, PathIndex(s), str(world["sessions"]))
    assert res["ok"] == 1 and res["errors"] == 1
    a, b = world["alpha"], world["beta"]
    got = {(e["item"], e["kind"], e["weight"]) for e in s.events(("claude_sessions",))}
    assert (f"file:{a}/pkg/core.py", "edit", 3.0) in got
    assert ("file:" + str(b / "main.py"), "read", 1.0) in got
    assert (f"file:{a}/pkg/b.py", "mention", 0.5) in got
    assert (f"project:dir:{a}", "session", 2.0) in got
    assert not any(".env" in item for item, _, _ in got)
    assert all(e["session"] == "s1" for e in s.events(("claude_sessions",)))


def test_path_index_resolution(world, tmp_path):
    s = _store(world, tmp_path)
    idx = PathIndex(s)
    a = world["alpha"]
    (a / "notes_untracked.md").write_text("x")
    assert idx.resolve("pkg/core.py", cwd=str(a)) == f"file:{a}/pkg/core.py"
    assert idx.resolve(str(a / "notes_untracked.md")) == f"file:{a}/notes_untracked.md"
    assert s.node(f"file:{a}/notes_untracked.md")["meta"]["untracked"] is True
    assert idx.resolve(str(a / "pkg")) == f"project:dir:{a}"
    assert idx.resolve("/somewhere/else.py") is None
    assert idx.resolve(str(a / ".env")) is None
    assert idx.root_by_name("ALPHA") == str(a)
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_sessions.py -q` -> FAIL (no module `neurotree.paths`)

- [ ] **Step 3: Implement**

**File:** `neurotree/paths.py`
```python
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
```

**File:** `neurotree/sources/claude_sessions.py`
```python
"""Claude Code sessions (~/.claude/projects/**/*.jsonl) -> attention events.
Keeps only file paths, times and session ids; message text is never stored."""
from __future__ import annotations

import glob
import itertools
import json
import os
import re
from datetime import datetime
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "mcp__plugin_mozcode_mozcode__code_edit"}
READ_TOOLS = {"Read", "mcp__plugin_mozcode_mozcode__code_read", "mcp__plugin_mozcode_mozcode__code_outline"}
SEARCH_TOOLS = {"Glob", "Grep", "LS"}
PATH_KEYS = ("file_path", "notebook_path", "path")
ABS_PATH = re.compile(r"""(?:(?<=[\s'"=(])|^)((?:~/|/)[\w.@+-][^\s'"|;&<>()`$]*)""")
WEIGHT = {"edit": 3.0, "read": 1.0, "search": 0.5, "mention": 0.5}


def _ts(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def session_files(root: str) -> list[str]:
    return sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))


def parse_session(path: str) -> dict:
    sid = cwd = start = None
    touches = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        first = fh.readline()
        json.loads(first)  # ValueError: not a JSON-lines session log
        for line in itertools.chain([first], fh):
            if not (cwd is None and '"cwd"' in line) and '"tool_use"' not in line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if cwd is None and m.get("cwd"):
                cwd, start = m["cwd"], _ts(m.get("timestamp"))
            sid = sid or m.get("sessionId")
            msg = m.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            ts = _ts(m.get("timestamp"))
            if ts is None or not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool_use" or not isinstance(c.get("input"), dict):
                    continue
                name, inp = c.get("name", ""), c["input"]
                if name in EDIT_TOOLS or name in READ_TOOLS:
                    raw = next((inp[k] for k in PATH_KEYS if isinstance(inp.get(k), str)), None)
                    kind = "edit" if name in EDIT_TOOLS else "read"
                    if raw:
                        touches.append((raw, ts, kind, WEIGHT[kind]))
                elif name in SEARCH_TOOLS and isinstance(inp.get("path"), str):
                    touches.append((inp["path"], ts, "search", WEIGHT["search"]))
                elif name == "Bash" and isinstance(inp.get("command"), str):
                    for raw in sorted(set(ABS_PATH.findall(inp["command"])))[:20]:
                        touches.append((raw, ts, "mention", WEIGHT["mention"]))
    return {"session": sid or Path(path).stem, "cwd": cwd, "start": start, "touches": touches}


def index_sessions(store, index, root: str) -> dict:
    ok = bad = 0
    events = []
    for f in session_files(root):
        try:
            s = parse_session(f)
        except (OSError, ValueError):
            bad += 1
            continue
        ok += 1
        if s["cwd"] and s["start"]:
            pid = index.project_of(os.path.normpath(s["cwd"]))
            if pid:
                events.append((pid, s["start"], "session", 2.0, "claude_sessions", s["session"]))
        for raw, ts, kind, weight in s["touches"]:
            node = index.resolve(raw, s["cwd"])
            if node:
                events.append((node, ts, kind, weight, "claude_sessions", s["session"]))
    store.add_events(events)
    return {"ok": ok, "errors": bad, "events": len(events)}
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_sessions.py -q` -> `3 passed`
- [ ] **Step 5: Commit** `git add -A && git commit -m "Path index and Claude Code session attention"`

---

### Task 5: claude-mem, documents, GitHub

**Files:** Create `neurotree/sources/claude_mem.py`, `neurotree/sources/files.py`, `neurotree/sources/github.py`, `tests/test_sources.py`

**Interfaces:**
- Consumes: `PathIndex`, `Store`.
- Produces: `index_memory(store, index, db_path, facts_per_node=5) -> dict`; `index_files(store, roots, repo_roots: set[str], since_days, extra=(), now=None) -> dict` (`ok, events, mentions`); `index_github(store, owner=None, limit=300, runner=subprocess.run) -> dict`. Fact nodes `fact:<observation id>` (level 4). Folder projects `project:dir:<folder>`, loose files `project:loose:<root>` (no path).

- [ ] **Step 1: Write the failing test**

**File:** `tests/test_sources.py`
```python
import json
import subprocess
import types

from neurotree.paths import PathIndex
from neurotree.sources.claude_mem import index_memory
from neurotree.sources.files import index_files
from neurotree.sources.git_repos import index_repo
from neurotree.sources.github import index_github
from neurotree.store import Store


def _store(world, tmp_path):
    s = Store(tmp_path / "g.db")
    for r in ("alpha", "beta"):
        index_repo(s, world[r], 365, now=world["now"])
    return s


def test_memory_resolves_relative_paths_read_only(world, tmp_path):
    s = _store(world, tmp_path)
    before = world["mem"].stat().st_mtime_ns
    res = index_memory(s, PathIndex(s), str(world["mem"]))
    assert res["ok"] == 2 and res["errors"] == 0
    a, b = world["alpha"], world["beta"]
    got = {(e["item"], e["kind"], e["weight"], e["session"]) for e in s.events(("claude_mem",))}
    assert (f"file:{a}/pkg/core.py", "modified", 3.0, "s1") in got
    assert (f"file:{a}/pkg/c.py", "read", 1.0, "s1") in got
    assert ("file:" + str(b / "main.py"), "read", 1.0, None) in got  # no cwd: falls back to project name
    assert s.node("fact:1")["parent"] == f"file:{a}/pkg/core.py" and s.node("fact:1")["label"] == "Core returns 2 now"
    assert world["mem"].stat().st_mtime_ns == before


def test_memory_missing_or_broken_db(tmp_path):
    s = Store(tmp_path / "g.db")
    assert index_memory(s, PathIndex(s), str(tmp_path / "nope.db"))["skipped"] == "no database"


def test_documents_projects_and_mentions(world, tmp_path):
    s = _store(world, tmp_path)
    repo_roots = {str(world["alpha"]), str(world["beta"])}
    res = index_files(s, world["settings"].doc_roots, repo_roots, 365, now=world["now"])
    school = world["docs"] / "School"
    assert res["ok"] == 2
    assert s.node(f"project:dir:{school}")["label"] == "School"
    assert s.node(f"file:{school}/report.pdf")["parent"] == f"project:dir:{school}"
    assert s.node(f"file:{school}/secret-plan.md") is None
    targets = {e["dst"] for e in s.edges("mention")}
    assert targets == {f"project:dir:{world['alpha']}", "project:gh:malek1414/beta"}


def test_github_only_repos(tmp_path):
    s = Store(tmp_path / "g.db")
    s.upsert_node("project:gh:malek1414/beta", "project", 2, "beta", path="/x/beta", source="git")
    repos = [{"nameWithOwner": "Malek1414/Beta", "url": "https://github.com/Malek1414/Beta", "pushedAt": "2026-10-30T10:00:00Z",
              "description": "b", "primaryLanguage": {"name": "Python"}, "isFork": False},
             {"nameWithOwner": "Malek1414/Gamma", "url": "https://github.com/Malek1414/Gamma", "pushedAt": "2026-09-01T10:00:00Z",
              "description": None, "primaryLanguage": None, "isFork": True}]

    def fake(cmd, **kw):
        out = "Malek1414\n" if cmd[:2] == ["gh", "api"] else json.dumps(repos)
        return types.SimpleNamespace(stdout=out, returncode=0)

    res = index_github(s, runner=fake)
    assert res == {"ok": 2, "new": 1, "events": 2}
    assert s.node("project:gh:malek1414/beta")["path"] == "/x/beta"
    assert s.node("project:gh:malek1414/gamma")["meta"]["fork"] is True

    def broken(cmd, **kw):
        raise FileNotFoundError("gh")

    assert index_github(Store(tmp_path / "h.db"), runner=broken)["errors"] == 1
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_sources.py -q` -> FAIL (no module `neurotree.sources.claude_mem`)

- [ ] **Step 3: Implement**

**File:** `neurotree/sources/claude_mem.py`
```python
"""claude-mem (~/.claude-mem/claude-mem.db, opened read-only) -> attention events and fact nodes.
Only titles, projects, file lists and times are kept."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict


def _cols(db, table: str) -> set:
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def _col(name: str, alias: str, cols: set) -> str:
    return f"{alias}.{name}" if name in cols else "NULL"


def index_memory(store, index, db_path: str, facts_per_node: int = 5) -> dict:
    if not os.path.exists(db_path):
        return {"ok": 0, "errors": 0, "skipped": "no database"}
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        oc, sc = _cols(db, "observations"), _cols(db, "sdk_sessions")
        join = "LEFT JOIN sdk_sessions s ON s.memory_session_id = o.memory_session_id" if sc else ""
        sc = sc if sc else set()
        rows = db.execute(
            f"SELECT o.id, {_col('title', 'o', oc)}, {_col('project', 'o', oc)}, {_col('created_at_epoch', 'o', oc)}, "
            f"{_col('files_read', 'o', oc)}, {_col('files_modified', 'o', oc)}, {_col('cwd', 's', sc)}, "
            f"{_col('content_session_id', 's', sc)} FROM observations o {join}").fetchall()
    finally:
        db.close()
    events, facts = [], defaultdict(list)
    ok = bad = 0
    for oid, title, project, epoch, files_read, files_modified, cwd, sid in rows:
        try:
            ts = float(epoch)
            ts = ts / 1000 if ts > 1e11 else ts
            cwd = cwd or index.root_by_name(project)
            touched = []
            for raw_list, kind, weight in ((files_modified, "modified", 3.0), (files_read, "read", 1.0)):
                for raw in json.loads(raw_list or "[]"):
                    node = index.resolve(raw, cwd)
                    if node:
                        events.append((node, ts, kind, weight, "claude_mem", sid))
                        touched.append(node)
            if title and touched:
                facts[touched[0]].append((ts, oid, title))
            ok += 1
        except (TypeError, ValueError):
            bad += 1
    store.add_events(events)
    for node_id, items in facts.items():
        owner = store.node(node_id)
        project = node_id if owner["kind"] == "project" else owner["project"]
        for ts, oid, title in sorted(items, reverse=True)[:facts_per_node]:
            store.upsert_node(f"fact:{oid}", "fact", 4, str(title)[:160], parent=node_id, project=project, ts=ts)
    return {"ok": ok, "errors": bad, "events": len(events), "facts": sum(min(len(v), facts_per_node) for v in facts.values())}
```

**File:** `neurotree/sources/files.py`
```python
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
```

**File:** `neurotree/sources/github.py`
```python
"""GitHub repos via the gh CLI (Malek's own login) -> project nodes for repos without a local clone, push events."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime

FIELDS = "nameWithOwner,url,pushedAt,description,primaryLanguage,isFork"


def index_github(store, owner: str | None = None, limit: int = 300, runner=subprocess.run) -> dict:
    try:
        if not owner:
            owner = runner(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True,
                           timeout=30).stdout.strip()
        out = runner(["gh", "repo", "list", owner, "--limit", str(limit), "--json", FIELDS],
                     capture_output=True, text=True, timeout=60)
        repos = json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"ok": 0, "errors": 1, "error": type(e).__name__}
    events, new = [], 0
    for r in repos:
        pid = "project:gh:" + r["nameWithOwner"].lower()
        existing = store.node(pid)
        new += existing is None
        label = existing["label"] if existing else r["nameWithOwner"].split("/", 1)[1]
        store.upsert_node(pid, "project", 2, label, url=r.get("url"), description=r.get("description") or "",
                          language=(r.get("primaryLanguage") or {}).get("name"), fork=bool(r.get("isFork")),
                          github=True)
        if r.get("pushedAt"):
            ts = datetime.fromisoformat(r["pushedAt"].replace("Z", "+00:00")).timestamp()
            events.append((pid, ts, "push", 1.0, "github", None))
    store.add_events(events)
    return {"ok": len(repos), "new": new, "events": len(events)}
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_sources.py -q` -> `4 passed`
- [ ] **Step 5: Commit** `git add -A && git commit -m "claude-mem, documents and GitHub sources"`

---

### Task 6: Scoring

**Files:** Create `neurotree/scoring.py`, `tests/test_scoring.py`

**Interfaces:**
- Consumes: `Store`, `pagerank_by_project`.
- Produces: `WEIGHTS`, `decay(weight, ts, now, half_life_days) -> float`, `percentile_ranks(values) -> dict`, `combine(signals, noise=1.0) -> float`, `runtime_shares(store) -> dict`, `score(store, now=None) -> int` (writes `scores`; `attention_rank` = attention percentile within the node's level; `self` gets 1.0).

- [ ] **Step 1: Write the failing test**

**File:** `tests/test_scoring.py`
```python
import json

import pytest

from neurotree.scoring import WEIGHTS, combine, decay, percentile_ranks, runtime_shares, score
from neurotree.store import Store

DAY = 86400.0


def test_decay_half_life():
    assert decay(4.0, 0, 30 * DAY, 30) == pytest.approx(2.0)
    assert decay(4.0, 10 * DAY, 10 * DAY, 30) == pytest.approx(4.0)
    assert decay(4.0, 20 * DAY, 10 * DAY, 30) == pytest.approx(4.0)  # future timestamps never grow


def test_percentile_ranks():
    r = percentile_ranks({"a": 0.0, "b": 1.0, "c": 5.0, "d": 5.0, "e": 2.0})
    assert r["a"] == 0.0 and r["b"] == 0.25 and r["e"] == 0.5 and r["c"] == r["d"] == 1.0
    assert percentile_ranks({"x": 0.0}) == {"x": 0.0}


def test_combine_weights_renormalize_floor_and_noise():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)
    full = combine({"attention": 1, "history": 0, "structure": 0, "runtime": 0})
    assert full == pytest.approx(0.45, abs=1e-6)
    no_runtime = combine({"attention": 1, "history": 0, "structure": 0, "runtime": None})
    assert no_runtime == pytest.approx(0.45 / 0.9, abs=1e-6)
    only_two = combine({"attention": 1, "history": 1, "structure": None, "runtime": None})
    assert only_two == pytest.approx(1.0, abs=1e-6)
    floor = combine({"attention": 0, "history": 0, "structure": 1, "runtime": None})
    assert floor == pytest.approx(max(0.2 / 0.9, 0.15), abs=1e-6)
    assert combine({"attention": 1, "history": 1, "structure": None, "runtime": None}, noise=0.1) == pytest.approx(0.1, abs=1e-6)


def test_score_rolls_up_and_explains(tmp_path):
    s = Store(tmp_path / "g.db")
    now = 1000 * DAY
    s.upsert_node("self", "self", 0, "Malek")
    s.upsert_node("area:a", "area", 1, "A", parent="self")
    s.upsert_node("project:p", "project", 2, "p", parent="area:a", path=str(tmp_path / "p"))
    s.upsert_node("project:q", "project", 2, "q", parent="area:a")
    for name, noise in (("hot.py", 1.0), ("cold.py", 1.0), ("package-lock.json", 0.1)):
        s.upsert_node(f"file:/p/{name}", "file", 3, name, parent="project:p", project="project:p", noise=noise)
    s.add_edge("file:/p/cold.py", "file:/p/hot.py", "import")
    s.add_events([("file:/p/hot.py", now - DAY, "edit", 3.0, "claude_sessions", "s1"),
                  ("file:/p/hot.py", now - 2 * DAY, "commit", 2.0, "git", None),
                  ("file:/p/package-lock.json", now - DAY, "edit", 3.0, "claude_sessions", "s1"),
                  ("project:q", now - 400 * DAY, "push", 1.0, "github", None)])
    assert score(s, now) == 7
    sc = s.scores()
    assert sc["self"]["importance"] == 1.0
    assert sc["file:/p/hot.py"]["importance"] > sc["file:/p/package-lock.json"]["importance"]
    assert sc["file:/p/hot.py"]["importance"] > sc["file:/p/cold.py"]["importance"] > 0  # structure floor
    assert sc["project:p"]["attention_rank"] == 1.0 and sc["project:q"]["attention_rank"] == 0.0
    ev = sc["file:/p/hot.py"]["evidence"]
    assert "1 Claude touch in 30 days" in ev and "1 commit in 90 days" in ev and "imported by 1 module" in ev
    assert "dimmed: lockfile, generated or vendored" in sc["file:/p/package-lock.json"]["evidence"]


def test_runtime_shares_from_repograph(tmp_path):
    s = Store(tmp_path / "g.db")
    repo = tmp_path / "r"
    (repo / "docs/graph").mkdir(parents=True)
    (repo / "docs/graph/graph.json").write_text(json.dumps({"nodes": [{"id": "pkg.a", "path": "pkg/a.py"},
                                                                       {"id": "pkg.b", "path": "pkg/b.py"}]}))
    (repo / "docs/graph/trace.json").write_text(json.dumps({"nodes": {"pkg.a": {"ms": 300}, "pkg.b": {"ms": 100}}}))
    s.upsert_node("project:r", "project", 2, "r", path=str(repo))
    assert runtime_shares(s) == {f"file:{repo}/pkg/a.py": 0.75, f"file:{repo}/pkg/b.py": 0.25}
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_scoring.py -q` -> FAIL (no module `neurotree.scoring`)

- [ ] **Step 3: Implement**

**File:** `neurotree/scoring.py`
```python
"""Signals -> importance. Attention 45 %, history 25 %, structure 20 %, runtime 10 %, times noise.
Each signal is a percentile rank among nodes of the same level; parents roll up their children's raw signals."""
from __future__ import annotations

import bisect
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

from .structure import pagerank_by_project

WEIGHTS = {"attention": 0.45, "history": 0.25, "structure": 0.20, "runtime": 0.10}
HALF_LIFE_DAYS = {"attention": 30.0, "history": 90.0}
SIGNAL_OF_SOURCE = {"claude_sessions": "attention", "claude_mem": "attention", "git": "history", "fs": "history",
                    "github": "history"}
STRUCTURE_FLOOR = 0.15
DAY = 86400.0


def decay(weight: float, ts: float, now: float, half_life_days: float) -> float:
    return weight * 0.5 ** (max(0.0, now - ts) / DAY / half_life_days)


def percentile_ranks(values: dict) -> dict:
    """Rank in (0, 1] among positive values (ties share the higher rank); zero stays 0."""
    pos = sorted(v for v in values.values() if v > 0)
    if not pos:
        return {k: 0.0 for k in values}
    return {k: (bisect.bisect_right(pos, v) / len(pos) if v > 0 else 0.0) for k, v in values.items()}


def combine(signals: dict, noise: float = 1.0) -> float:
    present = {k: w for k, w in WEIGHTS.items() if signals.get(k) is not None}
    total = sum(present.values())
    if not total:
        return 0.0
    value = sum(w / total * signals[k] for k, w in present.items())
    if signals.get("structure") is not None:  # essential but untouched code never goes fully dark
        value = max(value, STRUCTURE_FLOOR * signals["structure"])
    return round(value * noise, 6)


def runtime_shares(store) -> dict:
    """repograph traces (docs/graph/graph.json + trace.json in a repo) -> {file id: share of recorded time}."""
    out = {}
    for n in store.nodes("project"):
        if not n["path"]:
            continue
        g, t = Path(n["path"], "docs/graph/graph.json"), Path(n["path"], "docs/graph/trace.json")
        if not (g.exists() and t.exists()):
            continue
        try:
            graph, trace = json.loads(g.read_text()), json.loads(t.read_text())
        except (OSError, ValueError):
            continue
        path_of = {m["id"]: m.get("path") for m in graph.get("nodes", [])}
        ms = {mid: (v or {}).get("ms", 0) or 0 for mid, v in trace.get("nodes", {}).items()}
        total = sum(ms.values()) or 1
        for mid, v in ms.items():
            if path_of.get(mid):
                out[f"file:{os.path.join(n['path'], path_of[mid])}"] = round(v / total, 6)
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 'es' if word.endswith('ch') else 's'}"


def evidence(counts: dict, imported_by: int, runtime: float | None, noise: float) -> list[str]:
    out = []
    if counts.get("claude30"):
        out.append(f"{_plural(counts['claude30'], 'Claude touch')} in 30 days")
    if counts.get("commits90"):
        out.append(f"{_plural(counts['commits90'], 'commit')} in 90 days")
    if imported_by:
        out.append(f"imported by {_plural(imported_by, 'module')}")
    if runtime:
        out.append(f"{runtime * 100:.0f} % of recorded run time")
    if noise <= 0.1:
        out.append("dimmed: lockfile, generated or vendored")
    elif noise < 1:
        out.append("dimmed: test code")
    return out


def score(store, now: float | None = None) -> int:
    now = now or time.time()
    nodes = {n["id"]: n for n in store.nodes()}
    raw = {"attention": defaultdict(float), "history": defaultdict(float)}
    counts = defaultdict(Counter)
    for e in store.events():
        sig = SIGNAL_OF_SOURCE.get(e["source"])
        if not sig or e["item"] not in nodes:
            continue
        raw[sig][e["item"]] += decay(e["weight"], e["ts"], now, HALF_LIFE_DAYS[sig])
        age_days = (now - e["ts"]) / DAY
        if sig == "attention" and age_days <= 30:
            counts[e["item"]]["claude30"] += 1
        if e["kind"] == "commit" and age_days <= 90:
            counts[e["item"]]["commits90"] += 1
    for level in (4, 3, 2, 1):  # roll children up into parents, deepest first
        for nid, n in nodes.items():
            if n["level"] == level and n["parent"] in nodes:
                for sig in raw:
                    raw[sig][n["parent"]] += raw[sig].get(nid, 0.0)
                counts[n["parent"]].update(counts.get(nid, Counter()))
    structure, runtime = pagerank_by_project(store), runtime_shares(store)
    imported_by = Counter(e["dst"] for e in store.edges("import"))
    by_level = defaultdict(list)
    for nid, n in nodes.items():
        by_level[n["level"]].append(nid)
    ranks = {}
    for ids in by_level.values():
        att = percentile_ranks({i: raw["attention"].get(i, 0.0) for i in ids})
        his = percentile_ranks({i: raw["history"].get(i, 0.0) for i in ids})
        st = percentile_ranks({i: structure[i] for i in ids if i in structure})
        rt = percentile_ranks({i: runtime[i] for i in ids if i in runtime})
        for i in ids:
            ranks[i] = {"attention": att[i], "history": his[i], "structure": st.get(i), "runtime": rt.get(i)}
    rows = []
    for nid, n in nodes.items():
        r, noise = ranks[nid], float(n["meta"].get("noise", 1.0))
        is_self = n["kind"] == "self"
        rows.append({"id": nid, **r, "noise": noise, "importance": 1.0 if is_self else combine(r, noise),
                     "attention_rank": 1.0 if is_self else r["attention"],
                     "evidence": evidence(counts.get(nid, {}), imported_by.get(nid, 0), runtime.get(nid), noise)})
    store.write_scores(rows)
    return len(rows)
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_scoring.py -q` -> `5 passed`
- [ ] **Step 5: Commit** `git add -A && git commit -m "Attention-first scoring with evidence"`

---

### Task 7: Areas

**Files:** Create `neurotree/areas.py`, `tests/test_areas.py`

**Interfaces:**
- Consumes: `Store` events with sessions.
- Produces: `coattention(store) -> nx.Graph` (project nodes; weight 1/(n-1) per shared session with 2..12 projects); `assign_areas(store, areas_path, owner="Malek") -> dict` (`areas`, `unsorted`). Writes nodes `self`, `area:<slug>`, sets project parents, `tree` edges, project `co_attention` edges, and `areas.json` = `{"reviewed": bool, "areas": {slug: {name, color, projects}}}`.

- [ ] **Step 1: Write the failing test**

**File:** `tests/test_areas.py`
```python
import json

from neurotree.areas import UNSORTED, assign_areas, coattention
from neurotree.store import Store


def _store(tmp_path):
    s = Store(tmp_path / "g.db")
    for p in ("alpha", "beta", "gamma", "delta", "lonely"):
        s.upsert_node(f"project:{p}", "project", 2, p, path=f"/Desktop/Hackathons/{p}")
        s.upsert_node(f"file:/{p}/m.py", "file", 3, "m.py", parent=f"project:{p}", project=f"project:{p}")
    rows = []
    for i in range(3):
        rows += [(f"file:/alpha/m.py", 1.0, "edit", 3.0, "claude_sessions", f"a{i}"),
                 (f"file:/beta/m.py", 1.0, "read", 1.0, "claude_sessions", f"a{i}"),
                 (f"file:/gamma/m.py", 1.0, "edit", 3.0, "claude_mem", f"g{i}"),
                 (f"project:delta", 1.0, "session", 2.0, "claude_sessions", f"g{i}")]
    s.add_events(rows)
    return s


def test_coattention_graph(tmp_path):
    g = coattention(_store(tmp_path))
    assert g["project:alpha"]["project:beta"]["weight"] == 3.0
    assert g.degree("project:lonely") == 0


def test_assign_proposes_then_respects_review(tmp_path):
    s = _store(tmp_path)
    path = tmp_path / "areas.json"
    res = assign_areas(s, path)
    saved = json.loads(path.read_text())
    assert saved["reviewed"] is False and res["areas"] == 2 and res["unsorted"] == 1
    area_of = {p: slug for slug, a in saved["areas"].items() for p in a["projects"]}
    assert area_of["project:alpha"] == area_of["project:beta"] != area_of["project:gamma"] == area_of["project:delta"]
    assert area_of["project:lonely"] == UNSORTED
    assert s.node("project:alpha")["parent"] == f"area:{area_of['project:alpha']}"
    assert s.node(f"area:{UNSORTED}")["parent"] == "self" and s.node("self")["label"] == "Malek"
    assert {e["kind"] for e in s.edges()} >= {"tree", "co_attention"}
    # Malek reviews: renames an area and moves lonely into it; the next run keeps both
    first = area_of["project:alpha"]
    saved["reviewed"] = True
    saved["areas"][first]["name"] = "Hackathon builds"
    saved["areas"][first]["projects"].append("project:lonely")
    saved["areas"][UNSORTED]["projects"].remove("project:lonely")
    path.write_text(json.dumps(saved))
    s2 = _store_fresh(tmp_path)
    assign_areas(s2, path)
    again = json.loads(path.read_text())
    assert again["reviewed"] is True and again["areas"][first]["name"] == "Hackathon builds"
    assert "project:lonely" in again["areas"][first]["projects"]
    assert s2.node("project:lonely")["parent"] == f"area:{first}"


def _store_fresh(tmp_path):
    sub = tmp_path / "second"
    sub.mkdir()
    return _store(sub)
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_areas.py -q` -> FAIL (no module `neurotree.areas`)

- [ ] **Step 3: Implement**

**File:** `neurotree/areas.py`
```python
"""Life areas: Louvain communities on the project co-attention graph (projects touched in the same Claude
session or memory session). areas.json stays a proposal until Malek reviews it; after that it wins."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import networkx as nx

UNSORTED = "unsorted"
MIN_CONFIDENCE = 0.4
HUB_SESSION_MAX = 12
PALETTE = ["#f5a524", "#5b8def", "#3ecf8e", "#e5484d", "#a78bfa", "#2dd4bf", "#f472b6", "#facc15", "#60a5fa",
           "#fb923c", "#34d399", "#c084fc"]
STOP = {"project", "projects", "the", "and", "for", "site", "code", "test", "tests", "demo", "main", "repo", "data",
        "public", "private", "copy", "final", "desktop", "documents", "users", "malekhassan", "with", "from"}


def coattention(store) -> nx.Graph:
    project_of = {n["id"]: (n["id"] if n["kind"] == "project" else n["project"]) for n in store.nodes()}
    sessions = defaultdict(set)
    for e in store.events(("claude_sessions", "claude_mem")):
        p = project_of.get(e["item"])
        if e["session"] and p:
            sessions[e["session"]].add(p)
    g = nx.Graph()
    g.add_nodes_from(n["id"] for n in store.nodes("project"))
    for projects in sessions.values():
        if 2 <= len(projects) <= HUB_SESSION_MAX:
            w = 1.0 / (len(projects) - 1)
            for a, b in combinations(sorted(projects), 2):
                g.add_edge(a, b, weight=g.get_edge_data(a, b, {"weight": 0.0})["weight"] + w)
    return g


def _share(g: nx.Graph, node: str, members: set) -> float:
    total = sum(d["weight"] for _, _, d in g.edges(node, data=True))
    inside = sum(d["weight"] for _, v, d in g.edges(node, data=True) if v in members)
    return inside / total if total else 0.0


def _name(store, members: set, g: nx.Graph) -> str:
    words = Counter()
    for pid in members:
        n = store.node(pid)
        texts = [n["label"], Path(n["path"]).parent.name if n["path"] else ""]
        words.update({w.lower() for t in texts for w in re.findall(r"[A-Za-z]{4,}", t)} - STOP)
    common = [w for w, c in words.most_common() if c >= 2]
    if common:
        return common[0].capitalize()
    top = max(sorted(members), key=lambda p: g.degree(p, weight="weight"))
    return store.node(top)["label"]


def _slug(name: str, taken) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "area"
    slug, i = base, 2
    while slug in taken or slug == UNSORTED:
        slug, i = f"{base}-{i}", i + 1
    return slug


def assign_areas(store, areas_path, owner: str = "Malek") -> dict:
    g = coattention(store)
    areas_path = Path(areas_path)
    saved = json.loads(areas_path.read_text()) if areas_path.exists() else {}
    reviewed = bool(saved.get("reviewed"))
    areas, assignment = {}, {}
    if reviewed:  # Malek's areas win for every project he placed
        for slug, a in saved.get("areas", {}).items():
            if slug == UNSORTED:
                continue
            areas[slug] = {"name": a.get("name", slug), "color": a.get("color"),
                           "projects": [p for p in a.get("projects", []) if g.has_node(p)]}
            for p in areas[slug]["projects"]:
                assignment[p] = slug
        for p in sorted(n for n in g.nodes if n not in assignment):  # new projects join where their attention is
            weight = Counter()
            for _, v, d in g.edges(p, data=True):
                if v in assignment:
                    weight[assignment[v]] += d["weight"]
            total = sum(d["weight"] for _, _, d in g.edges(p, data=True))
            if weight and total and weight.most_common(1)[0][1] / total >= MIN_CONFIDENCE:
                slug = weight.most_common(1)[0][0]
                assignment[p] = slug
                areas[slug]["projects"].append(p)
    free = g.subgraph(n for n in g.nodes if n not in assignment)
    if free.number_of_edges():
        for community in nx.community.louvain_communities(free, weight="weight", seed=7):
            members = {p for p in community if _share(free, p, community) >= MIN_CONFIDENCE}
            if len(members) < 2:
                continue
            name = _name(store, members, free)
            slug = _slug(name, areas)
            areas[slug] = {"name": name, "color": None, "projects": sorted(members)}
            for p in members:
                assignment[p] = slug
    areas[UNSORTED] = {"name": "Unsorted", "color": "#6b7280",
                       "projects": sorted(p for p in g.nodes if p not in assignment)}
    used = {a["color"] for a in areas.values() if a["color"]}
    palette = [c for c in PALETTE if c not in used] or PALETTE
    for i, a in enumerate(a for a in areas.values() if not a["color"]):
        a["color"] = palette[i % len(palette)]
    store.upsert_node("self", "self", 0, owner)
    for slug, a in areas.items():
        aid = f"area:{slug}"
        store.upsert_node(aid, "area", 1, a["name"], parent="self", color=a["color"])
        store.add_edge("self", aid, "tree")
        for p in a["projects"]:
            store.set_parent(p, aid)
            store.add_edge(aid, p, "tree")
    for a, b, d in g.edges(data=True):
        store.add_edge(a, b, "co_attention", round(d["weight"], 4))
    areas_path.parent.mkdir(parents=True, exist_ok=True)
    areas_path.write_text(json.dumps({"reviewed": reviewed, "areas": areas}, indent=1, ensure_ascii=False))
    return {"areas": len(areas) - 1, "unsorted": len(areas[UNSORTED]["projects"])}
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_areas.py -q` -> `2 passed`
- [ ] **Step 5: Commit** `git add -A && git commit -m "Life areas from co-attention, review-aware areas.json"`

---

### Task 8: Export, pipeline, CLI

**Files:** Create `neurotree/export.py`, `neurotree/pipeline.py`, `neurotree/__main__.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `export(store, out_path, max_files=6000, event_days=120, now=None) -> dict` (counts); graph.json = `{"meta": {generated_at, weights, counts, status}, "nodes": [{id, kind, level, parent, label, path, project, lod, color, type, importance, attentionRank, signals, noise, evidence, lastActivity}], "edges": [{source, target, kind, weight}], "events": [[id, ts, kind, weight], ...] (ascending ts)}`; `run_all(settings=None, home=None, now=None, log=print) -> dict` (status per step); CLI `python -m neurotree all [--home DIR] [--settings FILE]`. Files written: `<home>/graph.db`, `<home>/graph.json`, `<home>/status.json`, `<home>/areas.json`.

- [ ] **Step 1: Write the failing test**

**File:** `tests/test_pipeline.py`
```python
import builtins
import io
import json

from neurotree.pipeline import run_all


def test_full_run_on_fixture_world(world):
    status = run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    assert all("error" not in v for v in status.values()), status
    assert status["claude_sessions"]["errors"] == 1  # the broken log is counted, not fatal
    data = json.loads((world["home"] / "graph.json").read_text())
    ids = {n["id"] for n in data["nodes"]}
    a = world["alpha"]
    assert {"self", f"project:dir:{a}", "project:gh:malek1414/beta", f"file:{a}/pkg/core.py"} <= ids
    assert all(e["source"] in ids and e["target"] in ids for e in data["edges"])
    assert [e[1] for e in data["events"]] == sorted(e[1] for e in data["events"])
    files = {n["id"]: n for n in data["nodes"] if n["level"] == 3}
    alpha_files = [n for n in files.values() if n["project"] == f"project:dir:{a}"]
    assert max(alpha_files, key=lambda n: n["importance"])["id"] == f"file:{a}/pkg/core.py"
    core = files[f"file:{a}/pkg/core.py"]
    assert core["lod"] == 0 and core["evidence"] and core["signals"]["attention"] == 1.0
    alpha, beta = (next(n for n in data["nodes"] if n["id"] == i) for i in (f"project:dir:{a}", "project:gh:malek1414/beta"))
    assert alpha["parent"] == beta["parent"] != "area:unsorted"
    assert (world["home"] / "status.json").exists() and (world["home"] / "graph.db").exists()
    assert not (world["home"] / "graph.db.building").exists()


def test_secrets_and_prompts_never_touched(world, monkeypatch):
    opened = []
    real_open = builtins.open

    def spy(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    monkeypatch.undo()
    assert not [p for p in opened if p.endswith((".env", "credentials.json", "secret-plan.md"))]
    raw = (world["home"] / "graph.db").read_bytes() + (world["home"] / "graph.json").read_bytes()
    assert b"TOP-SECRET-PROMPT-TEXT" not in raw and b"never read me" not in raw


def test_broken_source_is_isolated(world):
    world["mem"].write_text("this is not a database")
    status = run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    assert "error" in status["claude_mem"]
    assert status["git"]["ok"] == 2 and (world["home"] / "graph.json").exists()
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_pipeline.py -q` -> FAIL (no module `neurotree.pipeline`)

- [ ] **Step 3: Implement**

**File:** `neurotree/export.py`
```python
"""Store -> graph.json for the viewer: scored nodes with level-of-detail flags, edges, recent events."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .scoring import WEIGHTS


def export(store, out_path, max_files: int = 6000, event_days: int = 120, now: float | None = None) -> dict:
    now = now or time.time()
    out_path = Path(out_path)
    scores, nodes = store.scores(), store.nodes()
    imp = lambda n: scores.get(n["id"], {}).get("importance") or 0.0  # noqa: E731
    default_files = {n["id"] for n in sorted((n for n in nodes if n["level"] == 3), key=imp, reverse=True)[:max_files]}
    events = store.events()
    last = {}
    for e in events:
        if e["ts"] > last.get(e["item"], 0):
            last[e["item"]] = e["ts"]
    out_nodes = []
    for n in nodes:
        s = scores.get(n["id"], {})
        lod = 0 if n["level"] <= 2 or n["id"] in default_files else (1 if n["level"] == 3 else 2)
        out_nodes.append({"id": n["id"], "kind": n["kind"], "level": n["level"], "parent": n["parent"],
                          "label": n["label"], "path": n["path"], "project": n["project"], "lod": lod,
                          "color": n["meta"].get("color"), "type": n["meta"].get("kind_detail"),
                          "importance": s.get("importance", 0.0), "attentionRank": s.get("attention_rank", 0.0),
                          "signals": {k: s.get(k) for k in WEIGHTS}, "noise": s.get("noise", 1.0),
                          "evidence": s.get("evidence", []), "lastActivity": last.get(n["id"])})
    ids = {n["id"] for n in out_nodes}
    edges = [{"source": e["src"], "target": e["dst"], "kind": e["kind"], "weight": e["weight"]}
             for e in store.edges() if e["src"] in ids and e["dst"] in ids]
    cutoff = now - event_days * 86400
    recent = sorted(([e["item"], round(e["ts"], 1), e["kind"], round(e["weight"], 2)] for e in events
                     if e["ts"] >= cutoff and e["item"] in ids), key=lambda r: r[1])
    counts = {"nodes": len(out_nodes), "edges": len(edges), "events": len(recent),
              "projects": sum(n["level"] == 2 for n in out_nodes), "files": sum(n["level"] == 3 for n in out_nodes)}
    data = {"meta": {"generated_at": now, "weights": WEIGHTS, "counts": counts, "status": store.get_meta("status", {})},
            "nodes": out_nodes, "edges": edges, "events": recent}
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out_path)
    return counts
```

**File:** `neurotree/pipeline.py`
```python
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
```

**File:** `neurotree/__main__.py`
```python
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
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q` -> all tests pass (21)
- [ ] **Step 5: Commit** `git add -A && git commit -m "Export, pipeline and CLI"`

---

### Task 9: First real run on this Mac

- [ ] **Step 1:** `.venv/bin/python -m neurotree all` (writes to `~/Library/Application Support/Neurotree/`).
- [ ] **Step 2:** Check: every step without `error`; counts plausible (repos ≈ 118 local, sessions ≈ 963 with few errors, memory ≈ 27,868 observations); the top 10 files by importance are things Malek actually worked on recently (Arvantir, Neurotree, morgana…), and no path in graph.json matches a secret pattern:
  `.venv/bin/python - <<'EOF'` script that loads graph.json, prints the top 10 projects and files with evidence, and asserts no node path contains `/.ssh/`, `.env`, `id_rsa`, `credential`, `secret`.
- [ ] **Step 3:** Record timings in `docs/superpowers/plans/2026-10-31-neurotree-indexer.md` (this section) and commit code only (no data).
