"""Settings and the one folder Neurotree writes to."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()


def data_dir() -> Path:
    d = Path(os.environ.get("NEUROTREE_HOME", HOME / "Library" / "Application Support" / "Neurotree"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Settings:
    repo_roots: list = field(default_factory=lambda: [str(HOME / "Desktop"), str(HOME / "Documents")])
    doc_roots: list = field(default_factory=lambda: [str(HOME / "Desktop"), str(HOME / "Documents")])
    claude_projects: str = str(HOME / ".claude" / "projects")
    claude_mem_db: str = str(HOME / ".claude-mem" / "claude-mem.db")
    sources: dict = field(default_factory=lambda: {"git": True, "github": True, "files": True,
                                                   "claude_sessions": True, "claude_mem": True})
    exclude: list = field(default_factory=list)
    history_days: int = 365
    owner_name: str = "Malek"


def load_settings(path: Path | None = None) -> Settings:
    """settings.json in the data folder overrides the defaults key by key."""
    path = Path(path) if path else data_dir() / "settings.json"
    s = Settings()
    if path.exists():
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            if hasattr(s, key):
                setattr(s, key, value)
    return s
