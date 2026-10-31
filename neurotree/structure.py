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
