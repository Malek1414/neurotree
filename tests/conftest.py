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
