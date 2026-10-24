"""Validates model tool requests before executing an atomic kitchen command."""

import hashlib
from uuid import uuid4

from pydantic import ValidationError

from .models import (
    TOOL_REQUEST_MODELS,
    CookingAdaptation,
    CookingData,
    CookingGuide,
    KitchenDisplayAction,
    MealPlan,
    PendingQuestion,
    PreferenceField,
    PreferenceForm,
    PreferenceFormData,
    PreferenceValues,
    PrepChecklist,
    PrepData,
    RecipeChoices,
    RecipeDetail,
    RecipeDetailData,
    RecipeListData,
    Timers,
    TimersData,
)
from .support import FIELD_META, PREP_NOTE, DomainConflict, _dump, _explicit_durations, _validation_details


class ToolCommands:
    """Internal part of WorkflowService; persistence and revision checks stay in service.py."""

    def execute_tool(self, name, request=None, *, approved_recipes=(), operation_id=None, user_text=None):
        model = TOOL_REQUEST_MODELS.get(name)
        if model is None:
            raise ValueError("Unknown workflow tool")
        try:
            validated = model.model_validate(request or {})
        except ValidationError as error:
            result = self._response(self._state(self._get_state()), "invalid_arguments", "工具参数不符合字段要求，没有保存。")
            result["errors"] = _validation_details(error)
            result["recovery"] = ["只修正列出的字段，按当前工具schema重新调用；未修改的偏好不要列入changed_fields。"]
            return result
        args = validated.model_dump(mode="json", exclude_none=False)
        operation_id = operation_id or str(uuid4())
        if name == "get_cooking_workspace":
            return self.get_workspace()
        initial = self._state(self._get_state())
        base_revision = args.get("expected_revision", initial.revision)
        if name == "update_preferences":
            args["values"] = validated.values.model_dump(mode="json", include=set(validated.changed_fields))
        if operation_id:
            prior = next((record for record in initial.recent_actions if record.action_id == operation_id), None)
            fingerprint = hashlib.sha256(_dump({"type": name, "payload": args}).encode()).hexdigest()
            if prior:
                if prior.fingerprint != fingerprint:
                    return self._response(initial, "action_id_conflict", "同一操作标识已用于不同内容，请重新确认。")
                return self._response(initial, "ok", "此操作已经执行，没有重复读取源菜谱或修改状态。")
        if base_revision != initial.revision:
            return self._response(initial, "revision_conflict", "工作区已变化，请复核当前界面后重新操作。")
        frozen = None
        try:
            blockers = self._tool_blockers(initial, name)
            if blockers:
                raise DomainConflict(blockers[0]["code"], blockers[0]["message"])
            if name == "set_cooking_timer" and validated.basis == "user" and validated.seconds not in _explicit_durations(user_text):
                raise DomainConflict("unverified_duration", "用户没有明确提供这个时长，请让用户填写计时器或使用源步骤时长。")
            if name == "show_recipe_choices":
                frozen = self._freeze(validated.recipe_ids, validated.data_snapshot, approved_recipes)
            elif name in {"select_recipes", "set_meal_plan"}:
                frozen = self._resolve_selection(initial, validated.recipe_ids, validated.data_snapshot, approved_recipes)
            elif name == "show_recipe_details":
                try:
                    existing = self._find_recipe(initial, validated.recipe_id,
                        allow_candidate=initial.stage not in {"prep", "cooking", "completed"})
                except DomainConflict:
                    existing = None
                if existing is not None and (validated.data_snapshot is None or existing.data_snapshot == validated.data_snapshot):
                    frozen = [existing]
                else:
                    if initial.stage in {"prep", "cooking", "completed"}:
                        raise DomainConflict("invalid_reference", "执行中的任务只展示当前冻结菜谱，请先回到选菜。")
                    if validated.data_snapshot is None:
                        raise DomainConflict("snapshot_required", "新菜谱详情需要检索来源快照。")
                    candidate = next((recipe for recipe in initial.candidates
                        if recipe.recipe_id == validated.recipe_id and recipe.data_snapshot == validated.data_snapshot), None)
                    frozen = [candidate] if candidate else self._freeze([validated.recipe_id], validated.data_snapshot, approved_recipes)
        except DomainConflict as error:
            return self._response(initial, error.status, str(error))
        def command(state):
            blockers = self._tool_blockers(state, name)
            if blockers:
                raise DomainConflict(blockers[0]["code"], blockers[0]["message"])
            if name == "set_kitchen_display":
                selected = validated.view_kind
                if validated.mode == "focus":
                    views = [view for view in self._views(state) if view.kind != "task_progress"
                             and not (view.kind == "recipe_choices" and state.stage in {"prep", "cooking", "completed"})
                             and not (view.kind in {"preference_form", "prep_checklist"}
                                      and state.stage in {"cooking", "completed"})]
                    preferred = {"idea": "preference_form", "planning": "meal_plan",
                                 "prep": "prep_checklist", "cooking": "cooking_guide",
                                 "completed": "meal_plan"}.get(state.stage)
                    if selected is None:
                        current = next((view for view in views if view.kind == preferred), views[-1] if views else None)
                        selected = current.kind if current else None
                    if selected is None or not any(view.kind == selected for view in views):
                        raise DomainConflict("view_unavailable", "当前还没有这张厨房卡片，请先打开需要查看的内容。")
                return KitchenDisplayAction(action_id=operation_id, mode=validated.mode, view_kind=selected)
            if name == "ask_user":
                state.pending_question = PendingQuestion(id=str(uuid4()), title=validated.title,
                    question=validated.question, options=validated.options,
                    allow_custom=validated.allow_custom, multiple=validated.multiple)
                return None
            if name == "update_preferences":
                state.preferences = PreferenceValues.model_validate({**state.preferences.model_dump(), **args["values"]})
                fields = [PreferenceField(name=field, label=FIELD_META[field][0], input_type=FIELD_META[field][1],
                                          value=getattr(state.preferences, field)) for field in args["values"]]
                return self._show(state, PreferenceForm, "核对已更新的用餐条件",
                                  PreferenceFormData(fields=fields, values=state.preferences))
            if name == "select_recipes":
                return self._plan(state, frozen, validated.servings,
                                  diners=validated.diners, portion_mode=validated.portion_mode)
            if name == "update_prep_items":
                if state.stage not in {"planning", "prep"}:
                    raise DomainConflict("stage_conflict", "只有选菜和备菜阶段可以调整准备状态。")
                valid = {item.id for item in self._prep_items(state)}
                if any(item.item_id not in valid for item in validated.items):
                    raise DomainConflict("invalid_reference", "部分食材不属于当前餐单，没有修改任何准备状态。")
                for item in validated.items:
                    state.prep_status[item.item_id] = item.status
                return self._ensure_component(state, PrepChecklist, "逐项确认备料", PrepData(
                    items=self._prep_items(state), notes=[PREP_NOTE]))
            if name == "show_preference_form":
                fields = [PreferenceField(name=field, label=FIELD_META[field][0], input_type=FIELD_META[field][1],
                                          value=getattr(state.preferences, field)) for field in validated.fields]
                return self._show(state, PreferenceForm, validated.title, PreferenceFormData(fields=fields,
                                                                                            values=state.preferences))
            if name == "show_recipe_choices":
                state.candidates = frozen
                return self._show(state, RecipeChoices, validated.title, RecipeListData(
                    recipes=[self._recipe_view(recipe, state, summary=True) for recipe in frozen]))
            if name == "set_meal_plan":
                return self._plan(state, frozen, validated.servings,
                                  diners=validated.diners, portion_mode=validated.portion_mode)
            if name == "show_recipe_details":
                if not any(recipe.recipe_id == frozen[0].recipe_id and recipe.data_snapshot == frozen[0].data_snapshot
                           for recipe in [*state.meal_plan, *state.candidates]):
                    state.candidates = [recipe for recipe in state.candidates if recipe.recipe_id != frozen[0].recipe_id]
                    state.candidates.append(frozen[0])
                return self._show(state, RecipeDetail, "菜谱详情", RecipeDetailData(recipe=self._recipe_view(frozen[0], state)))
            if name == "show_meal_plan":
                return self._show(state, MealPlan, "这次的餐单", self._meal_data(state))
            if name == "show_prep_checklist":
                return self._show(state, PrepChecklist, "逐项确认备料", PrepData(
                    items=self._prep_items(state, validated.recipe_ids), recipe_ids=validated.recipe_ids, notes=[PREP_NOTE]))
            if name == "show_cooking_guide":
                if validated.recipe_id:
                    self._find_recipe(state, validated.recipe_id)
                    if state.stage == "cooking":
                        steps = self._recipe_view(self._find_recipe(state, validated.recipe_id), state).steps
                        target = next((step for step in steps if step.status != "done"), steps[0] if steps else None)
                        if target:
                            self._activate_step(state, validated.recipe_id, target.id)
                    else:
                        state.active_recipe_id = validated.recipe_id
                        steps = self._recipe_view(self._find_recipe(state, validated.recipe_id), state).steps
                        state.active_step_id = steps[0].id if steps else None
                return self._show(state, CookingGuide, "烹饪步骤", CookingData(recipe_id=state.active_recipe_id,
                    recipe_name=None, steps=[], active_step_id=state.active_step_id))
            if name == "update_cooking_guidance":
                if state.stage not in {"planning", "prep", "cooking"}:
                    raise DomainConflict("stage_conflict", "请先确认本次餐单。")
                recipe = self._find_recipe(state, validated.recipe_id)
                if validated.step_id is not None and validated.step_id not in {step.id for step in self._recipe_view(recipe, state).steps}:
                    raise DomainConflict("invalid_reference", "步骤不属于当前任务菜谱。")
                note = CookingAdaptation(recipe_id=validated.recipe_id, step_id=validated.step_id,
                                         instruction=validated.instruction, reason=validated.reason)
                state.adaptations = [item for item in state.adaptations if (item.recipe_id, item.step_id) != (note.recipe_id, note.step_id)]
                state.adaptations.append(note)
                state.adaptations = state.adaptations[-20:]
                if state.stage == "cooking" or note.step_id is not None:
                    state.active_recipe_id = recipe.recipe_id
                    steps = self._recipe_view(recipe, state).steps
                    if note.step_id is not None:
                        state.active_step_id = note.step_id
                    elif state.active_step_id not in {step.id for step in steps}:
                        state.active_step_id = next((step.id for step in steps if step.status != "done"), steps[0].id if steps else None)
                    return self._ensure_component(state, CookingGuide, "烹饪步骤", CookingData(
                        recipe_id=recipe.recipe_id, recipe_name=None, steps=[], active_step_id=state.active_step_id))
                return self._ensure_component(state, PrepChecklist, "逐项确认备料", PrepData(
                    items=self._prep_items(state, [recipe.recipe_id]), recipe_ids=[recipe.recipe_id], notes=[PREP_NOTE]))
            if name == "set_cooking_timer":
                return self._start_timer(state, args, validated.basis)
            if name == "show_timers":
                return self._show(state, Timers, "厨房计时", TimersData(timers=state.timers))
            return self._set_stage(state, validated.stage)
        try:
            result = self._command(operation_id, name, args, base_revision, command)
        except ValidationError as error:
            result = self._response(self._state(self._get_state()), "invalid_arguments", "修改后的字段值无效，没有保存。")
            result["errors"] = _validation_details(error)
            result["recovery"] = ["修正列出的字段后重新调用，不要原样重试无效参数。"]
            return result
        if name == "set_kitchen_display":
            return result
        if validated.display == "none":
            result["actions"] = []
        else:
            for action in result["actions"]:
                action["placement"] = validated.display
        return result
