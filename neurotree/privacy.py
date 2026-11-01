"""Hard exclusions: paths Neurotree never reads, whatever the settings say."""
from __future__ import annotations

import fnmatch
import os
import re
from functools import lru_cache
from pathlib import Path

HOME = Path.home()
NEVER_DIRS = tuple(HOME / d for d in (".ssh", ".gnupg", ".aws", ".kube", ".docker", "Library"))
SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx", "*.keychain", "*.keychain-db",
                   "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "*credential*", "*secret*",
                   ".netrc", ".npmrc", ".pypirc")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache", "target",
             ".tox", ".mypy_cache", ".pytest_cache", "Pods", "DerivedData", ".gradle", ".idea", ".turbo"}


_SECRET = re.compile("|".join(fnmatch.translate(p) for p in SECRET_PATTERNS))
_NEVER = tuple(str(d) for d in NEVER_DIRS)


@lru_cache(maxsize=65536)
def _bad_part(part: str) -> bool:
    return part in SKIP_DIRS or bool(_SECRET.match(part.lower()))


def is_secret_name(name: str) -> bool:
    return bool(_SECRET.match(name.lower()))


def is_excluded(path, extra=()) -> bool:
    """Plain string checks (called for every file of every index run: pathlib and fnmatch were ~1.5 ms a path)."""
    s = os.path.abspath(os.path.expanduser(os.fspath(path)))
    for d in _NEVER:
        if s == d or s.startswith(d + "/"):
            return True
    if any(_bad_part(part) for part in s.split("/") if part):  # folders named like secrets too
        return True
    for pattern in extra:
        e = os.path.expanduser(str(pattern))
        if s == e or s.startswith(e.rstrip("/") + "/") or fnmatch.fnmatch(s, e):
            return True
    return False
