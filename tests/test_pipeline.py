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
    alpha_node = next(n for n in data["nodes"] if n["id"] == f"project:dir:{a}")
    assert alpha_node["lastActivity"] >= core["lastActivity"]  # parents show their newest child activity
    alpha, beta = (next(n for n in data["nodes"] if n["id"] == i) for i in (f"project:dir:{a}", "project:gh:malek1414/beta"))
    assert alpha["parent"] == beta["parent"] != "area:unsorted"
    assert (world["home"] / "status.json").exists() and (world["home"] / "graph.db").exists()
    assert not list(world["home"].glob("graph.db.building*"))


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


def test_only_one_run_at_a_time(world):
    import fcntl
    world["home"].mkdir(parents=True)
    with open(world["home"] / ".index.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    assert "skipped" in status and not (world["home"] / "graph.json").exists()


def test_broken_source_is_isolated(world):
    world["mem"].write_text("this is not a database")
    status = run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    assert "error" in status["claude_mem"]
    assert status["git"]["ok"] == 2 and (world["home"] / "graph.json").exists()
