"""Validate source quotations, read baselines, and human confirmation receipts."""

import json
import re
from datetime import date as Day, timedelta


def _date_matches_source(day, evidence, source, today):
    if not evidence or evidence not in source:
        return False
    if (
        day in evidence
        or f"{Day.fromisoformat(day).year}年{Day.fromisoformat(day).month}月{Day.fromisoformat(day).day}日" in evidence
    ):
        return True
    for phrase, offset in (("今天", 0), ("今日", 0), ("昨天", 1), ("昨日", 1), ("前天", 2)):
        if phrase in evidence and day == (today - timedelta(days=offset)).isoformat():
            return True
    return False


def _provided_nutrient(key, value, evidence):
    number = rf"(?<![\d.]){re.escape(f'{value:g}')}(?![\d.])"
    if key == "calories_kcal":
        return bool(re.search(number + r"\s*(?:kcal|千卡|大卡)", evidence, re.IGNORECASE))
    labels = {
        "protein_g": r"蛋白(?:质)?",
        "fat_g": "脂肪",
        "carbohydrate_g": r"碳水(?:化合物)?",
        "sugar_g": r"(?:糖|sugar)",
        "sodium_mg": r"(?:钠|sodium)",
    }
    units = r"(?:毫克|mg)" if key == "sodium_mg" else r"(?:克|g)"
    return bool(re.search(labels[key] + r"\s*[:：]?\s*" + number + r"\s*" + units, evidence, re.IGNORECASE))


def _validate_item_sources(items, user_text, baseline_items=()):
    remaining = list(baseline_items)
    for item in items:
        original = next((old for old in remaining if old["name"] == item.name), None)
        if original is not None:
            remaining.remove(original)  # An old verified item cannot authorize multiple new copies.
        quantity = (item.portion, item.amount_g, item.portion_basis)
        old_quantity = tuple(original[key] for key in ("portion", "amount_g", "portion_basis")) if original else None
        quantity_changed = quantity != old_quantity
        evidence_changed = bool(original and item.evidence != original["evidence"])
        if evidence_changed and (
            item.nutrition_basis == "user_provided"
            or item.portion_basis == "user_provided"
            and item.nutrition_basis == "unknown"
        ):
            if not item.evidence or item.evidence not in user_text:
                raise ValueError("已核实的用户来源说明只能沿用原文或替换为本轮实际提供的原文")
        if item.portion_basis == "user_provided" and quantity_changed:
            if item.portion not in user_text:
                raise ValueError("用户提供的份量需引用本轮原话；视觉份量请标记为估算并说明假设")
            if item.amount_g is not None:
                grams = re.escape(f"{item.amount_g:g}")
                kilograms = re.escape(f"{item.amount_g / 1000:g}")
                if not re.search(
                    rf"(?<![\d.])(?:{grams}\s*(?:克|g)|{kilograms}\s*(?:千克|kg))(?![a-z])", item.portion, re.IGNORECASE
                ):
                    raise ValueError("用户未提供这项克重；从碗、盘推算的克重请标为估算")
        if item.nutrition_basis == "user_provided":
            numbers = item.nutrition.model_dump()
            changed = {
                key: number
                for key, number in numbers.items()
                if number is not None
                and (
                    not original
                    or quantity_changed
                    or evidence_changed
                    or original["nutrition_basis"] != "user_provided"
                    or original["nutrition"].get(key) != number
                )
            }
            if changed:
                if not item.evidence or item.evidence not in user_text:
                    raise ValueError(
                        "营养evidence必须是本轮用户消息中逐字连续的一段原文；不要加外层引号、总结语或改动标点。按份量换算请标为估算"
                    )
                if any(not _provided_nutrient(key, number, item.evidence) for key, number in changed.items()):
                    raise ValueError("营养数值需与用户原文一致；估算请明确标记")


def _draft_receipt(record):
    receipt = {
        key: record[key] for key in ("id", "status", "revision", "date", "record_kind", "nutrition", "source_image_ids")
    }
    receipt.update(
        title=record["document"]["title"],
        food_count=len(record["document"]["items"]),
        source_kind=record["document"]["source_kind"],
    )
    return receipt


def _read_baseline(messages, identity, revision):
    for message in reversed(messages):
        if message.get("role") != "tool" or message.get("name") != "get_food_log":
            continue
        try:
            output = json.loads(message.get("content", ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(output, dict):
            continue
        record = output.get("record")
        if (
            output.get("status") == "ok"
            and isinstance(record, dict)
            and record.get("id") == identity
            and record.get("revision") == revision
            and record.get("status") == "draft"
        ):
            return record
    return None


def _latest_user_index(messages):
    return next((index for index in range(len(messages) - 1, -1, -1) if messages[index].get("role") == "user"), -1)


def _previous_draft_receipt(messages, identity, revision):
    # A newly inferred or changed draft needs another human turn before it can
    # become intake. The caller cannot turn its own current-round tool result
    # into evidence that the user reviewed the inferred food and portions.
    latest = _latest_user_index(messages)
    user_text = messages[latest].get("content", "") if latest >= 0 else ""
    explicit_identity = identity in user_text
    for message in reversed(messages[:latest]):
        if message.get("role") != "tool" or message.get("name") not in {
            "create_food_log_draft",
            "get_food_log",
            "revise_food_log_draft",
        }:
            continue
        try:
            output = json.loads(message.get("content", ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(output, dict) or output.get("status") != "ok":
            continue
        record = output.get("record") or output.get("draft")
        if not isinstance(record, dict) or record.get("status") != "draft":
            continue
        if record.get("id") == identity and record.get("revision") == revision:
            return record
        if not explicit_identity:
            return None  # A vague "this one" cannot approve an older, different draft.
    return None


def _explicit_confirmation(evidence, user_text, identity):
    if not evidence or evidence not in user_text:
        return False
    # Refuse negation, questions and quotes rather than finding an affirmative
    # substring inside "不要确认保存" or a question about how confirmation works.
    if re.search(
        r"(?:不|别|勿|先别|暂不|还没|尚未|取消|拒绝|不能|不要|不用)[^\n，,。；;]{0,8}(?:确认|保存|记录|入账)"
        r"|(?:确认|保存|记录|入账).{0,5}(?:吗|么|能否|是否|要不要|[?？])"
        r"|(?:如何|怎么|能否|是否|要不要).{0,12}(?:确认|保存|记录|入账)",
        user_text,
    ):
        return False
    if any(
        re.search(r"确认|保存|记录|入账", clause) and re.search(r"[?？]|吗|么", clause)
        for clause in re.split(r"[，,。；;\n]", user_text)
    ):
        return False
    if re.search(r"(?:引用|原话|示例|举例|假设|如果)", user_text):
        return False
    if re.search(r"[\"“‘「『`][^\"”’」』`]{0,120}(?:确认|保存|记录|入账)[^\"”’」』`]{0,120}[\"”’」』`]", user_text):
        return False
    mentioned = set(re.findall(r"food-[a-f0-9-]{36}", user_text))
    if mentioned and identity not in mentioned:
        return False
    return user_text.strip("。！! \n\t") in {"确认", "确定"} or bool(
        re.search(
            r"(?:确认|确定)(?:保存|记录|入账)|(?:保存|记录|入账)(?:这|刚才|当前|它|吧)|就按.{0,20}(?:保存|记录|入账)",
            evidence,
        )
    )


def _turn_guard(host):
    def guard():
        if getattr(host, "cancelled", None) is not None and host.cancelled.is_set():
            raise ValueError("本轮已经停止，没有保存饮食草稿")
        binding = getattr(host, "binding", None)
        if binding is not None and not hasattr(binding.repository, "path"):
            state = binding.repository.get(host.session_id)
            if not state or not state.get("running") or (state.get("active_turn") or {}).get("id") != host.turn_id:
                raise ValueError("本轮已经结束，没有保存饮食草稿")

    return guard
