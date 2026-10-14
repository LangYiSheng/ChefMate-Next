"""Atomic kitchen commands and safe projections over frozen, real recipe evidence."""

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import ValidationError
from chefmate_storage.queries import NUTRIENTS

from .models import (
    TOOL_REQUEST_MODELS, ActionRecord, ApprovedRecipeRef, CompactComponentSnapshot,
    CookingData, CookingGuide, FrozenRecipe, IngredientView, MealPlan, PreferenceField, PreferenceForm,
    CookingAdaptation, PreferenceFormData, PreferenceValues, PrepChecklist, PrepData, ProgressData, RecipeChoices,
    RecipeDetail, RecipeDetailData, RecipeListData, RecipeView, ShoppingData, ShoppingItem, ShoppingList,
    SourceInfo, StepView, TaskProgress, TimerState, Timers, TimersData, UIAction, UISnapshot,
    UserActionRequest, WorkspaceState,
)

TOOL_DESCRIPTIONS = {
    "show_preference_form": "展示闭合的用户偏好表单；由用户填写，不能生成任意表单或偷偷保存偏好。",
    "show_recipe_choices": "展示真实搜索结果的菜谱候选。recipe_ids 和 data_snapshot 必须来自检索工具。",
    "show_recipe_details": "展示真实菜谱详情；已选任务使用冻结原件，新菜谱必须提供检索来源快照。",
    "set_meal_plan": "把用户已选的真实菜谱冻结为本次餐单，最多4道；不修改源菜谱、不凭空生成菜谱。",
    "show_meal_plan": "展示当前餐单和目标份量，保留来源、未知值和估算标记。",
    "show_shopping_list": "从当前餐单投影缺料清单；只合并相同食材身份、名称和单位且可可靠计算的数量。",
    "show_prep_checklist": "展示当前真实备料状态和原文依据，不猜测哪个是主料或可选食材。",
    "show_cooking_guide": "展示已选任务的烹饪步骤与实际进度，浏览步骤不等于完成步骤。",
    "set_cooking_timer": "设置持久化计时器；秒数必须由用户指定或原菜谱明确给出，不猜测时长。",
    "show_timers": "展示当前计时器与剩余时间，刷新和重启后依据截止时间继续。",
    "get_cooking_workspace": "读取餐单、阶段、用户已确认操作和界面短快照。",
    "advance_cooking_stage": "显式推进或回退厨房阶段；备料未确认或步骤未完成时拒绝越级。",
    "update_cooking_guidance": "按用户条件为当前任务追加备菜或步骤建议，保留冻结原步骤及真实进度。",
}
PREP_NOTE = "源菜谱没有可靠的必选/可选字段；这里逐项展示原文，跳过需由用户明确确认，不代表已证明可省略。"
FIELD_META = {
    "servings": ("用餐人数", "number"), "max_total_time_min": ("最长耗时（分钟）", "number"),
    "taste_tags": ("口味", "multiselect"), "available_tools": ("可用厨具", "multiselect"),
    "ingredient_terms": ("想吃或已有的食材", "multiselect"),
    "exclude_ingredient_terms": ("不想吃的食材", "multiselect"), "dietary_notes": ("其他饮食需求", "textarea"),
    "allow_estimated": ("允许营养估算值", "checkbox"),
}
STAGE_TRANSITIONS = {
    "idea": {"planning"}, "planning": {"idea", "shopping", "prep"},
    "shopping": {"planning", "prep"}, "prep": {"planning", "shopping", "cooking"},
    "cooking": {"prep", "completed"}, "completed": {"idea", "planning"},
}


class DomainConflict(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _amount(value, unit):
    return f"{value:g} {unit or ''}".strip() if value is not None else "用量未知"


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat()


def _ingredient_id(recipe_id, ordinal):
    return f"{recipe_id}:ingredient:{ordinal}"


def _step_id(recipe_id, ordinal):
    return f"{recipe_id}:step:{ordinal}"


def _explicit_durations(text):
    if not isinstance(text, str):
        return set()
    if re.search(r"\d+\s*[-~—到至]\s*\d+\s*(?:秒|分钟|小时|seconds?|minutes?|hours?|mins?)", text, re.I):
        return set()
    values = {1800} if "半小时" in text else set()
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(秒钟?|分钟|小时|seconds?|minutes?|hours?|mins?|s|min|h)(?![A-Za-z])", text, re.I):
        factor = 3600 if unit.lower() in {"小时", "hour", "hours", "h"} else 60 if unit.lower() in {
            "分钟", "minute", "minutes", "min", "mins"} else 1
        seconds = float(value) * factor
        if seconds.is_integer() and 0 < seconds <= 86400:
            values.add(int(seconds))
    return values


class WorkflowService:
    """Caller owns persistence; mutate(fn) executes fn(workspace_dict) atomically.

    The callback replaces that dictionary in place only after successful validation.
    get_state and mutate must use the same session, and must not expose credentials.
    """

    def __init__(self, retrieval, get_state, mutate, *, now=None):
        self.retrieval = retrieval
        self._get_state = get_state
        self._mutate = mutate
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _state(self, raw):
        state = WorkspaceState.model_validate(raw or {})
        self._refresh_timers(state)
        return state

    def _refresh_timers(self, state):
        instant = self._now()
        for timer in state.timers:
            if timer.status == "running" and timer.deadline_at:
                remaining = max(0.0, (datetime.fromisoformat(timer.deadline_at) - instant).total_seconds())
                timer.remaining_seconds = remaining
                if remaining == 0:
                    timer.status = "finished"
                    timer.deadline_at = None

    def _recipe_view(self, frozen, state, *, summary=False):
        recipe = frozen.recipe
        selected = any(item.recipe_id == frozen.recipe_id and item.data_snapshot == frozen.data_snapshot
                       for item in state.meal_plan)
        original_servings = recipe.get("servings")
        target = frozen.target_servings
        ratio = target / original_servings if target and _finite(original_servings) and original_servings > 0 else None
        nutrition = copy.deepcopy(recipe.get("nutrition") or {})
        warnings = list(recipe.get("warnings") or [])
        if target is not None and ratio is None:
            warnings.append("原菜谱人数未知，无法可靠换算用量和总营养；保留原始数值。")
        if ratio is not None and ratio != 1:
            source_items = recipe.get("ingredients") or []
            total = {}
            for key in NUTRIENTS:
                values = [(item.get("nutrition") or {}).get("contribution", {}).get(key) for item in source_items]
                if source_items and all(_finite(value) for value in values):
                    total[key] = sum(value * ratio if item.get("scalable") is True else value
                                     for value, item in zip(values, source_items))
                elif all(item.get("scalable") is True for item in source_items):
                    source_value = (nutrition.get("total") or {}).get(key)
                    total[key] = source_value * ratio if _finite(source_value) else None
                else:
                    total[key] = None
            nutrition["total"] = total
            nutrition["per_serving"] = {key: value / target if value is not None else None for key, value in total.items()}
            if recipe.get("estimated_servings"):
                fields = [key for key, value in total.items() if value is not None]
                nutrition["estimated_total_fields"] = sorted(set(nutrition.get("estimated_total_fields", [])) | set(fields))
                nutrition["estimated_per_serving_fields"] = sorted(set(nutrition.get("estimated_per_serving_fields", [])) | set(fields))
                nutrition["estimated"] = True
            nutrition["scaled_from_servings"] = original_servings
            nutrition["scaled_to_servings"] = target
            if any(value is None for value in total.values()):
                nutrition["status"] = "incomplete"
                warnings.append("换算后部分营养无法可靠重算，保持未知；不可缩放食材没有按人数倍增。")
        ingredients = []
        for index, item in enumerate(recipe.get("ingredients") or []):
            ordinal = item.get("ordinal", index)
            identity = _ingredient_id(frozen.recipe_id, ordinal)
            grams = item.get("grams") if _finite(item.get("grams")) else None
            raw_amount, raw_unit = item.get("raw_amount"), item.get("raw_unit")
            number = self._parse_number(raw_amount)
            scalable = item.get("scalable") is True
            scaled = bool(scalable and ratio is not None and ratio != 1)
            if scaled:
                grams = grams * ratio if grams is not None else None
                number = number * ratio if number is not None else None
            unit = raw_unit if isinstance(raw_unit, str) and raw_unit.strip() else None
            amount_text = (str(raw_amount).strip() if raw_amount is not None else "")
            amount_text = (amount_text + " " + (unit or "")).strip()
            if scaled and number is not None:
                amount_text = _amount(number, unit)
            elif scaled and grams is not None:
                amount_text = _amount(grams, "g")
                unit, number = "g", grams
            elif scaled:
                amount_text = (amount_text or "用量未知") + "（无法可靠换算）"
            if not amount_text:
                amount_text = _amount(grams, "g") if grams is not None else "用量未知"
                if grams is not None:
                    unit, number = "g", grams
            ingredients.append(IngredientView(
                id=identity, recipe_id=frozen.recipe_id, ingredient_id=item["ingredient_id"],
                canonical_name=item["canonical_name"], raw_text=item.get("raw_text") or "",
                amount_text=amount_text, grams=grams, unit=unit, amount_value=number, scaled=scaled,
                estimated=bool(item.get("estimated_grams") or (ratio is not None and recipe.get("estimated_servings"))),
                status=state.prep_status.get(identity, "pending") if selected else "pending",
            ))
        steps = []
        for index, raw in enumerate(recipe.get("steps") or []):
            # Source list order is the authoritative ordinal; never regenerate or reorder instructions.
            instruction = raw if isinstance(raw, str) else raw.get("description", raw.get("instruction", ""))
            seconds = raw.get("timer_seconds") if isinstance(raw, dict) else None
            seconds = seconds if type(seconds) is int and 0 < seconds <= 86400 else None
            if seconds is None:
                durations = _explicit_durations(instruction)
                seconds = next(iter(durations)) if len(durations) == 1 else None
            identity = _step_id(frozen.recipe_id, index)
            steps.append(StepView(id=identity, ordinal=index, instruction=instruction,
                                  status=state.step_status.get(identity, "pending") if selected else "pending",
                                  timer_seconds=seconds))
        return RecipeView(
            recipe_id=frozen.recipe_id, name=recipe["name"], description=recipe.get("description"),
            servings=target if ratio is not None else original_servings,
            target_servings=target, original_servings=original_servings,
            estimated_servings=bool(recipe.get("estimated_servings")), total_time_min=recipe.get("total_time_min"),
            difficulty=recipe.get("difficulty"), nutrition=nutrition, ingredients=[] if summary else ingredients,
            steps=[] if summary else steps, warnings=warnings,
            source=SourceInfo(source_recipe_id=frozen.recipe_id, data_snapshot=frozen.data_snapshot),
        )

    @staticmethod
    def _parse_number(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (ValueError, TypeError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

    def _prep_items(self, state, recipe_ids=None):
        if recipe_ids is not None and not set(recipe_ids) <= {recipe.recipe_id for recipe in state.meal_plan}:
            raise DomainConflict("invalid_reference", "清单菜品必须来自当前餐单。")
        return [ingredient for recipe in state.meal_plan if recipe_ids is None or recipe.recipe_id in recipe_ids
                for ingredient in self._recipe_view(recipe, state).ingredients]

    def _shopping(self, state, recipe_ids=None):
        merged = {}
        legacy_ids = {}
        for item in self._prep_items(state, recipe_ids):
            if item.status in {"ready", "skipped"}:
                continue
            # Unknown quantities do not merge: doing so would hide a missing amount from another dish.
            key = (item.ingredient_id, item.canonical_name, item.unit, item.id if item.amount_value is None else "")
            if key not in merged:
                identity = "shopping:" + hashlib.sha256(_dump(key).encode()).hexdigest()[:20]
                merged[key] = ShoppingItem(id=identity, ingredient_id=item.ingredient_id,
                    canonical_name=item.canonical_name, unit=item.unit, amount_value=item.amount_value,
                    amount_text=item.amount_text, estimated=item.estimated, checked=identity in state.shopping_checked,
                    source_recipe_ids=[item.recipe_id], source_item_ids=[item.id])
                legacy_ids[key] = identity
            else:
                target = merged[key]
                target.amount_value += item.amount_value
                target.amount_text = _amount(target.amount_value, target.unit)
                target.estimated = target.estimated or item.estimated
                target.source_recipe_ids = list(dict.fromkeys([*target.source_recipe_ids, item.recipe_id]))
                target.source_item_ids.append(item.id)
        for key, target in merged.items():
            # A purchase confirmation applies to actual dish contributions, not every dish sharing its name.
            target.id = "shopping:" + hashlib.sha256(_dump([key, sorted(target.source_item_ids)]).encode()).hexdigest()[:20]
            target.checked = legacy_ids[key] in state.shopping_checked or all(
                identity in state.shopping_checked for identity in target.source_item_ids)
        return list(merged.values())

    def _component_data(self, component, state):
        kind = component.kind
        if kind == "preference_form":
            values = state.preferences.model_dump()
            return PreferenceFormData(values=state.preferences, fields=[PreferenceField(
                name=field.name, label=field.label, input_type=field.input_type, value=values[field.name],
                options=field.options) for field in component.data.fields])
        if kind in {"meal_plan", "recipe_choices"}:
            source = state.meal_plan if kind == "meal_plan" else state.candidates
            return RecipeListData(recipes=[self._recipe_view(recipe, state, summary=True) for recipe in source])
        if kind == "recipe_detail":
            identity = component.data.recipe.recipe_id
            expected = component.data.recipe.source.data_snapshot
            source = next((recipe for recipe in [*state.meal_plan, *state.candidates]
                           if recipe.recipe_id == identity and recipe.data_snapshot == expected), None)
            if source is None:
                raise DomainConflict("stale_view", "详情来源已经离开当前工作区，请重新打开。")
            if state.stage in {"prep", "cooking", "completed"} and not any(
                    recipe.recipe_id == identity and recipe.data_snapshot == expected for recipe in state.meal_plan):
                raise DomainConflict("stale_view", "未选中的旧详情不属于当前执行任务。")
            return RecipeDetailData(recipe=self._recipe_view(source, state))
        if kind == "shopping_list":
            return ShoppingData(items=self._shopping(state, component.data.recipe_ids),
                                recipe_ids=component.data.recipe_ids, notes=[PREP_NOTE])
        if kind == "prep_checklist":
            return PrepData(items=self._prep_items(state, component.data.recipe_ids),
                            recipe_ids=component.data.recipe_ids, notes=[PREP_NOTE],
                            guidance=[note.instruction for note in state.adaptations if note.step_id is None
                                      and (component.data.recipe_ids is None or note.recipe_id in component.data.recipe_ids)])
        if kind == "cooking_guide":
            identity = state.active_recipe_id
            recipe = self._find_recipe(state, identity) if identity else None
            view = self._recipe_view(recipe, state) if recipe else None
            if view:
                for step in view.steps:
                    note = next((note for note in state.adaptations if note.recipe_id == identity and note.step_id == step.id), None)
                    if note:
                        step.adapted_instruction, step.adaptation_reason = note.instruction, note.reason
            return CookingData(recipe_id=identity, recipe_name=view.name if view else None,
                               steps=view.steps if view else [], active_step_id=state.active_step_id,
                               guidance=[note.instruction for note in state.adaptations if note.recipe_id == identity and note.step_id is None])
        if kind == "timers":
            return TimersData(timers=state.timers)
        items = self._prep_items(state)
        steps = [step for recipe in state.meal_plan for step in self._recipe_view(recipe, state).steps]
        return ProgressData(stage=state.stage, recipe_count=len(state.meal_plan), prep_total=len(items),
            prep_ready=sum(item.status in {"ready", "skipped"} for item in items),
            steps_done=sum(step.status == "done" for step in steps), steps_total=len(steps))

    def _views(self, state):
        views = []
        for component in state.components:
            try:
                data = self._component_data(component, state)
            except DomainConflict:
                continue
            editable = state.stage != "completed" or component.kind in {
                "preference_form", "recipe_choices", "timers", "task_progress"}
            views.append(component.model_copy(update={"data": data, "revision": state.revision, "editable": editable}))
        return views

    def _compact(self, state, views):
        components = []
        for view in views:
            raw = view.data.model_dump(mode="json")
            if view.kind == "preference_form":
                data = {"values": raw["values"], "fields": [item["name"] for item in raw["fields"]]}
            elif view.kind in {"recipe_choices", "meal_plan"}:
                data = {"recipes": [{"recipe_id": recipe["recipe_id"], "name": recipe["name"],
                                      "servings": recipe["servings"], "target_servings": recipe["target_servings"],
                                      "original_servings": recipe["original_servings"],
                                      "source": recipe["source"]}
                                     for recipe in raw["recipes"]],
                        "selected_recipe_ids": [recipe.recipe_id for recipe in state.meal_plan]}
            elif view.kind == "recipe_detail":
                recipe = raw["recipe"]
                data = {"recipe_id": recipe["recipe_id"], "name": recipe["name"], "source": recipe["source"]}
            elif view.kind == "shopping_list":
                data = {"items": [{key: item[key] for key in ("id", "canonical_name", "checked", "amount_text")}
                                  for item in raw["items"]], "recipe_ids": raw["recipe_ids"]}
            elif view.kind == "prep_checklist":
                data = {"items": [{key: item[key] for key in ("id", "recipe_id", "canonical_name", "status",
                                  "amount_text", "amount_value", "unit", "scaled", "estimated")}
                                  for item in raw["items"]], "recipe_ids": raw["recipe_ids"], "guidance": raw["guidance"]}
            elif view.kind == "cooking_guide":
                data = {"recipe_id": raw["recipe_id"], "active_step_id": raw["active_step_id"],
                        "steps": [{"id": step["id"], "ordinal": step["ordinal"], "status": step["status"]}
                                  for step in raw["steps"]],
                        "current_step": next((step for step in raw["steps"] if step["id"] == raw["active_step_id"]), None)}
                data["guidance"] = raw["guidance"]
            else:
                data = raw
            components.append(CompactComponentSnapshot(id=view.id, kind=view.kind, title=view.title,
                revision=view.revision, editable=view.editable, data=data))
        recent = []
        for record in state.recent_actions[-20:]:
            payload = copy.deepcopy(record.payload)
            if record.type == "update_preferences":
                # Current full preference values are authoritative below; history only needs which fields changed.
                payload = {"changed_fields": sorted((payload.get("values") or {}).keys())}
            recent.append(record.model_copy(update={"payload": payload}))
        return UISnapshot(revision=state.revision, stage=state.stage, components=components,
                          preferences=state.preferences, selected_recipe_ids=[recipe.recipe_id for recipe in state.meal_plan],
                          active_recipe_id=state.active_recipe_id, active_step_id=state.active_step_id,
                          recent_actions=recent)

    def _response(self, state, status="ok", message=None, action_component=None):
        views = self._views(state)
        snapshot = self._compact(state, views).model_dump(mode="json")
        actions = []
        if action_component is not None:
            current = next((view for view in views if view.id == action_component.id), None)
            if current:
                actions = [UIAction(component=current).model_dump(mode="json")]
        result = {"status": status, "workspace": snapshot, "views": [view.model_dump(mode="json") for view in views],
                  "ui_snapshot": snapshot, "actions": actions}
        if message is not None:
            result["message"] = message
        return result

    def get_workspace(self):
        return self._response(self._state(self._get_state()))

    def model_snapshot(self):
        return self.get_workspace()["ui_snapshot"]

    def _command(self, operation_id, operation_type, payload, base_revision, callback, view_id=None):
        fingerprint = hashlib.sha256(_dump({"type": operation_type, "payload": payload}).encode()).hexdigest()
        def mutate(raw):
            state = self._state(raw)
            previous = next((item for item in state.recent_actions if item.action_id == operation_id), None)
            if previous:
                if previous.fingerprint != fingerprint:
                    return self._response(state, "action_id_conflict", "同一操作标识已用于不同内容，请重新确认。")
                return self._response(state, "ok", "此操作已经执行，没有重复修改状态。")
            if base_revision != state.revision:
                return self._response(state, "revision_conflict", "工作区已变化，请复核当前界面后重新操作。")
            if view_id is not None:
                view = next((item for item in self._views(state) if item.id == view_id), None)
                if view is None or not view.editable:
                    return self._response(state, "stale_view", "这张界面已失效或只读，请在当前工作区操作。")
            try:
                component = callback(state)
            except DomainConflict as error:
                return self._response(self._state(raw), error.status, str(error))
            state.revision += 1
            state.recent_actions.append(ActionRecord(action_id=operation_id, type=operation_type, payload=payload,
                revision=state.revision, created_at=_stamp(self._now()), fingerprint=fingerprint))
            state.recent_actions = state.recent_actions[-20:]
            # Revalidate complete state before replacing persisted workspace, keeping failed commands atomic.
            encoded = WorkspaceState.model_validate(state.model_dump(mode="json")).model_dump(mode="json")
            raw.clear()
            raw.update(encoded)
            return self._response(state, action_component=component)
        return self._mutate(mutate)

    def _show(self, state, component_class, title, data):
        component = component_class(id=str(uuid4()), title=title, revision=state.revision + 1, data=data)
        state.components = [view for view in state.components if view.kind != component.kind]
        state.components.append(component)
        return component

    def _ensure_component(self, state, component_class, title, data):
        kind = component_class.model_fields["kind"].default
        for index, component in enumerate(state.components):
            if component.kind == kind:
                refreshed = component.model_copy(update={"title": title, "data": data,
                                                          "revision": state.revision + 1})
                state.components[index] = refreshed
                return refreshed
        return self._show(state, component_class, title, data)

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

    def _plan(self, state, recipes, servings=None):
        if state.stage not in {"idea", "planning", "shopping", "completed"}:
            raise DomainConflict("stage_conflict", "请先回到选菜阶段，再更换餐单；不会重置正在烹饪的任务。")
        requested = servings or {}
        if set(requested) - {recipe.recipe_id for recipe in recipes}:
            raise DomainConflict("invalid_reference", "份量参数包含未选中的菜谱。")
        state.meal_plan = [recipe.model_copy(deep=True, update={"target_servings": requested.get(recipe.recipe_id,
                           state.preferences.servings)}) for recipe in recipes]
        state.stage = "planning"
        state.prep_status = {}
        state.step_status = {}
        state.shopping_checked = []
        state.timers = []
        state.adaptations = []
        state.active_recipe_id = state.meal_plan[0].recipe_id if state.meal_plan else None
        first = self._recipe_view(state.meal_plan[0], state).steps if state.meal_plan else []
        state.active_step_id = first[0].id if first else None
        state.components = [view for view in state.components if view.kind in {"preference_form", "recipe_choices"}]
        plan = self._show(state, MealPlan, "这次的餐单", RecipeListData(
            recipes=[self._recipe_view(recipe, state, summary=True) for recipe in state.meal_plan]))
        self._ensure_component(state, TaskProgress, "当前进度", self._progress(state))
        return plan

    def _set_stage(self, state, stage):
        if stage != state.stage and stage not in STAGE_TRANSITIONS[state.stage]:
            raise DomainConflict("stage_conflict", f"不能从 {state.stage} 直接进入 {stage}。")
        if stage in {"shopping", "prep", "cooking", "completed"} and not state.meal_plan:
            raise DomainConflict("plan_required", "请先选择真实菜谱作为本次餐单。")
        if stage in {"prep", "cooking", "completed"}:
            if any(not recipe.recipe.get("ingredients") or not recipe.recipe.get("steps") for recipe in state.meal_plan):
                raise DomainConflict("recipe_incomplete", "餐单菜谱缺少食材或步骤，不能进入执行阶段。")
        if stage == "cooking" and any(item.status == "pending" for item in self._prep_items(state)):
            raise DomainConflict("prep_incomplete", "仍有待确认食材；请先标记备齐或明确确认跳过。")
        if stage == "completed":
            steps = [step for recipe in state.meal_plan for step in self._recipe_view(recipe, state).steps]
            if any(step.status != "done" for step in steps):
                raise DomainConflict("steps_incomplete", "仍有未完成步骤，不能把本次烹饪标为完成。")
            for timer in state.timers:
                if timer.status in {"running", "paused"}:
                    timer.status, timer.deadline_at = "cancelled", None
        state.stage = stage
        if stage == "idea":
            state.meal_plan, state.prep_status, state.step_status, state.shopping_checked = [], {}, {}, []
            state.active_recipe_id, state.active_step_id, state.timers = None, None, []
            state.adaptations = []
            state.components = [view for view in state.components if view.kind == "preference_form"]
        if stage == "cooking":
            recipe = self._find_recipe(state, state.active_recipe_id or state.meal_plan[0].recipe_id)
            steps = self._recipe_view(recipe, state).steps
            target = next((step for step in steps if step.id == state.active_step_id and step.status != "done"), None)
            target = target or next((step for step in steps if step.status != "done"), None)
            if target:
                self._activate_step(state, recipe.recipe_id, target.id)
        progress = self._ensure_component(state, TaskProgress, "当前进度", self._progress(state))
        if stage == "planning":
            return self._ensure_component(state, MealPlan, "这次的餐单", RecipeListData(
                recipes=[self._recipe_view(recipe, state, summary=True) for recipe in state.meal_plan]))
        if stage == "shopping":
            return self._ensure_component(state, ShoppingList, "需要准备的食材",
                                          ShoppingData(items=self._shopping(state), notes=[PREP_NOTE]))
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
        if sum(timer.status in {"running", "paused"} for timer in state.timers) >= 12:
            raise DomainConflict("timer_limit", "同时计时已达上限，请结束已有计时器后再添加。")
        state.timers.append(TimerState(id=str(uuid4()), label=payload["label"], seconds=payload["seconds"],
            remaining_seconds=float(payload["seconds"]), deadline_at=_stamp(self._now() + timedelta(seconds=payload["seconds"])),
            recipe_id=recipe_id, step_id=step_id, basis=basis))
        state.timers = state.timers[-30:]
        return self._show(state, Timers, "厨房计时", TimersData(timers=state.timers))

    def execute_tool(self, name, request=None, *, approved_recipes=(), operation_id=None, user_text=None):
        model = TOOL_REQUEST_MODELS.get(name)
        if model is None:
            raise ValueError("Unknown workflow tool")
        validated = model.model_validate(request or {})
        args = validated.model_dump(mode="json", exclude_none=False)
        if name == "get_cooking_workspace":
            return self.get_workspace()
        initial = self._state(self._get_state())
        base_revision = args.pop("base_revision")
        base_revision = initial.revision if base_revision is None else base_revision
        if operation_id:
            prior = next((record for record in initial.recent_actions if record.action_id == operation_id), None)
            fingerprint = hashlib.sha256(_dump({"type": name, "payload": args}).encode()).hexdigest()
            if prior:
                if prior.fingerprint != fingerprint:
                    return self._response(initial, "action_id_conflict", "同一操作标识已用于不同内容，请重新确认。")
                return self._response(initial, "ok", "此操作已经执行，没有重复读取源菜谱或修改状态。")
        frozen = None
        try:
            if name == "set_cooking_timer" and validated.basis == "user" and validated.seconds not in _explicit_durations(user_text):
                raise DomainConflict("unverified_duration", "用户没有明确提供这个时长，请让用户填写计时器或使用源步骤时长。")
            if name in {"show_recipe_choices", "set_meal_plan"}:
                frozen = self._freeze(validated.recipe_ids, validated.data_snapshot, approved_recipes)
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
                return self._plan(state, frozen, validated.servings)
            if name == "show_recipe_details":
                if not any(recipe.recipe_id == frozen[0].recipe_id and recipe.data_snapshot == frozen[0].data_snapshot
                           for recipe in [*state.meal_plan, *state.candidates]):
                    state.candidates = [recipe for recipe in state.candidates if recipe.recipe_id != frozen[0].recipe_id]
                    state.candidates.append(frozen[0])
                return self._show(state, RecipeDetail, "菜谱详情", RecipeDetailData(recipe=self._recipe_view(frozen[0], state)))
            if name == "show_meal_plan":
                return self._show(state, MealPlan, "这次的餐单", RecipeListData(
                    recipes=[self._recipe_view(recipe, state, summary=True) for recipe in state.meal_plan]))
            if name == "show_shopping_list":
                return self._show(state, ShoppingList, "需要准备的食材", ShoppingData(
                    items=self._shopping(state, validated.recipe_ids), recipe_ids=validated.recipe_ids, notes=[PREP_NOTE]))
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
                if state.stage not in {"planning", "shopping", "prep", "cooking"}:
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
        result = self._command(operation_id or str(uuid4()), name, args, base_revision, command)
        if validated.display == "none":
            result["actions"] = []
        else:
            for action in result["actions"]:
                action["placement"] = validated.display
        return result

    def apply_action(self, request):
        value = UserActionRequest.model_validate(request.model_dump(exclude_unset=True)
                                                if isinstance(request, UserActionRequest) else request).root
        payload = value.payload.model_dump(mode="json", exclude_unset=True)
        def command(state):
            kind = value.type
            if kind == "update_preferences":
                updated = {**state.preferences.model_dump(), **payload["values"]}
                state.preferences = PreferenceValues.model_validate(updated)
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
                if state.stage not in {"planning", "shopping", "prep"}:
                    raise DomainConflict("stage_conflict", "请先回到备料或选菜阶段调整份量。")
                recipe = self._find_recipe(state, payload["recipe_id"])
                recipe.target_servings = payload["servings"]
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
                state.shopping_checked = []
            elif kind == "set_checklist_scope":
                component = next((view for view in state.components if view.id == value.view_id
                                  and view.kind in {"shopping_list", "prep_checklist"}), None)
                if component is None:
                    raise DomainConflict("invalid_reference", "请在当前食材清单上选择菜品。")
                self._prep_items(state, payload.get("recipe_ids"))
                component.data.recipe_ids = payload.get("recipe_ids")
                return component
            elif kind == "check_shopping_item":
                if state.stage not in {"planning", "shopping", "prep"}:
                    raise DomainConflict("stage_conflict", "当前阶段不能修改购物勾选。")
                origin = next((view for view in state.components if view.id == value.view_id
                               and view.kind == "shopping_list"), None)
                scope = origin.data.recipe_ids if origin else None
                item = next((item for item in self._shopping(state, scope) if item.id == payload["item_id"]), None)
                if item is None:
                    raise DomainConflict("invalid_reference", "清单条目已变化，请复核当前清单。")
                selected = set(state.shopping_checked)
                # Expand old aggregate confirmations once, retaining existing purchase facts across scope changes.
                for previous in self._shopping(state):
                    if previous.checked:
                        selected.update(previous.source_item_ids)
                selected = {identity for identity in selected if not identity.startswith("shopping:")}
                if payload["checked"]:
                    selected.update(item.source_item_ids)
                else:
                    selected.difference_update(item.source_item_ids)
                state.shopping_checked = sorted(selected)
            elif kind == "check_prep_item":
                if state.stage not in {"shopping", "prep"}:
                    raise DomainConflict("stage_conflict", "只有备料阶段可以确认准备状态。")
                origin = next((view for view in state.components if view.id == value.view_id
                               and view.kind == "prep_checklist"), None)
                scope = origin.data.recipe_ids if origin else None
                if payload["item_id"] not in {item.id for item in self._prep_items(state, scope)}:
                    raise DomainConflict("invalid_reference", "食材不属于本次餐单。")
                state.prep_status[payload["item_id"]] = payload["status"]
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
                self._activate_step(state, payload["recipe_id"], payload["step_id"])
                completed = value.payload.completed
                state.step_status[payload["step_id"]] = "done" if completed else "current"
                recipe = self._find_recipe(state, payload["recipe_id"])
                if completed:
                    next_step = next((step for step in self._recipe_view(recipe, state).steps if step.status != "done"), None)
                    state.active_step_id = next_step.id if next_step else None
                    if next_step:
                        state.step_status[next_step.id] = "current"
            elif kind == "change_stage":
                return self._set_stage(state, payload["stage"])
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
                else:
                    timer.status, timer.deadline_at = "cancelled", None
            return None
        try:
            return self._command(value.action_id, value.type, payload, value.base_revision, command, value.view_id)
        except ValidationError:
            # Partial preference updates that violate final values never replace the persisted dictionary.
            return self._response(self._state(self._get_state()), "invalid_action", "更新后的值不符合界面字段要求。")
