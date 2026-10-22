"""Agent tools for creating, revising, and confirming personal intake."""

import re
from .food_log_models import (
    CHAT_COMPONENT_PRESENTATION,
    LOCAL_ZONE,
    ConfirmFoodLogRequest,
    FoodLogDraftRequest,
    FoodLogIdentityRequest,
    FoodLogSearchRequest,
    ReviseFoodLogDraftRequest,
    _encoded,
)
from .food_log_evidence import (
    _date_matches_source,
    _draft_receipt,
    _explicit_confirmation,
    _latest_user_index,
    _previous_draft_receipt,
    _read_baseline,
    _turn_guard,
    _validate_item_sources,
)


def make_meal_logging_tools(journal):
    from chefmate_agent import InjectedToolSpec
    from .cooked_intake import make_cooked_intake_tools

    def create(values, context):
        host = context.host_context
        if host is None or not getattr(host, "session_id", None) or not getattr(host, "turn_id", None):
            return {"status": "session_required", "message": "需要在当前对话中记录这顿饭。"}
        latest = next((message for message in reversed(context.messages) if message.get("role") == "user"), {})
        user_text = latest.get("content", "")
        images = [image["id"] for image in latest.get("images", []) if isinstance(image, dict) and image.get("id")]
        binding = getattr(host, "binding", None)

        try:
            request = FoodLogDraftRequest.model_validate(values)
            if request.source_kind == "text":
                normalized = re.sub(r"\s+", "", request.source_text)
                if not normalized or normalized not in re.sub(r"\s+", "", user_text):
                    raise ValueError("文字来源需引用用户本轮实际提供的内容")
            if request.date is not None and not _date_matches_source(
                request.date, request.date_evidence, user_text, journal.food_logs._now().astimezone(LOCAL_ZONE).date()
            ):
                raise ValueError(
                    "日期依据date_evidence必须是本轮原文中的连续片段，如今天；不要加解释或外层引号。不确定时date留null按今天记录"
                )
            _validate_item_sources(request.items, user_text)
            record = journal.food_logs.create_draft(
                request.model_dump(mode="json"),
                session_id=host.session_id,
                image_ids=images,
                operation_id=f"{host.session_id}:{host.turn_id}:{context.call_id}",
                guard=_turn_guard(host),
                active_turn_id=host.turn_id if binding is not None and hasattr(binding.repository, "path") else None,
            )
            return {
                "status": "ok",
                "draft": _draft_receipt(record),
                "message": "草稿已整理，当前聊天组件会展示食物、份量与营养。等待用户核对或继续修改；明确确认保存后才计入个人记录。",
            }
        except ValueError as error:
            return {"status": "invalid_food_log", "message": str(error)}

    def read(values, context):
        request = FoodLogIdentityRequest.model_validate(values)
        record = journal.food_logs.get(request.id)
        if not record:
            return {"status": "not_found", "message": "饮食记录不存在或已经移入回收站。"}
        if len(_encoded(record)) > 40000:
            return {
                "status": "record_too_large",
                "id": record["id"],
                "revision": record["revision"],
                "message": "这份记录较长，完整内容请在食光手记核对，未将截断内容作为修改依据。",
            }
        return {
            "status": "ok",
            "record": record,
            "message": "当前聊天组件会展示已读取的食物、份量与营养。draft可按此真实版本修改或等用户确认保存，saved已确认不可用草稿工具改写。",
        }

    def revise(values, context):
        host = context.host_context
        if host is None or not getattr(host, "session_id", None) or not getattr(host, "turn_id", None):
            return {"status": "session_required", "message": "需要在当前对话中修改这份饮食草稿。"}
        request = ReviseFoodLogDraftRequest.model_validate(values)
        baseline = _read_baseline(context.messages, request.id, request.revision)
        if baseline is None:
            return {
                "status": "read_required",
                "message": "请先调用get_food_log读取这份草稿的真实内容和revision，再修改同一个id；不要新建重复草稿。",
            }
        latest = next((message for message in reversed(context.messages) if message.get("role") == "user"), {})
        user_text = latest.get("content", "")
        original = baseline["document"]
        # Top-level provenance omitted by the model retains the actual read baseline.
        document = {**original, **request.record.model_dump(mode="json", exclude_unset=True)}
        images = list(baseline["source_image_ids"])
        current_images = [
            image["id"] for image in latest.get("images", []) if isinstance(image, dict) and image.get("id")
        ]
        try:
            if document["source_kind"] != original["source_kind"]:
                if not re.search(
                    r"(?:来源|记录).{0,8}(?:改|换|更正).{0,8}(?:照片|图片|文字)|(?:改为|改成)(?:照片|图片|文字)",
                    user_text,
                ):
                    raise ValueError("没有本轮明确更改来源的要求，请保留草稿原始来源类型")
                if document["source_kind"] == "image":
                    if baseline["source_session_id"] != host.session_id:
                        raise ValueError("跨对话修改请保留原始来源；补充照片请回到原对话上传，避免图片来源失效")
                    if not current_images:
                        raise ValueError("改为照片来源需要本轮真实图片")
                    images = list(dict.fromkeys([*images, *current_images]))
                elif not document["source_text"] or document["source_text"] not in user_text:
                    raise ValueError("改为文字来源需要本轮实际提供的原文")
            if document["source_text"] != original["source_text"]:
                if not document["source_text"] or document["source_text"] not in user_text:
                    raise ValueError("来源原文只能保持原基线或改为本轮用户明确提供的文字")
            if document["date"] is None:
                document["date"] = original["date"]
            if document["date"] != original["date"] or document["date_evidence"] != original["date_evidence"]:
                if not _date_matches_source(
                    document["date"],
                    document["date_evidence"],
                    user_text,
                    journal.food_logs._now().astimezone(LOCAL_ZONE).date(),
                ):
                    raise ValueError("记录日期只能保持原基线或根据本轮明确日期更改")
            parsed = FoodLogDraftRequest.model_validate(document)
            _validate_item_sources(parsed.items, user_text, original["items"])
            binding = getattr(host, "binding", None)
            record = journal.food_logs.update_draft(
                request.id,
                request.revision,
                parsed.model_dump(mode="json"),
                guard=_turn_guard(host),
                session_id=host.session_id,
                active_turn_id=host.turn_id if binding is not None and hasattr(binding.repository, "path") else None,
                source_image_ids=images,
                allow_source_change=True,
            )
            return {
                "status": "ok",
                "draft": _draft_receipt(record),
                "message": "已修改原来的同一份饮食草稿，没有重复新建或入账。当前聊天组件会展示更新后的份量与营养，等待用户下一条明确确认。",
            }
        except KeyError:
            return {"status": "not_found", "message": "饮食草稿不存在或已经移除。"}
        except ValueError as error:
            return {"status": "invalid_food_log_revision", "message": str(error)}

    def confirm(values, context):
        host = context.host_context
        if host is None or not getattr(host, "session_id", None) or not getattr(host, "turn_id", None):
            return {"status": "session_required", "message": "需要在当前对话中确认饮食记录。"}
        request = ConfirmFoodLogRequest.model_validate(values)
        latest = _latest_user_index(context.messages)
        user_text = context.messages[latest].get("content", "") if latest >= 0 else ""
        if not _explicit_confirmation(request.confirmation_evidence, user_text, request.id):
            return {
                "status": "confirmation_required",
                "message": "请等待用户在本轮明确说确认保存这份饮食记录；问题、否定、引用和助手文字不能作为确认。",
            }
        if _previous_draft_receipt(context.messages, request.id, request.revision) is None:
            return {
                "status": "review_required",
                "message": "请先在对话展示这份草稿当前版本的食物、份量和营养，再等用户下一条消息确认。不能刚创建或修改就自动入账。",
            }
        if _read_baseline(context.messages[latest + 1 :], request.id, request.revision) is None:
            return {
                "status": "read_required",
                "message": "请先在本轮get_food_log读取同一份草稿与revision，核对最新版本后再确认。",
            }
        try:
            binding = getattr(host, "binding", None)
            record = journal.food_logs.publish(
                request.id,
                request.revision,
                guard=_turn_guard(host),
                session_id=host.session_id,
                active_turn_id=host.turn_id if binding is not None and hasattr(binding.repository, "path") else None,
            )
            summary = {key: value for key, value in journal.food_logs.summary(7).items() if key != "days"}
            return {
                "status": "ok",
                "record": _draft_receipt(record),
                "summary": summary,
                "message": "这份饮食已按用户确认保存，现已计入个人营养统计，当前聊天组件会展示结果。估算和未知值保留，不代表当天完整摄入。",
            }
        except KeyError:
            return {"status": "not_found", "message": "饮食草稿不存在或已经移除。"}
        except ValueError as error:
            return {"status": "invalid_food_log_confirmation", "message": str(error)}

    def search(values, context):
        request = FoodLogSearchRequest.model_validate(values)
        records = journal.food_logs.list(**request.model_dump())
        items = [
            {
                "id": record["id"],
                "revision": record["revision"],
                "status": record["status"],
                "date": record["date"],
                "title": record["document"]["title"],
                "meal_type": record["document"]["meal_type"],
                "source_kind": record["document"]["source_kind"],
                "nutrition": record["nutrition"],
                "food_count": len(record["document"]["items"]),
                "notes": record["document"]["notes"][:300],
                "foods": [
                    {
                        "name": item["name"][:80],
                        "portion": item["portion"][:100],
                        "portion_basis": item["portion_basis"],
                        "nutrition_basis": item["nutrition_basis"],
                    }
                    for item in record["document"]["items"][:4]
                ],
            }
            for record in records["items"]
        ]
        summary = {key: value for key, value in journal.food_logs.summary(request.days).items() if key != "days"}
        return {
            "status": "ok",
            **records,
            "items": items,
            "summary": summary,
            "message": "当前聊天组件会展示营养汇总与饮食列表。只有saved计入个人摄入，draft仍待用户核对；每条最多摘要4种食物，详情可用get_food_log读取。"
            "汇总覆盖所选日期范围，制作量和个人摄入分开保存。",
        }

    return [
        *make_cooked_intake_tools(journal),
        InjectedToolSpec(
            "create_food_log_draft",
            CHAT_COMPONENT_PRESENTATION + "把用户本轮的饭菜照片或文字整理为个人饮食草稿，用户明确确认后才计入统计。"
            "每项营养是用户自己吃的这一份的总量；份量未知时克重和营养留null。视觉/描述估算必须标estimated并写assumptions，不能说精确识别。"
            "source_kind=image仅可用本轮真实图片。date不确定留null按今天；历史日期需引用本轮date_evidence。不能把多人制作量当个人摄入。",
            FoodLogDraftRequest,
            create,
            effect="write",
            required_skill="food-journal",
            side_effects=("保存待用户核对的个人饮食草稿",),
        ),
        InjectedToolSpec(
            "search_food_logs",
            CHAT_COMPONENT_PRESENTATION
            + "按关键词和日期范围读取已确认的个人饮食记录、草稿及营养统计。默认saved，只读；"
            "宏量总数仅代表已确认记录，未知保留null；制作量和个人摄入分别记录。",
            FoodLogSearchRequest,
            search,
            required_skill="food-journal",
        ),
        InjectedToolSpec(
            "get_food_log",
            CHAT_COMPONENT_PRESENTATION
            + "按search_food_logs返回的真实id读取完整饮食记录、document与revision，修改草稿前必须调用。"
            "过长记录不会截断冒充完整内容。只有draft能修订，saved只读。",
            FoodLogIdentityRequest,
            read,
            required_skill="food-journal",
        ),
        InjectedToolSpec(
            "revise_food_log_draft",
            CHAT_COMPONENT_PRESENTATION
            + "用户说去掉酸奶、米饭减半等时，先get_food_log读取，再传同一个id、revision及完整record修改原草稿，"
            "不要create重复草稿。来源和日期沿用读取的基线，除非用户本轮明确更改。未改的旧用户数值可保留，新增/改动的用户数值须引用本轮原话；"
            "按原份量缩放的营养标estimated并写换算假设。工具不会发布或计入摄入；在对话展示结果，等用户下一条明确确认。",
            ReviseFoodLogDraftRequest,
            revise,
            effect="write",
            required_skill="food-journal",
            side_effects=("修改同一份待核对饮食草稿，未确认入账",),
        ),
        InjectedToolSpec(
            "confirm_food_log",
            CHAT_COMPONENT_PRESENTATION
            + "用户在已展示饮食草稿后明确说确认保存/记录/入账时，先在本轮get_food_log读取最新版本，"
            "再以真实id、revision及本轮逐字确认原话调用。新创建/修改的草稿需下一条用户消息确认；否定、问题、引用、旧确认不成立。"
            "成功后直接在对话展示已记录与最新个人营养，不要求切换食光手记。",
            ConfirmFoodLogRequest,
            confirm,
            effect="write",
            required_skill="food-journal",
            side_effects=("用户明确确认后将该饮食草稿计入个人营养统计",),
        ),
    ]
