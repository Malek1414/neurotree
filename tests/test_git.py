from neurotree.sources.git_repos import (find_repos, index_repo, normalize_remote, numstat_path,
                                         shared_code_edges)
from neurotree.store import Store
from neurotree.structure import add_structure, pagerank_by_project


def test_helpers():
    assert normalize_remote("git@github.com:Malek1414/Beta.git") == "gh:malek1414/beta"
    assert normalize_remote("https://github.com/sami-mag07/searchify/") == "gh:sami-mag07/searchify"
    assert normalize_remote("https://gitlab.com/x/y") is None
    assert numstat_path("src/{a.py => b.py}") == "src/b.py"
    assert numstat_path("{old => new}/f.py") == "new/f.py"
    assert numstat_path("src/{ => sub}/f.py") == "src/sub/f.py"
    assert numstat_path("a.py => b.py") == "b.py"
    assert numstat_path("plain.py") == "plain.py"


def test_index_repo_and_structure(world, tmp_path):
    repos = find_repos(world["settings"].repo_roots)
    assert [r.name for r in repos] == ["alpha", "beta"]
    s = Store(tmp_path / "g.db")
    info = index_repo(s, world["alpha"], 365, now=world["now"])
    a = world["alpha"]
    assert info["project"] == f"project:dir:{a}"
    rels = set(info["kept"])
    assert {"pkg/core.py", "pkg/b.py", "web/app.ts", "package-lock.json", "tests/test_core.py"} <= rels
    assert "credentials.json" not in rels and ".env" not in rels
    commits = [e for e in s.events(("git",)) if e["item"] == f"file:{a}/pkg/core.py"]
    assert len(commits) == 2 and all(e["kind"] == "commit" for e in commits)
    assert s.node(f"file:{a}/package-lock.json")["meta"]["noise"] == 0.1
    beta = index_repo(s, world["beta"], 365, now=world["now"])
    assert beta["project"] == "project:gh:malek1414/beta"
    st = add_structure(s, a, info["kept"])
    assert st["imports"] >= 4 and st["functions"] >= 2
    imports = {(e["src"].rsplit("/", 1)[-1], e["dst"].rsplit("/", 1)[-1]) for e in s.edges("import")}
    assert ("b.py", "core.py") in imports and ("c.py", "core.py") in imports and ("app.ts", "util.ts") in imports
    assert s.node(f"fn:{a}/pkg/core.py#Engine")["parent"] == f"file:{a}/pkg/core.py"
    pr = pagerank_by_project(s)
    assert max(pr, key=pr.get) == f"file:{a}/pkg/core.py" and pr[f"file:{a}/pkg/core.py"] == 1.0


def test_shared_code(tmp_path):
    s = Store(tmp_path / "g.db")
    for p in ("project:a", "project:b"):
        s.upsert_node(p, "project", 2, p)
    s.upsert_node("file:/a/util.py", "file", 3, "util.py", project="project:a", noise=1.0)
    s.upsert_node("file:/b/util.py", "file", 3, "util.py", project="project:b", noise=1.0)
    s.upsert_node("file:/a/LICENSE.md", "file", 3, "LICENSE.md", project="project:a", noise=1.0)
    s.upsert_node("file:/b/LICENSE.md", "file", 3, "LICENSE.md", project="project:b", noise=1.0)
    n = shared_code_edges(s, {"file:/a/util.py": "abc", "file:/b/util.py": "abc",
                              "file:/a/LICENSE.md": "lic", "file:/b/LICENSE.md": "lic"})
    assert n == 1 and s.edges("shared_code")[0]["weight"] == 1.0


def test_second_clone_is_an_alias_not_a_duplicate(world, tmp_path):
    import shutil
    import subprocess
    from neurotree.paths import PathIndex
    from neurotree.pipeline import run_all
    copy = world["root"] / "Desktop" / "copies" / "beta"
    shutil.copytree(world["beta"], copy)
    env = {"GIT_AUTHOR_DATE": "@1700000000 +0000", "GIT_COMMITTER_DATE": "@1700000000 +0000"}
    subprocess.run(["git", "-C", str(copy), "reset", "-q", "--hard"], check=True)
    # make the copy's history older than the original's
    subprocess.run(["git", "-C", str(copy), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--amend",
                    "--no-edit", "--date=@1700000000"], check=True, env={**__import__("os").environ, **env})
    run_all(world["settings"], home=world["home"], now=world["now"], log=lambda *_: None)
    from neurotree.store import Store
    s = Store(world["home"] / "graph.db")
    beta = s.node("project:gh:malek1414/beta")
    assert beta["path"] == str(world["beta"]) and beta["meta"]["clones"] == [str(copy)]
    assert [n["path"] for n in s.nodes("file") if n["project"] == "project:gh:malek1414/beta"] == [str(world["beta"] / "main.py")]
    idx = PathIndex(s)
    assert idx.resolve(str(copy / "main.py")) == "file:" + str(world["beta"] / "main.py")
    assert idx.project_of(str(copy / "x")) == "project:gh:malek1414/beta"
