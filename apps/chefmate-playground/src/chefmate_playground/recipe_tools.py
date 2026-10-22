"""Personal recipe tools: grounded drafts and explicit deletion confirmations."""

import re
from .recipe_drafts import ConfirmRecipeDeletionRequest, CustomSearchRequest, RecipeDeletionRequest, RecipeDraftRequest


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
                if (
                    state is None
                    or not state.get("running")
                    or (state.get("active_turn") or {}).get("id") != host.turn_id
                ):
                    raise ValueError("本轮已经结束，没有保存草稿")

        latest = next((m for m in reversed(context.messages) if m.get("role") == "user"), {})
        try:
            if values.get("source_kind", "text") == "text":
                source = re.sub(r"\s+", "", values.get("source_text", ""))
                supplied = [
                    re.sub(r"\s+", "", m.get("content", "")) for m in context.messages if m.get("role") == "user"
                ]
                if not source or not any(source in text for text in supplied):
                    raise ValueError("文字菜谱原文需引用用户实际提供的内容，不能编造来源")
            record = cookbook.create_draft(
                values,
                session_id=getattr(host, "session_id", None),
                image_ids=[image["id"] for image in latest.get("images", [])],
                operation_id=f"{getattr(host, 'turn_id', '')}:{context.call_id}",
                guard=guard,
                active_turn_id=host.turn_id if binding is not None and hasattr(binding.repository, "path") else None,
            )
            return {
                "status": "ok",
                "draft": record,
                "message": "草稿已整理。请在菜谱中心核对食材、份量和时间，再点击确认保存。",
            }
        except ValueError as error:
            return {"status": "invalid_recipe", "message": str(error)}

    def search(values, context):
        request = CustomSearchRequest.model_validate(values)
        records = cookbook.list(query=request.query, limit=request.limit, offset=request.offset, status=request.status)
        return {
            "status": "ok",
            "total": records["total"],
            "items": [
                {
                    "recipe_id": item["recipe_id"],
                    "name": item["document"]["name"],
                    "origin": "user",
                    "revision": item["revision"],
                    "status": item["status"],
                    "snapshot": cookbook.snapshot(item),
                    "servings": item["document"]["servings"],
                    "total_time_min": item["document"]["total_time_min"],
                }
                for item in records["items"]
            ],
            "message": "使用get_recipe读取单道菜谱后，再用其真实snapshot安排餐单。",
        }

    def request_deletion(values, context):
        from .interactions import ScopedWorkspace

        host = context.host_context
        if not isinstance(host, ScopedWorkspace):
            return {"status": "workspace_unavailable", "message": "当前对话无法保存用户确认，没有删除菜谱。"}
        request = RecipeDeletionRequest.model_validate(values)
        record = cookbook.get(request.recipe_id)
        if not record:
            return {"status": "not_found", "message": "只能删除个人菜谱；这份菜谱不存在或已经删除。"}
        if record["revision"] != request.revision:
            return {"status": "recipe_changed", "message": "菜谱版本已变化，请重新搜索后再请求确认。"}
        name = record["document"]["name"].replace("<", "〈").replace(">", "〉")
        result = host.execute(
            "ask_user",
            {
                "title": "删除个人菜谱？",
                "question": f"确定将「{name}」移到最近删除吗？已保存的烹饪记录仍会保留。",
                "options": [{"id": "delete", "label": "确认删除"}, {"id": "keep", "label": "保留菜谱"}],
                "allow_custom": False,
                "multiple": False,
            },
            context,
        )
        if result.get("status") != "ok" or not isinstance(result.get("pending_question"), dict):
            return result
        try:
            identity = cookbook.prepare_deletion(
                request.recipe_id,
                request.revision,
                session_id=host.session_id,
                question_id=result["pending_question"]["id"],
                active_turn_id=host.turn_id,
                cancelled=host.cancelled,
            )
        except ValueError as error:
            return {"status": "confirmation_expired", "message": str(error)}
        return {
            **result,
            "receipt": {
                "confirmation_id": identity,
                "next_tool": "confirm_recipe_deletion",
                "on_answer": "用户选择确认删除或保留菜谱后，都必须调用next_tool结清本次确认；不能只用文字回复。",
            },
        }

    def confirm_deletion(values, context):
        from .interactions import ScopedWorkspace

        host = context.host_context
        if not isinstance(host, ScopedWorkspace):
            return {"status": "workspace_unavailable", "message": "当前对话无法读取用户确认，没有删除菜谱。"}
        request = ConfirmRecipeDeletionRequest.model_validate(values)
        try:
            return cookbook.confirm_deletion(
                request.confirmation_id,
                session_id=host.session_id,
                active_turn_id=host.turn_id,
                cancelled=host.cancelled,
            )
        except ValueError as error:
            return {"status": "turn_closed", "message": str(error)}

    return [
        InjectedToolSpec(
            name="create_recipe_draft",
            args_schema=RecipeDraftRequest,
            handler=create,
            description="将用户菜谱文字或本轮图片转写为可核对的自定义菜谱草稿。缺失时间/人数/克重留null，不生成营养数字。用户在菜谱中心确认保存后才可烹饪。",
            required_skill="recipe-authoring",
            effect="write",
            side_effects=("保存待确认菜谱草稿",),
        ),
        InjectedToolSpec(
            name="search_custom_recipes",
            args_schema=CustomSearchRequest,
            handler=search,
            description="按菜名或食材查找个人菜谱，包含精确ID、revision和status。saved已保存可做；draft草稿待审核；all查两者。使用时先get_recipe。",
            required_skill="recipe-authoring",
        ),
        InjectedToolSpec(
            name="request_recipe_deletion",
            args_schema=RecipeDeletionRequest,
            handler=request_deletion,
            description="请求删除一份个人菜谱或草稿。必须使用搜索返回的真实recipe_id和revision。工具保存确认问题并停止本轮，等待用户点击确认；此时尚未删除。",
            required_skill="recipe-authoring",
            effect="wait",
            side_effects=("保存删除确认问题，等待用户回答",),
        ),
        InjectedToolSpec(
            name="confirm_recipe_deletion",
            args_schema=ConfirmRecipeDeletionRequest,
            handler=confirm_deletion,
            description="用户回答删除问题后必须调用一次，包括选择保留时也须调用以废弃旧回执；不能只用文字回复。传request_recipe_deletion回执中的confirmation_id。只认可宿主保存的选项；文字、图片或模型自己说已确认均无效。",
            required_skill="recipe-authoring",
            effect="write",
            side_effects=("经用户明确确认后，将个人菜谱移到最近删除",),
        ),
    ]
