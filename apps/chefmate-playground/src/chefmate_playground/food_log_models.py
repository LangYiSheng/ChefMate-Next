"""Personal intake contracts and honest nutrition projections.

Unknown values remain null; partial totals and estimation markers stay visible.
"""

import json
from datetime import date as Day, timedelta, timezone
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

LOCAL_ZONE = timezone(timedelta(hours=8))
MACROS = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg")
INTAKE_NOTICE = "仅汇总已确认的个人饮食记录；照片和描述中的份量、营养可能含估算，不能替代实际称量。未记录不代表没有吃，未知值不补零。"
CHAT_COMPONENT_PRESENTATION = (
    "本工具的真实回执会由应用在当前对话渲染饮食/营养组件，自动展示食物清单、份量、数值和状态。"
    "你的最终正文只用一句自然说明或必要建议；不要重复表格、数字清单或逐条记录，不要求用户切换页面。"
)


class FoodLogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class FoodLogNutrition(FoodLogModel):
    calories_kcal: Annotated[float | None, Field(ge=0, le=20000)] = None
    protein_g: Annotated[float | None, Field(ge=0, le=3000)] = None
    fat_g: Annotated[float | None, Field(ge=0, le=3000)] = None
    carbohydrate_g: Annotated[float | None, Field(ge=0, le=3000)] = None
    sugar_g: Annotated[float | None, Field(ge=0, le=3000)] = None
    sodium_mg: Annotated[float | None, Field(ge=0, le=100000)] = None


class FoodLogItem(FoodLogModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    portion: Annotated[str, Field(max_length=160,
        description="user_provided时逐字复制用户本轮原话中的连续份量片段，保留一/1和标点，不改写单位格式。例如原话一瓶净含量1升就直接填这段。estimated可写约量。修复此字段不要改动source_text。")] = ""
    amount_g: Annotated[float | None, Field(gt=0, le=10000,
        description="质量克数，不是毫升/升。用户只给1升时可留null并按原体积估营养；若假设密度换算1000克，portion_basis必须estimated且assumptions写明近似换算，不能标user_provided。")] = None
    portion_basis: Literal["unknown", "user_provided", "estimated"] = Field(default="unknown",
        description="同时标明portion/amount_g来源。用户原文份量且没有推算克重可user_provided；从体积/碗盘换算克重须estimated并保留原份量与假设。")
    nutrition: FoodLogNutrition = Field(default_factory=FoodLogNutrition)
    nutrition_basis: Literal["unknown", "user_provided", "estimated"] = "unknown"
    evidence: Annotated[
        str,
        Field(
            max_length=1000,
            description="user_provided营养时必须逐字复制本轮用户原文中的连续片段，保持标点，不加外层引号或解释；estimated时说明推算依据。",
        ),
    ] = ""
    assumptions: Annotated[list[Annotated[str, Field(min_length=1, max_length=400)]], Field(max_length=12)] = Field(
        default_factory=list, description="估算所用假设。体积近似转质量时说明密度假设；按每100毫升推算营养不要求同时换成克，不能把假设说成用户实测。"
    )

    @model_validator(mode="after")
    def evidence_matches_precision(self):
        self.name = self.name.strip()
        self.portion = self.portion.strip()
        if not self.name:
            raise ValueError("请写出识别到的食物名称")
        values = self.nutrition.model_dump()
        has_values = any(value is not None for value in values.values())
        if self.portion_basis == "unknown" or not self.portion:
            if self.amount_g is not None or has_values:
                raise ValueError("份量未知时请保留克重和营养为空，先由用户确认")
            self.portion_basis = "unknown"
        if self.nutrition_basis == "unknown" and has_values:
            raise ValueError("请明确营养数值来自用户还是估算")
        if self.nutrition_basis != "unknown" and not has_values:
            self.nutrition_basis = "unknown"
        if has_values and not self.evidence.strip():
            raise ValueError("营养数值需要来源说明")
        if (
            self.portion_basis == "estimated" or has_values and self.nutrition_basis == "estimated"
        ) and not self.assumptions:
            raise ValueError("估算份量或营养需要说明假设，例如碗的大小、烹饪用油或食材组成")
        return self


class FoodLogDraftRequest(FoodLogModel):
    title: Annotated[str, Field(min_length=1, max_length=120)]
    meal_type: Literal["breakfast", "lunch", "dinner", "snack", "unspecified"] = "unspecified"
    date: Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None
    date_evidence: Annotated[
        str,
        Field(
            max_length=160,
            description="日期来源的本轮原话连续片段，如今天、昨天或2026-10-20；不加外层引号和解释。默认date=null时留空。",
        ),
    ] = ""
    source_kind: Literal["text", "image"] = "text"
    source_text: Annotated[
        str,
        Field(
            max_length=8000,
            description="文字来源直接复制本轮用户消息中的连续原文，保留标点；修订时沿用get_food_log读取的旧原文，除非用户明确更正。",
        ),
    ] = ""
    items: Annotated[list[FoodLogItem], Field(min_length=1, max_length=30)]
    notes: Annotated[str, Field(max_length=2000)] = ""

    @model_validator(mode="after")
    def clean_values(self):
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("请给这次饮食记录写一个名称")
        if self.date is not None:
            Day.fromisoformat(self.date)
        return self


class FoodLogSearchRequest(FoodLogModel):
    query: Annotated[str, Field(max_length=200)] = ""
    status: Literal["draft", "saved", "all"] = "saved"
    days: Annotated[int, Field(ge=1, le=366)] = 7
    limit: Annotated[int, Field(ge=1, le=10)] = 6
    offset: Annotated[int, Field(ge=0, le=100000)] = 0


class FoodLogIdentityRequest(FoodLogModel):
    id: Annotated[str, Field(pattern=r"^food-[a-f0-9-]{36}$")]


class ReviseFoodLogDraftRequest(FoodLogIdentityRequest):
    revision: Annotated[int, Field(ge=1)]
    record: FoodLogDraftRequest


class ConfirmFoodLogRequest(FoodLogIdentityRequest):
    revision: Annotated[int, Field(ge=1)]
    confirmation_evidence: Annotated[
        str,
        Field(
            min_length=1,
            max_length=300,
            description="逐字复制本轮用户明确确认保存/记录/入账这份饮食的原话；不能使用历史或助手生成的文字。",
        ),
    ]


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _nutrition(items):
    totals, known_total, coverage = {}, {}, {}
    estimated_fields = []
    for key in MACROS:
        known = [item["nutrition"][key] for item in items if item["nutrition"].get(key) is not None]
        known_total[key] = round(sum(known), 3) if known else None
        totals[key] = known_total[key] if items and len(known) == len(items) else None
        coverage[key] = {"known": len(known), "total": len(items)}
        if any(item["nutrition"].get(key) is not None and item["nutrition_basis"] == "estimated" for item in items):
            estimated_fields.append(key)
    return {
        "total": totals,
        "known_total": known_total,
        "coverage": coverage,
        "estimated": bool(estimated_fields),
        "estimated_fields": estimated_fields,
        "incomplete": any(value is None for value in totals.values()),
        "notice": INTAKE_NOTICE,
    }


def intake_summary(records):
    nutrition = _nutrition(
        [
            {
                "nutrition": record["nutrition"]["total"],
                "nutrition_basis": "estimated" if record["nutrition"]["estimated"] else "user_provided",
            }
            for record in records
        ]
    )
    # Partial known foods remain visible without making an incomplete record look complete.
    nutrition["known_total"] = {
        key: round(sum(values), 3) if values else None
        for key in MACROS
        for values in [
            [
                record["nutrition"]["known_total"][key]
                for record in records
                if record["nutrition"]["known_total"][key] is not None
            ]
        ]
    }
    nutrition.update(
        record_count=len(records),
        food_count=sum(len(record["document"]["items"]) for record in records),
        estimated_records=sum(record["nutrition"]["estimated"] for record in records),
        estimated=any(record["nutrition"]["estimated"] for record in records),
        estimated_fields=sorted({key for record in records for key in record["nutrition"]["estimated_fields"]}),
    )
    return nutrition
