"""CLI for an existing PostgreSQL server; credentials only come from the environment."""

import argparse
from datetime import datetime
from decimal import Decimal
import json
import os
import sys

import psycopg

from .database import RecipeStore
from .importer import load_export
from .queries import NutrientRange, RecipeFilter


def _nutrient(value: str) -> NutrientRange:
    try:
        name, basis, low, high = value.split(":")
        return NutrientRange(name, float(low) if low else None, float(high) if high else None, basis)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use NAME:BASIS:MIN:MAX, e.g. protein_g:per_serving:20:") from error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="ChefMate current recipe dataset on an existing PostgreSQL server")
    result.add_argument("--schema", default="chefmate", help="Dedicated PostgreSQL schema (default: chefmate)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Initialize/upgrade package tables without deleting data")
    commands.add_parser("metadata", help="Show current dataset and database schema versions")
    for name in ("validate", "import"):
        command = commands.add_parser(name, help="Validate an export" if name == "validate" else "Replace current data")
        command.add_argument("directory")
        command.add_argument("--data-version")
        command.add_argument("--allow-unapproved", action="store_true", help="Explicitly permit diagnostic exports")
        command.add_argument("--allow-empty", action="store_true", help="Explicitly permit an empty complete dataset")
        if name == "import":
            command.add_argument("--replace", action="store_true", required=True, help="Replace the entire current dataset")
    commands.add_parser("get", help="Read selected recipe fields").add_argument("recipe_id")
    search = commands.add_parser("search", help="Filter and paginate in PostgreSQL")
    search.add_argument("--name")
    for name in ("ingredient", "exclude-ingredient", "taste", "method", "tool", "nutrition-tag", "exclude-allergen"):
        search.add_argument("--" + name, action="append", default=[])
    search.add_argument("--max-time", type=float)
    search.add_argument("--min-servings", type=float)
    search.add_argument("--include-unready", action="store_true")
    search.add_argument("--nutrient", type=_nutrient, action="append", default=[])
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--offset", type=int, default=0)
    return result


def _serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _run(args):
    if args.command == "validate":
        snapshot = load_export(args.directory, data_version=args.data_version,
                               allow_unapproved=args.allow_unapproved, allow_empty=args.allow_empty)
        return {"data_version": snapshot.data_version, "export_schema_version": snapshot.export_schema_version,
                "exported_at": snapshot.exported_at, "recipe_count": snapshot.recipe_count,
                "rows": {name: len(rows) for name, rows in snapshot.tables.items()}}
    dsn = os.environ.get("CHEFMATE_DATABASE_URL")
    if not dsn:
        raise ValueError("Set CHEFMATE_DATABASE_URL for database commands")
    store = RecipeStore(dsn, schema=args.schema)
    if args.command == "migrate":
        return {"db_schema_version": store.migrate()}
    if args.command == "metadata":
        return store.metadata()
    if args.command == "import":
        return store.replace_from_export(args.directory, data_version=args.data_version,
                                         allow_unapproved=args.allow_unapproved, allow_empty=args.allow_empty)
    if args.command == "get":
        return store.get_recipe(args.recipe_id)
    return store.search(RecipeFilter(
        name=args.name, ingredient_ids=tuple(args.ingredient), exclude_ingredient_ids=tuple(args.exclude_ingredient),
        taste_tags=tuple(args.taste), cooking_methods=tuple(args.method), required_tools=tuple(args.tool),
        nutrition_tags=tuple(args.nutrition_tag), exclude_allergens=tuple(args.exclude_allergen),
        max_total_time_min=args.max_time, min_servings=args.min_servings,
        ready_for_recommendation=None if args.include_unready else True, nutrients=tuple(args.nutrient),
    ), limit=args.limit, offset=args.offset)


def main(argv=None) -> int:
    # JSON output is UTF-8 even when Windows redirects a legacy-codepage console.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        output = _run(args)
        print(json.dumps(output, ensure_ascii=False, indent=2, default=_serialize, allow_nan=False))
        return 0
    except psycopg.Error as error:
        # Server errors may echo rejected row values or connection credentials.
        print(f"Database operation failed ({error.sqlstate or type(error).__name__}); "
              "check connection, permissions, schema and constraints. No replacement was committed.", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
