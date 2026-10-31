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
