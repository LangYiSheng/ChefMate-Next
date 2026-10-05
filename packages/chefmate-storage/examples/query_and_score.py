"""Read-only query and transparent scoring experiment, outside the storage API.

Run with CHEFMATE_DATABASE_URL set. Diagnostic data is only used when the caller
explicitly selects --include-unready. Scores describe a hypothetical pantry,
not health or nutritional suitability.
"""

import argparse
import json
import math
import os
import sys

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from chefmate_storage import NutrientRange, RecipeFilter, RecipeStore

NUTRIENTS = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg")
# Explicit hypothetical supplies, not inferred from the user's actual pantry.
STAPLES = (
    "salt", "water", "cooking_oil", "garlic", "ginger", "scallion", "light_soy_sauce", "dark_soy_sauce",
    "soy_sauce", "cooking_wine", "white_sugar", "sugar", "sesame_oil", "oyster_sauce", "starch",
)


def _query_examples(store, include_unready):
    ready = None if include_unready else True
    examples = (
        ("30分钟以内", RecipeFilter(ready_for_recommendation=ready, max_total_time_min=30)),
        ("含鸡蛋且30分钟以内", RecipeFilter(ready_for_recommendation=ready, ingredient_ids=("egg",),
                                      max_total_time_min=30)),
        ("蒸菜", RecipeFilter(ready_for_recommendation=ready, cooking_methods=("steam",))),
        ("每份蛋白质至少20g（仅测试字段筛选）", RecipeFilter(ready_for_recommendation=ready,
                                                nutrients=(NutrientRange("protein_g", minimum=20),))),
    )
    result = []
    for title, filters in examples:
        page = store.search(filters, limit=3)
        result.append({"query": title, "count": page["total"], "sample": [r["name"] for r in page["items"]]})
    return result


def rank_demo(dsn, *, schema="chefmate", pantry=("egg", "tomato", "potato"), max_time=30,
              limit=10, include_unready=False):
    """Compute a capped, deterministic 100-point score inside PostgreSQL."""
    # Reuse the storage package's schema/connection timeout validation.
    store = RecipeStore(dsn, schema=schema)
    if not isinstance(pantry, (list, tuple)) or not pantry or any(not isinstance(v, str) or not v.strip() for v in pantry):
        raise ValueError("pantry must contain at least one ingredient identity")
    if isinstance(max_time, bool) or not isinstance(max_time, (int, float)) or not math.isfinite(max_time) or max_time <= 0:
        raise ValueError("max_time must be a finite positive number")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    if type(include_unready) is not bool:
        raise ValueError("include_unready must be bool")
    pantry = list(dict.fromkeys(pantry))
    supplies = list(dict.fromkeys([*pantry, *STAPLES]))
    quality_terms = [f"CASE WHEN n.total_{name} IS NULL THEN 0 "
                     f"WHEN n.estimated_total_{name} THEN 0.5 ELSE 1 END" for name in NUTRIENTS]
    quality = "(" + " + ".join(quality_terms) + ") * 10.0 / 6"
    statement = """
        WITH candidate AS (
            SELECT r.*, n.status AS nutrition_status, n.estimated AS nutrition_estimated,
                   QUALITY AS quality_score
            FROM recipes r JOIN recipe_nutrition n USING(recipe_id)
            WHERE r.total_time_min <= %(max_time)s
              AND (%(include_unready)s OR r.ready_for_recommendation)
        ), availability AS (
            SELECT i.recipe_id,
                   count(DISTINCT i.ingredient_id) FILTER (WHERE NOT (i.ingredient_id=ANY(%(staples)s))) AS main_count,
                   count(DISTINCT i.ingredient_id) FILTER (WHERE NOT (i.ingredient_id=ANY(%(staples)s))
                       AND i.ingredient_id=ANY(%(pantry)s)) AS pantry_count,
                   COALESCE(array_agg(DISTINCT i.canonical_name ORDER BY i.canonical_name)
                       FILTER (WHERE NOT (i.ingredient_id=ANY(%(supplies)s))), ARRAY[]::text[]) AS missing_ingredients,
                   COALESCE(array_agg(DISTINCT i.canonical_name ORDER BY i.canonical_name)
                       FILTER (WHERE i.ingredient_id=ANY(%(pantry)s)), ARRAY[]::text[]) AS matched_ingredients
            FROM recipe_ingredients i JOIN candidate c USING(recipe_id)
            GROUP BY i.recipe_id
        ), scored AS (
            SELECT c.recipe_id,c.name,c.total_time_min,c.difficulty,c.ready_for_recommendation,
                   c.nutrition_status,c.nutrition_estimated,a.missing_ingredients,a.matched_ingredients,
                   a.main_count,a.pantry_count,
                   40.0*a.pantry_count/NULLIF(a.main_count,0) AS pantry_score,
                   30.0*(1-c.total_time_min/%(max_time)s) AS time_score,
                   CASE c.difficulty WHEN 'beginner' THEN 20 WHEN 'normal' THEN 10 ELSE 0 END AS difficulty_score,
                   c.quality_score
            FROM candidate c JOIN availability a USING(recipe_id)
            WHERE a.pantry_count>0 AND a.main_count>0
        )
        SELECT *, pantry_score+time_score+difficulty_score+quality_score AS score
        FROM scored
        ORDER BY score DESC, total_time_min, recipe_id LIMIT %(limit)s
    """.replace("QUALITY", quality)
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=store.connect_timeout,
                         application_name="chefmate-query-score-demo") as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL lock_timeout='30s'")
        connection.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
        connection.execute("SELECT pg_catalog.pg_advisory_xact_lock_shared("
                           "pg_catalog.hashtext('chefmate-storage'),pg_catalog.hashtext(%s))", (schema,))
        rows = connection.execute(statement, {"max_time": max_time, "include_unready": include_unready,
                                  "pantry": pantry, "staples": list(STAPLES), "supplies": supplies, "limit": limit}).fetchall()
        version = connection.execute("SELECT data_version FROM dataset_metadata WHERE id=1").fetchone()
    for row in rows:
        for key in ("score", "pantry_score", "time_score", "difficulty_score", "quality_score"):
            row[key] = round(float(row[key]), 2)
    return {"data_version": version["data_version"] if version else None, "pantry": pantry,
            "assumed_staples": list(STAPLES), "max_time_min": max_time, "include_unready": include_unready,
            "weights": {"pantry": 40, "time": 30, "difficulty": 20, "nutrition_information": 10},
            "ranking": rows}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", default="chefmate")
    parser.add_argument("--ingredients", nargs="+", default=["egg", "tomato", "potato"])
    parser.add_argument("--max-time", type=float, default=30)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--include-unready", action="store_true")
    args = parser.parse_args(argv)
    try:
        dsn = os.environ.get("CHEFMATE_DATABASE_URL")
        if not dsn:
            raise ValueError("Set CHEFMATE_DATABASE_URL")
        store = RecipeStore(dsn, schema=args.schema)
        output = rank_demo(dsn, schema=args.schema, pantry=args.ingredients, max_time=args.max_time,
                           limit=args.top, include_unready=args.include_unready)
        output["queries"] = _query_examples(store, args.include_unready)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except psycopg.Error as error:
        print(f"Database query failed ({error.sqlstate or type(error).__name__})", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
