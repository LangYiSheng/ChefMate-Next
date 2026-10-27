"""Container readiness includes the successful bundled-catalog init marker."""

import json
import os
from pathlib import Path
from urllib.request import urlopen


def ready():
    if os.environ.get("CHEFMATE_CATALOG_SEED_REQUIRED", "false").lower() == "true":
        manifest = json.loads(Path("/app/data/seed/catalog/manifest.json").read_text(encoding="utf-8"))
        marker = json.loads(Path("/var/lib/chefmate-catalog-status/ready.json").read_text(encoding="utf-8"))
        if (marker.get("ready") is not True or marker.get("data_version") != manifest.get("data_version")
                or marker.get("recipes_sha256") != manifest.get("sha256", {}).get("recipes.json")
                or marker.get("recipe_count") != manifest.get("recipe_count")):
            return False
    port = os.environ.get("CHEFMATE_PLAYGROUND_PORT", "18765")
    with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
        result = json.load(response)
    return result.get("status") == "ok" and result.get("service") == "chefmate"


if __name__ == "__main__":
    try:
        raise SystemExit(0 if ready() else 1)
    except (OSError, ValueError, KeyError):
        raise SystemExit(1) from None
