"""Initialize the fixed bundled catalog without replacing existing data."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import psycopg
from chefmate_storage import RecipeStore
from chefmate_storage.database import SeedConflictError

BUNDLED_VERSION = "chefmate-bundled-20261005"
BUNDLED_SHA256 = "534cf9e71d0977b4b23d57ab23a46444bf06510f75fab87f642cee105009e04e"
DIRECTORY = Path("/app/data/seed/catalog")
STATUS = Path("/var/lib/chefmate-catalog-status/ready.json")


def initialize(directory=DIRECTORY, status=STATUS):
    status.parent.mkdir(parents=True, exist_ok=True)
    status.unlink(missing_ok=True)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("data_version") != BUNDLED_VERSION
            or manifest.get("sha256", {}).get("recipes.json") != BUNDLED_SHA256
            or hashlib.sha256((directory / "recipes.json").read_bytes()).hexdigest() != BUNDLED_SHA256):
        raise ValueError("Bundled catalog identity or checksum is invalid")
    store = RecipeStore(os.environ.get("CHEFMATE_DATABASE_URL", ""),
                        schema=os.environ.get("CHEFMATE_DATABASE_SCHEMA", "chefmate"))
    store.migrate()
    # Only this fixed published source is opted into unapproved import. Its
    # review/readiness/nutrition flags remain unchanged during initialization.
    result = store.seed_from_export(directory, data_version=BUNDLED_VERSION, allow_unapproved=True)
    marker = {"ready": True, "data_version": BUNDLED_VERSION, "recipes_sha256": BUNDLED_SHA256,
              "recipe_count": result["recipe_count"], "source_approved_only": manifest["approved_only"]}
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=status.parent, delete=False, suffix=".tmp") as stream:
            temporary = Path(stream.name)
            json.dump(marker, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(status)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {**marker, "status": result["status"]}


if __name__ == "__main__":
    try:
        print(json.dumps(initialize(), ensure_ascii=False))
    except SeedConflictError:
        raise SystemExit("Catalog init refused to overwrite a different or modified dataset; existing data retained.") from None
    except (ValueError, RuntimeError, OSError, psycopg.Error):
        raise SystemExit("Catalog initialization failed; verify bundled files, database connection and schema. No connection secrets logged.") from None
