"""Owns frozen selections, meal planning, stage transitions, step execution and timers."""

import copy
from datetime import datetime, timedelta
from uuid import uuid4

from .contracts import TOOL_CONTRACTS
from .models import (
    ApprovedRecipeRef,
    CookingData,
    CookingGuide,
    FrozenRecipe,
    MealPlan,
    PrepChecklist,
    PrepData,
    ProgressData,
    TaskProgress,
    TimerCapacity,
    Timers,
    TimersData,
    TimerState,
)
from .support import PREP_NOTE, STAGE_TRANSITIONS, DomainConflict, _dump, _stamp


class KitchenCommands:
    """Internal part of WorkflowService; persistence and revision checks stay in service.py."""

    @staticmethod
    def _prune_step_timer_history(state):
        # Old terminal duplicates are redundant. Preserve every active reminder until explicitly ended.
        active_keys = {(timer.recipe_id, timer.step_id) for timer in state.timers
                       if timer.step_id is not None and timer.status in {"running", "paused"}}
        seen = set(active_keys)
        kept = []
        for timer in reversed(state.timers):
            key = timer.recipe_id, timer.step_id
            if timer.step_id is not None and timer.status not in {"running", "paused"}:
                if key in seen:
                    continue
                seen.add(key)
            kept.append(timer)
        state.timers = list(reversed(kept))

    @staticmethod
    def _step_timers(state, recipe_id):
        preferred = {}
        for timer in state.timers:
            if recipe_id is None or timer.recipe_id != recipe_id or timer.step_id is None:
                continue
            prior = preferred.get(timer.step_id)
            if prior is None or timer.status in {"running", "paused"} or prior.status not in {"running", "paused"}:
                preferred[timer.step_id] = timer
        selected = {timer.id for timer in preferred.values()}
        return [timer for timer in state.timers if timer.recipe_id == recipe_id
                and recipe_id is not None and (timer.step_id is None or timer.id in selected)]

    def _timer_capacity(self, state, settings=None):
        limit = (settings or self.cooking_settings()).timer_limit
        active = sum(timer.status in {"running", "paused"} for timer in state.timers)
        return TimerCapacity(limit=limit, active_count=active, available_slots=max(0, limit - active), over_limit=active > limit)

    @staticmethod
    def _timer_limit_message(capacity):
        return f"当前已有{capacity.active_count}个运行或暂停的计时器，上限为{capacity.limit}个；请先结束已有计时器再添加。"

    def _refresh_timers(self, state):
        instant = self._now()
        for timer in state.timers:
            if timer.status == "running" and timer.deadline_at:
                remaining = max(0.0, (datetime.fromisoformat(timer.deadline_at) - instant).total_seconds())
                timer.remaining_seconds = remaining
                if remaining == 0:
                    timer.status = "finished"
                    timer.deadline_at = None

    def _find_recipe(self, state, recipe_id, *, allow_candidate=False):
        matches = [recipe for recipe in state.meal_plan if recipe.recipe_id == recipe_id]
        if not matches and allow_candidate:
            matches = [recipe for recipe in state.candidates if recipe.recipe_id == recipe_id]
        if not matches:
            raise DomainConflict("invalid_reference", "菜谱不属于当前可信候选或餐单。")
        return matches[0]

    def _freeze(self, recipe_ids, data_snapshot, approved_recipes):
        approved = [ApprovedRecipeRef.model_validate(item) if isinstance(item, dict) else item for item in approved_recipes]
        wanted = data_snapshot.model_dump(mode="json")
        for identity in recipe_ids:
            if not any(item.recipe_id == identity and item.data_snapshot.model_dump(mode="json") == wanted
                       for item in approved):
                raise DomainConflict("unverified_reference", "请先检索真实菜谱，再引用该结果的数据快照。")
        frozen = []
        for identity in dict.fromkeys(recipe_ids):
            result = self.retrieval.get_recipe({"recipe_id": identity, "expected_snapshot": wanted})
            if result.get("status") != "ok" or not result.get("recipe"):
                raise DomainConflict(result.get("status", "not_found"), "源菜谱已更新或不可读取，请重新检索。")
            source = result["recipe"]
            allowed = ("recipe_id", "name", "description", "servings", "estimated_servings", "total_time_min",
                       "difficulty", "ingredients", "steps", "nutrition", "warnings", "blocking_reasons")
            clean = {key: copy.deepcopy(source[key]) for key in allowed if key in source}
            ingredient_fields = ("ordinal", "ingredient_id", "canonical_name", "raw_name", "raw_amount", "raw_unit",
                                 "raw_text", "grams", "scalable", "estimated_grams", "nutrition")
            clean["ingredients"] = [{key: item[key] for key in ingredient_fields if key in item}
                                    for item in clean.get("ingredients", [])]
            for item in clean["ingredients"]:
                item["nutrition"] = {key: value for key, value in (item.get("nutrition") or {}).items()
                                     if key in {"contribution", "per_100g", "fdc_id", "estimated", "estimated_nutrients"}}
            frozen.append(FrozenRecipe(recipe_id=identity, data_snapshot=data_snapshot, recipe=clean))
        return frozen

    def _resolve_selection(self, state, recipe_ids, data_snapshot, approved_recipes):
        """Resolve only frozen/current references or server-approved retrieval evidence."""
        approved = [ApprovedRecipeRef.model_validate(item) if isinstance(item, dict) else item for item in approved_recipes]
        recipes = []
        for identity in dict.fromkeys(recipe_ids):
            current = [item for item in [*state.meal_plan, *state.candidates] if item.recipe_id == identity
                       and (data_snapshot is None or item.data_snapshot == data_snapshot)]
            refs = {_dump(ref.data_snapshot.model_dump(mode="json")): ref.data_snapshot for ref in [*current, *approved]
                    if ref.recipe_id == identity and (data_snapshot is None or ref.data_snapshot == data_snapshot)}
            if len(refs) > 1:
                raise DomainConflict("snapshot_required", "该菜谱有多个已检索来源，请明确使用对应data_snapshot。")
            existing = next(iter(current), None)
            if existing is not None:
                recipes.append(existing)
                continue
            if not refs:
                raise DomainConflict("invalid_reference", "菜谱不属于当前任务或已批准的真实检索结果，请先检索。")
            recipes.extend(self._freeze([identity], next(iter(refs.values())), approved))
        return recipes

    def _plan(self, state, recipes, servings=None, *, diners=None, portion_mode="shared"):
        if state.stage not in {"idea", "planning", "completed"}:
            raise DomainConflict("stage_conflict", "请先回到选菜阶段，再更换餐单；不会重置正在烹饪的任务。")
        requested = servings or {}
        if set(requested) - {recipe.recipe_id for recipe in recipes}:
            raise DomainConflict("invalid_reference", "份量参数包含未选中的菜谱。")
        existing = {(item.recipe_id, _dump(item.data_snapshot.model_dump(mode="json"))): item for item in state.meal_plan}
        diners = diners if diners is not None else state.preferences.servings
        diners = diners if diners is not None else state.meal_diners
        factor = 1.0 if portion_mode == "per_dish" else {1: 1.0, 2: 0.75, 3: 0.6, 4: 0.5}[len(recipes)]
        default_target = round(diners * factor, 2) if diners is not None else None
        selected = []
        for recipe in recipes:
            prior = existing.get((recipe.recipe_id, _dump(recipe.data_snapshot.model_dump(mode="json"))))
            override = recipe.recipe_id in requested or bool(prior and prior.portion_override)
            target = requested.get(recipe.recipe_id, prior.target_servings if prior and prior.portion_override else default_target)
            selected.append(recipe.model_copy(deep=True, update={"target_servings": target, "portion_override": override}))
        same_plan = (state.stage != "completed" and diners == state.meal_diners and portion_mode == state.portion_mode
                     and [item.model_dump(mode="json") for item in selected] == [item.model_dump(mode="json") for item in state.meal_plan])
        if same_plan:
            return self._show(state, MealPlan, "这次的餐单", self._meal_data(state))
        state.meal_plan = selected
        state.meal_diners, state.portion_mode = diners, portion_mode
        state.meal_id, state.started_at, state.completed_at = str(uuid4()), None, None
        state.stage = "planning"
        state.prep_status = {}
        state.prep_availability = {}
        state.step_status = {}
        state.timers = []
        state.adaptations = []
        state.active_recipe_id = state.meal_plan[0].recipe_id if state.meal_plan else None
        first = self._recipe_view(state.meal_plan[0], state).steps if state.meal_plan else []
        state.active_step_id = first[0].id if first else None
        state.components = [view for view in state.components if view.kind in {"preference_form", "recipe_choices"}]
        plan = self._show(state, MealPlan, "这次的餐单", self._meal_data(state))
        self._ensure_component(state, TaskProgress, "当前进度", self._progress(state))
        return plan

    def _transition_blockers(self, state, stage):
        blockers = []
        if state.pending_question is not None:
            blockers.append({"code": "pending_question_exists", "message": "请先回答当前问题。"})
        if stage != state.stage and stage not in STAGE_TRANSITIONS[state.stage]:
            blockers.append({"code": "stage_conflict", "message": f"不能从 {state.stage} 直接进入 {stage}。"})
        if stage != "idea" and not state.meal_plan:
            blockers.append({"code": "plan_required", "message": "请先选择真实菜谱作为本次餐单。"})
        if stage in {"prep", "cooking", "completed"} and state.meal_plan:
            if any(not recipe.recipe.get("ingredients") or not recipe.recipe.get("steps") for recipe in state.meal_plan):
                blockers.append({"code": "recipe_incomplete", "message": "餐单菜谱缺少食材或步骤，不能进入执行阶段。"})
        if stage == "cooking" and any(item.status == "pending" for item in self._prep_items(state)):
            blockers.append({"code": "prep_incomplete", "message": "仍有待确认食材；请先标记备齐或明确确认跳过。"})
        if stage == "completed":
            steps = [step for recipe in state.meal_plan for step in self._recipe_view(recipe, state).steps]
            if any(step.status != "done" for step in steps):
                blockers.append({"code": "steps_incomplete", "message": "仍有未完成步骤，不能把本次烹饪标为完成。"})
        return blockers

    def _transitions(self, state):
        allowed, blockers = [], {}
        for target in STAGE_TRANSITIONS:
            if target == state.stage:
                continue
            reasons = self._transition_blockers(state, target)
            if reasons:
                blockers[target] = reasons
            else:
                allowed.append(target)
        return allowed, blockers

    def _tool_blockers(self, state, name, *, model_visible=False, allowed_transitions=None):
        contract = TOOL_CONTRACTS[name]
        blockers = []
        if model_visible and not contract.model_visible:
            blockers.append({"code": "use_select_recipes", "message": "请使用统一的select_recipes入口。"})
        if state.pending_question is not None and name != "get_cooking_workspace":
            blockers.append({"code": "pending_question_exists", "message": "请先回答当前问题，不会替换尚未回答的确认。"})
        if state.stage not in contract.allowed_stages:
            blockers.append({"code": "stage_conflict", "message": "此工具在当前阶段不可用，请查看allowed_transitions及blockers。"})
        if name in {"show_meal_plan", "show_prep_checklist", "show_cooking_guide", "update_prep_items", "update_cooking_guidance"} and not state.meal_plan:
            blockers.append({"code": "plan_required", "message": "请先选择真实菜谱作为本次餐单。"})
        capacity = self._timer_capacity(state)
        if model_visible and name == "set_cooking_timer" and capacity.available_slots == 0:
            blockers.append({"code": "timer_limit", "message": self._timer_limit_message(capacity)})
        if name == "advance_cooking_stage" and model_visible:
            allowed = self._transitions(state)[0] if allowed_transitions is None else allowed_transitions
            if not allowed:
                blockers.append({"code": "no_available_transition", "message": "当前没有可进入的不同阶段，请查看各目标的blockers。"})
        return blockers

    def _current_actions(self, state, allowed):
        actions = {}
        for name, contract in TOOL_CONTRACTS.items():
            blockers = self._tool_blockers(state, name, model_visible=True, allowed_transitions=allowed)
            actions[name] = {"available": not blockers, "effect": contract.effect,
                             "auto_display": contract.auto_display, "blockers": blockers}
        return actions

    def _set_stage(self, state, stage):
        blockers = self._transition_blockers(state, stage)
        if blockers:
            raise DomainConflict(blockers[0]["code"], blockers[0]["message"])
        if stage == "completed":
            state.completed_at = state.completed_at or _stamp(self._now())
            for timer in state.timers:
                if timer.status in {"running", "paused"}:
                    timer.status, timer.deadline_at = "cancelled", None
        state.stage = stage
        if stage == "idea":
            state.meal_plan, state.prep_status, state.step_status = [], {}, {}
            state.prep_availability = {}
            state.active_recipe_id, state.active_step_id, state.timers = None, None, []
            state.adaptations = []
            state.meal_id, state.started_at, state.completed_at, state.meal_diners = None, None, None, None
            state.components = [view for view in state.components if view.kind == "preference_form"]
        if stage == "cooking":
            state.started_at = state.started_at or _stamp(self._now())
            recipe = self._find_recipe(state, state.active_recipe_id or state.meal_plan[0].recipe_id)
            steps = self._recipe_view(recipe, state).steps
            target = next((step for step in steps if step.id == state.active_step_id and step.status != "done"), None)
            target = target or next((step for step in steps if step.status != "done"), None)
            if target:
                self._activate_step(state, recipe.recipe_id, target.id)
        progress = self._ensure_component(state, TaskProgress, "当前进度", self._progress(state))
        if stage == "planning":
            return self._ensure_component(state, MealPlan, "这次的餐单", self._meal_data(state))
        if stage == "prep":
            return self._ensure_component(state, PrepChecklist, "逐项确认备料",
                                          PrepData(items=self._prep_items(state), notes=[PREP_NOTE]))
        if stage == "cooking":
            recipe = self._find_recipe(state, state.active_recipe_id)
            view = self._recipe_view(recipe, state)
            return self._ensure_component(state, CookingGuide, "烹饪步骤", CookingData(
                recipe_id=recipe.recipe_id, recipe_name=view.name, steps=view.steps,
                active_step_id=state.active_step_id))
        return progress

    def _progress(self, state):
        temporary = TaskProgress(id="progress", title="进度", revision=state.revision,
                                 data=ProgressData(stage=state.stage, recipe_count=0, prep_total=0,
                                                   prep_ready=0, steps_done=0, steps_total=0))
        return self._component_data(temporary, state)

    @staticmethod
    def _set_prep_status(state, item_id, status, availability=None):
        state.prep_status[item_id] = status
        if status != "pending" or availability == "unknown":
            state.prep_availability.pop(item_id, None)
        elif availability == "missing":
            state.prep_availability[item_id] = "missing"

    def _activate_step(self, state, recipe_id, step_id):
        if state.stage != "cooking":
            raise DomainConflict("stage_conflict", "进入烹饪阶段后才能设置当前步骤。")
        recipe = self._find_recipe(state, recipe_id)
        steps = self._recipe_view(recipe, state).steps
        if step_id not in {step.id for step in steps}:
            raise DomainConflict("invalid_reference", "步骤不属于这道任务菜谱。")
        for key, value in list(state.step_status.items()):
            if value == "current":
                state.step_status[key] = "pending"
        state.active_recipe_id, state.active_step_id = recipe_id, step_id
        if state.step_status.get(step_id) != "done":
            state.step_status[step_id] = "current"

    def _navigate_step(self, state, direction, *, recipe_id=None, step_number=None):
        recipe = self._find_recipe(state, recipe_id or state.active_recipe_id)
        steps = self._recipe_view(recipe, state).steps
        if not steps:
            raise DomainConflict("steps_unavailable", "这道任务菜谱没有可切换的步骤，请先核对原菜谱。")
        reference = next((step for step in steps if recipe.recipe_id == state.active_recipe_id
                          and step.id == state.active_step_id), None)
        reference = reference or next((step for step in steps if step.status != "done"), steps[-1])
        index = steps.index(reference)
        if direction == "step":
            index = step_number - 1
            if index >= len(steps):
                raise DomainConflict("invalid_reference", "步骤编号超出了这道任务菜谱的范围，没有切换步骤。")
        elif direction == "next":
            if index == len(steps) - 1:
                raise DomainConflict("last_step", "已经是这道菜的最后一步，没有自动完成任何步骤或结束烹饪。")
            index += 1
        elif direction == "previous":
            if index == 0:
                raise DomainConflict("first_step", "已经是这道菜的第一步，没有更早的步骤。")
            index -= 1
        self._activate_step(state, recipe.recipe_id, steps[index].id)

    def _complete_step(self, state, recipe_id, step_id, completed=True):
        self._activate_step(state, recipe_id, step_id)
        state.step_status[step_id] = "done" if completed else "current"
        if completed:
            recipe = self._find_recipe(state, recipe_id)
            next_step = next((step for step in self._recipe_view(recipe, state).steps if step.status != "done"), None)
            state.active_step_id = next_step.id if next_step else None
            if next_step:
                state.step_status[next_step.id] = "current"

    def _start_timer(self, state, payload, basis="user"):
        if state.stage != "cooking":
            raise DomainConflict("stage_conflict", "只有烹饪阶段可以设置本任务计时器。")
        recipe_id, step_id = payload.get("recipe_id"), payload.get("step_id")
        if recipe_id:
            recipe = self._find_recipe(state, recipe_id)
            steps = self._recipe_view(recipe, state).steps
            if step_id and step_id not in {step.id for step in steps}:
                raise DomainConflict("invalid_reference", "计时步骤不属于当前餐单。")
        elif step_id:
            raise DomainConflict("invalid_reference", "步骤计时必须指定所属菜谱。")
        if basis == "recipe_text":
            step = next((item for item in steps if item.id == step_id), None) if recipe_id else None
            if step is None or step.timer_seconds != payload["seconds"]:
                raise DomainConflict("unverified_duration", "源步骤没有这个明确时长；请让用户指定，不能猜测。")
        existing = next((timer for timer in reversed(state.timers) if step_id is not None
            and timer.status in {"running", "paused"} and timer.recipe_id == recipe_id and timer.step_id == step_id), None)
        if existing is not None:
            if existing.seconds != payload["seconds"]:
                raise DomainConflict("timer_already_exists", "这个步骤已有未结束的计时器，请先继续或取消它，再设置新的时长。")
            return self._ensure_component(state, Timers, "厨房计时", TimersData(timers=state.timers))
        capacity = self._timer_capacity(state)
        if capacity.available_slots == 0:
            raise DomainConflict("timer_limit", self._timer_limit_message(capacity))
        if step_id is not None:
            state.timers = [timer for timer in state.timers if (timer.recipe_id, timer.step_id) != (recipe_id, step_id)]
        state.timers.append(TimerState(id=str(uuid4()), label=payload["label"], seconds=payload["seconds"],
            remaining_seconds=float(payload["seconds"]), deadline_at=_stamp(self._now() + timedelta(seconds=payload["seconds"])),
            recipe_id=recipe_id, step_id=step_id, basis=basis))
        # Bounded terminal history must never evict a still-running or paused reminder.
        active_ids = {timer.id for timer in state.timers if timer.status in {"running", "paused"}}
        terminal = [timer for timer in state.timers if timer.id not in active_ids]
        keep_ids = active_ids.copy()
        terminal_slots = max(0, 30 - len(active_ids))
        if terminal_slots:
            keep_ids.update(timer.id for timer in terminal[-terminal_slots:])
        state.timers = [timer for timer in state.timers if timer.id in keep_ids]
        return self._show(state, Timers, "厨房计时", TimersData(timers=state.timers))
