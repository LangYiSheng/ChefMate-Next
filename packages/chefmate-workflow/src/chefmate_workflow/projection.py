"""Projects frozen recipes and workspace state into typed views and compact model snapshots."""

import copy
import math
from uuid import uuid4

from chefmate_storage.queries import NUTRIENTS

from .models import (
    CompactComponentSnapshot,
    CookingData,
    IngredientView,
    PreferenceField,
    PreferenceFormData,
    PrepData,
    ProgressData,
    RecipeDetailData,
    RecipeListData,
    RecipeView,
    SourceInfo,
    StepView,
    TimersData,
    KitchenDisplayAction,
    ShowComponentAction,
    UISnapshot,
)
from .support import PREP_NOTE, DomainConflict, _amount, _explicit_durations, _finite, _ingredient_id, _stamp, _step_id


class WorkspaceProjection:
    """Internal part of WorkflowService; persistence and revision checks stay in service.py."""

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

    @staticmethod
    def _portion_note(state):
        if state.meal_diners is None:
            return "尚未确认整餐用餐人数；保留菜谱原量，可按实际食量调整。"
        if state.portion_mode == "per_dish":
            return f"整餐{state.meal_diners:g}人；按明确要求，每道菜分别准备足量，单菜仍可调整。"
        count = len(state.meal_plan)
        return (f"{state.meal_diners:g}人共享{count}道菜，已按一桌搭配安排菜量。"
                "可根据主食、食量和实际食材调整每盘份量。")

    def _meal_data(self, state):
        return RecipeListData(recipes=[self._recipe_view(recipe, state, summary=True) for recipe in state.meal_plan],
                              meal_diners=state.meal_diners, portion_mode=state.portion_mode,
                              portion_note=self._portion_note(state))

    def _component_data(self, component, state):
        kind = component.kind
        if kind == "preference_form":
            values = state.preferences.model_dump()
            return PreferenceFormData(values=state.preferences, fields=[PreferenceField(
                name=field.name, label=field.label, input_type=field.input_type, value=values[field.name],
                options=field.options) for field in component.data.fields])
        if kind in {"meal_plan", "recipe_choices"}:
            if kind == "meal_plan":
                return self._meal_data(state)
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
                               guidance=[note.instruction for note in state.adaptations if note.recipe_id == identity and note.step_id is None],
                               timers=self._step_timers(state, identity),
                               server_now=_stamp(self._now()))
        if kind == "timers":
            return TimersData(timers=state.timers, server_now=_stamp(self._now()))
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
            views.append(component.model_copy(update={"data": data, "revision": state.revision,
                "presentation_revision": state.presentation_revision, "editable": editable}))
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
                if view.kind == "meal_plan":
                    data.update(meal_diners=state.meal_diners, portion_mode=state.portion_mode,
                                portion_note=self._portion_note(state))
            elif view.kind == "recipe_detail":
                recipe = raw["recipe"]
                data = {"recipe_id": recipe["recipe_id"], "name": recipe["name"], "source": recipe["source"]}
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
                data["timers"] = raw["timers"]
                data["server_now"] = raw["server_now"]
            else:
                data = raw
            components.append(CompactComponentSnapshot(id=view.id, kind=view.kind, title=view.title,
                revision=view.revision, presentation_revision=view.presentation_revision, editable=view.editable, data=data))
        recent = []
        for record in state.recent_actions[-20:]:
            payload = copy.deepcopy(record.payload)
            if record.type == "update_preferences":
                # Current full preference values are authoritative below; history only needs which fields changed.
                payload = {"changed_fields": sorted((payload.get("values") or {}).keys())}
            recent.append(record.model_copy(update={"payload": payload}))
        allowed, blockers = self._transitions(state)
        settings = self.cooking_settings()
        return UISnapshot(revision=state.revision, presentation_revision=state.presentation_revision,
                          stage=state.stage, components=components,
                          preferences=state.preferences, selected_recipe_ids=[recipe.recipe_id for recipe in state.meal_plan],
                          meal_diners=state.meal_diners, portion_mode=state.portion_mode,
                          portion_note=self._portion_note(state) if state.meal_plan else "",
                          active_recipe_id=state.active_recipe_id, active_step_id=state.active_step_id,
                          recent_actions=recent, pending_question=state.pending_question, last_answer=state.last_answer,
                          current_actions=self._current_actions(state, allowed), allowed_transitions=allowed, blockers=blockers,
                          cooking_settings=settings, timer_capacity=self._timer_capacity(state, settings))

    def _response(self, state, status="ok", message=None, action_component=None):
        views = self._views(state)
        snapshot = self._compact(state, views).model_dump(mode="json")
        actions = []
        if isinstance(action_component, KitchenDisplayAction):
            actions = [action_component.model_dump(mode="json")]
        elif action_component is not None:
            current = next((view for view in views if view.id == action_component.id), None)
            if current:
                actions = [ShowComponentAction(component=current).model_dump(mode="json")]
        result = {"status": status, "workspace": snapshot, "views": [view.model_dump(mode="json") for view in views],
                  "ui_snapshot": snapshot, "actions": actions,
                  "pending_question": snapshot["pending_question"], "last_answer": snapshot["last_answer"]}
        if message is not None:
            result["message"] = message
        return result

    def get_workspace(self):
        return self._response(self._state(self._get_state()))

    def model_snapshot(self):
        return self.get_workspace()["ui_snapshot"]

    def meal_record(self):
        """Return a source-marked meal snapshot for a host's independent food journal.

        Historic workspaces without observed start times are deliberately excluded.
        Reading a journal must never invent when an older meal was cooked.
        """
        state = self._state(self._get_state())
        if not state.meal_id or not state.started_at or not state.meal_plan:
            return None
        return {"meal_id": state.meal_id, "started_at": state.started_at,
                "completed_at": state.completed_at, "status": "completed" if state.completed_at else "attempted",
                "diners": state.meal_diners, "portion_mode": state.portion_mode,
                "portion_note": self._portion_note(state),
                "recipes": [self._recipe_view(recipe, state).model_dump(mode="json") for recipe in state.meal_plan]}

    def _show(self, state, component_class, title, data):
        component = component_class(id=str(uuid4()), title=title, revision=state.revision,
                                    presentation_revision=state.presentation_revision + 1, data=data)
        state.components = [view for view in state.components if view.kind != component.kind]
        state.components.append(component)
        return component

    def _ensure_component(self, state, component_class, title, data):
        kind = component_class.model_fields["kind"].default
        for index, component in enumerate(state.components):
            if component.kind == kind:
                refreshed = component.model_copy(update={"title": title, "data": data,
                    "revision": state.revision, "presentation_revision": state.presentation_revision + 1})
                state.components[index] = refreshed
                return refreshed
        return self._show(state, component_class, title, data)
