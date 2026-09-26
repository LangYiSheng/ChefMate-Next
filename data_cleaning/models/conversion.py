import json

from data_cleaning.config import ROOT
from data_cleaning.models.repository import encode, now
from data_cleaning.models.workflow import WorkflowRepository
from data_cleaning.schemas.conversion import ConvertedRecipe, ConversionSettings


class ConversionRepository(WorkflowRepository):
    def browse_recipes(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT r.recipe_id,r.name,r.source,r.content_hash,c.source_hash,c.status,"
                "c.data_json,c.nutrition_json,c.updated_at,c.review_note,c.error_message "
                "FROM recipes r LEFT JOIN converted_recipes c ON c.recipe_id=r.recipe_id ORDER BY r.source"
            )]

    def confirmed_usda_mappings(self) -> list[dict]:
        """Derive the shared registry from current approvals, so revocation is immediate."""
        from data_cleaning.services.conversion_nutrition import food_key

        mappings = []
        with self.connection() as db:
            rows = db.execute(
                "SELECT c.* FROM converted_recipes c JOIN recipes r ON r.recipe_id=c.recipe_id "
                "WHERE c.status='approved' AND c.source_hash=r.content_hash"
            ).fetchall()
        for row in rows:
            document = ConvertedRecipe.model_validate_json(row["data_json"])
            snapshots = json.loads(row["nutrition_json"])
            for ingredient in document.ingredients:
                snapshot = snapshots.get(food_key(ingredient), {})
                if str(snapshot.get("reason", "")).startswith("近似匹配："):
                    continue  # An estimation substitute must not become a global identity/alias rule.
                if snapshot.get("source") != "USDA_FDC" or not snapshot.get("fdc_id") or not snapshot.get("per_100g"):
                    continue
                mappings.append({
                    "name": ingredient.canonical_name,
                    "aliases": ingredient.aliases,
                    "query": ingredient.usda_query,
                    "fdc_id": str(snapshot["fdc_id"]),
                    "recipe_id": row["recipe_id"],
                    "reviewed_at": row["reviewed_at"],
                    "snapshot": snapshot,
                })
        return mappings

    def lookup_confirmed_usda(self, ingredient) -> dict | None:
        normalize = lambda value: " ".join(value.casefold().split())
        # Never use raw_name: a whole egg in the source may mean egg white in the recipe.
        name = normalize(ingredient.canonical_name)
        candidates = [m for m in self.confirmed_usda_mappings()
                      if name in {normalize(n) for n in [m["name"], *m["aliases"]]}]
        if not candidates or len({m["fdc_id"] for m in candidates}) != 1:
            return None
        # A changed query may express a different preparation/part. Abstain, do not guess.
        candidates = [m for m in candidates if normalize(m["query"]) == normalize(ingredient.usda_query)]
        if not candidates:
            return None
        match = max(candidates, key=lambda m: m["reviewed_at"] or "")
        return {**match["snapshot"], "reused_from_recipe_id": match["recipe_id"],
                "mapping_reviewed_at": match["reviewed_at"]}

    def conversion_settings(self) -> ConversionSettings:
        with self.connection() as db:
            row = db.execute("SELECT value_json FROM settings WHERE key='conversion_settings'").fetchone()
        value = ConversionSettings.model_validate_json(row[0]) if row else ConversionSettings()
        if not value.prompt:
            value.prompt = (ROOT / "prompts/convert.txt").read_text(encoding="utf-8")
        return value

    def save_conversion_settings(self, value: ConversionSettings) -> None:
        value = ConversionSettings.model_validate(value.model_dump())
        with self.connection(write=True) as db:
            db.execute(
                "INSERT INTO settings VALUES('conversion_settings',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (value.model_dump_json(),),
            )
            self._audit(db, None, "conversion_settings", value.model_dump())

    def conversion(self, recipe_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM converted_recipes WHERE recipe_id=?", (recipe_id,)).fetchone()
        return dict(row) if row else {}

    def conversions(self) -> list[dict]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT r.recipe_id,r.name,r.source,r.content_hash,c.status,c.error_message,c.source_hash FROM recipes r LEFT JOIN converted_recipes c ON c.recipe_id=r.recipe_id ORDER BY r.source"
                )
            ]

    def validate_document(self, value: ConvertedRecipe) -> None:
        value = ConvertedRecipe.model_validate(value.model_dump())
        with self.connection() as db:
            for kind, codes in [
                ("taste", value.taste_tags),
                ("tool", value.required_tools),
                ("cooking_method", value.cooking_methods),
                ("difficulty", [value.difficulty] if value.difficulty else []),
            ]:
                self.check_enum(db, kind, codes)
            for ingredient in value.ingredients:
                self.check_enum(db, "category", [ingredient.category])
                self.check_enum(db, "allergen", ingredient.allergens)

    def save_conversion(
        self,
        recipe_id: str,
        value: ConvertedRecipe,
        *,
        source_hash: str,
        expected: str | None,
        prompt: str,
        status: str = "pending_review",
        note: str = "",
        nutrition: dict | None = None,
        error: str | None = None,
    ) -> None:
        from data_cleaning.services.conversion_nutrition import keep_snapshots

        self.validate_document(value)
        if status not in ("pending_review", "approved", "rejected"):
            raise ValueError("无效审核状态")
        with self.connection(write=True) as db:
            source = db.execute("SELECT content_hash FROM recipes WHERE recipe_id=?", (recipe_id,)).fetchone()
            old = db.execute("SELECT * FROM converted_recipes WHERE recipe_id=?", (recipe_id,)).fetchone()
            if not source or source[0] != source_hash:
                raise ValueError("原文已变化，请重新转换")
            if (old and old["updated_at"] != expected) or (not old and expected):
                raise ValueError("JSON 已被修改，请刷新后再提交")
            snapshots = (
                nutrition
                if nutrition is not None
                else keep_snapshots(value, json.loads(old["nutrition_json"]) if old else {})
            )
            stamp = now()
            db.execute(
                """INSERT INTO converted_recipes VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(recipe_id) DO UPDATE SET source_hash=excluded.source_hash,data_json=excluded.data_json,
                nutrition_json=excluded.nutrition_json,prompt=excluded.prompt,status=excluded.status,error_message=excluded.error_message,
                review_note=excluded.review_note,reviewed_at=excluded.reviewed_at,updated_at=excluded.updated_at""",
                (
                    recipe_id,
                    source_hash,
                    value.model_dump_json(),
                    encode(snapshots),
                    prompt,
                    status,
                    error,
                    note,
                    stamp if status in ("approved", "rejected") else None,
                    stamp,
                    stamp,
                ),
            )
            self._audit(
                db,
                recipe_id,
                "conversion_saved",
                {
                    "document": value.model_dump(),
                    "nutrition": snapshots,
                    "status": status,
                    "prompt": prompt,
                    "note": note,
                },
            )

    def conversion_failure(self, recipe_id: str, expected: str | None, error: str) -> None:
        with self.connection(write=True) as db:
            old = db.execute("SELECT updated_at FROM converted_recipes WHERE recipe_id=?", (recipe_id,)).fetchone()
            if old and old[0] == expected:
                db.execute("UPDATE converted_recipes SET error_message=? WHERE recipe_id=?", (error, recipe_id))
            self._audit(db, recipe_id, "conversion_failed", {"error": error})

    def conversion_export(self, approved_only: bool = True) -> dict:
        from data_cleaning.services.conversion_nutrition import calculate

        recipes = []
        with self.connection() as db:
            for row in db.execute(
                "SELECT c.*,r.source,s.raw_text FROM converted_recipes c JOIN recipes r ON r.recipe_id=c.recipe_id JOIN source_versions s ON s.recipe_id=r.recipe_id AND s.revision=r.revision WHERE c.source_hash=r.content_hash AND (?=0 OR c.status='approved')",
                (int(approved_only),),
            ):
                document = ConvertedRecipe.model_validate_json(row["data_json"])
                nutrition = calculate(document, json.loads(row["nutrition_json"]), self.thresholds())
                recipes.append(
                    {
                        **document.model_dump(),
                        "recipe_id": row["recipe_id"],
                        "source": row["source"],
                        "raw_source_text": row["raw_text"],
                        "nutrition": nutrition,
                        "ready_for_recommendation": row["status"] == "approved" and nutrition["status"] == "complete",
                        "provenance": {
                            k: row[k]
                            for k in ("source_hash", "status", "reviewed_at", "review_note", "updated_at", "prompt")
                        },
                    }
                )
        return {
            "recipes": recipes,
            "usda_mappings": [
                {key: value for key, value in mapping.items() if key != "snapshot"}
                for mapping in self.confirmed_usda_mappings()
            ],
            "_meta": {
                "phase": "unified",
                "workflow": "markdown_ai_review",
                "ready_for_recommendation": bool(recipes) and all(r["ready_for_recommendation"] for r in recipes),
                "notice": "仅一次整篇审核；营养缺失为null，各菜谱通过ready_for_recommendation标识完整性",
            },
        }
