"""Ingredient resolution and transparent retrieval over one read-only recipe snapshot."""

from datetime import datetime, timezone
from typing import TypeVar

from chefmate_contracts import default_vocabulary
from chefmate_food_data import FoodDataError, LocalFoodData
from chefmate_storage import (
    IngredientGroup, IngredientSelector, NutrientRange, RecipeFilter, RecipeSort, RecipeStore,
)
from chefmate_storage.queries import NUTRIENTS, SORT_FIELDS

from .models import (
    DatasetSnapshot, IngredientLookupRequest, RecipeDetailRequest, RecipeSearchRequest, ToolRequest,
)

Request = TypeVar("Request", bound=ToolRequest)
REFERENCE_NOTE = "USDA FDC 是营养参考；共享 FDC ID 不代表食材同义、相同组成或必然含有某种食材。"
EVIDENCE_NOTE = "当前库没有可靠的主料/必选/可选字段；原文和 warnings 是核对依据，不作为确定组成的硬过滤。"


def _request(model: type[Request], request: dict | Request) -> Request:
    # Revalidate supplied model instances too, including nested mutable lists.
    return model.model_validate(request.model_dump() if isinstance(request, ToolRequest) else request)


def _snapshot(metadata: dict | None) -> dict | None:
    if not metadata:
        return None
    stamp = metadata["imported_at"]
    if isinstance(stamp, datetime):
        stamp = stamp.astimezone(timezone.utc).isoformat()
    return DatasetSnapshot(data_version=metadata["data_version"], imported_at=stamp).model_dump()


def _stale(expected: DatasetSnapshot | None, snapshot: dict | None) -> dict | None:
    if expected is not None and expected.model_dump() != snapshot:
        return {"status": "snapshot_changed", "snapshot": snapshot,
                "expected_snapshot": expected.model_dump(),
                "message": "菜谱数据集已替换，请重新查找食材并查询；没有使用过期引用执行查询。"}
    return None


def _selector_matches(selection, ingredient):
    return (selection.ingredient_id == ingredient["ingredient_id"]
            and (selection.canonical_name is None or selection.canonical_name == ingredient["canonical_name"]))


def _options(session) -> dict:
    observed = session.search_options()
    result = {}
    vocabulary = default_vocabulary()
    for kind, entries in observed.items():
        defaults = {entry.code: entry.label for entry in vocabulary if entry.kind == kind and entry.enabled}
        counts = {entry["code"]: entry["recipe_count"] for entry in entries}
        result[kind] = [{"code": code, "label": defaults.get(code, code), "recipe_count": counts.get(code, 0)}
                        for code in sorted(defaults.keys() | counts.keys())]
    return result


class RetrievalService:
    """No model, framework, HTTP server, database writes or automatic condition relaxation."""

    def __init__(self, store: RecipeStore, foods: LocalFoodData | None = None):
        self.store = store
        self.foods = foods

    def find_ingredients(self, request: dict | IngredientLookupRequest) -> dict:
        query = _request(IngredientLookupRequest, request)
        with self.store.read_session() as session:
            snapshot = _snapshot(session.metadata())
            if error := _stale(query.expected_snapshot, snapshot):
                return error
            usda_candidates, usda_error = [], None
            if query.usda_query:
                if self.foods is None:
                    usda_error = "USDA 本地库未配置，未执行 USDA 补充检索。"
                else:
                    try:
                        usda_candidates = self.foods.search(query.usda_query, limit=15)
                    except (FoodDataError, ValueError) as exc:
                        usda_error = str(exc)
            reference_ids = tuple(dict.fromkeys([*query.fdc_ids, *(str(item["fdcId"]) for item in usda_candidates)]))
            candidates = session.lookup_ingredients(tuple(dict.fromkeys(query.queries)), reference_ids)
            for candidate in candidates:
                evidence, score = [], 0
                for term in dict.fromkeys(query.queries):
                    normalized = term.casefold()
                    for field, values, exact_score in (
                        ("canonical_name", [candidate["canonical_name"]], 100),
                        ("alias", candidate["aliases"], 90), ("ingredient_id", [candidate["ingredient_id"]], 80),
                    ):
                        for value in values:
                            if normalized in value.casefold():
                                exact = normalized == value.casefold()
                                evidence.append({"query": term, "field": field, "value": value,
                                                 "match": "exact" if exact else "contains"})
                                score = max(score, exact_score if exact else 40)
                matching_refs = sorted(set(candidate["fdc_ids"]) & set(reference_ids))
                if matching_refs:
                    evidence.extend({"field": "usda_reference", "fdc_id": value, "match": "shared_reference"}
                                    for value in matching_refs)
                    score = max(score, 10)
                candidate["selector"] = {"ingredient_id": candidate["ingredient_id"],
                                         "canonical_name": candidate["canonical_name"]}
                candidate["matched_by"] = sorted({entry["field"] for entry in evidence})
                candidate["match_evidence"] = evidence
                candidate["reference_only"] = bool(evidence) and all(
                    entry["field"] == "usda_reference" for entry in evidence)
                candidate["_score"] = score
            candidates.sort(key=lambda item: (-item["_score"], item["ingredient_id"], item["canonical_name"]))
            for candidate in candidates:
                candidate.pop("_score")
            status = "usda_unavailable" if usda_error and not query.queries and not query.fdc_ids else "ok"
            return {"status": status, "snapshot": snapshot, "normalized_query": query.model_dump(mode="json"),
                    "total": len(candidates), "items": candidates[:query.limit],
                    "has_more": len(candidates) > query.limit,
                    "usda": {"query": query.usda_query, "candidates": usda_candidates, "error": usda_error},
                    "notes": [REFERENCE_NOTE, EVIDENCE_NOTE]}

    def get_search_options(self) -> dict:
        with self.store.read_session() as session:
            return {"status": "ok", "snapshot": _snapshot(session.metadata()), "options": _options(session),
                    "nutrients": [{"code": name, "unit": "kcal" if name == "calories_kcal"
                                   else "mg" if name == "sodium_mg" else "g"} for name in NUTRIENTS],
                    "nutrition_bases": ["total", "per_serving"], "sort_fields": list(SORT_FIELDS),
                    "notes": [EVIDENCE_NOTE, "标签和过敏原排除仅针对已记录的信息，未记录不等于不存在。",
                              "营养未知值保留 null；范围筛选不匹配未知值。估算限制逐个指标检查。",
                              "每份营养沿用菜谱人数基准；人数估算标记随结果返回。"]}

    def search_recipes(self, request: dict | RecipeSearchRequest) -> dict:
        query = _request(RecipeSearchRequest, request)
        with self.store.read_session() as session:
            snapshot = _snapshot(session.metadata())
            if error := _stale(query.expected_snapshot, snapshot):
                return error
            selectors = [selection for group in query.ingredient_groups for selection in group.any_of]
            selectors.extend(query.exclude_ingredients)
            unknown = session.missing_selectors(tuple(IngredientSelector(selection.ingredient_id,
                                                                        selection.canonical_name)
                                                       for selection in selectors))
            options = _options(session)
            invalid_options = []
            for kind, fields in (
                ("taste", ("taste_tags_all", "taste_tags_any", "exclude_taste_tags")),
                ("cooking_method", ("cooking_methods_any",)), ("tool", ("available_tools",)),
                ("nutrition", ("nutrition_tags_all",)), ("allergen", ("exclude_allergens",)),
            ):
                known = {item["code"] for item in options[kind]}
                for field in fields:
                    invalid_options.extend({"field": field, "value": value} for value in getattr(query, field) or []
                                           if value not in known)
            if unknown or invalid_options:
                return {"status": "invalid_selection", "snapshot": snapshot,
                        "unknown_ingredients": unknown, "unknown_options": invalid_options,
                        "message": "部分食材或选项不存在；请使用 find_ingredients / get_search_options 的真实候选。",
                        "normalized_query": query.model_dump(mode="json")}
            filters = RecipeFilter(
                name=query.name, ingredient_groups=tuple(IngredientGroup(tuple(IngredientSelector(
                    selection.ingredient_id, selection.canonical_name) for selection in group.any_of))
                    for group in query.ingredient_groups),
                exclude_ingredients=tuple(IngredientSelector(selection.ingredient_id, selection.canonical_name)
                                          for selection in query.exclude_ingredients),
                min_total_time_min=query.min_total_time_min, max_total_time_min=query.max_total_time_min,
                difficulties=tuple(query.difficulties), taste_tags=tuple(query.taste_tags_all),
                taste_tags_any=tuple(query.taste_tags_any), exclude_taste_tags=tuple(query.exclude_taste_tags),
                cooking_methods_any=tuple(query.cooking_methods_any),
                available_tools=tuple(query.available_tools) if query.available_tools is not None else None,
                nutrition_tags=tuple(query.nutrition_tags_all), exclude_allergens=tuple(query.exclude_allergens),
                ready_for_recommendation=query.ready_for_recommendation,
                nutrients=tuple(NutrientRange(bound.name, bound.minimum, bound.maximum, bound.basis,
                                             bound.allow_estimated) for bound in query.nutrients),
            )
            result = session.search(filters, limit=query.limit, offset=query.offset,
                                    sort=RecipeSort(query.sort.field, query.sort.direction))
            for recipe in result["items"]:
                ingredients = recipe.pop("ingredients")
                recipe["matched_ingredients"] = []
                for ingredient in ingredients:
                    matched_groups = [index for index, group in enumerate(query.ingredient_groups)
                                      if any(_selector_matches(selection, ingredient) for selection in group.any_of)]
                    if matched_groups:
                        recipe["matched_ingredients"].append({**ingredient, "matched_groups": matched_groups})
            notes = [EVIDENCE_NOTE]
            if query.exclude_taste_tags or query.exclude_allergens:
                notes.append("排除仅针对已记录的标签/过敏原，缺失信息不能证明口味或过敏安全。")
            return {"status": "ok", "snapshot": snapshot, "normalized_query": query.model_dump(mode="json"),
                    **result, "limit": query.limit, "offset": query.offset,
                    "has_more": query.offset + len(result["items"]) < result["total"], "notes": notes}

    def get_recipe(self, request: dict | RecipeDetailRequest) -> dict:
        query = _request(RecipeDetailRequest, request)
        with self.store.read_session() as session:
            snapshot = _snapshot(session.metadata())
            if error := _stale(query.expected_snapshot, snapshot):
                return error
            recipe = session.get_recipe(query.recipe_id)
            return {"status": "ok" if recipe is not None else "not_found", "snapshot": snapshot, "recipe": recipe,
                    "notes": [EVIDENCE_NOTE, REFERENCE_NOTE]}
