"""Recipe normalization, USDA review and consistent final export snapshots."""

import json

from data_cleaning.models.catalog import CatalogRepository
from data_cleaning.models.repository import encode, now
from data_cleaning.schemas.cleaning import RecipeProfile, TagThresholds


class WorkflowRepository(CatalogRepository):
    def profile(self, recipe_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM recipe_profiles WHERE recipe_id=?", (recipe_id,)).fetchone()
            return dict(row) if row else {}

    def save_profile(
        self,
        recipe_id: str,
        profile: RecipeProfile,
        *,
        source_expected: str,
        expected: str | None,
        status: str = "pending_review",
        note: str = "",
        error: str | None = None,
    ) -> None:
        profile = RecipeProfile.model_validate(profile.model_dump())
        if status not in ("pending_review", "approved", "rejected", "failed"):
            raise ValueError("无效审核状态")
        with self.connection(write=True) as db:
            self._check_current(db, recipe_id, source_expected)
            previous = db.execute("SELECT * FROM recipe_profiles WHERE recipe_id=?", (recipe_id,)).fetchone()
            if (previous and previous["updated_at"] != expected) or (not previous and expected):
                raise ValueError("标准化结果已变化，请刷新后保存")
            for kind, codes in (
                ("taste", profile.taste_tags),
                ("tool", profile.required_tools),
                ("cooking_method", profile.cooking_methods),
                ("difficulty", [profile.difficulty] if profile.difficulty else []),
            ):
                self.check_enum(db, kind, codes)
            count = db.execute("SELECT COUNT(*) FROM recipe_ingredients WHERE recipe_id=?", (recipe_id,)).fetchone()[0]
            if count == 0 or sorted(i.ordinal for i in profile.items) != list(range(count)):
                raise ValueError("标准用量必须覆盖当前食材明细且 ordinal 不重复")
            if status == "approved":
                recipe = db.execute("SELECT status FROM recipes WHERE recipe_id=?", (recipe_id,)).fetchone()
                if recipe[0] != "approved":
                    raise ValueError("请先通过原始提取审核")
                for row in db.execute(
                    "SELECT r.* FROM raw_ingredients r JOIN recipe_ingredients i ON i.raw_ingredient_id=r.id WHERE i.recipe_id=?",
                    (recipe_id,),
                ):
                    if row["status"] != "approved" or not row["canonical_ingredient_id"]:
                        raise ValueError(f"食材尚未审核：{row['raw_name']}")
                    standard = self.require_standard(db, row["canonical_ingredient_id"])
                    self.check_enum(db, "category", [standard["category"]])
                    self.check_enum(db, "allergen", json.loads(standard["allergens_json"]))
            stamp = now()
            db.execute(
                """INSERT INTO recipe_profiles VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(recipe_id) DO UPDATE SET
                source_updated_at=excluded.source_updated_at,data_json=excluded.data_json,status=excluded.status,
                error_message=excluded.error_message,review_note=excluded.review_note,reviewed_at=excluded.reviewed_at,updated_at=excluded.updated_at""",
                (
                    recipe_id,
                    source_expected,
                    profile.model_dump_json(),
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
                "profile_saved",
                {"profile": profile.model_dump(), "status": status, "note": note, "error": error},
            )

    def cached_food(self, fdc_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT data_json FROM usda_cache WHERE fdc_id=?", (fdc_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def cache_food(self, food: dict) -> None:
        with self.connection(write=True) as db:
            db.execute(
                "INSERT INTO usda_cache VALUES(?,?,?) ON CONFLICT(fdc_id) DO UPDATE SET data_json=excluded.data_json,fetched_at=excluded.fetched_at",
                (str(food["fdcId"]), encode(food), now()),
            )

    def stage_nutrition(self, ingredient_id: str, fdc_id: str, expected: str) -> None:
        from data_cleaning.services.usda import parse_nutrients

        with self.connection(write=True) as db:
            standard = db.execute(
                "SELECT * FROM standard_ingredients WHERE ingredient_id=? AND status<>'rejected'", (ingredient_id,)
            ).fetchone()
            if not standard:
                raise ValueError("标准食材不存在或已拒绝")
            if standard["updated_at"] != expected:
                raise ValueError("标准食材已变化，请刷新")
            cached = db.execute("SELECT data_json,fetched_at FROM usda_cache WHERE fdc_id=?", (str(fdc_id),)).fetchone()
            if not cached:
                raise ValueError("必须先从 USDA 获取该 FDC ID 的详情")
            food = json.loads(cached[0])
            nutrients = parse_nutrients(food)
            snapshot = {"per_100g": nutrients.model_dump(), "food": food, "fetched_at": cached[1]}
            db.execute(
                """UPDATE standard_ingredients SET nutrition_source='USDA_FDC',nutrition_source_id=?,nutrition_source_name=?,
                nutrition_match_status='pending_review',nutrition_json=?,nutrition_error=NULL,nutrition_review_note=NULL,
                nutrition_reviewed_at=NULL,updated_at=? WHERE ingredient_id=?""",
                (str(fdc_id), food["description"], encode(snapshot), now(), ingredient_id),
            )
            self.invalidate(db)
            self._audit(db, None, "nutrition_candidate", {"ingredient_id": ingredient_id, "snapshot": snapshot})

    def review_nutrition(self, ingredient_id: str, expected: str, approve: bool, note: str) -> None:
        with self.connection(write=True) as db:
            row = self.require_standard(db, ingredient_id)
            if row["updated_at"] != expected or not row["nutrition_json"]:
                raise ValueError("请先获取 USDA 候选详情或刷新已变化的数据")
            stamp = now()
            db.execute(
                "UPDATE standard_ingredients SET nutrition_match_status=?,nutrition_review_note=?,nutrition_error=NULL,nutrition_reviewed_at=?,updated_at=? WHERE ingredient_id=?",
                ("approved" if approve else "rejected", note, stamp, stamp, ingredient_id),
            )
            self.invalidate(db)
            self._audit(
                db,
                None,
                "nutrition_review",
                {
                    "ingredient_id": ingredient_id,
                    "fdc_id": row["nutrition_source_id"],
                    "approved": approve,
                    "note": note,
                },
            )

    def nutrition_error(self, ingredient_id: str, expected: str, error: str) -> None:
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT updated_at FROM standard_ingredients WHERE ingredient_id=?", (ingredient_id,)
            ).fetchone()
            if not row or row[0] != expected:
                return
            db.execute(
                "UPDATE standard_ingredients SET nutrition_match_status='pending_review',nutrition_error=?,nutrition_reviewed_at=NULL,updated_at=? WHERE ingredient_id=?",
                (error, now(), ingredient_id),
            )
            self.invalidate(db)
            self._audit(db, None, "nutrition_failed", {"ingredient_id": ingredient_id, "error": error})

    @staticmethod
    def _bundle(db, recipe_id: str) -> dict:
        recipe = db.execute(
            "SELECT r.*,s.raw_text FROM recipes r JOIN source_versions s ON s.recipe_id=r.recipe_id AND s.revision=r.revision WHERE r.recipe_id=?",
            (recipe_id,),
        ).fetchone()
        if not recipe or not recipe["extracted_json"]:
            raise ValueError("菜谱尚未提取")
        profile = db.execute("SELECT * FROM recipe_profiles WHERE recipe_id=?", (recipe_id,)).fetchone()
        items = []
        for row in db.execute(
            "SELECT ri.ordinal,ri.data_json,r.* FROM recipe_ingredients ri JOIN raw_ingredients r ON r.id=ri.raw_ingredient_id WHERE ri.recipe_id=? ORDER BY ordinal",
            (recipe_id,),
        ):
            standard = db.execute(
                "SELECT * FROM standard_ingredients WHERE ingredient_id=?", (row["canonical_ingredient_id"],)
            ).fetchone()
            items.append({"raw": dict(row), "standard": dict(standard) if standard else None})
        config = db.execute("SELECT value_json FROM settings WHERE key='nutrition_thresholds'").fetchone()
        return {
            "recipe": dict(recipe),
            "profile": dict(profile) if profile else None,
            "items": items,
            "thresholds": TagThresholds.model_validate_json(config[0]) if config else TagThresholds(),
        }

    def result(self, recipe_id: str) -> dict:
        from data_cleaning.services.nutrition import build_result

        with self.connection() as db:
            return build_result(self._bundle(db, recipe_id))

    def export_snapshot(self, approved_only: bool = True) -> dict:
        from data_cleaning.services.nutrition import build_result

        with self.connection() as db:
            recipes, excluded = [], []
            for row in db.execute("SELECT recipe_id FROM recipes WHERE extracted_json IS NOT NULL ORDER BY recipe_id"):
                result = build_result(self._bundle(db, row[0]))
                if approved_only and not result["ready_for_recommendation"]:
                    excluded.append({"recipe_id": row[0], "reasons": result["blocking_reasons"]})
                else:
                    recipes.append(result)
            standards = []
            for row in db.execute(
                "SELECT * FROM standard_ingredients WHERE (?=0 OR status='approved') ORDER BY ingredient_id",
                (int(approved_only),),
            ):
                item = dict(row)
                item["aliases"] = [
                    r[0]
                    for r in db.execute(
                        "SELECT alias FROM ingredient_aliases WHERE ingredient_id=?", (row["ingredient_id"],)
                    )
                ]
                item["allergens"] = json.loads(item.pop("allergens_json"))
                if item["nutrition_json"]:
                    item["nutrition"] = json.loads(item.pop("nutrition_json"))
                standards.append(item)
            ids = {s["ingredient_id"] for s in standards}
            threshold_row = db.execute("SELECT value_json FROM settings WHERE key='nutrition_thresholds'").fetchone()
            thresholds = TagThresholds.model_validate_json(threshold_row[0]) if threshold_row else TagThresholds()
            return {
                "recipes": recipes,
                "ingredients": standards,
                "raw_ingredients": [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM raw_ingredients WHERE (?=0 OR status='approved') ORDER BY id",
                        (int(approved_only),),
                    )
                ],
                "ingredient_aliases": [
                    dict(r) for r in db.execute("SELECT * FROM ingredient_aliases") if r["ingredient_id"] in ids
                ],
                "fixed_mappings": [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM fixed_mappings WHERE (?=0 OR status='approved')", (int(approved_only),)
                    )
                    if r["ingredient_id"] in ids
                ],
                "enums": [dict(r) for r in db.execute("SELECT * FROM enum_options ORDER BY kind,code")],
                "_meta": {
                    "phase": 3,
                    "ready_for_recommendation": approved_only,
                    "excluded": excluded,
                    "nutrition_thresholds": thresholds.model_dump(),
                },
            }

    def dashboard(self) -> dict[str, int]:
        stats = super().dashboard()
        with self.connection() as db:
            stats["待匹配食材"] = db.execute(
                "SELECT COUNT(DISTINCT r.id) FROM raw_ingredients r JOIN recipe_ingredients i ON i.raw_ingredient_id=r.id WHERE r.canonical_ingredient_id IS NULL"
            ).fetchone()[0]
            stats["待审核食材"] = db.execute(
                "SELECT COUNT(DISTINCT r.id) FROM raw_ingredients r JOIN recipe_ingredients i ON i.raw_ingredient_id=r.id WHERE r.status IN ('pending_review','failed')"
            ).fetchone()[0]
            stats["标准食材数"] = db.execute(
                "SELECT COUNT(*) FROM standard_ingredients WHERE status<>'rejected'"
            ).fetchone()[0]
            stats["USDA 已匹配"] = db.execute(
                "SELECT COUNT(*) FROM standard_ingredients WHERE status='approved' AND nutrition_match_status='approved'"
            ).fetchone()[0]
            stats["USDA 未匹配"] = db.execute(
                "SELECT COUNT(*) FROM standard_ingredients WHERE status<>'rejected' AND nutrition_match_status<>'approved'"
            ).fetchone()[0]
            stats["标准化已审核"] = db.execute(
                "SELECT COUNT(*) FROM recipe_profiles p JOIN recipes r ON r.recipe_id=p.recipe_id WHERE p.status='approved' AND p.source_updated_at=r.updated_at"
            ).fetchone()[0]
        return stats
