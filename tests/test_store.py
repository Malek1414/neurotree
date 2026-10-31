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
