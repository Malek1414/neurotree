"""What a file is (code or document) and how much of it is noise."""
from __future__ import annotations

import os

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".swift", ".rs", ".go", ".java", ".kt", ".rb",
            ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".sql", ".sh", ".html", ".css", ".scss", ".vue", ".svelte",
            ".ipynb", ".json", ".yaml", ".yml", ".toml"}
DOC_EXT = {".md", ".txt", ".pdf", ".docx", ".pptx", ".pages", ".xlsx", ".csv", ".rtf"}
LOCK_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "uv.lock",
              "pipfile.lock", "gemfile.lock", "composer.lock", "bun.lockb"}
NOISE_DIRS = ("vendor", "third_party", "generated", "fixtures", "__snapshots__")


def classify(path) -> str | None:
    ext = os.path.splitext(str(path))[1].lower()
    if ext in CODE_EXT:
        return "code"
    if ext in DOC_EXT:
        return "doc"
    return None


def noise_factor(rel: str) -> float:
    """0.1 for lockfiles, minified, vendored, generated and fixture files; 0.5 for tests; else 1."""
    low = "/" + rel.lower().strip("/")
    name = low.rsplit("/", 1)[-1]
    if name in LOCK_NAMES or name.endswith(".lock") or ".min." in name or any(f"/{d}/" in low for d in NOISE_DIRS):
        return 0.1
    if "/tests/" in low or "/test/" in low or "/__tests__/" in low or name.startswith("test_") \
            or ".test." in name or ".spec." in name or name.endswith("_test.py"):
        return 0.5
    return 1.0
