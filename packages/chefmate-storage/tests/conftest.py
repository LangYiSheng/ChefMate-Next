"""Real unified-export fixtures and isolated PostgreSQL test schemas."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg import sql

from chefmate_storage import RecipeStore


NUTRIENT_NAMES = (
    "calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg",
)


@pytest.fixture
def make_recipe():
    """Create the actual data_cleaning unified export shape, without importing its app."""
    def build(recipe_id="r1", name="番茄鸡蛋", *, ingredient_ids=("egg", "tomato")):
        catalog = {
            "egg": ("鸡蛋", "蛋类", ["鸡子"], ["egg"]),
            "tomato": ("番茄", "蔬菜", ["西红柿"], []),
            "chicken": ("鸡肉", "肉类", ["鸡胸肉"], []),
            "peanut": ("花生", "坚果", ["落花生"], ["peanut"]),
        }
        per_100g = dict(zip(NUTRIENT_NAMES, (100.0, 10.0, 5.0, 4.0, 1.0, 20.0)))
        ingredients = []
        nutrition_ingredients = []
        for index, ingredient_id in enumerate(ingredient_ids):
            canonical_name, category, aliases, allergens = catalog.get(
                ingredient_id, (ingredient_id.title(), "other", [], [])
            )
            grams = 100.0 + index * 50.0
            ingredients.append({
                "raw_name": f"{canonical_name}（切好）",
                "raw_amount": str(grams),
                "raw_unit": "g",
                "raw_text": f"{canonical_name} {grams:g}g，切好备用",
                "ingredient_id": ingredient_id,
                "canonical_name": canonical_name,
                "category": category,
                "aliases": deepcopy(aliases),
                "allergens": deepcopy(allergens),
                "grams": grams,
                "amount_evidence": "original ingredient measurement",
                "scalable": True,
                "usda_query": f"{ingredient_id}, raw",
            })
            nutrition_ingredients.append({
                "name": canonical_name,
                "grams": grams,
                "original_grams": grams,
                "estimated": False,
                "estimated_grams": False,
                "estimated_nutrients": [],
                "estimate": None,
                "nutrition": {key: value * grams / 100 for key, value in per_100g.items()},
                "source": {
                    "source": "USDA_FDC",
                    "fdc_id": str(1000 + index),
                    "per_100g": deepcopy(per_100g),
                    "confidence": 0.9,
                    "reason": "review-only matching rationale",
                    "dataset": {"name": "test fixture"},
                },
                "source_issue": None,
                "issue": None,
            })
        total = {
            key: sum(item["nutrition"][key] for item in nutrition_ingredients)
            for key in NUTRIENT_NAMES
        }
        return {
            "recipe_id": recipe_id,
            "name": name,
            "description": "一个用于验证关系存储的菜谱",
            "original_servings": "两人份",
            "servings": 2.0,
            "servings_evidence": "原文写明两人份",
            "prep_time_min": 5.0,
            "cook_time_min": 10.0,
            "total_time_min": 15.0,
            "difficulty": "easy",
            "ingredients": ingredients,
            "steps": ["先备料", "再下锅", "最后盛盘"],
            "taste_tags": ["salty", "sweet"],
            "cooking_methods": ["stir_fry"],
            "required_tools": ["pan", "spatula"],
            "warnings": ["注意热锅", "鸡蛋炒熟"],
            "references": [
                {"title": "第二个参考先展示", "url": "https://example.org/two"},
                {"title": "第一个参考后展示", "url": "https://example.org/one"},
            ],
            "source": "测试菜谱集",
            "raw_source_text": "DO_NOT_STORE_RAW_SOURCE",
            "nutrition": {
                "status": "complete",
                "estimated": False,
                "estimated_total_fields": [],
                "estimated_per_serving_fields": [],
                "servings": 2.0,
                "estimated_servings": False,
                "estimation": None,
                "tags_estimated": False,
                "total": total,
                "per_serving": {key: value / 2 for key, value in total.items()},
                "tags": [],
                "coverage": {key: len(ingredients) for key in NUTRIENT_NAMES},
                "ingredients": nutrition_ingredients,
                "thresholds": {"basis": "per_serving", "high_protein": 30},
                "blocking_reasons": [],
            },
            "ready_for_recommendation": True,
            "blocking_reasons": [],
            "provenance": {
                "status": "approved",
                "source_hash": "DO_NOT_STORE_SOURCE_HASH",
                "review_note": "DO_NOT_STORE_REVIEW_NOTE",
                "prompt": "DO_NOT_STORE_PROMPT",
                "reviewed_at": "2026-10-05T00:00:00+00:00",
                "updated_at": "2026-10-05T00:00:00+00:00",
            },
        }
    return build


@pytest.fixture
def make_export(tmp_path):
    def write(recipes, *, approved_only=True, directory_name=None):
        directory = tmp_path / (directory_name or uuid.uuid4().hex)
        directory.mkdir()
        hashes = {}
        for name, content in {"recipes.json": recipes, "usda_mappings.json": []}.items():
            raw = json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            (directory / name).write_bytes(raw)
            hashes[name] = hashlib.sha256(raw).hexdigest()
        manifest = {
            "schema_version": 2,
            "phase": "unified",
            "approved_only": approved_only,
            "ready_for_recommendation": bool(recipes) and all(
                recipe["ready_for_recommendation"] for recipe in recipes
            ),
            "recipe_count": len(recipes),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashes,
            "notice": "DO_NOT_STORE_NOTICE",
            "filter_report": {"workflow": "markdown_ai_review"},
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return directory
    return write


@pytest.fixture
def postgres_store():
    """Never use/drop a shared schema: every test owns exactly one random schema."""
    dsn = os.environ.get("CHEFMATE_TEST_DSN")
    if not dsn:
        pytest.skip("Set CHEFMATE_TEST_DSN to run real PostgreSQL integration tests")
    schema = "cm_test_" + uuid.uuid4().hex
    store = RecipeStore(dsn, schema=schema, connect_timeout=5)
    try:
        store.migrate()
        yield store
    finally:
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def postgres_execute(postgres_store):
    """Run direct assertions/constraints exclusively inside the fixture's schema."""
    def execute(statement, parameters=()):
        with psycopg.connect(os.environ["CHEFMATE_TEST_DSN"], connect_timeout=5) as connection:
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(postgres_store.schema))
            )
            cursor = connection.execute(statement, parameters)
            return cursor.fetchall() if cursor.description is not None else None
    return execute
