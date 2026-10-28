"""Personal recipe tools: grounded drafts and explicit deletion confirmations."""

import re
import json
from pydantic import ValidationError
from .recipe_drafts import (ConfirmRecipeDeletionRequest, CustomSearchRequest, RecipeDeletionRequest,
                            RecipeDraftRequest, RecipeDraftReadRequest, RecipeDraftRevisionRequest, apply_metadata_patch)
from .recipe_provenance import validate_metadata_provenance


def _read_draft(messages, recipe_id, revision):
    for message in reversed(messages):
        if message.get("role") != "tool" or message.get("name") not in {
                "create_recipe_draft", "get_recipe_draft", "revise_recipe_draft"}:
            continue
        try:
            result = json.loads(message.get("content", ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(result, dict) or result.get("status") != "ok":
            continue
        record = result.get("draft")
        if isinstance(record, dict) and record.get("recipe_id") == recipe_id and record.get("revision") == revision:
            return record
    return None


def _turn_guard(host):
    def guard():
        if host.cancelled.is_set():
            raise ValueError("本轮已经结束，没有修改菜谱草稿")
        state = host.binding.repository.get(host.session_id)
        if not state or not state.get("running") or (state.get("active_turn") or {}).get("id") != host.turn_id:
            raise ValueError("本轮已经结束，没有修改菜谱草稿")
    return guard


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
            request = RecipeDraftRequest.model_validate(values)
            provenance_error = validate_metadata_provenance(request, context.messages, host)
            if provenance_error:
                return {"status": "invalid_recipe_provenance", "message": provenance_error}
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
                provenance_context=(context.messages, host),
            )
            return {
                "status": "ok",
                "draft": record,
                "message": "草稿已整理，聊天审核卡会展示当前版本。核对食材、份量、时间及未知项，可继续对话修改；用户确认后才发布。",
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
                    **cookbook.summary(item),
                    "snapshot": cookbook.snapshot(item),
                }
                for item in records["items"]
            ],
            "message": "使用get_recipe读取单道菜谱后，再用其真实snapshot安排餐单。",
        }

    def read_draft(values, context):
        request = RecipeDraftReadRequest.model_validate(values)
        record = cookbook.get(request.recipe_id)
        if not record:
            return {"status": "not_found", "message": "个人菜谱不存在或已删除。"}
        return {"status": "ok", "draft": record,
                "message": "聊天审核卡展示此真实版本；draft可继续修改。保留metadata_provenance；source_metadata_evidence保留原文依据，建议/用户确认与原文分开。saved不能用草稿工具覆盖。"}

    def revise_draft(values, context):
        from .interactions import ScopedWorkspace

        host = context.host_context
        if not isinstance(host, ScopedWorkspace):
            return {"status": "session_required", "message": "请在当前对话中修改草稿。"}
        request = RecipeDraftRevisionRequest.model_validate(values)
        baseline = _read_draft(context.messages, request.recipe_id, request.revision)
        if baseline is None:
            return {"status": "read_required", "message": "请先get_recipe_draft读取这份真实版本，再修改同一个id。"}
        metadata_fields = None
        recipe = request.recipe
        if request.metadata_patch is not None:
            try:
                recipe, metadata_fields = apply_metadata_patch(baseline, request.metadata_patch)
            except ValidationError as error:
                return {"status": "invalid_arguments", "message": "元数据patch未通过来源校验，请修正所列字段；没有修改草稿。",
                        "errors": error.errors(include_input=False, include_context=False, include_url=False)}
            except ValueError as error:
                return {"status": "invalid_recipe_metadata_patch", "message": str(error)}
        provenance_error = validate_metadata_provenance(recipe, context.messages, host, baseline)
        if provenance_error:
            return {"status": "invalid_recipe_provenance", "message": provenance_error}
        original = baseline["source_text"]
        supplied = recipe.source_text
        latest = next((item.get("content", "") for item in reversed(context.messages) if item.get("role") == "user"), "")
        # Revisions can retain provenance or append the user's actual supplement.
        if supplied != original:
            supplement = supplied[len(original):].strip() if supplied.startswith(original) else ""
            if not supplement or supplement not in latest:
                return {"status": "invalid_recipe_source", "message": "保留原始来源；新增依据必须来自本轮用户实际补充的文字。"}
        try:
            record = cookbook.update_draft(request.recipe_id, recipe, request.revision,
                                           guard=_turn_guard(host), session_id=host.session_id,
                                           active_turn_id=host.turn_id if hasattr(host.binding.repository, "path") else None,
                                           provenance_context=(context.messages, host), metadata_fields=metadata_fields)
            return {"status": "ok", "draft": record,
                    "message": "已修改同一份草稿，审核卡展示新版本；已有明确参数答案不需重复询问。旧版发布确认失效，正式发布时再核对当前版本。"}
        except ValueError as error:
            return {"status": "recipe_changed", "message": str(error)}

    def request_publication(values, context):
        from .interactions import ScopedWorkspace

        host = context.host_context
        if not isinstance(host, ScopedWorkspace):
            return {"status": "workspace_unavailable", "message": "当前对话无法保存用户确认，没有发布菜谱。"}
        request = RecipeDeletionRequest.model_validate(values)
        if _read_draft(context.messages, request.recipe_id, request.revision) is None:
            return {"status": "review_required", "message": "先get_recipe_draft展示这份草稿当前版本，供用户审核后再请求发布确认。"}
        record = cookbook.get(request.recipe_id)
        if not record or record["status"] != "draft" or record["revision"] != request.revision:
            return {"status": "recipe_changed", "message": "草稿已更新或已保存，请重新读取后核对。"}
        name = record["document"]["name"].replace("<", "〈").replace(">", "〉")
        result = host.execute("ask_user", {"title": "确认保存个人菜谱？",
            "question": f"已核对「{name}」的食材、用量、步骤和未知信息，确认保存第 {request.revision} 版吗？",
            "options": [{"id": "publish", "label": "确认保存菜谱"}, {"id": "keep", "label": "继续修改草稿"}],
            "allow_custom": False, "multiple": False}, context)
        if result.get("status") != "ok" or not isinstance(result.get("pending_question"), dict):
            return result
        try:
            identity = cookbook.publication.prepare(request.recipe_id, request.revision, session_id=host.session_id,
                question_id=result["pending_question"]["id"], active_turn_id=host.turn_id, cancelled=host.cancelled)
        except ValueError as error:
            revision = cookbook.publication.discard_question(host.binding.repository, host.session_id,
                                                             result["pending_question"]["id"])
            if revision is not None:
                host.expected_revision = revision
            return {"status": "confirmation_expired", "message": str(error)}
        return {**result, "receipt": {"confirmation_id": identity, "recipe_id": request.recipe_id,
                "revision": request.revision, "next_tool": "confirm_recipe_publication",
                "on_answer": "等待用户点击选项，下一轮调用next_tool结清确认；此时尚未发布。"}}

    def confirm_publication(values, context):
        from .interactions import ScopedWorkspace

        host = context.host_context
        if not isinstance(host, ScopedWorkspace):
            return {"status": "workspace_unavailable", "message": "当前对话无法核对用户确认，没有发布菜谱。"}
        request = ConfirmRecipeDeletionRequest.model_validate(values)
        return cookbook.publication.confirm(request.confirmation_id, session_id=host.session_id,
                                            active_turn_id=host.turn_id, cancelled=host.cancelled)

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
            description="将用户文字/本轮图片整理为草稿。原文缺失值默认未知；用户要求建议时元数据可用metadata_provenance标记suggested并引用其请求，已选定值用user_confirmed及真实question_id，不为字段反复确认。不生成营养数字；发布仍需审核。",
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
            name="get_recipe_draft", args_schema=RecipeDraftReadRequest, handler=read_draft,
            description="读取一份个人菜谱或草稿的真实完整内容和revision，并在聊天里展示审核卡；修改和发布前先读同一份草稿。",
            required_skill="recipe-authoring",
        ),
        InjectedToolSpec(
            name="revise_recipe_draft", args_schema=RecipeDraftRevisionRequest, handler=revise_draft,
            description="修改已读回的同一草稿。只改难度/份数/总耗时优先metadata_patch；未改字段传null保留，仅需改值填写对应metadata_provenance，clear_fields通常为[]，仅用户明确要求清空才列字段名；不会为凑总时长改动分项。改食材/步骤才传完整recipe，二者严格二选一。真实id/revision与来源校验照旧，发布仍需确认。",
            required_skill="recipe-authoring", effect="write", side_effects=("修改待审核菜谱草稿",),
        ),
        InjectedToolSpec(
            name="request_recipe_publication", args_schema=RecipeDeletionRequest, handler=request_publication,
            description="对已在聊天展示审核的真实草稿版本发起用户保存确认，生成绑定id、revision的问题并停止本轮；尚未发布。",
            required_skill="recipe-authoring", effect="wait", side_effects=("保存发布确认问题并等待用户回答",),
        ),
        InjectedToolSpec(
            name="confirm_recipe_publication", args_schema=ConfirmRecipeDeletionRequest, handler=confirm_publication,
            description="用户回答保存问题后，用request_recipe_publication的confirmation_id结清确认。只认可宿主保存的同一问题选项，不能用approved或文字代替。",
            required_skill="recipe-authoring", effect="write", side_effects=("用户确认后发布对应版本菜谱",),
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
