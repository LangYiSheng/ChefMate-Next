"""Publication receipts bind one reviewed draft revision to a host-saved answer."""

import json
from uuid import uuid4
from chefmate_recipe_processing import validate_recipe
from chefmate_workflow import WorkspaceState

from .sessions import now


class RecipePublicationRepository:
    def __init__(self, cookbook):
        self.cookbook = cookbook
        with cookbook._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS recipe_publish_confirmations (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, recipe_id TEXT NOT NULL,
                recipe_revision INTEGER NOT NULL, question_id TEXT NOT NULL, origin_turn_id TEXT NOT NULL,
                state TEXT NOT NULL, created_at TEXT NOT NULL)""")

    @staticmethod
    def discard_question(repository, session_id, question_id):
        """Withdraw only the question created for a receipt that could not be prepared."""
        def discard(state):
            raw = state.get("workspace") or {}
            if (raw.get("pending_question") or {}).get("id") != question_id:
                return None
            workspace = WorkspaceState.model_validate(raw)
            workspace.pending_question = None
            workspace.revision += 1
            state["workspace"] = workspace.model_dump(mode="json")
            state["updated_at"] = now()
            return workspace.revision
        try:
            return repository.mutate(session_id, discard)
        except KeyError:
            return None  # Conversation deletion already withdrew its question.

    @staticmethod
    def _state(db, session_id, turn_id, cancelled):
        row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
        state = json.loads(row[0]) if row else {}
        if (cancelled is not None and cancelled.is_set() or not state.get("running")
                or (state.get("active_turn") or {}).get("id") != turn_id):
            raise ValueError("本轮已经结束，没有确认保存菜谱")
        return state

    def prepare(self, recipe_id, revision, *, session_id, question_id, active_turn_id, cancelled=None):
        identity = str(uuid4())
        with self.cookbook._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._state(db, session_id, active_turn_id, cancelled)
            if ((state.get("workspace") or {}).get("pending_question") or {}).get("id") != question_id:
                raise ValueError("确认问题已变化，没有准备发布菜谱")
            record = self.cookbook._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
            if not record or record["status"] != "draft" or record["revision"] != revision:
                raise ValueError("草稿已变化，请重新读取后核对")
            db.execute("UPDATE recipe_publish_confirmations SET state='superseded' WHERE session_id=? AND state='pending'",
                       (session_id,))
            db.execute("INSERT INTO recipe_publish_confirmations VALUES(?,?,?,?,?,?,'pending',?)",
                       (identity, session_id, recipe_id, revision, question_id, active_turn_id, now()))
            if cancelled is not None and cancelled.is_set():
                raise ValueError("本轮已经结束，没有准备发布菜谱")
        return identity

    def confirm(self, identity, *, session_id, active_turn_id, cancelled=None):
        with self.cookbook._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT session_id,recipe_id,recipe_revision,question_id,origin_turn_id,state "
                             "FROM recipe_publish_confirmations WHERE id=?", (identity,)).fetchone()
            if not row or row[0] != session_id:
                return {"status": "confirmation_required", "message": "没有当前对话的菜谱保存确认。"}
            _, recipe_id, revision, question_id, origin_turn, receipt_state = row
            try:
                state = self._state(db, session_id, active_turn_id, cancelled)
            except ValueError:
                db.execute("UPDATE recipe_publish_confirmations SET state='cancelled' WHERE id=? AND state='pending'", (identity,))
                return {"status": "turn_closed", "message": "本轮已结束，没有保存菜谱。"}
            record = self.cookbook._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
            if receipt_state == "used":
                if record and record["status"] == "saved" and record["revision"] == revision + 1:
                    return {"status": "ok", "published": True, "draft": record}
                return {"status": "confirmation_expired", "message": "这次确认已处理，菜谱已经变化。"}
            if receipt_state != "pending":
                return {"status": "confirmation_expired", "message": "这次确认已经失效，没有保存菜谱。"}
            workspace = state.get("workspace") or {}
            answer = workspace.get("last_answer") or {}
            if ((workspace.get("pending_question") or {}).get("id") == question_id
                    or answer.get("question_id") != question_id):
                return {"status": "confirmation_required", "message": "请先由用户回答这份草稿的保存确认，不能代替用户确认。"}
            if not any(message.get("role") == "assistant" and message.get("turn_id") == origin_turn
                       for message in state.get("messages", [])):
                db.execute("UPDATE recipe_publish_confirmations SET state='cancelled' WHERE id=?", (identity,))
                return {"status": "confirmation_expired", "message": "原确认轮次未完成，请重新核对并确认。"}
            if answer.get("option_ids") != ["publish"]:
                db.execute("UPDATE recipe_publish_confirmations SET state='cancelled' WHERE id=?", (identity,))
                return {"status": "cancelled", "published": False, "message": "保留为草稿，可以继续核对和修改。"}
            if not record or record["status"] != "draft" or record["revision"] != revision:
                db.execute("UPDATE recipe_publish_confirmations SET state='superseded' WHERE id=?", (identity,))
                return {"status": "recipe_changed", "message": "草稿在确认期间已修改，需重新审核新版本；没有发布。"}
            try:
                validate_recipe(record["document"])
            except ValueError:
                return {"status": "invalid_recipe", "message": "当前草稿内容校验未通过，请重新核对，没有发布。"}
            db.execute("UPDATE personal_recipes SET status='saved',revision=revision+1,updated_at=? WHERE id=?", (now(), recipe_id))
            db.execute("UPDATE recipe_publish_confirmations SET state='used' WHERE id=?", (identity,))
            if cancelled is not None and cancelled.is_set():
                db.rollback()
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE recipe_publish_confirmations SET state='cancelled' WHERE id=? AND state='pending'", (identity,))
                return {"status": "turn_closed", "message": "本轮已经停止，没有发布菜谱，请重新确认。"}
            saved = self.cookbook._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
            return {"status": "ok", "published": True, "draft": saved,
                    "message": "已按用户确认保存菜谱，现在可以读取并安排烹饪；未知信息保持未知。"}
