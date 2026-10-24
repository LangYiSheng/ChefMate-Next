"""Validates browser actions before executing an atomic kitchen command."""

from datetime import timedelta

from pydantic import ValidationError

from .models import (
    PreferenceValues,
    QuestionAnswer,
    RecipeDetail,
    RecipeDetailData,
    UserActionRequest,
)
from .support import DomainConflict, _stamp, _validation_details


class UserActions:
    """Internal part of WorkflowService; persistence and revision checks stay in service.py."""

    def apply_action(self, request):
        try:
            value = UserActionRequest.model_validate(request.model_dump(exclude_unset=True)
                                                    if isinstance(request, UserActionRequest) else request).root
        except ValidationError as error:
            result = self._response(self._state(self._get_state()), "invalid_action", "操作参数不符合字段要求，没有保存。")
            result["errors"] = _validation_details(error)
            result["recovery"] = ["修正列出的字段后重新提交当前界面。"]
            return result
        payload = value.payload.model_dump(mode="json", exclude_unset=True)
        def command(state):
            kind = value.type
            if kind == "update_preferences":
                updated = {**state.preferences.model_dump(), **payload["values"]}
                state.preferences = PreferenceValues.model_validate(updated)
            elif kind == "answer_question":
                pending = state.pending_question
                if pending is None:
                    raise DomainConflict("question_already_answered", "这条问题已经回答，请继续当前对话。")
                if payload["question_id"] != pending.id:
                    raise DomainConflict("stale_question", "问题已经变化，请回答当前显示的问题。")
                ids = payload.get("option_ids", [])
                text = payload.get("text", "").strip()
                available = {option.id: option.label for option in pending.options}
                if len(ids) != len(set(ids)) or any(identity not in available for identity in ids):
                    raise DomainConflict("invalid_answer", "答案包含无效或重复选项，没有保存。")
                if len(ids) > 1 and not pending.multiple:
                    raise DomainConflict("invalid_answer", "这条问题只需要选择一个选项。")
                if text and not pending.allow_custom or not ids and not text:
                    raise DomainConflict("invalid_answer", "请提供这条问题允许的答案。")
                state.last_answer = QuestionAnswer(question_id=pending.id, question=pending.question,
                    option_ids=ids, labels=[available[identity] for identity in ids], text=text)
                state.pending_question = None
            elif kind == "select_recipes":
                recipes = []
                for identity in dict.fromkeys(payload["recipe_ids"]):
                    candidate = next((recipe for recipe in state.candidates if recipe.recipe_id == identity), None)
                    recipes.append(candidate if candidate is not None else self._find_recipe(state, identity))
                return self._plan(state, recipes)
            elif kind == "open_recipe_detail":
                source_view = next((view for view in state.components if view.id == value.view_id), None)
                if source_view is not None and source_view.kind == "recipe_choices":
                    recipe = next((item for item in state.candidates if item.recipe_id == payload["recipe_id"]), None)
                    if recipe is None:
                        raise DomainConflict("invalid_reference", "菜谱不属于这张候选界面。")
                    if state.stage in {"prep", "cooking", "completed"}:
                        task_recipe = self._find_recipe(state, payload["recipe_id"])
                        if recipe.data_snapshot != task_recipe.data_snapshot:
                            raise DomainConflict("task_source_conflict", "当前任务采用冻结原件，请先回到选菜再查看新来源。")
                else:
                    recipe = self._find_recipe(state, payload["recipe_id"],
                        allow_candidate=state.stage not in {"prep", "cooking", "completed"})
                return self._show(state, RecipeDetail, "菜谱详情", RecipeDetailData(recipe=self._recipe_view(recipe, state)))
            elif kind == "change_servings":
                if state.stage not in {"planning", "prep"}:
                    raise DomainConflict("stage_conflict", "请先回到备料或选菜阶段调整份量。")
                recipe = self._find_recipe(state, payload["recipe_id"])
                recipe.target_servings = payload["servings"]
                recipe.portion_override = True
                for item in self._recipe_view(recipe, state).ingredients:
                    state.prep_status[item.id] = "pending"
                # A changed batch needs fresh execution confirmation; old completed steps cannot finish new quantities.
                state.active_recipe_id = recipe.recipe_id
                steps = self._recipe_view(recipe, state).steps
                changed_step_ids = {step.id for step in steps}
                state.step_status = {key: status for key, status in state.step_status.items() if key not in changed_step_ids}
                state.active_step_id = steps[0].id if steps else None
                for timer in state.timers:
                    if timer.recipe_id == recipe.recipe_id and timer.status in {"running", "paused"}:
                        timer.status, timer.deadline_at = "cancelled", None
            elif kind == "set_checklist_scope":
                component = next((view for view in state.components if view.id == value.view_id
                                  and view.kind == "prep_checklist"), None)
                if component is None:
                    raise DomainConflict("invalid_reference", "请在当前食材清单上选择菜品。")
                self._prep_items(state, payload.get("recipe_ids"))
                component.data.recipe_ids = payload.get("recipe_ids")
                return component
            elif kind == "check_prep_item":
                if state.stage not in {"planning", "prep"}:
                    raise DomainConflict("stage_conflict", "只有备料阶段可以确认准备状态。")
                origin = next((view for view in state.components if view.id == value.view_id
                               and view.kind == "prep_checklist"), None)
                scope = origin.data.recipe_ids if origin else None
                if payload["item_id"] not in {item.id for item in self._prep_items(state, scope)}:
                    raise DomainConflict("invalid_reference", "食材不属于本次餐单。")
                self._set_prep_status(state, payload["item_id"], payload["status"], "unknown")
            elif kind == "set_active_recipe":
                recipe = self._find_recipe(state, payload["recipe_id"])
                steps = self._recipe_view(recipe, state).steps
                if state.stage == "cooking" and steps:
                    target = next((step for step in steps if step.status != "done"), steps[0])
                    self._activate_step(state, recipe.recipe_id, target.id)
                else:
                    state.active_recipe_id = recipe.recipe_id
                    state.active_step_id = steps[0].id if steps else None
            elif kind == "set_active_step":
                self._activate_step(state, payload["recipe_id"], payload["step_id"])
            elif kind == "complete_step":
                self._complete_step(state, payload["recipe_id"], payload["step_id"], value.payload.completed)
            elif kind == "change_stage":
                return self._set_stage(state, payload["stage"])
            elif kind == "complete_meal":
                return self._set_stage(state, "completed")
            elif kind == "start_timer":
                return self._start_timer(state, payload)
            else:
                timer = next((item for item in state.timers if item.id == payload["timer_id"]), None)
                if timer is None:
                    raise DomainConflict("invalid_reference", "计时器不属于当前工作区。")
                if kind == "pause_timer":
                    if timer.status != "running":
                        raise DomainConflict("timer_state_conflict", "只有运行中的计时器可以暂停。")
                    timer.status, timer.deadline_at = "paused", None
                elif kind == "resume_timer":
                    if timer.status != "paused" or timer.remaining_seconds <= 0:
                        raise DomainConflict("timer_state_conflict", "只有仍有剩余时间的暂停计时器可以恢复。")
                    timer.status = "running"
                    timer.deadline_at = _stamp(self._now() + timedelta(seconds=timer.remaining_seconds))
                elif timer.status in {"running", "paused"}:
                    timer.status, timer.deadline_at = "cancelled", None
            return None
        try:
            return self._command(value.action_id, value.type, payload, value.base_revision, command, value.view_id)
        except ValidationError as error:
            # Partial preference updates that violate final values never replace the persisted dictionary.
            result = self._response(self._state(self._get_state()), "invalid_action", "更新后的值不符合界面字段要求。")
            result["errors"] = _validation_details(error)
            result["recovery"] = ["修正列出的字段后重新提交当前界面。"]
            return result
