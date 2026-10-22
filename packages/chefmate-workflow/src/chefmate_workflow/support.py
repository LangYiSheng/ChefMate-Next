"""Shared kitchen constants, validation errors and deterministic source helpers."""

import json
import math
import re
from datetime import timezone

PREP_NOTE = "源菜谱没有可靠的必选/可选字段；这里逐项展示原文，跳过需由用户明确确认，不代表已证明可省略。"


FIELD_META = {
    "servings": ("用餐人数", "number"), "max_total_time_min": ("最长耗时（分钟）", "number"),
    "taste_tags": ("口味", "multiselect"), "available_tools": ("可用厨具", "multiselect"),
    "ingredient_terms": ("想吃或已有的食材", "multiselect"),
    "exclude_ingredient_terms": ("不想吃的食材", "multiselect"), "dietary_notes": ("其他饮食需求", "textarea"),
    "allow_estimated": ("允许营养估算值", "checkbox"),
}


STAGE_TRANSITIONS = {
    "idea": {"planning"}, "planning": {"idea", "prep"}, "prep": {"planning", "cooking"},
    "cooking": {"prep", "completed"}, "completed": {"idea", "planning"},
}


class DomainConflict(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _validation_details(error):
    """Do not echo rejected values, private input or Pydantic context into tools."""
    return [{"loc": list(item["loc"]), "type": item["type"], "hint": _validation_message(item["type"])}
            for item in error.errors(include_input=False, include_context=False, include_url=False)]


def _validation_message(code):
    if code == "missing":
        return "缺少必填字段。"
    if code == "extra_forbidden":
        return "不支持这个字段，请按工具契约提供参数。"
    if code in {"list_type", "bool_type", "string_type"}:
        return "字段类型不符，不能使用null；省略保留，列表用[]、文字用空串清空。"
    return "字段值或类型不符合工具契约，请修正该字段。"


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _amount(value, unit):
    return f"{value:g} {unit or ''}".strip() if value is not None else "用量未知"


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat()


def _ingredient_id(recipe_id, ordinal):
    return f"{recipe_id}:ingredient:{ordinal}"


def _step_id(recipe_id, ordinal):
    return f"{recipe_id}:step:{ordinal}"


def _explicit_durations(text):
    if not isinstance(text, str):
        return set()
    if re.search(r"\d+\s*[-~—到至]\s*\d+\s*(?:秒|分钟|小时|seconds?|minutes?|hours?|mins?)", text, re.I):
        return set()
    values = {1800} if "半小时" in text else set()
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(秒钟?|分钟|小时|seconds?|minutes?|hours?|mins?|s|min|h)(?![A-Za-z])", text, re.I):
        factor = 3600 if unit.lower() in {"小时", "hour", "hours", "h"} else 60 if unit.lower() in {
            "分钟", "minute", "minutes", "min", "mins"} else 1
        seconds = float(value) * factor
        if seconds.is_integer() and 0 < seconds <= 86400:
            values.add(int(seconds))
    return values
