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
