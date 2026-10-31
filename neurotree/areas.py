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
