"""Personal recipes: validated drafts, explicit publication, and versioned retrieval.

Official recipes remain read-only in PostgreSQL. Personal records live in the
application database with an explicit origin and survive conversation deletion.
"""

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from chefmate_contracts import RecipeDocument
from chefmate_recipe_processing import calculate_nutrition, validate_recipe

from .sessions import now


def _quantity_in(evidence, expected, kind):
    """Quantitative source fields must match a quoted amount, including units."""
    units = {"servings": r"人份?|份|位|口|servings?|people|persons?|portions?",
             "grams": r"千克|公斤|克|kg|g", "minutes": r"小时|分钟?|秒钟?|hours?|minutes?|seconds?|min"}
    chinese = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": .5}
    for match in re.finditer(r"(\d+(?:\.\d+)?|\.\d+|[一二两三四五六七八九十半])\s*(" + units[kind] + ")", evidence, re.I):
        token, unit = match.groups()
        number = chinese[token] if token in chinese else float(token)
        if kind == "grams" and unit.lower() in {"kg", "千克", "公斤"}:
            number *= 1000
        if kind == "minutes":
            if unit.lower() in {"小时", "hour", "hours"}:
                number *= 60
            elif unit.lower() in {"秒", "秒钟", "second", "seconds"}:
                number /= 60
        if abs(number - expected) < .000001:
            return True
    return False


class DraftIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    amount: str = Field(default="适量", max_length=200)
    grams: float | None = Field(default=None, ge=0, le=100000,
        description="原文明确的精确克重；个数、体积、约量或范围不自行换算，留null。")
    amount_evidence: str = Field(default="", max_length=300)
    scalable: bool = True
    usda_query: str = Field(default="", max_length=150)
    allergens: list[str] = Field(default_factory=list, max_length=15)


class RecipeDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    source_text: str = Field(min_length=1, max_length=20000,
        description="用户文字或图片中可辨认的原文转写。看不清的内容标为未知，不编造。")
    source_kind: Literal["text", "image"] = "text"
    servings: float | None = Field(default=None, gt=0, le=100)
    servings_evidence: str = Field(default="", max_length=300, description="逐字引用source_text中人数/份数依据。")
    prep_time_min: float | None = Field(default=None, ge=0, le=2880)
    cook_time_min: float | None = Field(default=None, ge=0, le=2880)
    total_time_min: float | None = Field(default=None, ge=0, le=2880,
        description="只有原文明确总耗时才填写；单一步骤计时不等于整道菜总耗时。")
    time_evidence: str = Field(default="", max_length=500,
        description="逐字引用source_text的时间依据；没有原文则时间保持null，可在warnings说明建议估时。")
    ingredients: list[DraftIngredient] = Field(min_length=1, max_length=80)
    steps: list[str] = Field(min_length=1, max_length=80,
        description="可直接执行的步骤，保留火候和原文时间；推断的安全建议需明确标识。")
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def grounded(self):
        if self.servings is not None and not self.servings_evidence:
            raise ValueError("人数需要原文依据；未写明时保留未知")
        if any(v is not None for v in (self.prep_time_min, self.cook_time_min, self.total_time_min)) and not self.time_evidence:
            raise ValueError("耗时需要原文依据；推断时间请写入注意事项")
        if any(not step.strip() or len(step) > 2000 for step in self.steps):
            raise ValueError("步骤应为可阅读的非空文本")
        if any(item.grams is not None and not item.amount_evidence for item in self.ingredients):
            raise ValueError("克重需要原文依据；鸡蛋个数等不能直接编成克重")
        normalized_source = re.sub(r"\s+", "", self.source_text)
        for evidence in [self.servings_evidence, self.time_evidence,
                         *(item.amount_evidence for item in self.ingredients if item.grams is not None)]:
            if evidence and re.sub(r"\s+", "", evidence) not in normalized_source:
                raise ValueError("人数、时间和克重依据必须逐字引用原文，不能补造依据")
        if self.servings is not None and not re.search(r"[人份位口]|servings?|people|persons?|portions?", self.servings_evidence, re.I):
            raise ValueError("原文需明确人数或份数，不能把盘数当成就餐人数")
        if any(v is not None for v in (self.prep_time_min, self.cook_time_min, self.total_time_min)) and not re.search(
                r"秒|分钟?|小时|minute|second|hour|(?<![A-Za-z])min(?![A-Za-z])", self.time_evidence, re.I):
            raise ValueError("原文未给明确时间单位，耗时请保留未知")
        if any(item.grams is not None and not re.search(r"克|(?<![A-Za-z])k?g(?![A-Za-z])", item.amount_evidence, re.I)
               for item in self.ingredients):
            raise ValueError("原文未给克重依据，不能把个数或毫升直接换成克重")
        if self.servings is not None and not _quantity_in(self.servings_evidence, self.servings, "servings"):
            raise ValueError("份数与引用原文不一致，请保留原值或留空")
        if any(item.grams is not None and not _quantity_in(item.amount_evidence, item.grams, "grams") for item in self.ingredients):
            raise ValueError("克重与引用原文不一致，请核对原始用量")
        if any(value is not None and not _quantity_in(self.time_evidence, value, "minutes")
               for value in (self.prep_time_min, self.cook_time_min, self.total_time_min)):
            raise ValueError("耗时与引用原文不一致，请保留未知，不补造时间")
        if self.total_time_min is not None and not re.search(r"总|合计|全程|total", self.time_evidence, re.I):
            raise ValueError("单一步骤时长不能当作整道菜总耗时")
        return self


class CustomSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=8, ge=1, le=30)
    offset: int = Field(default=0, ge=0, le=100000)


def _document(draft: RecipeDraftRequest) -> RecipeDocument:
    ingredients = []
    for item in draft.ingredients:
        amount, unit = _amount_parts(item.amount)
        ingredients.append({"raw_name": item.name, "raw_amount": amount, "raw_unit": unit,
            "raw_text": f"{item.name} {item.amount}",
            "ingredient_id": "user_" + hashlib.sha256(item.name.encode()).hexdigest()[:16],
            "canonical_name": item.name, "category": "other", "aliases": [], "allergens": item.allergens,
            "grams": item.grams, "amount_evidence": item.amount_evidence, "scalable": item.scalable,
            "usda_query": item.usda_query})
    return validate_recipe({"name": draft.name, "description": draft.description or None,
        "original_servings": draft.servings_evidence or None, "servings": draft.servings,
        "servings_evidence": draft.servings_evidence, "prep_time_min": draft.prep_time_min,
        "cook_time_min": draft.cook_time_min, "total_time_min": draft.total_time_min,
        "difficulty": None, "ingredients": ingredients, "steps": draft.steps,
        "taste_tags": [], "cooking_methods": [], "required_tools": [], "warnings": draft.warnings})


def _amount_parts(amount):
    """Split an explicit scalar and unit; keep ranges/approximation as source text."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?|\.\d+|[一二两三四五六七八九十半])\s*([A-Za-z\u4e00-\u9fff]{1,10})\s*", amount)
    if not match:
        return amount, None
    number, unit = match.groups()
    chinese = dict(zip("一二两三四五六七八九十半", (1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0.5)))
    return str(chinese[number]) if number in chinese else number, unit


class CookbookRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS personal_recipes (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_session_id TEXT,
                operation_id TEXT UNIQUE, body TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS recipe_favorites (
                recipe_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def _public(row):
        if not row:
            return None
        identity, status, revision, created, updated, session_id, _, body = row
        return {**json.loads(body), "recipe_id": identity, "status": status, "revision": revision,
                "created_at": created, "updated_at": updated, "source_session_id": session_id,
                "origin": "user", "is_custom": True}

    def get(self, recipe_id):
        with self._connect() as db:
            return self._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())

    def create_draft(self, values, *, session_id=None, image_ids=(), operation_id=None, guard=None, active_turn_id=None):
        draft = RecipeDraftRequest.model_validate(values)
        if draft.source_kind == "image" and not image_ids:
            raise ValueError("本轮没有图片来源，不能标记为图片提取")
        document = _document(draft)
        # Nutrition belongs to the existing deterministic processor, never model-authored numbers.
        nutrition = calculate_nutrition(document, {})
        body = {"document": document.model_dump(mode="json"), "nutrition": nutrition,
                "source_text": draft.source_text, "source_kind": draft.source_kind,
                "source_image_ids": list(image_ids), "time_evidence": draft.time_evidence}
        stamp = now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != active_turn_id:
                    raise ValueError("本轮已经结束，没有保存草稿")
            if operation_id:
                existing = db.execute("SELECT * FROM personal_recipes WHERE operation_id=?", (operation_id,)).fetchone()
                if existing:
                    if json.loads(existing[-1]) != body or existing[5] != session_id:
                        raise ValueError("同一操作标识已用于其他草稿内容，请读取已有草稿后核对")
                    return self._public(existing)
            identity = "custom-" + str(uuid4())
            if guard:
                guard()
            db.execute("INSERT INTO personal_recipes VALUES(?,?,?,?,?,?,?,?)", (
                identity, "draft", 1, stamp, stamp, session_id, operation_id,
                json.dumps(body, ensure_ascii=False, allow_nan=False)))
            if guard:
                guard()
        return self.get(identity)

    def update_draft(self, recipe_id, values, revision):
        draft = RecipeDraftRequest.model_validate(values)
        document = _document(draft)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone()
            record = self._public(row)
            if not record or record["status"] != "draft" or record["revision"] != revision:
                raise ValueError("草稿已更新或已保存，请刷新后核对")
            old = json.loads(row[-1])
            if draft.source_kind != old["source_kind"]:
                raise ValueError("请保留原始来源类型")
            old.update(document=document.model_dump(mode="json"), nutrition=calculate_nutrition(document, {}),
                       source_text=draft.source_text, time_evidence=draft.time_evidence)
            db.execute("UPDATE personal_recipes SET body=?,revision=revision+1,updated_at=? WHERE id=?",
                       (json.dumps(old, ensure_ascii=False, allow_nan=False), now(), recipe_id))
        return self.get(recipe_id)

    def publish(self, recipe_id, revision):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
            if not record:
                raise KeyError(recipe_id)
            if record["status"] == "saved" and revision == record["revision"] - 1:
                return record  # A repeated confirmation after an uncertain response is idempotent.
            if record["revision"] != revision or record["status"] != "draft":
                raise ValueError("草稿版本已变化，请刷新后核对")
            validate_recipe(record["document"])
            db.execute("UPDATE personal_recipes SET status='saved',revision=revision+1,updated_at=? WHERE id=?",
                       (now(), recipe_id))
        return self.get(recipe_id)

    def list(self, *, query="", status="saved", limit=20, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000 or status not in {"saved", "draft", "all"}:
            raise ValueError("菜谱列表参数无效")
        with self._connect() as db:
            records = [self._public(row) for row in db.execute("SELECT * FROM personal_recipes ORDER BY created_at DESC,id")]
        terms = query.casefold().strip()
        records = [item for item in records if (status == "all" or item["status"] == status)
                   and (not terms or terms in json.dumps(item["document"], ensure_ascii=False).casefold())]
        return {"total": len(records), "items": records[offset:offset+limit], "offset": offset, "limit": limit}

    def favorite(self, recipe_id, enabled):
        with self._connect() as db:
            if enabled:
                db.execute("INSERT OR IGNORE INTO recipe_favorites VALUES(?,?)", (recipe_id, now()))
            else:
                db.execute("DELETE FROM recipe_favorites WHERE recipe_id=?", (recipe_id,))
        return {"recipe_id": recipe_id, "favorite": enabled}

    def favorites(self):
        with self._connect() as db:
            return [row[0] for row in db.execute("SELECT recipe_id FROM recipe_favorites ORDER BY created_at DESC")]

    @staticmethod
    def snapshot(record):
        return {"data_version": f"{record['recipe_id']}:v{record['revision']}", "imported_at": record["updated_at"]}

    @staticmethod
    def normalized(record):
        document = record["document"]
        recipe = {**document, "recipe_id": record["recipe_id"], "origin": "user", "is_custom": True,
                  "source": "user", "estimated_servings": False, "ready_for_recommendation": True,
                  "blocking_reasons": [], "nutrition": record["nutrition"]}
        recipe["ingredients"] = [{**item, "ordinal": index, "nutrition": {"estimated": False}}
                                 for index, item in enumerate(document["ingredients"])]
        recipe["allergens"] = sorted({a for item in document["ingredients"] for a in item["allergens"]})
        return recipe

    def retrieve(self, recipe_id, expected_snapshot=None):
        record = self.get(recipe_id)
        if not record or record["status"] != "saved":
            return {"status": "not_found", "recipe": None}
        snapshot = self.snapshot(record)
        if expected_snapshot is not None and expected_snapshot != snapshot:
            return {"status": "snapshot_changed", "snapshot": snapshot, "message": "自定义菜谱已更新，请重新读取。"}
        return {"status": "ok", "snapshot": snapshot, "recipe": self.normalized(record),
                "notes": ["用户自定义菜谱；营养未知项保留未知，时间与人数请核对来源。"]}


class PersonalRecipeRetrieval:
    """Preserve the official retrieval contract and resolve personal recipe references."""
    def __init__(self, base, cookbook):
        self.base, self.cookbook = base, cookbook
        self.store = getattr(base, "store", None)
        self.foods = getattr(base, "foods", None)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def get_recipe(self, request):
        values = request.model_dump(mode="json") if isinstance(request, BaseModel) else request
        if values.get("recipe_id", "").startswith("custom-"):
            return self.cookbook.retrieve(values["recipe_id"], values.get("expected_snapshot"))
        return self.base.get_recipe(request)


def make_recipe_tools(cookbook):
    from chefmate_agent import InjectedToolSpec

    def create(values, context):
        host = context.host_context
        binding = getattr(host, "binding", None)
        def guard():
            if host is None or getattr(host, "cancelled", None) is not None and host.cancelled.is_set():
                raise ValueError("本轮已经结束，没有保存草稿")
            if binding is not None and not hasattr(binding.repository, "path"):
                state = binding.repository.get(host.session_id)
                if (state is None or not state.get("running")
                        or (state.get("active_turn") or {}).get("id") != host.turn_id):
                    raise ValueError("本轮已经结束，没有保存草稿")
        latest = next((m for m in reversed(context.messages) if m.get("role") == "user"), {})
        try:
            if values.get("source_kind", "text") == "text":
                source = re.sub(r"\s+", "", values.get("source_text", ""))
                supplied = [re.sub(r"\s+", "", m.get("content", "")) for m in context.messages
                            if m.get("role") == "user"]
                if not source or not any(source in text for text in supplied):
                    raise ValueError("文字菜谱原文需引用用户实际提供的内容，不能编造来源")
            record = cookbook.create_draft(values, session_id=getattr(host, "session_id", None),
                image_ids=[image["id"] for image in latest.get("images", [])],
                operation_id=f"{getattr(host, 'turn_id', '')}:{context.call_id}", guard=guard,
                active_turn_id=host.turn_id if binding is not None and hasattr(binding.repository, "path") else None)
            return {"status": "ok", "draft": record,
                    "message": "草稿已整理。请在菜谱中心核对食材、份量和时间，再点击确认保存。"}
        except ValueError as error:
            return {"status": "invalid_recipe", "message": str(error)}

    def search(values, context):
        request = CustomSearchRequest.model_validate(values)
        records = cookbook.list(query=request.query, limit=request.limit, offset=request.offset)
        return {"status": "ok", "total": records["total"], "items": [
            {"recipe_id": item["recipe_id"], "name": item["document"]["name"], "origin": "user",
             "snapshot": cookbook.snapshot(item), "servings": item["document"]["servings"],
             "total_time_min": item["document"]["total_time_min"]} for item in records["items"]],
            "message": "使用get_recipe读取单道菜谱后，再用其真实snapshot安排餐单。"}

    return [InjectedToolSpec(name="create_recipe_draft", args_schema=RecipeDraftRequest, handler=create,
                description="将用户菜谱文字或本轮图片转写为可核对的自定义菜谱草稿。缺失时间/人数/克重留null，不生成营养数字。用户在菜谱中心确认保存后才可烹饪。",
                required_skill="recipe-authoring", effect="write", side_effects=("保存待确认菜谱草稿",)),
            InjectedToolSpec(name="search_custom_recipes", args_schema=CustomSearchRequest, handler=search,
                description="按菜名或食材查找用户已确认保存的自定义菜谱。得到ID后get_recipe，再用于正常选菜和烹饪。",
                required_skill="recipe-authoring")]
