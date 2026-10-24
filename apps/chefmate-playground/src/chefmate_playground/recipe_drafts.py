"""Quoted recipe inputs and conversion into the shared recipe contract."""

import hashlib
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError
from chefmate_contracts import RecipeDocument
from chefmate_recipe_processing import validate_recipe


def _quantity_in(evidence, expected, kind):
    """Quantitative source fields must match a quoted amount, including units."""
    units = {
        "servings": r"人份?|份|位|口|servings?|people|persons?|portions?",
        "grams": r"千克|公斤|克|kg|g",
        "minutes": r"小时|分钟?|秒钟?|hours?|minutes?|seconds?|min",
    }
    chinese = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "半": 0.5,
    }
    for match in re.finditer(
        r"(\d+(?:\.\d+)?|\.\d+|[一二两三四五六七八九十半])\s*(" + units[kind] + r")(?![A-Za-z])", evidence, re.I
    ):
        token, unit = match.groups()
        number = chinese[token] if token in chinese else float(token)
        if kind == "grams" and unit.lower() in {"kg", "千克", "公斤"}:
            number *= 1000
        if kind == "minutes":
            if unit.lower() in {"小时", "hour", "hours"}:
                number *= 60
            elif unit.lower() in {"秒", "秒钟", "second", "seconds"}:
                number /= 60
        if abs(number - expected) < 0.000001:
            return True
    return False


class DraftIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    amount: str = Field(default="适量", max_length=200, description="只填用量，如250克、2个、少许；不重复食材名。用户要求的建议配比可写入，并在warnings明确标记建议。")
    grams: float | None = Field(
        default=None, ge=0, le=100000, description="仅原文明确的精确克重。助手建议配比写amount且本字段必须为null；建议evidence不能作为实测克重依据。个数、体积、约量或范围不自行换算。"
    )
    amount_evidence: str = Field(
        default="",
        max_length=300,
        description="逐字复制source_text中的连续原文，保留表格竖线和标点；不能改写成另一种格式。",
    )
    scalable: bool = True
    usda_query: str = Field(default="", max_length=150)
    allergens: list[str] = Field(default_factory=list, max_length=15)


class DraftValueProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["suggested", "user_confirmed"]
    evidence: str = Field(min_length=1, max_length=1500,
        description="suggested逐字引用本轮用户要求补充/建议的文字；user_confirmed逐字引用用户实际给出的数值答案，不引用助手自己的判断。")
    question_id: str | None = Field(default=None, max_length=100,
        description="用户通过ask_user选项回答时填写真实last_answer.question_id；直接补充文字或请求建议时为null。不是发布确认。")


class RecipeMetadataProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servings: DraftValueProvenance | None = None
    difficulty: DraftValueProvenance | None = None
    prep_time_min: DraftValueProvenance | None = None
    cook_time_min: DraftValueProvenance | None = None
    total_time_min: DraftValueProvenance | None = None


class RecipeDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    source_text: str = Field(
        min_length=1,
        max_length=20000,
        description="文字来源直接复制用户消息的完整原文或连续片段，保留Markdown表格与标点，不能摘要改写。图片来源为可辨认原文转写。",
    )
    source_kind: Literal["text", "image"] = "text"
    servings: float | None = Field(default=None, gt=0, le=100)
    servings_evidence: str = Field(default="", max_length=300, description="逐字引用source_text中人数/份数依据。")
    prep_time_min: float | None = Field(
        default=None, ge=0, le=2880, description="原文明确的备菜总耗时；未明确时为null，不能从总时间倒推。"
    )
    cook_time_min: float | None = Field(
        default=None, ge=0, le=2880, description="原文明确的烹饪总耗时；未明确时为null，不能从总时间减去备菜时间倒推。"
    )
    total_time_min: float | None = Field(
        default=None, ge=0, le=2880, description="只有原文明确总耗时才填写；单一步骤计时不等于整道菜总耗时。"
    )
    time_evidence: str = Field(
        default="",
        max_length=500,
        description="逐字引用source_text的时间依据；没有原文则时间保持null，可在warnings说明建议估时。",
    )
    difficulty: Literal["beginner", "normal", "advanced"] | None = None
    difficulty_evidence: str = Field(default="", max_length=300, description="逐字引用原文的明确难度说明；未提供难度时保留null，不按菜名或步骤猜测。")
    taste_tags: list[str] = Field(default_factory=list, max_length=30)
    cooking_methods: list[str] = Field(default_factory=list, max_length=30)
    required_tools: list[str] = Field(default_factory=list, max_length=30)
    ingredients: list[DraftIngredient] = Field(min_length=1, max_length=80)
    steps: list[str] = Field(
        min_length=1, max_length=80, description="可直接执行的步骤，保留火候和原文时间；推断的安全建议需明确标识。"
    )
    warnings: list[str] = Field(default_factory=list, max_length=30)
    metadata_provenance: RecipeMetadataProvenance = Field(default_factory=RecipeMetadataProvenance,
        description="仅对非原文的元数据填写：用户要求建议用suggested；用户已明确给出/选定值用user_confirmed。未列字段仍必须有原文依据。建议会自动标记，不用为建议反复追问。")

    @model_validator(mode="after")
    def grounded(self):
        # Stable codes and precise locations survive the agent's safe error filter.
        # Only static repair instructions are exposed; never echo input or exceptions.
        errors = []

        def reject(location, code, message):
            errors.append({"type": PydanticCustomError(code, message), "loc": location})

        normalized_source = re.sub(r"\s+", "", self.source_text)
        provenance = self.metadata_provenance.model_dump(exclude_none=True)
        evidence_fields = [(("servings_evidence",), self.servings_evidence),
                           (("difficulty_evidence",), self.difficulty_evidence),
                           (("time_evidence",), self.time_evidence)]
        evidence_fields.extend(
            (("ingredients", index, "amount_evidence"), item.amount_evidence)
            for index, item in enumerate(self.ingredients)
            if item.grams is not None
        )
        for location, evidence in evidence_fields:
            if evidence and re.sub(r"\s+", "", evidence) not in normalized_source:
                reject(location, "recipe_evidence_not_quoted", "依据必须逐字引用原文，保留表格和标点")
        choices = {"beginner": r"新手|简单|入门|beginner|easy", "normal": r"普通|中等|normal|medium",
                   "advanced": r"进阶|困难|advanced|hard"}
        for field, basis in provenance.items():
            value = getattr(self, field)
            if value is None:
                reject(("metadata_provenance", field), "recipe_metadata_value_missing", "标记来源的字段必须同时提供实际值")
            elif basis["kind"] == "user_confirmed":
                matches = (re.search(r"(?<![不非A-Za-z])(?:" + choices[value] + r")(?![A-Za-z])", basis["evidence"], re.I)
                           if field == "difficulty" else _quantity_in(basis["evidence"], value,
                                                                     "servings" if field == "servings" else "minutes"))
                if not matches:
                    reject(("metadata_provenance", field, "evidence"), "recipe_metadata_confirmation_mismatch",
                           "确认值须与用户真实答案一致；选项答案直接引用已选标签，不要求用户改写句子")
            if basis["kind"] == "suggested" and basis.get("question_id"):
                reject(("metadata_provenance", field, "question_id"), "recipe_metadata_question_invalid",
                       "建议值引用用户请求，不冒充已回答的问题")
        if self.difficulty is not None and "difficulty" not in provenance:
            evidence = re.sub(r"[*_#]", "", self.difficulty_evidence)
            if not re.search(r"(?:难度|difficulty)\s*[:：]?\s*(?:为|是)?\s*(?:" + choices[self.difficulty] + r")", evidence, re.I):
                reject(("difficulty",), "recipe_difficulty_unquoted", "难度需要原文明确说明，未提供时保留未知")
        if self.servings is not None and "servings" not in provenance:
            if not self.servings_evidence:
                reject(("servings_evidence",), "recipe_evidence_required", "人数需要原文依据；未写明时保留未知")
            elif not _quantity_in(self.servings_evidence, self.servings, "servings"):
                reject(("servings",), "recipe_servings_mismatch", "份数与原文人数或份数不一致")
        for index, item in enumerate(self.ingredients):
            if item.grams is None:
                continue
            if not item.amount_evidence:
                reject(("ingredients", index, "amount_evidence"), "recipe_evidence_required", "克重需要原文依据")
            elif not _quantity_in(item.amount_evidence, item.grams, "grams"):
                reject(("ingredients", index, "grams"), "recipe_grams_mismatch", "克重与原文不一致，不能换算个数或体积")
        for field in ("prep_time_min", "cook_time_min", "total_time_min"):
            value = getattr(self, field)
            if value is not None and field not in provenance:
                if not self.time_evidence:
                    reject(("time_evidence",), "recipe_evidence_required", "耗时需要原文依据")
                    break
                if not _quantity_in(self.time_evidence, value, "minutes"):
                    reject((field,), "recipe_time_mismatch", "耗时与原文不一致，不能倒推或补造")
        # Recipe metadata commonly says "制作时间" without the word "总".
        # A step heading alone must still never establish the whole recipe time.
        explicit_total = re.search(r"总|合计|全程|total", self.time_evidence, re.I)
        recipe_time_heading = re.search(r"制作(?:时间|耗时)", self.time_evidence)
        step_heading = re.search(r"步骤|第.{0,5}步", self.time_evidence)
        if self.total_time_min is not None and "total_time_min" not in provenance and not (explicit_total or recipe_time_heading and not step_heading):
            reject(("total_time_min",), "recipe_total_time_ambiguous", "单一步骤时长不能当作整道菜总耗时")
        for index, step in enumerate(self.steps):
            if not step.strip() or len(step) > 2000:
                reject(("steps", index), "recipe_step_invalid", "步骤应为可阅读的非空文本")
        if errors:
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self


class CustomSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=8, ge=1, le=30)
    offset: int = Field(default=0, ge=0, le=100000)
    status: Literal["saved", "draft", "all"] = Field(
        default="saved", description="saved找已保存菜谱；draft找待核对草稿；all同时搜索两者。"
    )


class RecipeDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    recipe_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)


class ConfirmRecipeDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmation_id: str = Field(min_length=1, max_length=100)


class RecipeDraftReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    recipe_id: str = Field(min_length=1, max_length=100)


class RecipePatchProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servings: DraftValueProvenance | None = None
    difficulty: DraftValueProvenance | None = None
    total_time_min: DraftValueProvenance | None = None


class RecipeMetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    servings: float | None = Field(default=None, gt=0, le=100,
        description="只提供需要修改的份数；省略保留原值，null明确清空且移除该项来源。非空新值提供对应metadata_provenance。")
    difficulty: Literal["beginner", "normal", "advanced"] | None = Field(default=None,
        description="只改用户要求的难度；省略保留原值，null清空。")
    total_time_min: float | None = Field(default=None, ge=0, le=2880,
        description="只改整道菜总耗时；省略保留原值，null清空。此patch绝不会修改备菜/烹饪小计。")
    metadata_provenance: RecipePatchProvenance = Field(default_factory=RecipePatchProvenance,
        description="每个修改后的非空值提供该字段的真实建议/用户确认依据；未修改字段不要重抄来源。")

    @model_validator(mode="after")
    def targeted(self):
        fields = self.model_fields_set & {"servings", "difficulty", "total_time_min"}
        if not fields:
            raise PydanticCustomError("recipe_metadata_patch_empty", "metadata_patch至少提供一个需要修改的字段")
        if set(self.metadata_provenance.model_dump(exclude_none=True)) - fields:
            raise PydanticCustomError("recipe_metadata_patch_unrelated", "来源仅能对应本次patch列出的字段")
        if any(getattr(self, field) is None for field in self.metadata_provenance.model_dump(exclude_none=True)):
            raise PydanticCustomError("recipe_metadata_value_missing", "清空值时不要提供新的来源；该项旧来源会一并移除")
        return self


class RecipeDraftRevisionRequest(RecipeDeletionRequest):
    recipe: RecipeDraftRequest | None = Field(default=None,
        description="修改食材/步骤等正文时提供完整recipe；仅修改难度/份数/总耗时请省略，改用metadata_patch。")
    metadata_patch: RecipeMetadataPatch | None = Field(default=None,
        description="仅改难度/份数/总耗时的优先入口。只列要改字段及其来源，其他食材、步骤、分项时间与原文逐字保留。与recipe严格二选一。")

    @model_validator(mode="after")
    def one_edit_mode(self):
        if (self.recipe is None) == (self.metadata_patch is None):
            raise PydanticCustomError("recipe_edit_mode_required", "recipe与metadata_patch必须且只能提供一个，不能合并两种修改")
        return self


def apply_metadata_patch(record, patch: RecipeMetadataPatch):
    """Build validated input from the real read record, never from a model copy of its body."""
    from .recipe_provenance import SUGGESTION_PREFIX

    doc = record["document"]
    source = record.get("source_metadata_evidence") or {}
    values = {key: doc[key] for key in ("name", "servings", "difficulty", "prep_time_min", "cook_time_min",
                                      "total_time_min", "taste_tags", "cooking_methods", "required_tools", "steps", "warnings")}
    values["warnings"] = [text for text in values["warnings"] if not text.startswith(SUGGESTION_PREFIX)]
    values.update(description=doc.get("description") or "", source_text=record["source_text"], source_kind=record["source_kind"],
        servings_evidence=source.get("servings", doc.get("servings_evidence", "")),
        difficulty_evidence=source.get("difficulty", record.get("difficulty_evidence", "")),
        time_evidence=source.get("time", record.get("time_evidence", "")),
        ingredients=[{"name": item["canonical_name"],
                      "amount": f"{item.get('raw_amount') or ''}{item.get('raw_unit') or ''}" or "适量",
                      **{key: item[key] for key in ("grams", "amount_evidence", "scalable", "usda_query", "allergens")}}
                     for item in doc["ingredients"]])
    provenance = dict(record.get("metadata_provenance") or {})
    requested = patch.model_fields_set & {"servings", "difficulty", "total_time_min"}
    bases = patch.metadata_provenance.model_dump(exclude_none=True)
    for field in requested:
        value = getattr(patch, field)
        if value is None:
            provenance.pop(field, None)
        elif field in bases:
            provenance[field] = bases[field]
        elif value != doc.get(field):
            raise ValueError(f"metadata_patch.{field}的新值需要该项metadata_provenance；引用真实当前答案，不必再次询问用户")
        values[field] = value
    values["metadata_provenance"] = provenance
    return RecipeDraftRequest.model_validate(values), requested


def _document(draft: RecipeDraftRequest) -> RecipeDocument:
    from .recipe_provenance import metadata_evidence, metadata_warnings

    ingredients = []
    for item in draft.ingredients:
        amount_text = item.amount
        # Models sometimes include the name in amount despite the separate field.
        # Keep the original text but parse the explicit same-ingredient quantity.
        if amount_text.startswith(item.name):
            rest = amount_text[len(item.name):].strip()
            if re.match(r"[\d.一二两三四五六七八九十半约少适]", rest):
                amount_text = rest
        amount, unit = _amount_parts(amount_text)
        ingredients.append(
            {
                "raw_name": item.name,
                "raw_amount": amount,
                "raw_unit": unit,
                "raw_text": f"{item.name} {item.amount}",
                "ingredient_id": "user_" + hashlib.sha256(item.name.encode()).hexdigest()[:16],
                "canonical_name": item.name,
                "category": "other",
                "aliases": [],
                "allergens": item.allergens,
                "grams": item.grams,
                "amount_evidence": item.amount_evidence,
                "scalable": item.scalable,
                "usda_query": item.usda_query,
            }
        )
    return validate_recipe(
        {
            "name": draft.name,
            "description": draft.description or None,
            "original_servings": (draft.servings_evidence or None) if draft.metadata_provenance.servings is None else None,
            "servings": draft.servings,
            "servings_evidence": metadata_evidence(draft, "servings", draft.servings_evidence),
            "prep_time_min": draft.prep_time_min,
            "cook_time_min": draft.cook_time_min,
            "total_time_min": draft.total_time_min,
            "difficulty": draft.difficulty,
            "ingredients": ingredients,
            "steps": draft.steps,
            "taste_tags": draft.taste_tags,
            "cooking_methods": draft.cooking_methods,
            "required_tools": draft.required_tools,
            "warnings": metadata_warnings(draft),
        }
    )


def _amount_parts(amount):
    """Split an explicit scalar and unit; keep ranges/approximation as source text."""
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?|\.\d+|[一二两三四五六七八九十半])\s*([A-Za-z\u4e00-\u9fff]{1,10})\s*", amount
    )
    if not match:
        return amount, None
    number, unit = match.groups()
    chinese = dict(zip("一二两三四五六七八九十半", (1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0.5)))
    return str(chinese[number]) if number in chinese else number, unit
