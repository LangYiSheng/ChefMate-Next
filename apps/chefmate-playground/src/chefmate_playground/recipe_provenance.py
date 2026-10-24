"""Keep user-approved suggestions separate from verbatim recipe source facts."""

import re


SUGGESTION_PREFIX = "建议参数（非原文或实测）："
LABELS = {"servings": "份数", "difficulty": "难度", "prep_time_min": "备菜耗时",
          "cook_time_min": "烹饪耗时", "total_time_min": "总耗时"}
DIFFICULTIES = {"beginner": "新手", "normal": "普通", "advanced": "进阶"}


def _normalized(text):
    return re.sub(r"\s+", "", text or "")


def metadata_evidence(draft, field, fallback=""):
    basis = getattr(draft.metadata_provenance, field)
    if basis is None:
        return fallback
    label = "助手建议；用户请求" if basis.kind == "suggested" else "用户确认"
    return f"{label}：{basis.evidence}"


def metadata_warnings(draft):
    warnings = [text for text in draft.warnings if not text.startswith(SUGGESTION_PREFIX)]
    values = []
    for field, basis in draft.metadata_provenance.model_dump(exclude_none=True).items():
        if basis["kind"] != "suggested":
            continue
        value = getattr(draft, field)
        text = DIFFICULTIES[value] if field == "difficulty" else f"{value:g}{'人份' if field == 'servings' else '分钟'}"
        values.append(f"{LABELS[field]}{text}")
    return [*warnings, SUGGESTION_PREFIX + "、".join(values) + "。"] if values else warnings


def metadata_time_evidence(draft):
    parts = [draft.time_evidence] if draft.time_evidence else []
    for field in ("prep_time_min", "cook_time_min", "total_time_min"):
        if getattr(draft.metadata_provenance, field) is not None:
            parts.append(f"{LABELS[field]}：{metadata_evidence(draft, field)}")
    return "；".join(parts)


def validate_metadata_provenance(draft, messages, host, baseline=None):
    """Only new provenance needs a current human request/answer; old facts stay bound to their value."""
    latest = next((message.get("content", "") for message in reversed(messages) if message.get("role") == "user"), "")
    previous = (baseline or {}).get("metadata_provenance") or {}
    original = (baseline or {}).get("document") or {}
    provenance = draft.metadata_provenance.model_dump(exclude_none=True)
    answer = None
    for field, basis in provenance.items():
        if previous.get(field) == basis and original.get(field) == getattr(draft, field):
            continue
        evidence = _normalized(basis["evidence"])
        if evidence not in _normalized(latest):
            return "新增建议或确认依据须引用本轮用户实际请求/答案；保留原文，不编造用户说过的话。"
        if basis["kind"] == "suggested":
            continue
        question_id = basis.get("question_id")
        question = ""
        if question_id:
            if answer is None:
                binding = getattr(host, "binding", None)
                state = binding.repository.get(host.session_id) if binding is not None else None
                workspace = (state or {}).get("workspace") or {}
                answer = {} if workspace.get("pending_question") else workspace.get("last_answer") or {}
            chosen = [*answer.get("labels", []), answer.get("text", "")]
            if answer.get("question_id") != question_id or not any(evidence in _normalized(text) for text in chosen):
                return "请使用宿主last_answer中本次真实问题与已选标签；不需让用户再次确认，也不能重用旧问题。"
            question = answer.get("question", "")
        # A grouped answer can identify the three metadata values without magic
        # keywords. A lone step timer still cannot become whole-recipe time.
        grouped = all(
            key in provenance and provenance[key]["kind"] == "user_confirmed"
            and provenance[key].get("question_id") == question_id
            and _normalized(provenance[key]["evidence"]) == evidence
            for key in ("difficulty", "servings", "total_time_min")
        )
        context = basis["evidence"] + " " + question
        required = {"difficulty": r"难度|difficulty", "prep_time_min": r"备菜|准备(?:时间|耗时)|prep",
                    "cook_time_min": r"烹饪(?:时间|耗时)|cook", "total_time_min": r"总(?:时间|耗时|用时)|制作(?:时间|耗时)|全程|total"}
        if field == "total_time_min" and not re.search(required[field], context, re.I) and re.search(
            r"步骤|第.{0,5}步|这一步|此步骤|(?:煮|炒|蒸|焖|烤|煎|炖)\s*[\d一二两三四五六七八九十半]", context
        ):
            return "单一步骤或火候计时仍不能当作整道菜总耗时；保留步骤原计时，或使用明确标记的建议总耗时。"
        if field in required and not re.search(required[field], context, re.I) and not (
            grouped and field in {"difficulty", "total_time_min"}
        ):
            return "确认依据须能区分菜谱元数据与单一步骤计时；引用实际问题或同组选项答案，不要求用户使用固定句式。"
    return None


def require_storage_provenance(draft, baseline=None, context=None):
    """Raw HTTP/internal edits cannot invent host-confirmed conversation provenance."""
    provenance = draft.metadata_provenance.model_dump(exclude_none=True)
    if not provenance:
        return
    previous = (baseline or {}).get("metadata_provenance") or {}
    original = (baseline or {}).get("document") or {}
    changed = any(previous.get(field) != basis or original.get(field) != getattr(draft, field)
                  for field, basis in provenance.items())
    if not changed:
        return
    if context is None:
        raise ValueError("新增或更改对话建议/确认来源须通过当前对话核对；手动编辑不能伪造问题答案来源")
    messages, host = context
    error = validate_metadata_provenance(draft, messages, host, baseline)
    if error:
        raise ValueError(error)
