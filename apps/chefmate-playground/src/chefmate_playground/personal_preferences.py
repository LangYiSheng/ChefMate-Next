"""One consent and CAS boundary for personal preferences and long-term memory.

Online PostgreSQL updates the account and workspace in one transaction. SQLite
fixtures use an attached database with the same transaction boundary.
"""

from contextlib import contextmanager
import hashlib
import json
import secrets

from .accounts import AccountError
from .memory import MemoryConflict
from .sessions import now


class PersonalPreferencesService:
    def __init__(self, accounts, user_id, memory):
        self.accounts, self.user_id, self.memory = accounts, user_id, memory
        self.namespace = memory.database.schema if accounts.database.is_postgres else "preference_workspace"

    def _table(self, name):
        # Database validates schema names; table names are fixed internal literals.
        return f'"{self.namespace}"."{name}"'

    @contextmanager
    def transaction(self):
        with self.accounts._connect() as db:
            if not self.accounts.database.is_postgres:
                db.execute("ATTACH DATABASE ? AS preference_workspace", (str(self.memory.path),))
            db.execute("BEGIN IMMEDIATE")
            if self.accounts.database.is_postgres:
                db.execute("SELECT pg_advisory_xact_lock(?)", (self.memory.database.lock_key,))
            yield db

    def _read(self, db):
        user = db.execute("SELECT * FROM accounts WHERE id=? AND disabled=0", (self.user_id,)).fetchone()
        if not user:
            raise AccountError(401, "请重新登录。")
        memory = db.execute(f"SELECT revision,enabled,user_notes,summary,updated_at,summary_source FROM {self._table('personal_memory')} WHERE id=1").fetchone()
        return user, dict(memory), json.loads(user["profile"])

    @staticmethod
    def _revision(user, memory, profile):
        return hashlib.sha256(json.dumps([user["id"], user["display_name"], profile,
            memory["revision"]], sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _snapshot(self, db, user, memory, profile):
        fields = {"cooking_level": profile.get("cooking_level", "beginner"),
                  "household_size": profile.get("household_size", 1),
                  "preferences": profile.get("preferences", [])}
        return {"revision": self._revision(user, memory, profile), "enabled": bool(memory["enabled"]),
                "user_notes": memory["user_notes"], "summary": memory["summary"],
                "updated_at": memory["updated_at"], **fields,
                "kitchen_profile": {"display_name": user["display_name"], **fields},
                "user": self.accounts._public(db, user)}

    def _projection(self, db, profile, enabled, *, preserve_clear=False):
        # Nicknames belong to the independent identity context. Clearing memory
        # or changing a nickname cannot silently restore removed preferences.
        data = {key: profile[key] for key in ("cooking_level", "household_size", "preferences") if key in profile} if enabled else {}
        body = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
        fingerprint = hashlib.sha256(json.dumps([profile.get("revision"), data],
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        previous = db.execute(f"SELECT source_revision,body FROM {self._table('kitchen_profile')} WHERE id=1").fetchone()
        if previous and previous[0] == fingerprint:
            return False
        if preserve_clear and previous and previous[1] == "{}":
            body = "{}"
        db.execute(f"INSERT INTO {self._table('kitchen_profile')} VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "source_revision=excluded.source_revision,body=excluded.body,updated_at=excluded.updated_at",
                   (fingerprint, body, now()))
        return True

    def sync(self):
        with self.transaction() as db:
            user, memory, profile = self._read(db)
            # Migration is deliberately conservative. A prior explicit opt-out
            # or disabled memory is never upgraded into new consent.
            migrating = "memory_enabled" not in profile
            enabled = bool(memory["enabled"]) and profile.get("use_for_ai", True) is True
            changed = profile.get("memory_enabled") is not enabled or profile.get("use_for_ai") is not enabled
            if changed:
                profile.update(memory_enabled=enabled, use_for_ai=enabled)
                profile.setdefault("revision", secrets.token_hex(16))
                db.execute("UPDATE accounts SET profile=? WHERE id=?", (json.dumps(profile, ensure_ascii=False), self.user_id))
            projected = self._projection(db, profile, enabled, preserve_clear=migrating and enabled)
            if bool(memory["enabled"]) != enabled or projected:
                db.execute(f"UPDATE {self._table('personal_memory')} SET enabled=?,revision=revision+1,updated_at=? WHERE id=1", (enabled, now()))
            user, memory, profile = self._read(db)
            return self._snapshot(db, user, memory, profile)

    def snapshot(self):
        with self.transaction() as db:
            user, memory, profile = self._read(db)
            return self._snapshot(db, user, memory, profile)

    def update(self, revision, changes, *, clear_summary=False):
        if not isinstance(revision, str) or len(revision) != 64:
            raise AccountError(422, "个人偏好版本无效。")
        allowed = {"enabled", "user_notes", "cooking_level", "household_size", "preferences"}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise AccountError(422, "个人偏好包含未知字段。")
        if "enabled" in changes and type(changes["enabled"]) is not bool:
            raise AccountError(422, "偏好和长期记忆开关必须为布尔值。")
        if "user_notes" in changes and (not isinstance(changes["user_notes"], str) or len(changes["user_notes"]) > 6000):
            raise AccountError(422, "个人偏好最多 6000 字。")
        if "cooking_level" in changes and (not isinstance(changes["cooking_level"], str) or changes["cooking_level"] not in {"beginner", "comfortable", "confident"}):
            raise AccountError(422, "请选择有效的下厨经验。")
        if "household_size" in changes and (type(changes["household_size"]) is not int or not 1 <= changes["household_size"] <= 20):
            raise AccountError(422, "用餐人数需为 1 至 20 人。")
        if "preferences" in changes and (not isinstance(changes["preferences"], list) or len(changes["preferences"]) > 12
                or any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 80 for item in changes["preferences"])):
            raise AccountError(422, "厨房偏好最多 12 项，每项不超过 80 字。")
        with self.transaction() as db:
            user, memory, profile = self._read(db)
            if revision != self._revision(user, memory, profile):
                raise AccountError(409, "个人偏好已在另一处更新，草稿保留，请重新读取后核对。")
            enabled = changes.get("enabled", bool(memory["enabled"]))
            if clear_summary:
                db.execute(f"UPDATE {self._table('personal_memory')} SET summary='',summary_source=NULL,revision=revision+1,updated_at=? WHERE id=1", (now(),))
            else:
                for key in ("cooking_level", "household_size", "preferences"):
                    if key in changes:
                        profile[key] = list(dict.fromkeys(item.strip() for item in changes[key])) if key == "preferences" else changes[key]
                profile.update(memory_enabled=enabled, use_for_ai=enabled, revision=secrets.token_hex(16))
                db.execute("UPDATE accounts SET profile=?,updated_at=? WHERE id=?", (json.dumps(profile, ensure_ascii=False), now(), self.user_id))
                db.execute(f"UPDATE {self._table('personal_memory')} SET enabled=?,user_notes=?,revision=revision+1,updated_at=? WHERE id=1",
                           (enabled, changes.get("user_notes", memory["user_notes"]).strip(), now()))
                self._projection(db, profile, enabled)
            user, memory, profile = self._read(db)
            return self._snapshot(db, user, memory, profile)

    def update_legacy(self, user_notes, expected_revision, *, enabled=None):
        current = self.snapshot()
        if self.memory.snapshot()["revision"] != expected_revision:
            raise MemoryConflict("记忆已更新，请重新读取。")
        changes = {"user_notes": user_notes}
        if enabled is not None:
            changes["enabled"] = enabled
        try:
            self.update(current["revision"], changes)
        except AccountError as error:
            if error.status_code == 409:
                raise MemoryConflict(error.detail) from None
            raise ValueError(error.detail) from None
        return self.memory.snapshot()


def update_display_name(accounts, user_id, display_name):
    if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 40 or any(ord(char) < 32 for char in display_name):
        raise AccountError(422, "称呼需为 1 至 40 个字符。")
    with accounts._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        result = db.execute("UPDATE accounts SET display_name=?,updated_at=? WHERE id=? AND disabled=0",
                            (display_name.strip(), now(), user_id))
        if not result.rowcount:
            raise AccountError(401, "请重新登录。")
    return accounts.get_user(user_id)
