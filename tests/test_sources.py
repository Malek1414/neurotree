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
