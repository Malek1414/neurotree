"""GitHub repos via the gh CLI (Malek's own login) -> project nodes for repos without a local clone, push events."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime

FIELDS = "nameWithOwner,url,pushedAt,description,primaryLanguage,isFork"


def index_github(store, owner: str | None = None, limit: int = 300, runner=subprocess.run) -> dict:
    try:
        if not owner:
            owner = runner(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True,
                           timeout=30).stdout.strip()
        out = runner(["gh", "repo", "list", owner, "--limit", str(limit), "--json", FIELDS],
                     capture_output=True, text=True, timeout=60)
        repos = json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"ok": 0, "errors": 1, "error": type(e).__name__}
    events, new = [], 0
    for r in repos:
        pid = "project:gh:" + r["nameWithOwner"].lower()
        existing = store.node(pid)
        new += existing is None
        label = existing["label"] if existing else r["nameWithOwner"].split("/", 1)[1]
        store.upsert_node(pid, "project", 2, label, url=r.get("url"), description=r.get("description") or "",
                          language=(r.get("primaryLanguage") or {}).get("name"), fork=bool(r.get("isFork")),
                          github=True)
        if r.get("pushedAt"):
            ts = datetime.fromisoformat(r["pushedAt"].replace("Z", "+00:00")).timestamp()
            events.append((pid, ts, "push", 1.0, "github", None))
    store.add_events(events)
    return {"ok": len(repos), "new": new, "events": len(events)}
