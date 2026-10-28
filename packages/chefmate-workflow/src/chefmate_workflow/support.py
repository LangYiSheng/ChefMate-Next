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


_DURATION_NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万点]+|半)"
_DURATION_UNIT = r"(?:秒钟?|分钟|小时|hours?|minutes?|seconds?|mins?|min|s|h|分(?=\s|$|[，,。；;、!?！？）)\d零〇一二两三四五六七八九十半~～〜—–−－到至-]))(?![A-Za-z])"
_DURATION_PART = re.compile(
    rf"(?<![\d.零〇一二两三四五六七八九十百千万点个])(?P<number>{_DURATION_NUMBER})"
    rf"(?P<half_before>\s*(?:个\s*)?半)?\s*(?:个\s*)?(?P<unit>{_DURATION_UNIT})(?P<half_after>\s*半)?", re.I)
_DURATION_HALF = r"(?:\s*(?:个\s*)?半)?\s*(?:个\s*)?"
_DURATION_RANGE = re.compile(
    rf"(?<![\d.零〇一二两三四五六七八九十百千万点个]){_DURATION_NUMBER}{_DURATION_HALF}"
    rf"(?:{_DURATION_UNIT})?(?:\s*半)?\s*(?:[-~～〜—–−－]{{1,2}}|到|至)\s*"
    rf"{_DURATION_NUMBER}{_DURATION_HALF}{_DURATION_UNIT}", re.I)
_DURATION_NEGATIVE = re.compile(rf"[-−－负]\s*{_DURATION_NUMBER}{_DURATION_HALF}{_DURATION_UNIT}", re.I)


def _duration_number(value):
    if value == "半":
        return .5
    digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    if value in digits:
        return digits[value]
    if value.count("十") == 1:
        tens, ones = value.split("十")
        if (not tens or tens in digits) and (not ones or ones in digits):
            return digits.get(tens, 1) * 10 + digits.get(ones, 0)
    try:
        return float(value)
    except ValueError:
        return None


def _explicit_durations(text):
    if not isinstance(text, str) or _DURATION_RANGE.search(text) or _DURATION_NEGATIVE.search(text):
        return set()
    values, total, last_factor, end = set(), 0., 0, 0
    def finish():
        if total is not None and math.isfinite(total) and total.is_integer() and 0 < total <= 86400:
            values.add(int(total))
    for part in _DURATION_PART.finditer(text):
        value = _duration_number(part["number"])
        if part["half_before"] or part["half_after"]:
            value = value + .5 if value is not None and part["number"] != "半" else None
        unit = part["unit"].lower()
        factor = 3600 if unit in {"小时", "hour", "hours", "h"} else 60 if unit in {
            "分", "分钟", "minute", "minutes", "min", "mins"} else 1
        connector = text[end:part.start()].strip().lower()
        # Only adjacent, descending units form a single duration. Separate
        # instructions (e.g. boil 1 minute, then fry 30 seconds) stay separate.
        if not last_factor or factor >= last_factor or connector not in {"", "and", "又", "零"}:
            finish()
            total = 0.
        total = total + value * factor if total is not None and value is not None else None
        last_factor, end = factor, part.end()
    finish()
    return values
