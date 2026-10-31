"""Hard exclusions: paths Neurotree never reads, whatever the settings say."""
from __future__ import annotations

import fnmatch
from pathlib import Path

HOME = Path.home()
NEVER_DIRS = tuple(HOME / d for d in (".ssh", ".gnupg", ".aws", ".kube", ".docker", "Library"))
SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx", "*.keychain", "*.keychain-db",
                   "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "*credential*", "*secret*",
                   ".netrc", ".npmrc", ".pypirc")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache", "target",
             ".tox", ".mypy_cache", ".pytest_cache", "Pods", "DerivedData", ".gradle", ".idea", ".turbo"}


def is_secret_name(name: str) -> bool:
    n = name.lower()
    return any(fnmatch.fnmatch(n, p) for p in SECRET_PATTERNS)


def is_excluded(path, extra=()) -> bool:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.absolute()
    for d in NEVER_DIRS:
        if p == d or d in p.parents:
            return True
    if any(is_secret_name(part) or part in SKIP_DIRS for part in p.parts):  # folders named like secrets too
        return True
    s = str(p)
    for pattern in extra:
        e = str(Path(pattern).expanduser())
        if s == e or s.startswith(e.rstrip("/") + "/") or fnmatch.fnmatch(s, e):
            return True
    return False
