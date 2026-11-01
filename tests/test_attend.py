import json
import os

from neurotree import attend as A
from neurotree.keys import summary_of, words
from neurotree.paths import Translator
from neurotree.pipeline import run_all

DAY = 86400.0


def test_words_split_identifiers():
    assert words("pagerank_by_project") == "pagerank by project"
    assert words("fitGrid") == "fit grid" and words("HTMLParser.ts") == "html parser ts"


def test_summaries_are_the_first_thing_a_file_says():
    assert summary_of("a.py", '#!/usr/bin/env python\n"""Signals -> importance, with evidence."""\n') == \
        "Signals -> importance, with evidence."
    assert summary_of("README.md", "# Neurotree\n\nA 3D attention tree of this Mac.\n") == \
        "Neurotree: A 3D attention tree of this Mac."
    assert summary_of("notes.md", "---\ntitle: x\n---\n# Launch plan\nbody\n") == "Launch plan"
    header = "/*---\n * Copyright (c) Microsoft Corporation. All rights reserved.\n * Licensed under the MIT License.\n *---*/\nimport x\n"
    assert summary_of("a.ts", header) == ""
    assert summary_of("x.sh", "#!/bin/zsh\n# Stage prep: free the GPU for the demo\n") == "Stage prep: free the GPU for the demo"
    assert summary_of("data.txt", '{"data": null}\n') == ""


def _index(world):
    status = run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    assert "error" not in status["keys"], status["keys"]
    return A.load(world["home"] / "graph.db")


def test_a_question_attends_to_the_right_file(world):
    ix = _index(world)
    a = world["alpha"]
    res = A.attend(ix, "the core engine run function", cwd=str(a), as_of=world["now"])
    files = [ix.ids[u] for u, _ in res["units"] if ix.kind[u] == "file"]
    assert files[0] == f"file:{a}/pkg/core.py"
    assert abs(sum(m for _, m in res["units"]) - 1.0) < 1e-6, "attention is a distribution"
    text = A.report(ix, "core engine", cwd=str(a))
    assert f"{a}/pkg/core.py" in text and "why:" in text


def test_replaying_the_past_cannot_see_later_memory(world):
    ix = _index(world)
    a = world["alpha"]
    fact = ix.by_id["fact:1"]  # "Core returns 2 now", written a day before NOW
    later = A.attend(ix, "core returns", cwd=str(a), as_of=world["now"])
    assert any(b == fact for b, _ in later["best"].values())
    before = A.attend(ix, "core returns", cwd=str(a), as_of=world["now"] - 2 * DAY)
    assert all(b != fact for b, _ in before["best"].values())


def test_second_run_reuses_repos_and_session_logs(world):
    first = run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    graph1 = json.loads((world["home"] / "graph.json").read_text())
    second = run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    graph2 = json.loads((world["home"] / "graph.json").read_text())
    assert first["git"]["reused"] == 0 and second["git"]["reused"] == 2
    assert second["claude_sessions"]["parsed"] == 0
    imp = lambda g: {n["id"]: n["importance"] for n in g["nodes"]}  # noqa: E731
    assert imp(graph1) == imp(graph2) and len(graph1["edges"]) == len(graph2["edges"])
    (world["alpha"] / "pkg" / "core.py").write_text('"""core."""\ndef run():\n    return 3\n\ndef stop():\n    pass\n')
    third = run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    assert third["git"]["reused"] == 1, "an uncommitted edit redoes that repo only"
    ix = A.load(world["home"] / "graph.db")
    assert f"fn:{world['alpha']}/pkg/core.py#stop" in ix.by_id


def test_moved_projects_and_worktrees_keep_their_attention(tmp_path):
    new = tmp_path / "Desktop" / "Projects" / "simulacrum"
    (new / "src").mkdir(parents=True)
    (new / "src" / "x.py").write_text("")
    tr = Translator([str(new)])
    old = str(tmp_path / "Desktop" / "simulacrum" / "src" / "x.py")
    assert tr(old) == str(new / "src" / "x.py")
    assert tr(str(new / ".claude" / "worktrees" / "agent-1" / "src" / "x.py")) == str(new / "src" / "x.py")
    assert tr(str(tmp_path / "elsewhere" / "y.py")) == str(tmp_path / "elsewhere" / "y.py"), "unknown stays as is"


def test_without_the_model_the_lexical_head_still_works(world, monkeypatch):
    import neurotree.keys as keys
    monkeypatch.setattr(keys, "load_model", lambda: None)
    ix = _index(world)
    assert ix.vecs is None
    res = A.attend(ix, "engine", cwd=str(world["alpha"]))
    assert res["units"] and ix.label[res["units"][0][0]] == "core.py"
    assert os.path.exists(world["home"] / "cache.db")
