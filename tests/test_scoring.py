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
