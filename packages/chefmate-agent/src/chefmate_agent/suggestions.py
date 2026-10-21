"""Closed recommendation tool and conservative, fact-free completion fallback."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .extensions import InjectedToolSpec
from .models import ReplySuggestion

REPLY_SUGGESTIONS_TOOL = "suggest_replies"
PANTRY_REPLY = {
    "title": "已拥有所有小料，帮我勾选掉",
    "text": "当前清单里的调味小料我都已有，请帮我勾选为已备齐；主料保持原来的状态，不确定属于小料的项目先问我。",
}


class ReplySuggestionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    suggested_replies: list[ReplySuggestion] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def distinct_replies(self):
        if (len({item.title for item in self.suggested_replies}) != 3
                or len({item.text for item in self.suggested_replies}) != 3):
            raise ValueError("Reply suggestions must be distinct")
        return self


def make_reply_suggestions_tool() -> InjectedToolSpec:
    def save(request, _context):
        return {"status": "ok", "suggested_replies": request["suggested_replies"]}

    return InjectedToolSpec(
        name=REPLY_SUGGESTIONS_TOOL,
        description=("为本轮最终回答提供恰好3条可编辑的后续提问。title为20字以内的短标题，text为500字以内、"
                     "可替换到输入框的用户口吻草稿；点击不会发送。应结合本轮结果和真实任务状态，方向各异，"
                     "不编造菜谱事实、不声称操作已经完成、不替用户确认食材或步骤。只保存建议，不执行任何业务操作。"),
        args_schema=ReplySuggestionsRequest,
        handler=save,
    )


_FALLBACKS = {
    "planning": (
        ("核对餐单", "请帮我核对当前选菜和用餐需求。"),
        ("准备食材", "请根据当前餐单整理需要准备的食材，缺少信息时先问我。"),
        ("调整搭配", "我想调整这顿饭的搭配，请先看看当前餐单。"),
    ),
    "shopping": (
        ("核对食材", "请帮我核对当前清单里还需要准备什么。"),
        ("食材替换", "有些食材不方便买，我想了解可以怎么调整。"),
        ("接下来做什么", "请核对当前准备状态，告诉我接下来可以做什么。"),
    ),
    "prep": (
        ("安排备菜顺序", "请根据当前餐单和备菜状态，帮我安排准备顺序。"),
        ("看看待办", "请帮我核对当前还有哪些准备事项。"),
        ("调整做法", "我想补充一下手头的食材和厨具，请帮我看看做法能怎样调整。"),
    ),
    "cooking": (
        ("讲讲当前步骤", "请结合当前进度，解释一下这一步的操作要点。"),
        ("安排操作顺序", "请根据当前进度，帮我安排接下来的操作顺序。"),
        ("遇到问题了", "我在操作中遇到一个问题，想请你帮我一起判断。"),
    ),
    "completed": (
        ("回顾这次做饭", "帮我回顾这次做饭的过程，看看下次可以怎样安排。"),
        ("再安排一餐", "我想再安排一顿饭，先帮我确认这次的需求。"),
        ("换个用餐人数", "我想按不同的用餐人数安排下次做饭，请先问我具体人数。"),
    ),
    "idea": (
        ("按食材找菜", "我想用手头的食材做饭，先帮我梳理一下。"),
        ("安排一顿饭", "帮我搭配一顿饭，先问问我的人数、口味和时间要求。"),
        ("找简单的做法", "我想找做法简单、省时间的菜，请先了解一下我的需求。"),
    ),
}


def finalize_reply_suggestions(value, ui_context: dict | None = None) -> list[dict[str, str]]:
    """Reject an invalid batch in full; defaults request help without inventing facts."""
    try:
        batch = ReplySuggestionsRequest.model_validate({"suggested_replies": value})
        replies = [item.model_dump(mode="json") for item in batch.suggested_replies]
    except (ValidationError, TypeError):
        stage = (ui_context or {}).get("stage", "idea")
        replies = [{"title": title, "text": text} for title, text in _FALLBACKS.get(stage, _FALLBACKS["idea"])]
    if ui_context is None:
        return replies
    replies = [item for item in replies if item["title"] != PANTRY_REPLY["title"] and item["text"] != PANTRY_REPLY["text"]]
    pending_prep = ui_context.get("stage") == "prep" and any(
        view.get("kind") == "prep_checklist"
        and any(item.get("status") == "pending" for item in (view.get("data") or {}).get("items", []))
        for view in ui_context.get("components", [])
    )
    if pending_prep:
        replies.insert(0, dict(PANTRY_REPLY))
    for title, text in _FALLBACKS.get(ui_context.get("stage"), _FALLBACKS["idea"]):
        if len(replies) >= 3:
            break
        if all(item["title"] != title and item["text"] != text for item in replies):
            replies.append({"title": title, "text": text})
    return replies[:3]
