"""Content and runtime identities shared by local experiment caches."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
import platform
import sys

from .dataset import sha256_file, stable_hash


def runtime_identity() -> dict:
    packages = sorted(
        (distribution.metadata["Name"].lower().replace("_", "-"),
         distribution.version)
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    )
    return {
        "python": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
    }


def implementation_fingerprint(roots: dict[str, tuple[Path, tuple[str, ...]]]) -> str:
    sources = {}
    for label, (root, packages) in sorted(roots.items()):
        root = Path(root).resolve()
        files = set()
        for package in packages:
            files.update((root / package).rglob("*.py"))
        for pattern in ("pyproject.toml", "requirements*.txt", "requirements*.lock",
                        "constraints*.txt"):
            files.update(root.glob(pattern))
        sources[label] = {
            "root": str(root),
            "files": {path.relative_to(root).as_posix(): sha256_file(path)
                      for path in sorted(files) if path.is_file()},
        }
    return stable_hash({"schema": 1, "sources": sources,
                        "runtime": runtime_identity()})
