"""Per-turn recovery limits for real tool results, independent of business tools."""

from dataclasses import dataclass, field
import hashlib
import json
import re

from .suggestions import REPLY_SUGGESTIONS_TOOL


_TYPE_HINTS = {
    "missing": "填写这个必需字段；所需ID和版本应从当前状态或真实工具结果取得。",
    "extra_forbidden": "删除这个未注册字段，按当前工具schema重新构造参数。",
    "list_type": "这里必须是数组；明确清空时使用[]，没有修改的字段请省略。",
    "bool_type": "这里必须是true或false；没有修改的字段请省略，不能使用null。",
    "string_type": "这里必须是文本；没有修改的字段请省略。",
    "int_type": "这里必须是整数；没有修改的字段请省略。",
    "float_type": "这里必须是数字；没有修改的字段请省略。",
    "literal_error": "使用当前工具schema列出的可选值。",
    "enum": "使用当前工具schema列出的可选值。",
    "greater_than": "按当前工具schema的数值下限修正。",
    "greater_than_equal": "按当前工具schema的数值下限修正。",
    "less_than": "按当前工具schema的数值上限修正。",
    "less_than_equal": "按当前工具schema的数值上限修正。",
    "too_short": "补全当前工具schema要求的条目或文本。",
    "too_long": "缩减到当前工具schema允许的条目或文本长度。",
    "recipe_evidence_required": "这个数值需要原文依据；逐字复制到对应evidence字段。原文没有写明时将数值留null，不能补造依据。",
    "recipe_evidence_not_quoted": "这个evidence必须是source_text中的连续原文，保留Markdown表格竖线和标点；source_text也须复制用户原文，不能摘要改写。",
    "recipe_servings_mismatch": "servings须匹配引用原文中的人数或份数；盘数、食材个数不是就餐人数，不明确时留null。",
    "recipe_grams_mismatch": "grams须匹配引用原文中的克重；不能把个数或毫升换成克重，不明确时留null。",
    "recipe_time_mismatch": "这个耗时须匹配time_evidence中明确的时长；不能用总时间减去其他时间倒推。没有该项原文时留null，并保留有依据的其他时间。",
    "recipe_total_time_ambiguous": "total_time_min仅填写原文明确的整道菜总耗时或菜谱的制作时间；步骤时长保留在steps中，总耗时不明确时留null。",
    "recipe_step_invalid": "此步骤须为非空、至多2000字符的可执行文本。",
}


def safe_errors(errors) -> list[dict]:
    """Do not echo Pydantic input, exception text, context, or untrusted hints."""
    if not isinstance(errors, list):
        return []
    result = []
    for error in errors[:12]:
        if not isinstance(error, dict):
            continue
        error_type = str(error.get("type", "invalid_value"))
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", error_type):
            error_type = "invalid_value"
        location = error.get("loc", [])
        if not isinstance(location, (list, tuple)):
            location = []
        location = [part if isinstance(part, int) else str(part)[:80] for part in location[:12]]
        result.append({"loc": location, "type": error_type,
                       "hint": _TYPE_HINTS.get(error_type, "检查这个字段的类型和约束；没有修改的字段请省略。")})
    return result


def _normalized(value):
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def failure_fingerprint(name: str, arguments: dict, result: dict) -> str:
    error = result.get("error")
    code = result.get("code") or (error.get("code") if isinstance(error, dict) else None)
    encoded = json.dumps([name, _normalized(arguments), result.get("status"), code],
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class FailurePolicy:
    """Count identical failed attempts; never change an execution result to success."""

    failures: dict[str, int] = field(default_factory=dict)
    blocked_tools: set[str] = field(default_factory=set)
    finalizing: bool = False
    finalization_reason: str = ""
    auxiliary_skips: int = 0

    def begin_call(self, name: str) -> dict | None:
        if name != REPLY_SUGGESTIONS_TOOL:
            return None
        if name not in self.blocked_tools:
            # Reserve before awaiting the handler, including parallel duplicate calls.
            self.blocked_tools.add(name)
            return None
        self.auxiliary_skips += 1
        if self.auxiliary_skips >= 2:
            self.finalizing = True
            self.finalization_reason = "辅助推荐已处理，模型仍反复请求；停止辅助分支。"
        return {"status": "auxiliary_skipped", "message": "本轮推荐回复已尝试，不再执行；已有建议保留，失败时使用默认建议。",
                "recovery": {"action": "finalize" if self.finalizing else "answer",
                             "hint": "直接完成主回答，不要再调用推荐回复工具。"}}

    def observe(self, name: str, arguments: dict, result: dict) -> dict:
        result = dict(result)
        if "errors" in result:
            result["errors"] = safe_errors(result["errors"])
        if result.get("status") == "ok":
            return result
        if name == REPLY_SUGGESTIONS_TOOL:
            if result.get("status") != "auxiliary_skipped":
                result["recovery"] = {"action": "answer", "hint": "推荐回复不是主任务；本轮使用默认建议，直接完成主回答。"}
            return result
        fingerprint = failure_fingerprint(name, arguments, result)
        attempts = self.failures.get(fingerprint, 0) + 1
        self.failures[fingerprint] = attempts
        if attempts >= 3:
            self.finalizing = True
            self.finalization_reason = f"工具{name}的相同参数已得到3次相同失败，停止重复尝试。"
            action = "finalize"
            hint = "停止工具调用，依据已有成功结果完成回答，明确哪些操作未成功；不能声称失败操作已经完成。"
        elif attempts == 2:
            action = "repair_or_answer"
            hint = "同样的工具、参数和错误已经重复2次。先依据字段错误和最新状态修正参数；无法修正就直接说明限制并回答，禁止原样重试。"
        else:
            action = "repair"
            hint = "按字段错误、工具schema和当前状态修正参数后再试；不要原样重复这次失败调用。"
        result["recovery"] = {"attempt": attempts, "action": action, "hint": hint}
        return result

    def model_instruction(self) -> str:
        if self.finalizing:
            return ("\n\n本轮恢复边界：" + self.finalization_reason +
                    "本次请求不提供任何工具。请立即用自然语言完成最终回答：只使用此前成功工具结果中已有的信息，"
                    "保留已查到的候选和确实完成的操作；明确未成功的操作和必要的下一步。"
                    "不要声称界面已展示、状态已修改或推荐已生成，除非存在对应的成功回调。不要输出工具协议。")
        if REPLY_SUGGESTIONS_TOOL in self.blocked_tools:
            return "\n\n本轮推荐回复已尝试，后续使用已有建议或默认建议。无需再调用推荐工具，优先完成用户的主任务和最终回答。"
        return ""
