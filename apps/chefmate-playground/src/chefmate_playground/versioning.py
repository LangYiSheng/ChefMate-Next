"""Read-only build identity; source archives can omit Git metadata."""

import json
import re
import subprocess
import tomllib
from pathlib import Path


def current_version(root: Path) -> str:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def release_history(root: Path) -> dict:
    return {
        "current_version": current_version(root),
        "releases": json.loads((root / "releases.json").read_text(encoding="utf-8")),
    }


def current_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{7,40}", commit) else None
