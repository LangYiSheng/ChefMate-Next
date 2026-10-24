"""Personal recipe persistence and versioned retrieval.

Official recipes stay read-only in PostgreSQL. Personal records live in the
application database and survive conversation deletion. Input contracts and
agent tool behavior are defined separately from storage transactions.
"""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel
from chefmate_recipe_processing import calculate_nutrition, validate_recipe
from .sessions import now
from .recipe_drafts import (
    DraftIngredient as DraftIngredient,
    RecipeDraftRequest as RecipeDraftRequest,
    CustomSearchRequest as CustomSearchRequest,
    RecipeDeletionRequest as RecipeDeletionRequest,
    ConfirmRecipeDeletionRequest as ConfirmRecipeDeletionRequest,
    _document,
)
from .recipe_tools import make_recipe_tools as make_recipe_tools
from .recipe_provenance import metadata_evidence, metadata_time_evidence, require_storage_provenance


def _draft_nutrition(document, draft):
    nutrition = calculate_nutrition(document, {})
    if draft.metadata_provenance.servings and draft.metadata_provenance.servings.kind == "suggested":
        nutrition["estimated_servings"] = True
        nutrition["estimated"] = True
        nutrition["estimated_per_serving_fields"] = sorted(nutrition["total"])
        if nutrition["status"] == "complete":
            nutrition["status"] = "estimated"
    return nutrition


class CookbookRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS personal_recipes (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_session_id TEXT,
                operation_id TEXT UNIQUE, body TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS recipe_favorites (
                recipe_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS recipe_delete_confirmations (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, recipe_id TEXT NOT NULL,
                recipe_revision INTEGER NOT NULL, question_id TEXT NOT NULL, origin_turn_id TEXT NOT NULL,
                state TEXT NOT NULL, created_at TEXT NOT NULL)""")
        from .recipe_publication import RecipePublicationRepository
        self.publication = RecipePublicationRepository(self)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def _public(row):
        if not row:
            return None
        identity, status, revision, created, updated, session_id, _, body = row
        return {
            **json.loads(body),
            "recipe_id": identity,
            "status": status,
            "revision": revision,
            "created_at": created,
            "updated_at": updated,
            "source_session_id": session_id,
            "origin": "user",
            "is_custom": True,
        }

    def get(self, recipe_id, *, include_deleted=False):
        with self._connect() as db:
            record = self._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
            return record if record and (include_deleted or record["status"] != "deleted") else None

    def create_draft(
        self, values, *, session_id=None, image_ids=(), operation_id=None, guard=None, active_turn_id=None,
        provenance_context=None
    ):
        draft = RecipeDraftRequest.model_validate(values)
        if draft.source_kind == "image" and not image_ids:
            raise ValueError("本轮没有图片来源，不能标记为图片提取")
        document = _document(draft)
        # Nutrition belongs to the existing deterministic processor, never model-authored numbers.
        nutrition = _draft_nutrition(document, draft)
        body = {
            "document": document.model_dump(mode="json"),
            "nutrition": nutrition,
            "source_text": draft.source_text,
            "source_kind": draft.source_kind,
            "source_image_ids": list(image_ids),
            "time_evidence": metadata_time_evidence(draft),
            "difficulty_evidence": metadata_evidence(draft, "difficulty", draft.difficulty_evidence),
            "metadata_provenance": draft.metadata_provenance.model_dump(exclude_none=True),
            "source_metadata_evidence": {"servings": draft.servings_evidence, "difficulty": draft.difficulty_evidence,
                                         "time": draft.time_evidence},
        }
        stamp = now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != active_turn_id:
                    raise ValueError("本轮已经结束，没有保存草稿")
            if operation_id:
                existing = db.execute("SELECT * FROM personal_recipes WHERE operation_id=?", (operation_id,)).fetchone()
                if existing:
                    if json.loads(existing[-1]) != body or existing[5] != session_id:
                        raise ValueError("同一操作标识已用于其他草稿内容，请读取已有草稿后核对")
                    return self._public(existing)
            identity = "custom-" + str(uuid4())
            require_storage_provenance(draft, context=provenance_context)
            if guard:
                guard()
            db.execute(
                "INSERT INTO personal_recipes VALUES(?,?,?,?,?,?,?,?)",
                (
                    identity,
                    "draft",
                    1,
                    stamp,
                    stamp,
                    session_id,
                    operation_id,
                    json.dumps(body, ensure_ascii=False, allow_nan=False),
                ),
            )
            if guard:
                guard()
        return self.get(identity)

    def update_draft(self, recipe_id, values, revision, *, guard=None, session_id=None, active_turn_id=None,
                     provenance_context=None, metadata_fields=None):
        draft = RecipeDraftRequest.model_validate(values)
        document = _document(draft)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != active_turn_id:
                    raise ValueError("本轮已经结束，没有修改草稿")
            row = db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone()
            record = self._public(row)
            if not record or record["status"] != "draft" or record["revision"] != revision:
                raise ValueError("草稿已更新或已保存，请刷新后核对")
            require_storage_provenance(draft, record, provenance_context)
            old = json.loads(row[-1])
            if draft.source_kind != old["source_kind"]:
                raise ValueError("请保留原始来源类型")
            if metadata_fields is not None:
                if not set(metadata_fields) <= {"servings", "difficulty", "total_time_min"}:
                    raise ValueError("元数据patch不能修改食材、步骤或分项时间")
                preserved = dict(old["document"])
                rebuilt = document.model_dump(mode="json")
                for field in metadata_fields:
                    preserved[field] = rebuilt[field]
                if "servings" in metadata_fields:
                    for field in ("servings_evidence", "original_servings"):
                        preserved[field] = rebuilt[field]
                preserved["warnings"] = rebuilt["warnings"]
                document = validate_recipe(preserved)
            old.update(
                document=document.model_dump(mode="json"),
                nutrition=_draft_nutrition(document, draft),
                source_text=draft.source_text,
                time_evidence=metadata_time_evidence(draft),
                difficulty_evidence=metadata_evidence(draft, "difficulty", draft.difficulty_evidence),
                metadata_provenance=draft.metadata_provenance.model_dump(exclude_none=True),
                source_metadata_evidence={"servings": draft.servings_evidence, "difficulty": draft.difficulty_evidence,
                                          "time": draft.time_evidence},
            )
            if guard:
                guard()
            db.execute(
                "UPDATE personal_recipes SET body=?,revision=revision+1,updated_at=? WHERE id=?",
                (json.dumps(old, ensure_ascii=False, allow_nan=False), now(), recipe_id),
            )
            if guard:
                guard()
        return self.get(recipe_id)

    def publish(self, recipe_id, revision):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
            if not record:
                raise KeyError(recipe_id)
            if record["status"] == "saved" and revision == record["revision"] - 1:
                return record  # A repeated confirmation after an uncertain response is idempotent.
            if record["revision"] != revision or record["status"] != "draft":
                raise ValueError("草稿版本已变化，请刷新后核对")
            validate_recipe(record["document"])
            db.execute(
                "UPDATE personal_recipes SET status='saved',revision=revision+1,updated_at=? WHERE id=?",
                (now(), recipe_id),
            )
        return self.get(recipe_id)

    def list(self, *, query="", status="saved", limit=20, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000 or status not in {"saved", "draft", "all", "deleted"}:
            raise ValueError("菜谱列表参数无效")
        with self._connect() as db:
            records = [
                self._public(row) for row in db.execute("SELECT * FROM personal_recipes ORDER BY created_at DESC,id")
            ]
        terms = query.casefold().strip()
        records = [
            item
            for item in records
            if (item["status"] in {"saved", "draft"} if status == "all" else item["status"] == status)
            and (not terms or terms in json.dumps(item["document"], ensure_ascii=False).casefold())
        ]
        return {"total": len(records), "items": records[offset : offset + limit], "offset": offset, "limit": limit}

    @staticmethod
    def _delete(db, recipe_id, revision):
        record = CookbookRepository._public(
            db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone()
        )
        if not record:
            raise KeyError(recipe_id)
        if record["status"] == "deleted" and record["revision"] == revision + 1:
            return record
        if record["revision"] != revision or record["status"] not in {"draft", "saved"}:
            raise ValueError("菜谱已更新或已删除，请刷新后核对")
        body = json.loads(db.execute("SELECT body FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone()[0])
        body.update(deleted_from=record["status"], deleted_at=now())
        db.execute(
            "UPDATE personal_recipes SET status='deleted',revision=revision+1,updated_at=?,body=? WHERE id=?",
            (now(), json.dumps(body, ensure_ascii=False, allow_nan=False), recipe_id),
        )
        db.execute("DELETE FROM recipe_favorites WHERE recipe_id=?", (recipe_id,))
        db.execute(
            "UPDATE recipe_delete_confirmations SET state='superseded' WHERE recipe_id=? AND state='pending'",
            (recipe_id,),
        )
        return CookbookRepository._public(
            db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone()
        )

    def delete(self, recipe_id, revision):
        """Hide a personal recipe atomically; frozen cooking tasks remain untouched."""
        if type(revision) is not int or revision < 1:
            raise ValueError("菜谱版本无效")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._delete(db, recipe_id, revision)

    def restore(self, recipe_id, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError("菜谱版本无效")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
            if not record:
                raise KeyError(recipe_id)
            if record["status"] != "deleted" or record["revision"] != revision:
                raise ValueError("菜谱状态已变化，请刷新后核对")
            body = json.loads(db.execute("SELECT body FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone()[0])
            restored_status = body.pop("deleted_from")
            body.pop("deleted_at", None)
            db.execute(
                "UPDATE personal_recipes SET status=?,revision=revision+1,updated_at=?,body=? WHERE id=?",
                (restored_status, now(), json.dumps(body, ensure_ascii=False, allow_nan=False), recipe_id),
            )
        return self.get(recipe_id)

    def prepare_deletion(self, recipe_id, revision, *, session_id, question_id, active_turn_id, cancelled=None):
        """Bind an already committed host question to exactly one recipe revision."""
        identity = str(uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            state = json.loads(row[0]) if row else {}
            if (
                cancelled is not None
                and cancelled.is_set()
                or not state.get("running")
                or (state.get("active_turn") or {}).get("id") != active_turn_id
            ):
                raise ValueError("本轮已经停止，没有准备删除操作")
            pending = (state.get("workspace") or {}).get("pending_question") or {}
            if pending.get("id") != question_id:
                raise ValueError("删除问题已变化，请重新发起确认")
            record = self._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
            if not record or record["status"] not in {"draft", "saved"} or record["revision"] != revision:
                raise ValueError("菜谱状态已变化，请刷新后重新确认")
            db.execute(
                "UPDATE recipe_delete_confirmations SET state='superseded' WHERE session_id=? AND state='pending'",
                (session_id,),
            )
            db.execute(
                "INSERT INTO recipe_delete_confirmations VALUES(?,?,?,?,?,?,'pending',?)",
                (identity, session_id, recipe_id, revision, question_id, active_turn_id, now()),
            )
            if cancelled is not None and cancelled.is_set():
                raise ValueError("本轮已经停止，没有准备删除操作")
        return identity

    def confirm_deletion(self, confirmation_id, *, session_id, active_turn_id, cancelled=None):
        """Only the host-persisted answer can authorize a model-requested deletion."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            confirmation = db.execute(
                "SELECT session_id,recipe_id,recipe_revision,question_id,origin_turn_id,state FROM recipe_delete_confirmations WHERE id=?",
                (confirmation_id,),
            ).fetchone()
            if not confirmation or confirmation[0] != session_id:
                return {"status": "confirmation_required", "message": "没有这段对话的删除确认，请先发起确认。"}
            _, recipe_id, revision, question_id, origin_turn_id, confirmation_state = confirmation
            row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            state = json.loads(row[0]) if row else {}
            if (
                cancelled is not None
                and cancelled.is_set()
                or not state.get("running")
                or (state.get("active_turn") or {}).get("id") != active_turn_id
            ):
                db.execute(
                    "UPDATE recipe_delete_confirmations SET state='cancelled' WHERE id=? AND state='pending'",
                    (confirmation_id,),
                )
                return {"status": "turn_closed", "message": "本轮已经停止，没有删除菜谱。"}
            workspace = state.get("workspace") or {}
            answer = workspace.get("last_answer") or {}
            if confirmation_state == "used":
                record = self._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
                if record and record["status"] == "deleted" and record["revision"] == revision + 1:
                    return {
                        "status": "ok",
                        "recipe_id": recipe_id,
                        "deleted": True,
                        "revision": record["revision"],
                        "message": "已移到最近删除；已保存的烹饪任务和饮食记录不受影响，可在菜谱中心恢复。",
                    }
                return {"status": "confirmation_expired", "message": "这次确认已处理，菜谱状态已变化；没有再次删除。"}
            if confirmation_state != "pending":
                return {"status": "confirmation_expired", "message": "这次确认已失效，没有删除菜谱。"}
            if (workspace.get("pending_question") or {}).get("id") == question_id or answer.get(
                "question_id"
            ) != question_id:
                return {"status": "confirmation_required", "message": "请先由用户回答当前删除确认，不能代替用户确认。"}
            # Cancelled/interrupted questions can remain visible, but only a completed
            # question turn can issue a usable receipt. User text cannot forge this log.
            if not any(
                message.get("role") == "assistant" and message.get("turn_id") == origin_turn_id
                for message in state.get("messages", [])
            ):
                db.execute("UPDATE recipe_delete_confirmations SET state='cancelled' WHERE id=?", (confirmation_id,))
                return {"status": "confirmation_expired", "message": "原来的确认轮次没有完成，请重新发起删除确认。"}
            if answer.get("option_ids") != ["delete"]:
                db.execute("UPDATE recipe_delete_confirmations SET state='cancelled' WHERE id=?", (confirmation_id,))
                return {"status": "cancelled", "deleted": False, "message": "已保留这份菜谱，没有删除。"}
            record = self._public(db.execute("SELECT * FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone())
            if not record or record["status"] not in {"draft", "saved"} or record["revision"] != revision:
                db.execute("UPDATE recipe_delete_confirmations SET state='superseded' WHERE id=?", (confirmation_id,))
                return {"status": "recipe_changed", "message": "菜谱在确认期间已更新，请重新确认；没有删除。"}
            result = self._delete(db, recipe_id, revision)
            db.execute("UPDATE recipe_delete_confirmations SET state='used' WHERE id=?", (confirmation_id,))
            if cancelled is not None and cancelled.is_set():
                # Undo both recipe/favorite changes, then durably revoke the receipt.
                db.rollback()
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "UPDATE recipe_delete_confirmations SET state='cancelled' WHERE id=? AND state='pending'",
                    (confirmation_id,),
                )
                return {"status": "turn_closed", "message": "本轮已经停止，没有删除菜谱；请重新发起确认。"}
            return {
                "status": "ok",
                "recipe_id": recipe_id,
                "deleted": True,
                "revision": result["revision"],
                "message": "已移到最近删除；已保存的烹饪任务和饮食记录不受影响，可在菜谱中心恢复。",
            }

    def favorite(self, recipe_id, enabled):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if enabled:
                if recipe_id.startswith("custom-"):
                    record = db.execute("SELECT status FROM personal_recipes WHERE id=?", (recipe_id,)).fetchone()
                    if not record or record[0] != "saved":
                        raise ValueError("个人菜谱尚未保存或已删除，不能收藏")
                db.execute("INSERT OR IGNORE INTO recipe_favorites VALUES(?,?)", (recipe_id, now()))
            else:
                db.execute("DELETE FROM recipe_favorites WHERE recipe_id=?", (recipe_id,))
        return {"recipe_id": recipe_id, "favorite": enabled}

    def favorites(self):
        with self._connect() as db:
            return [row[0] for row in db.execute("SELECT recipe_id FROM recipe_favorites ORDER BY created_at DESC")]

    @staticmethod
    def snapshot(record):
        return {"data_version": f"{record['recipe_id']}:v{record['revision']}", "imported_at": record["updated_at"]}

    @staticmethod
    def normalized(record):
        document = record["document"]
        recipe = {
            **document,
            "recipe_id": record["recipe_id"],
            "origin": "user",
            "is_custom": True,
            "source": "user",
            "estimated_servings": (record.get("metadata_provenance", {}).get("servings") or {}).get("kind") == "suggested",
            "metadata_provenance": record.get("metadata_provenance", {}),
            "ready_for_recommendation": True,
            "blocking_reasons": [],
            "nutrition": record["nutrition"],
        }
        recipe["ingredients"] = [
            {**item, "ordinal": index, "estimated_grams": False,
             "nutrition": {"estimated": False, "fdc_id": None, "estimated_nutrients": [],
                           "per_100g": {key: None for key in record["nutrition"].get("total", {})},
                           "contribution": {key: None for key in record["nutrition"].get("total", {})}}}
            for index, item in enumerate(document["ingredients"])
        ]
        recipe["allergens"] = sorted({a for item in document["ingredients"] for a in item["allergens"]})
        return recipe

    @staticmethod
    def summary(record):
        document = record["document"]
        return {"recipe_id": record["recipe_id"], "origin": "user", "status": record["status"],
                "revision": record["revision"], **{key: document.get(key) for key in
                ("name", "description", "servings", "total_time_min", "difficulty")},
                "ingredient_count": len(document["ingredients"]),
                **{key: record[key] for key in ("deleted_from", "deleted_at") if key in record}}

    def retrieve(self, recipe_id, expected_snapshot=None):
        record = self.get(recipe_id)
        if not record or record["status"] != "saved":
            return {"status": "not_found", "recipe": None}
        snapshot = self.snapshot(record)
        if expected_snapshot is not None and expected_snapshot != snapshot:
            return {"status": "snapshot_changed", "snapshot": snapshot, "message": "自定义菜谱已更新，请重新读取。"}
        return {
            "status": "ok",
            "snapshot": snapshot,
            "recipe": self.normalized(record),
            "notes": ["用户自定义菜谱；营养未知项保留未知，时间与人数请核对来源。"],
        }


class PersonalRecipeRetrieval:
    """Preserve the official retrieval contract and resolve personal recipe references."""

    def __init__(self, base, cookbook):
        self.base, self.cookbook = base, cookbook
        self.store = getattr(base, "store", None)
        self.foods = getattr(base, "foods", None)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def get_recipe(self, request):
        values = request.model_dump(mode="json") if isinstance(request, BaseModel) else request
        if values.get("recipe_id", "").startswith("custom-"):
            return self.cookbook.retrieve(values["recipe_id"], values.get("expected_snapshot"))
        return self.base.get_recipe(request)
