"""Claude Code sessions (~/.claude/projects/**/*.jsonl) -> attention events.
Keeps only file paths, times and session ids; message text is never stored."""
from __future__ import annotations

import glob
import itertools
import json
import os
import re
from datetime import datetime
from pathlib import Path

from ..cache import file_stamp

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "mcp__plugin_mozcode_mozcode__code_edit"}
READ_TOOLS = {"Read", "mcp__plugin_mozcode_mozcode__code_read", "mcp__plugin_mozcode_mozcode__code_outline"}
SEARCH_TOOLS = {"Glob", "Grep", "LS"}
PATH_KEYS = ("file_path", "notebook_path", "path")
ABS_PATH = re.compile(r"""(?:(?<=[\s'"=(])|^)((?:~/|/)[\w.@+-][^\s'"|;&<>()`$]*)""")
WEIGHT = {"edit": 3.0, "read": 1.0, "search": 0.5, "mention": 0.5}


def _ts(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def session_files(root: str) -> list[str]:
    return sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))


def parse_session(path: str) -> dict:
    sid = cwd = start = None
    touches = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        first = fh.readline()
        json.loads(first)  # ValueError: not a JSON-lines session log
        for line in itertools.chain([first], fh):
            if not (cwd is None and '"cwd"' in line) and '"tool_use"' not in line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if cwd is None and m.get("cwd"):
                cwd, start = m["cwd"], _ts(m.get("timestamp"))
            sid = sid or m.get("sessionId")
            msg = m.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            ts = _ts(m.get("timestamp"))
            if ts is None or not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool_use" or not isinstance(c.get("input"), dict):
                    continue
                name, inp = c.get("name", ""), c["input"]
                if name in EDIT_TOOLS or name in READ_TOOLS:
                    raw = next((inp[k] for k in PATH_KEYS if isinstance(inp.get(k), str)), None)
                    kind = "edit" if name in EDIT_TOOLS else "read"
                    if raw:
                        touches.append((raw, ts, kind, WEIGHT[kind]))
                elif name in SEARCH_TOOLS and isinstance(inp.get("path"), str):
                    touches.append((inp["path"], ts, "search", WEIGHT["search"]))
                elif name == "Bash" and isinstance(inp.get("command"), str):
                    for raw in sorted(set(ABS_PATH.findall(inp["command"])))[:20]:
                        touches.append((raw, ts, "mention", WEIGHT["mention"]))
    return {"session": sid or Path(path).stem, "cwd": cwd, "start": start, "touches": touches}


def index_sessions(store, index, root: str, cache=None) -> dict:
    """With a cache, only logs that changed since the last run are parsed again (the live one keeps growing,
    the other thousand don't)."""
    ok = bad = parsed = 0
    events = []
    if cache:
        cache.preload("session")
    for f in session_files(root):
        stamp = file_stamp(f) if cache else None
        s = cache.get("session", f, stamp) if stamp else None
        if s is None:
            parsed += 1
            try:
                s = parse_session(f)
            except (OSError, ValueError):
                s = {"error": True}
            if stamp:
                cache.put("session", f, stamp, s)
        if s.get("error"):
            bad += 1
            continue
        ok += 1
        if s["cwd"] and s["start"]:
            pid = index.project_of(os.path.normpath(s["cwd"]))
            if pid:
                events.append((pid, s["start"], "session", 2.0, "claude_sessions", s["session"]))
        for raw, ts, kind, weight in s["touches"]:
            node = index.resolve(raw, s["cwd"])
            if node:
                events.append((node, ts, kind, weight, "claude_sessions", s["session"]))
    store.add_events(events)
    return {"ok": ok, "errors": bad, "events": len(events), "parsed": parsed}
