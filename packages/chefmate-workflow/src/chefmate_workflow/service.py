"""Atomic persistence boundary assembling kitchen commands and typed UI projections."""

import hashlib
from datetime import datetime, timezone

from .kitchen import KitchenCommands
from .models import (
    ActionRecord,
    CookingSettings,
    WorkspaceState,
)
from .projection import WorkspaceProjection
from .support import DomainConflict, _dump, _stamp
from .tool_commands import ToolCommands
from .user_actions import UserActions


class WorkflowService(WorkspaceProjection, KitchenCommands, ToolCommands, UserActions):
    """Caller owns persistence; mutate(fn) executes fn(workspace_dict) atomically.

    The callback replaces that dictionary in place only after successful validation.
    get_state and mutate must use the same session, and must not expose credentials.
    """

    def __init__(self, retrieval, get_state, mutate, *, now=None, cooking_settings=None):
        self.retrieval = retrieval
        self._get_state = get_state
        self._mutate = mutate
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._get_cooking_settings = cooking_settings

    def cooking_settings(self):
        value = self._get_cooking_settings() if callable(self._get_cooking_settings) else self._get_cooking_settings
        return CookingSettings.model_validate(value or {})

    def _state(self, raw):
        state = WorkspaceState.model_validate(raw or {})
        self._refresh_timers(state)
        self._prune_step_timer_history(state)
        return state

    @staticmethod
    def _business_state(state):
        # Timers' remaining_seconds/status are refreshed from real time on read, before this comparison.
        return state.model_dump(mode="json", exclude={"components", "recent_actions", "revision", "presentation_revision"})

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
            business_before = self._business_state(state)
            previous_step = state.active_recipe_id, state.active_step_id
            presentation_before = [view.model_dump(mode="json") for view in state.components]
            try:
                component = callback(state)
            except DomainConflict as error:
                return self._response(self._state(raw), error.status, str(error))
            if (self.cooking_settings().clear_timer_on_step_change and previous_step[1] is not None
                    and previous_step != (state.active_recipe_id, state.active_step_id)):
                state.timers = [timer for timer in state.timers
                    if (timer.recipe_id, timer.step_id) != previous_step]
            if self._business_state(state) != business_before:
                state.revision += 1
            if [view.model_dump(mode="json") for view in state.components] != presentation_before:
                state.presentation_revision += 1
            state.recent_actions.append(ActionRecord(action_id=operation_id, type=operation_type, payload=payload,
                revision=state.revision, created_at=_stamp(self._now()), fingerprint=fingerprint))
            state.recent_actions = state.recent_actions[-20:]
            # Revalidate complete state before replacing persisted workspace, keeping failed commands atomic.
            encoded = WorkspaceState.model_validate(state.model_dump(mode="json")).model_dump(mode="json")
            raw.clear()
            raw.update(encoded)
            return self._response(state, action_component=component)
        return self._mutate(mutate)
