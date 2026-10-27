"""Account boundary, revocable sessions, quota reservations, and immutable pricing.

Application data stays in a separate database namespace for each opaque user ID.
This database contains identity and content-free accounting metadata only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

from .database import Database
from .entitlements import EntitlementsLedger


COOKIE_NAME = "chefmate_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_TOKEN_LIMIT = 100_000
DEFAULT_COST_LIMIT_MICROS = 5_000_000
_USERNAME = re.compile(r"[a-zA-Z0-9_.-]{3,32}\Z")
_USER_ID = re.compile(r"[0-9a-f]{32}\Z")
_RECONCILIATION_JOIN = """ LEFT JOIN account_usage_reconciliations r ON r.user_id=u.user_id AND r.turn_id=u.turn_id
    AND r.revision=(SELECT MAX(h.revision) FROM account_usage_reconciliations h
                    WHERE h.user_id=u.user_id AND h.turn_id=u.turn_id) """


class AccountError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class QuotaExceeded(AccountError):
    def __init__(self, detail="可用额度不足，请联系管理员调整额度。"):
        super().__init__(402, detail)


def _timestamp(now=None):
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("Account timestamps require a timezone")
    return instant.astimezone(timezone.utc).isoformat()


def _username(value):
    if not isinstance(value, str):
        raise AccountError(422, "账号需为 3 至 32 位英文字母、数字、点、横线或下划线。")
    value = unicodedata.normalize("NFKC", value).strip()
    if not _USERNAME.fullmatch(value):
        raise AccountError(422, "账号需为 3 至 32 位英文字母、数字、点、横线或下划线。")
    return value.lower()


def _password(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 128 or len(value.encode("utf-8")) > 512:
        raise AccountError(422, "密码需为 10 至 128 位，建议混合字母、数字和符号。")
    if value.isspace():
        raise AccountError(422, "密码不能只包含空白。")
    return value


def hash_password(password):
    password = _password(password)
    salt = secrets.token_bytes(16)
    # OWASP's 16 MiB baseline uses p=5 for this N/r pair; the small memory
    # footprint also bounds concurrent login memory inside the Docker service.
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=5, dklen=32)
    return f"scrypt$16384$8$5${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or (n, r, p) not in {("16384", "8", "1"), ("16384", "8", "5")}:
            return False
        if not isinstance(password, str) or len(password) > 128 or len(password.encode("utf-8")) > 512:
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                                n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError, AttributeError):
        return False


def _limit(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 10**15):
        raise AccountError(422, "额度必须为非负整数，或留空表示不限额。")
    return value


def _price(value):
    if isinstance(value, bool):
        raise AccountError(422, "单价必须为有限的非负数字。")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise AccountError(422, "单价必须为有限的非负数字。") from None
    if not amount.is_finite() or not 0 <= amount <= 1_000_000:
        raise AccountError(422, "单价必须为有限的非负数字。")
    return format(amount.quantize(Decimal("0.000001")), "f")


class AccountsRepository:
    """Database transactions serialize identity changes and quota admission.

    ``reserve_usage`` accepts the caller's upper bound for an entire turn. It
    atomically refuses admission beyond the monetary budget. ``settle_usage`` replaces
    the reservation with observed provider tokens and its admission-time prices.
    Unknown usage is explicitly retained; it never becomes a measured zero.
    """

    def __init__(self, path: Path | str | Database, *, now=None,
                 default_cost_limit_micros=DEFAULT_COST_LIMIT_MICROS, default_token_limit=None):
        self.database = Database.coerce(path)
        self.path = self.database.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(timezone.utc))
        # The old token column and constructor keyword are compatibility data,
        # never an admission limit. Budgets are denominated in currency only.
        self.default_cost_limit_micros = _limit(default_cost_limit_micros)
        self._dummy_password = hash_password(secrets.token_urlsafe(32))
        self.entitlements = EntitlementsLedger(self)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, display_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','user')),
                    developer_allowed INTEGER NOT NULL DEFAULT 0,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    onboarding_completed INTEGER NOT NULL DEFAULT 0,
                    profile TEXT NOT NULL DEFAULT '{}', token_limit INTEGER,
                    cost_limit_micros INTEGER, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, auth_revision INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS account_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES accounts(id),
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS account_sessions_user ON account_sessions(user_id);
                CREATE TABLE IF NOT EXISTS account_rate_limits (
                    bucket TEXT NOT NULL, happened_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS account_rate_limits_bucket ON account_rate_limits(bucket,happened_at);
                CREATE TABLE IF NOT EXISTS account_settings (
                    name TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS account_reservations (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES accounts(id),
                    tokens INTEGER NOT NULL, cost_micros INTEGER NOT NULL,
                    pricing TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS account_reservations_user ON account_reservations(user_id);
                CREATE TABLE IF NOT EXISTS account_usage (
                    user_id TEXT NOT NULL REFERENCES accounts(id), turn_id TEXT NOT NULL,
                    session_id TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                    input_tokens INTEGER, output_tokens INTEGER, cached_input_tokens INTEGER,
                    total_tokens INTEGER, cost_micros INTEGER NOT NULL, incomplete INTEGER NOT NULL,
                    pricing TEXT NOT NULL, created_at TEXT NOT NULL,
                    estimated_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_micros INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id,turn_id));
                CREATE INDEX IF NOT EXISTS account_usage_created ON account_usage(created_at);
                CREATE TABLE IF NOT EXISTS account_usage_reconciliations (
                    user_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision>=1),
                    final_cost_micros INTEGER NOT NULL CHECK(final_cost_micros>=0),
                    reason TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES accounts(id),
                    created_at TEXT NOT NULL, PRIMARY KEY(user_id,turn_id,revision),
                    FOREIGN KEY(user_id,turn_id) REFERENCES account_usage(user_id,turn_id));
                CREATE TABLE IF NOT EXISTS account_audit (
                    id INTEGER PRIMARY KEY, actor_id TEXT, target_id TEXT,
                    action TEXT NOT NULL, fields TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS account_audit_recent ON account_audit(created_at DESC,id DESC);
                CREATE INDEX IF NOT EXISTS account_audit_actor ON account_audit(actor_id,created_at DESC,id DESC);
                CREATE INDEX IF NOT EXISTS account_audit_target ON account_audit(target_id,created_at DESC,id DESC);
            """.replace("id INTEGER PRIMARY KEY, actor_id", f"id {self.database.identity_primary_key}, actor_id"))
            self.entitlements.create_tables(db)
            if "revocation_reason" not in {column["name"] for column in self.database.table_columns("account_memberships", connection=db)}:
                db.execute("ALTER TABLE account_memberships ADD COLUMN revocation_reason TEXT")
            columns = {column["name"] for column in self.database.table_columns("account_usage", connection=db)}
            for column in ("estimated_tokens", "estimated_cost_micros"):
                if column not in columns:
                    db.execute(f"ALTER TABLE account_usage ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
            if "auth_revision" not in {column["name"] for column in self.database.table_columns("accounts", connection=db)}:
                db.execute("ALTER TABLE accounts ADD COLUMN auth_revision INTEGER NOT NULL DEFAULT 1")
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO account_settings VALUES('pricing',?)", (json.dumps({
                "currency": "CNY", "input_per_million": "0.000000", "output_per_million": "0.000000",
                "cached_input_per_million": "0.000000", "revision": 1, "configured": False}),))
            existing_policy = db.execute("SELECT body FROM account_settings WHERE name='policy'").fetchone()
            db.execute("INSERT OR IGNORE INTO account_settings VALUES('policy',?)", (json.dumps({
                "revision": 1, "registration_enabled": True,
                "default_cost_limit_micros": self.default_cost_limit_micros}),))
            pricing = json.loads(db.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])
            if "configured" not in pricing:
                pricing["configured"] = any(Decimal(pricing.get(key, "0")) > 0 for key in (
                    "input_per_million", "output_per_million", "cached_input_per_million"))
                db.execute("UPDATE account_settings SET body=? WHERE name='pricing'", (json.dumps(pricing),))
            policy = json.loads(db.execute("SELECT body FROM account_settings WHERE name='policy'").fetchone()[0])
            legacy_policy = "default_token_limit" in policy
            legacy_accounts = existing_policy is None and db.execute(
                "SELECT 1 FROM accounts WHERE role='user' AND token_limit IS NOT NULL AND cost_limit_micros IS NULL LIMIT 1").fetchone()
            if legacy_policy or legacy_accounts:
                legacy_token_limit = policy.pop("default_token_limit", None)
                migration_credit = policy.get("default_cost_limit_micros")
                if migration_credit is None:
                    migration_credit = self.default_cost_limit_micros if self.default_cost_limit_micros is not None else DEFAULT_COST_LIMIT_MICROS
                if policy.get("default_cost_limit_micros") is None and legacy_token_limit is not None:
                    policy["default_cost_limit_micros"] = migration_credit
                db.execute("UPDATE accounts SET cost_limit_micros=CASE WHEN token_limit<=0 THEN 0 ELSE ? END,updated_at=? WHERE role='user' "
                           "AND token_limit IS NOT NULL AND cost_limit_micros IS NULL", (migration_credit, self._time()))
                policy["revision"] += 1
                db.execute("UPDATE account_settings SET body=? WHERE name='policy'", (json.dumps(policy),))
                self._audit(db, None, None, "migrate_monetary_budgets", ["cost_limit_micros", "legacy_token_budget"], self._time())
            self.entitlements.migrate_existing(db)

    def _connect(self):
        return self.database.connect(row_factory=sqlite3.Row, timeout=15)

    def _time(self):
        return _timestamp(self._now())

    @staticmethod
    def _audit(db, actor_id, target_id, action, fields, instant):
        db.execute("INSERT INTO account_audit(actor_id,target_id,action,fields,created_at) VALUES(?,?,?,?,?)",
                   (actor_id, target_id, action, json.dumps(sorted(fields)), instant))

    @staticmethod
    def _clear_login_failures(db, username):
        bucket = hashlib.sha256(("login-user:" + username).encode("utf-8")).hexdigest()
        db.execute("DELETE FROM account_rate_limits WHERE bucket=?", (bucket,))

    def _quota(self, db, row):
        totals = db.execute("SELECT COALESCE(SUM(u.total_tokens),0) tokens,"
                            "COALESCE(SUM(COALESCE(r.final_cost_micros,u.cost_micros)),0) cost,"
                            "COALESCE(SUM(u.cost_micros),0) reported_cost,"
                            "COALESCE(SUM(u.estimated_tokens),0) estimated_tokens,"
                            "COALESCE(SUM(CASE WHEN r.revision IS NULL THEN u.estimated_cost_micros ELSE 0 END),0) estimated_cost,"
                            "COALESCE(SUM(CASE WHEN r.revision IS NULL THEN 0 ELSE 1 END),0) reconciled_turns,"
                            "COALESCE(SUM(u.incomplete),0) incomplete FROM account_usage u " + _RECONCILIATION_JOIN + " WHERE u.user_id=?",
                            (row["id"],)).fetchone()
        # Expiry ends permission to make further calls, but cannot erase an
        # admitted provider cost. Recovery converts these bounds into estimates.
        pending = db.execute("SELECT COALESCE(SUM(tokens),0) tokens,COALESCE(SUM(cost_micros),0) cost "
                             "FROM account_reservations WHERE user_id=?", (row["id"],)).fetchone()
        funds = self.entitlements.snapshot(db, row)
        balance = funds["available_micros"]
        return {"token_limit": None, "cost_limit_micros": funds["permanent"]["total_micros"], "currency": "CNY",
                "funds": funds,
                "balance_micros": balance, "remaining_cost_micros": balance,
                "used_tokens": totals["tokens"], "used_cost_micros": totals["cost"],
                "reported_cost_micros": totals["reported_cost"], "reconciled_turns": totals["reconciled_turns"],
                "estimated_tokens": totals["estimated_tokens"], "estimated_cost_micros": totals["estimated_cost"],
                "quota_used_tokens": totals["tokens"] + totals["estimated_tokens"],
                "quota_used_cost_micros": totals["cost"] + totals["estimated_cost"],
                "reserved_tokens": pending["tokens"], "reserved_cost_micros": pending["cost"],
                "incomplete_turns": totals["incomplete"]}

    def _public(self, db, row):
        if row is None:
            return None
        return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
                "role": row["role"], "developer_allowed": bool(row["developer_allowed"]),
                "disabled": bool(row["disabled"]), "onboarding_completed": bool(row["onboarding_completed"]),
                "profile": json.loads(row["profile"]), "quota": self._quota(db, row),
                "session_revision": row["auth_revision"],
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def get_user(self, user_id):
        with self._connect() as db:
            return self._public(db, db.execute("SELECT * FROM accounts WHERE id=?", (user_id,)).fetchone())

    def list_users(self):
        with self._connect() as db:
            return [self._public(db, row) for row in db.execute("SELECT * FROM accounts ORDER BY created_at,id")]

    def tenant_database(self, user_id):
        """Logical runtime locator used for tenant settings and image files."""
        if not isinstance(user_id, str) or not _USER_ID.fullmatch(user_id):
            raise AccountError(404, "用户不存在。")
        return self.path.parent / "users" / user_id / "sessions.db"

    def register(self, username, password):
        normalized, encoded = _username(username), hash_password(password)
        return self._create_user(normalized, encoded)

    def _create_user(self, username, encoded, *, admin=False):
        user_id, instant = secrets.token_hex(16), self._time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = self.policy(connection=db)
            if not admin and not policy["registration_enabled"]:
                raise AccountError(403, "当前暂停开放注册，请联系管理员。")
            try:
                db.execute("INSERT INTO accounts(id,username,password_hash,display_name,role,developer_allowed,disabled,"
                           "onboarding_completed,profile,token_limit,cost_limit_micros,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    user_id, username, encoded, username, "admin" if admin else "user", int(admin), 0, 0,
                    "{}", None,
                    None if admin else policy["default_cost_limit_micros"], instant, instant))
            except sqlite3.IntegrityError:
                raise AccountError(409, "这个账号已被注册，请换一个账号或登录。") from None
            self.entitlements.ensure_account(db, db.execute("SELECT * FROM accounts WHERE id=?", (user_id,)).fetchone())
            self._audit(db, user_id, user_id, "bootstrap_admin" if admin else "register", [], instant)
        return self.get_user(user_id)

    def bootstrap_admin(self, username, password):
        """Create the named administrator once; never reset existing credentials.

        Supplying an existing ordinary account is an error, not an elevation.
        Subsequent starts may keep the same environment without resetting its
        changed password. Existing administrator accounts remain authoritative.
        """
        normalized = _username(username)
        _password(password)
        with self._connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE username=?", (normalized,)).fetchone()
            if row:
                if row["role"] != "admin":
                    raise AccountError(409, "初始化管理员账号已被普通用户占用，请使用其他账号。")
                return self._public(db, row)
        return self._create_user(normalized, hash_password(password), admin=True)

    def check_rate_limit(self, bucket, *, count=10, window_seconds=900, record=True):
        """Hash identifiers before persistence; callers supply no raw credentials."""
        key = hashlib.sha256(bucket.encode("utf-8")).hexdigest()
        instant = self._now()
        cutoff = _timestamp(instant - timedelta(seconds=window_seconds))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM account_rate_limits WHERE happened_at<?", (
                _timestamp(instant - timedelta(days=1)),))
            attempts = db.execute("SELECT COUNT(*) FROM account_rate_limits WHERE bucket=? AND happened_at>?",
                                  (key, cutoff)).fetchone()[0]
            if attempts >= count:
                raise AccountError(429, "操作太频繁，请稍后再试。")
            if record:
                db.execute("INSERT INTO account_rate_limits VALUES(?,?)", (key, _timestamp(instant)))

    def login(self, username, password, *, client_key="local"):
        # An invalid account format follows the same path and error as a miss.
        try:
            normalized = _username(username)
        except AccountError:
            normalized = "<invalid>"
        self.check_rate_limit("login-ip:" + client_key, count=60)
        self.check_rate_limit("login-user:" + normalized, count=10)
        with self._connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE username=?", (normalized,)).fetchone()
        valid = verify_password(password, row["password_hash"] if row else self._dummy_password)
        if not row or not valid or row["disabled"]:
            raise AccountError(401, "账号或密码不正确，或账号暂不可用。")
        # Successful authentication clears this account's failures. IP admission
        # remains bounded so repeated successful logins cannot flood sessions.
        key = hashlib.sha256(("login-user:" + normalized).encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute("DELETE FROM account_rate_limits WHERE bucket=?", (key,))
            # Preserve the identity revision whose password was verified. A
            # reset racing between verification and cookie issuance must fail.
            if row["password_hash"].startswith("scrypt$16384$8$1$"):
                upgraded = hash_password(password)
                result = db.execute("UPDATE accounts SET password_hash=?,updated_at=? "
                                    "WHERE id=? AND password_hash=? AND disabled=0", (
                                        upgraded, self._time(), row["id"], row["password_hash"]))
                if not result.rowcount:
                    raise AccountError(401, "请重新登录。")
                row = db.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone()
            return self._public(db, row)

    def issue_session(self, user_id, *, expected_updated_at=None, expected_revision=None):
        token, instant = secrets.token_urlsafe(32), self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT disabled,updated_at,auth_revision FROM accounts WHERE id=?", (user_id,)).fetchone()
            if (not row or row["disabled"]
                    or (expected_updated_at is not None and row["updated_at"] != expected_updated_at)
                    or (expected_revision is not None and row["auth_revision"] != expected_revision)):
                raise AccountError(401, "请重新登录。")
            db.execute("DELETE FROM account_sessions WHERE expires_at<=?", (_timestamp(instant),))
            db.execute("INSERT INTO account_sessions VALUES(?,?,?,?)", (
                hashlib.sha256(token.encode()).hexdigest(), user_id, _timestamp(instant),
                _timestamp(instant + timedelta(seconds=SESSION_SECONDS))))
            db.execute("DELETE FROM account_sessions WHERE user_id=? AND token_hash NOT IN "
                       "(SELECT token_hash FROM account_sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 16)",
                       (user_id, user_id))
        return token

    def authenticate_session(self, token):
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            return None
        with self._connect() as db:
            row = db.execute("SELECT a.* FROM accounts a JOIN account_sessions s ON a.id=s.user_id "
                             "WHERE s.token_hash=? AND s.expires_at>? AND a.disabled=0",
                             (hashlib.sha256(token.encode()).hexdigest(), self._time())).fetchone()
            return self._public(db, row)

    def revoke_session(self, token):
        if isinstance(token, str):
            with self._connect() as db:
                db.execute("DELETE FROM account_sessions WHERE token_hash=?", (
                    hashlib.sha256(token.encode()).hexdigest(),))

    def change_password(self, user_id, current_password, new_password):
        encoded = hash_password(new_password)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT password_hash,username FROM accounts WHERE id=? AND disabled=0", (user_id,)).fetchone()
            if not row or not verify_password(current_password, row["password_hash"]):
                raise AccountError(400, "当前密码不正确。")
            db.execute("UPDATE accounts SET password_hash=?,updated_at=?,auth_revision=auth_revision+1 WHERE id=?",
                       (encoded, self._time(), user_id))
            db.execute("DELETE FROM account_sessions WHERE user_id=?", (user_id,))
            self._clear_login_failures(db, row["username"])
            self._audit(db, user_id, user_id, "change_password", [], self._time())

    def complete_onboarding(self, user_id, values):
        allowed = {"display_name", "cooking_level", "household_size", "preferences", "theme", "use_for_ai"}
        if set(values) - allowed:
            raise AccountError(422, "欢迎设置包含未知字段。")
        display_name = values.get("display_name", "").strip()
        if not 1 <= len(display_name) <= 40 or any(ord(c) < 32 for c in display_name):
            raise AccountError(422, "称呼需为 1 至 40 个字符。")
        level = values.get("cooking_level", "beginner")
        household_size = values.get("household_size", 1)
        preferences = values.get("preferences", [])
        theme = values.get("theme", "system")
        use_for_ai = values.get("use_for_ai", False)
        if type(use_for_ai) is not bool:
            raise AccountError(422, "个性化授权开关必须为布尔值。")
        if level not in {"beginner", "comfortable", "confident"}:
            raise AccountError(422, "请选择有效的烹饪经验。")
        if type(household_size) is not int or not 1 <= household_size <= 20:
            raise AccountError(422, "用餐人数需为 1 至 20 人。")
        if (not isinstance(preferences, list) or len(preferences) > 12 or any(
                not isinstance(item, str) or not 1 <= len(item.strip()) <= 80 for item in preferences)):
            raise AccountError(422, "偏好最多 12 项，每项不超过 80 个字符。")
        if theme not in {"light", "dark", "system"}:
            raise AccountError(422, "请选择有效的主题。")
        profile = {"cooking_level": level, "household_size": household_size,
                   "preferences": list(dict.fromkeys(item.strip() for item in preferences)), "theme": theme,
                   "use_for_ai": use_for_ai, "revision": secrets.token_hex(16)}
        with self._connect() as db:
            result = db.execute("UPDATE accounts SET display_name=?,profile=?,onboarding_completed=1,updated_at=? "
                                "WHERE id=? AND disabled=0", (
                                    display_name, json.dumps(profile, ensure_ascii=False), self._time(), user_id))
            if not result.rowcount:
                raise AccountError(401, "请重新登录。")
        return self.get_user(user_id)

    def update_user(self, actor_id, user_id, changes):
        if not changes or set(changes) - {"disabled", "developer_allowed", "role", "cost_limit_micros"}:
            raise AccountError(422, "用户设置无效。")
        for key in {"disabled", "developer_allowed"} & changes.keys():
            if type(changes[key]) is not bool:
                raise AccountError(422, "用户开关必须为布尔值。")
        if "role" in changes and changes["role"] not in {"admin", "user"}:
            raise AccountError(422, "用户角色无效。")
        for key in {"cost_limit_micros"} & changes.keys():
            _limit(changes[key])
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            actor = db.execute("SELECT * FROM accounts WHERE id=?", (actor_id,)).fetchone()
            if not actor or actor["disabled"] or actor["role"] != "admin":
                raise AccountError(403, "需要管理员权限。")
            row = db.execute("SELECT * FROM accounts WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise AccountError(404, "用户不存在。")
            if row["role"] == "admin" and not row["disabled"] and (
                    changes.get("role", row["role"]) != "admin" or changes.get("disabled", False)):
                others = db.execute("SELECT COUNT(*) FROM accounts WHERE role='admin' AND disabled=0 AND id<>?",
                                    (user_id,)).fetchone()[0]
                if not others:
                    raise AccountError(409, "至少保留一个可用的管理员账号。")
            columns = list(changes)
            db.execute("UPDATE accounts SET " + ",".join(f"{key}=?" for key in columns) + ",updated_at=? WHERE id=?",
                       (*[changes[key] for key in columns], self._time(), user_id))
            if "cost_limit_micros" in changes:
                db.execute("UPDATE account_credit_grants SET amount_micros=? WHERE id=?",
                           (changes["cost_limit_micros"], "base-" + user_id))
            if changes.get("disabled") or changes.get("role", row["role"]) != row["role"]:
                db.execute("UPDATE accounts SET auth_revision=auth_revision+1 WHERE id=?", (user_id,))
                db.execute("DELETE FROM account_sessions WHERE user_id=?", (user_id,))
            self._audit(db, actor_id, user_id, "update_user", columns, self._time())
        return self.get_user(user_id)

    def reset_password(self, actor_id, user_id, new_password):
        encoded = hash_password(new_password)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            actor = db.execute("SELECT * FROM accounts WHERE id=?", (actor_id,)).fetchone()
            if not actor or actor["disabled"] or actor["role"] != "admin":
                raise AccountError(403, "需要管理员权限。")
            result = db.execute("UPDATE accounts SET password_hash=?,updated_at=?,auth_revision=auth_revision+1 WHERE id=?",
                                (encoded, self._time(), user_id))
            if not result.rowcount:
                raise AccountError(404, "用户不存在。")
            username = db.execute("SELECT username FROM accounts WHERE id=?", (user_id,)).fetchone()[0]
            self._clear_login_failures(db, username)
            db.execute("DELETE FROM account_sessions WHERE user_id=?", (user_id,))
            self._audit(db, actor_id, user_id, "reset_password", [], self._time())

    def pricing(self):
        with self._connect() as db:
            return json.loads(db.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])

    @staticmethod
    def _pricing_override(values):
        """Validate an internal model-profile quote; no HTTP accepts this object."""
        allowed = {"currency", "input_per_million", "output_per_million", "cached_input_per_million",
                   "configured", "revision", "model_profile_id", "model_profile_label"}
        fields = ("input_per_million", "output_per_million", "cached_input_per_million")
        if (not isinstance(values, dict) or set(values) - allowed or values.get("currency") != "CNY"
                or type(values.get("configured")) is not bool or any(key not in values for key in fields)):
            raise AccountError(422, "模型计价快照无效。")
        result = {"currency": "CNY", "configured": values["configured"], **{key: _price(values[key]) for key in fields}}
        if "revision" in values:
            if type(values["revision"]) is not int or values["revision"] < 1:
                raise AccountError(422, "模型计价版本无效。")
            result["revision"] = values["revision"]
        if "model_profile_id" in values:
            if not isinstance(values["model_profile_id"], str) or not _USER_ID.fullmatch(values["model_profile_id"]):
                raise AccountError(422, "模型档案标识无效。")
            result["model_profile_id"] = values["model_profile_id"]
        if "model_profile_label" in values:
            label = values["model_profile_label"]
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or "\x00" in label:
                raise AccountError(422, "模型档案名称无效。")
            result["model_profile_label"] = label.strip()
        return result

    def get_entitlements(self, user_id):
        user = self.get_user(user_id)
        if not user:
            raise AccountError(404, "用户不存在。")
        return {"user": user, "entitlements": user["quota"]["funds"]}

    def grant_credit(self, actor_id, user_id, **values):
        return self.entitlements.grant_credit(actor_id, user_id, **values)

    def grant_membership(self, actor_id, user_id, **values):
        return self.entitlements.grant_membership(actor_id, user_id, **values)

    def revoke_membership(self, actor_id, user_id, membership_id, **values):
        return self.entitlements.revoke_membership(actor_id, user_id, membership_id, **values)

    def policy(self, *, connection=None):
        if connection is None:
            with self._connect() as active:
                return self.policy(connection=active)
        return json.loads(connection.execute("SELECT body FROM account_settings WHERE name='policy'").fetchone()[0])

    def update_policy(self, actor_id, changes):
        allowed = {"revision", "registration_enabled", "default_cost_limit_micros"}
        if (not changes or set(changes) - allowed or type(changes.get("revision")) is not int
                or changes["revision"] < 1 or set(changes) == {"revision"}):
            raise AccountError(422, "注册策略请求无效，请带上当前版本与要修改的字段。")
        if "registration_enabled" in changes and type(changes["registration_enabled"]) is not bool:
            raise AccountError(422, "注册开关必须为布尔值。")
        for key in {"default_cost_limit_micros"} & changes.keys():
            _limit(changes[key])
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            actor = db.execute("SELECT role,disabled FROM accounts WHERE id=?", (actor_id,)).fetchone()
            if not actor or actor["disabled"] or actor["role"] != "admin":
                raise AccountError(403, "需要管理员权限。")
            current = self.policy(connection=db)
            if changes["revision"] != current["revision"]:
                raise AccountError(409, "注册策略已变化，请刷新后保留草稿并复核。")
            current.update({key: value for key, value in changes.items() if key != "revision"})
            current["revision"] += 1
            db.execute("UPDATE account_settings SET body=? WHERE name='policy'", (json.dumps(current),))
            self._audit(db, actor_id, None, "update_policy", set(changes) - {"revision"}, self._time())
        return current

    def update_pricing(self, actor_id, changes):
        allowed = {"currency", "input_per_million", "output_per_million", "cached_input_per_million", "revision"}
        if not changes or set(changes) - allowed or changes.get("currency", "CNY") != "CNY":
            raise AccountError(422, "计价设置无效，当前使用人民币计价。")
        prices = {key: _price(value) for key, value in changes.items() if key.endswith("per_million")}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            actor = db.execute("SELECT role,disabled FROM accounts WHERE id=?", (actor_id,)).fetchone()
            if not actor or actor["disabled"] or actor["role"] != "admin":
                raise AccountError(403, "需要管理员权限。")
            current = json.loads(db.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])
            if "revision" in changes and changes["revision"] != current["revision"]:
                raise AccountError(409, "计价设置已变化，请刷新后重试。")
            current.update(prices)
            current["configured"] = True
            current["revision"] += 1
            db.execute("UPDATE account_settings SET body=? WHERE name='pricing'", (json.dumps(current),))
            self._audit(db, actor_id, None, "update_pricing", prices, self._time())
        return current

    def reserve_usage(self, user_id, reservation_id, *, tokens=1, cost_micros=0, ttl_seconds=900, pricing_override=None):
        """Reserve an explicit upper bound, retaining prices until settlement."""
        if not isinstance(reservation_id, str) or not 1 <= len(reservation_id) <= 200:
            raise AccountError(422, "用量请求标识无效。")
        if type(tokens) is not int or tokens < 1 or type(cost_micros) is not int or cost_micros < 0:
            raise AccountError(422, "预留额度无效。")
        self.recover_reservations(expired_only=True)
        instant = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM accounts WHERE id=? AND disabled=0", (user_id,)).fetchone()
            if not row:
                raise AccountError(401, "请重新登录。")
            previous = db.execute("SELECT * FROM account_reservations WHERE id=?", (reservation_id,)).fetchone()
            if previous:
                if previous["user_id"] != user_id:
                    raise AccountError(409, "请求标识已被使用。")
                return {**dict(previous), "pricing": json.loads(previous["pricing"])}
            recorded = db.execute("SELECT 1 FROM account_usage WHERE user_id=? AND turn_id=?",
                                  (user_id, reservation_id)).fetchone()
            if recorded:
                raise AccountError(409, "这次请求的用量已结算。")
            quota = self._quota(db, row)
            pricing = (self._pricing_override(pricing_override) if pricing_override is not None else
                       json.loads(db.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0]))
            if quota["cost_limit_micros"] is not None and not pricing.get("configured", False):
                raise AccountError(503, "管理员尚未配置模型计价，请联系管理员。")
            if quota["balance_micros"] is not None and quota["balance_micros"] == 0:
                raise QuotaExceeded()
            self.entitlements.resize(db, row, reservation_id, cost_micros)
            expires = _timestamp(instant + timedelta(seconds=max(60, min(3600, ttl_seconds))))
            db.execute("INSERT INTO account_reservations VALUES(?,?,?,?,?,?,?)", (
                reservation_id, user_id, tokens, cost_micros, json.dumps(pricing), _timestamp(instant), expires))
        return {"id": reservation_id, "user_id": user_id, "tokens": tokens, "cost_micros": cost_micros,
                "pricing": pricing, "expires_at": expires}

    def resize_usage(self, user_id, reservation_id, *, tokens, cost_micros, observed=False, cost_portions=None):
        """Atomically replace this turn's reservation before a model call.

        The bound is cumulative for the entire turn. Its old reservation is
        excluded from the comparison, while other admitted turns remain counted.
        ``observed=True`` records already incurred provider usage even beyond a
        cap; it never authorizes another model call and has no HTTP endpoint.
        """
        if (type(observed) is not bool or type(tokens) is not int or not int(not observed) <= tokens <= 10**15
                or type(cost_micros) is not int or not 0 <= cost_micros <= 10**15):
            raise AccountError(422, "预留额度无效。")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM accounts WHERE id=? AND disabled=0", (user_id,)).fetchone()
            current = db.execute("SELECT * FROM account_reservations WHERE id=? AND user_id=? AND expires_at>?",
                                 (reservation_id, user_id, self._time())).fetchone()
            if not row:
                raise AccountError(401, "请重新登录。")
            if not current:
                raise AccountError(409, "用量预留已过期，请重新发送。")
            quota = self._quota(db, row)
            if not observed and quota["cost_limit_micros"] is not None and not json.loads(current["pricing"]).get("configured", False):
                raise AccountError(503, "管理员尚未配置模型计价，请联系管理员。")
            self.entitlements.resize(db, row, reservation_id, cost_micros, portions=cost_portions, observed=observed)
            db.execute("UPDATE account_reservations SET tokens=?,cost_micros=? WHERE id=? AND user_id=?",
                       (tokens, cost_micros, reservation_id, user_id))
            return {**dict(current), "tokens": tokens, "cost_micros": cost_micros,
                    "pricing": json.loads(current["pricing"])}

    def release_usage(self, user_id, reservation_id):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.entitlements.release(db, user_id, reservation_id)
            db.execute("DELETE FROM account_reservations WHERE id=? AND user_id=?", (reservation_id, user_id))

    def reservation_pricing(self, user_id, reservation_id):
        with self._connect() as db:
            row = db.execute("SELECT pricing FROM account_reservations WHERE id=? AND user_id=?",
                             (reservation_id, user_id)).fetchone()
            if not row:
                raise AccountError(404, "用量预留不存在。")
            return json.loads(row["pricing"])

    def recover_reservations(self, *, expired_only=False):
        """Recover after restart, retaining unknown admitted costs as estimates.

        The host calls the default mode once before serving any requests. During
        admission, expired-only mode prevents a lost worker's bound disappearing.
        """
        with self._connect() as db:
            condition = " WHERE expires_at<=?" if expired_only else ""
            parameters = (self._time(),) if expired_only else ()
            rows = [dict(row) for row in db.execute("SELECT * FROM account_reservations" + condition, parameters)]
        recovered = 0
        for row in rows:
            result = self.settle_usage(row["user_id"], row["id"], {"attempted_calls": 1, "calls": []},
                                       status="interrupted", _only_if_reserved=True)
            recovered += result is not None
        return recovered

    @staticmethod
    def _usage(usage):
        def count(value):
            return value if type(value) is int and 0 <= value <= 10**15 else None
        calls = usage.get("calls", []) if isinstance(usage, dict) else []
        if not isinstance(calls, list):
            calls = []
        fields = ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens")
        def field(call, key):
            return count(call.get(key, call.get("cached_tokens")) if key == "cached_input_tokens" else call.get(key))
        records = []
        incomplete = False
        for call in calls or [usage]:
            record = {key: field(call, key) if isinstance(call, dict) else None for key in fields}
            # Both operands must belong to this same provider call. Values from
            # two different incomplete calls cannot form a fabricated total.
            if record["total_tokens"] is None and record["input_tokens"] is not None and record["output_tokens"] is not None:
                record["total_tokens"] = record["input_tokens"] + record["output_tokens"]
            required = [record[key] for key in ("input_tokens", "output_tokens", "total_tokens")]
            incomplete = incomplete or any(value is None for value in required)
            if all(value is not None for value in required) and required[2] < required[0] + required[1]:
                incomplete = True  # Contradictory provider metadata is not complete accounting.
            records.append(record)
        values = {}
        for key in fields:
            known = [record[key] for record in records if record[key] is not None]
            values[key] = sum(known) if known else None
        return values, records, incomplete

    @staticmethod
    def calculate_cost(values, pricing):
        # A currency unit per million tokens equals one micro-currency per token.
        input_tokens, output_tokens = values["input_tokens"], values["output_tokens"]
        cached = values["cached_input_tokens"]
        incomplete = input_tokens is None or output_tokens is None or values["total_tokens"] is None
        # Cache metadata is optional; absence retains the conservative full input price.
        cached = min(cached or 0, input_tokens or 0)
        amount = (Decimal((input_tokens or 0) - cached) * Decimal(pricing["input_per_million"])
                  + Decimal(cached) * Decimal(pricing["cached_input_per_million"])
                  + Decimal(output_tokens or 0) * Decimal(pricing["output_per_million"]))
        return int(amount.to_integral_value(rounding=ROUND_CEILING)), incomplete

    def settle_usage(self, user_id, turn_id, usage, *, session_id="", model="", status="completed", _only_if_reserved=False):
        if status not in {"running", "completed", "cancelled", "error", "interrupted"}:
            status = "error"
        values, records, incomplete_observation = self._usage(usage)
        attempts = usage.get("attempted_calls", usage.get("model_calls")) if isinstance(usage, dict) else None
        calls = usage.get("calls", []) if isinstance(usage, dict) else []
        calls = calls if isinstance(calls, list) else []
        missing_calls = type(attempts) is int and attempts > len(calls) and bool(calls)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM account_usage WHERE user_id=? AND turn_id=?",
                                  (user_id, turn_id)).fetchone()
            reservation = db.execute("SELECT * FROM account_reservations WHERE id=? AND user_id=?",
                                     (turn_id, user_id)).fetchone()
            # Recovery's earlier scan may race a real final settlement. Check
            # under this same write transaction before marking it interrupted.
            if _only_if_reserved and reservation is None:
                return None
            if attempts == 0 and not calls and not existing:
                if status != "running":
                    self.entitlements.release(db, user_id, turn_id)
                    db.execute("DELETE FROM account_reservations WHERE id=? AND user_id=?", (turn_id, user_id))
                return {**values, "cost_micros": 0, "incomplete": False, "estimated_tokens": 0,
                        "estimated_cost_micros": 0, "pricing": json.loads(reservation["pricing"]) if reservation else None}
            if existing:
                # Runtime observations may fill fields progressively. A repeated
                # final/replay cannot charge a second time or change its prices.
                pricing = json.loads(existing["pricing"])
                for key in values:
                    if values[key] is None:
                        values[key] = existing[key]
                    elif existing[key] is not None:
                        values[key] = max(values[key], existing[key])
                if status == "running" and existing["status"] != "running":
                    status = existing["status"]
                created_at = existing["created_at"]
            else:
                pricing = json.loads(reservation["pricing"]) if reservation else json.loads(db.execute(
                    "SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])
                created_at = self._time()
            # Price cache hits within their own call. Aggregating cache tokens
            # first can incorrectly discount a different call's known input.
            cost = sum(self.calculate_cost(record, pricing)[0] for record in records)
            if existing:
                cost = max(cost, existing["cost_micros"])
            incomplete = incomplete_observation or missing_calls
            # Unknown provider metadata consumes the unobserved part of the
            # admitted bound. These estimates remain distinct from actual usage.
            known_tokens = values["total_tokens"] or 0
            estimated_tokens = max(0, (reservation["tokens"] if reservation else (
                existing["estimated_tokens"] + known_tokens if existing else 0)) - known_tokens) if incomplete else 0
            estimated_cost = max(0, (reservation["cost_micros"] if reservation else (
                existing["estimated_cost_micros"] + cost if existing else 0)) - cost) if incomplete else 0
            db.execute("INSERT INTO account_usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,turn_id) "
                       "DO UPDATE SET status=excluded.status,input_tokens=excluded.input_tokens,"
                       "output_tokens=excluded.output_tokens,cached_input_tokens=excluded.cached_input_tokens,"
                       "total_tokens=excluded.total_tokens,cost_micros=excluded.cost_micros,incomplete=excluded.incomplete,"
                       "estimated_tokens=excluded.estimated_tokens,estimated_cost_micros=excluded.estimated_cost_micros", (
                           user_id, turn_id, str(session_id)[:200], str(model)[:200], status, values["input_tokens"],
                           values["output_tokens"], values["cached_input_tokens"], values["total_tokens"], cost,
                           int(incomplete), json.dumps(pricing), created_at, estimated_tokens, estimated_cost))
            if status != "running":
                row = db.execute("SELECT * FROM accounts WHERE id=?", (user_id,)).fetchone()
                reconciliation = db.execute("SELECT final_cost_micros FROM account_usage_reconciliations "
                    "WHERE user_id=? AND turn_id=? ORDER BY revision DESC LIMIT 1", (user_id, turn_id)).fetchone()
                self.entitlements.settle(db, row, turn_id,
                    reconciliation[0] if reconciliation else cost + estimated_cost, 0 if reconciliation else estimated_cost)
                db.execute("DELETE FROM account_reservations WHERE id=? AND user_id=?", (turn_id, user_id))
        return {**values, "cost_micros": cost, "incomplete": incomplete, "pricing": pricing,
                "estimated_tokens": estimated_tokens, "estimated_cost_micros": estimated_cost}

    @staticmethod
    def _usage_rows(db, where, parameters, *, limit=None):
        statement = ("SELECT u.*,r.revision AS reconciliation_revision,r.final_cost_micros AS reconciliation_final_cost,"
            "r.reason AS reconciliation_reason,r.actor_id AS reconciliation_actor,r.created_at AS reconciliation_created,"
            "COALESCE(r.final_cost_micros,u.cost_micros) AS effective_cost_micros,"
            "CASE WHEN r.revision IS NULL THEN u.estimated_cost_micros ELSE 0 END AS effective_estimated_cost_micros,"
            "CASE WHEN u.status='running' OR EXISTS(SELECT 1 FROM account_reservations pending "
            "WHERE pending.user_id=u.user_id AND pending.id=u.turn_id) THEN 1 ELSE 0 END AS active "
            "FROM account_usage u " + _RECONCILIATION_JOIN + " WHERE " + where + " ORDER BY u.created_at DESC,u.turn_id")
        if limit is not None:
            statement += " LIMIT ?"
            parameters = (*parameters, limit)
        result = []
        for source in db.execute(statement, parameters):
            row = dict(source)
            row["reconciliation"] = ({"revision": row["reconciliation_revision"],
                "final_cost_micros": row["reconciliation_final_cost"], "reason": row["reconciliation_reason"],
                "actor_id": row["reconciliation_actor"], "created_at": row["reconciliation_created"]}
                if row["reconciliation_revision"] is not None else None)
            for key in ("reconciliation_revision", "reconciliation_final_cost", "reconciliation_reason",
                        "reconciliation_actor", "reconciliation_created"):
                row.pop(key)
            row["pricing"] = json.loads(row["pricing"])
            row["incomplete"], row["active"] = bool(row["incomplete"]), bool(row["active"])
            result.append(row)
        return result

    def get_usage(self, user_id, *, limit=100):
        limit = max(1, min(1000, int(limit)))
        with self._connect() as db:
            return self._usage_rows(db, "u.user_id=?", (user_id,), limit=limit)

    def reconciliations(self, user_id, turn_id):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT revision,final_cost_micros,reason,actor_id,created_at "
                "FROM account_usage_reconciliations WHERE user_id=? AND turn_id=? ORDER BY revision DESC",
                (user_id, turn_id))]

    def reconcile_usage(self, actor_id, user_id, turn_id, *, revision, final_cost_micros, reason):
        if (type(revision) is not int or revision < 0 or type(final_cost_micros) is not int
                or not 0 <= final_cost_micros <= 10**15 or not isinstance(reason, str)
                or not 1 <= len(reason.strip()) <= 500 or "\x00" in reason):
            raise AccountError(422, "核对金额、版本或依据无效，请填写 1 至 500 字核对依据。")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            actor = db.execute("SELECT role,disabled FROM accounts WHERE id=?", (actor_id,)).fetchone()
            if not actor or actor["disabled"] or actor["role"] != "admin":
                raise AccountError(403, "需要管理员权限。")
            target = db.execute("SELECT * FROM accounts WHERE id=?", (user_id,)).fetchone()
            entry = db.execute("SELECT status FROM account_usage WHERE user_id=? AND turn_id=?", (user_id, turn_id)).fetchone()
            if not target or not entry:
                raise AccountError(404, "用户或用量记录不存在。")
            pending = db.execute("SELECT 1 FROM account_reservations WHERE user_id=? AND id=?", (user_id, turn_id)).fetchone()
            if entry["status"] == "running" or pending:
                raise AccountError(409, "该请求尚未结束，请等待预留金额结算后再核对。")
            previous = db.execute("SELECT MAX(revision) FROM account_usage_reconciliations WHERE user_id=? AND turn_id=?",
                                  (user_id, turn_id)).fetchone()[0] or 0
            if previous != revision:
                raise AccountError(409, "这条金额已被另一位管理员核对，请保留草稿并重新读取。")
            db.execute("INSERT INTO account_usage_reconciliations VALUES(?,?,?,?,?,?,?)", (
                user_id, turn_id, revision + 1, final_cost_micros, reason.strip(), actor_id, self._time()))
            self.entitlements._replace_used(db, user_id, turn_id, final_cost_micros)
            self._audit(db, actor_id, user_id, "reconcile_usage", ["final_cost_micros", "usage_reconciliation"], self._time())
            return {"ok": True, "user": self._public(db, target),
                    "entry": self._usage_rows(db, "u.user_id=? AND u.turn_id=?", (user_id, turn_id), limit=1)[0]}

    def usage_summary(self, *, days=30):
        if type(days) is not int or not 1 <= days <= 366:
            raise AccountError(422, "统计范围需为 1 至 366 天。")
        cutoff = _timestamp(self._now() - timedelta(days=days))
        with self._connect() as db:
            rows = self._usage_rows(db, "u.created_at>=?", (cutoff,))
            users = [self._public(db, row) for row in db.execute("SELECT * FROM accounts ORDER BY created_at")]
        def summary(items):
            return {"turns": len(items), "total_tokens": sum(row["total_tokens"] or 0 for row in items),
                    "input_tokens": sum(row["input_tokens"] or 0 for row in items),
                    "output_tokens": sum(row["output_tokens"] or 0 for row in items),
                    "cost_micros": sum(row["effective_cost_micros"] for row in items),
                    "reported_cost_micros": sum(row["cost_micros"] for row in items),
                    "reconciled_turns": sum(row["reconciliation"] is not None for row in items),
                    "estimated_tokens": sum(row["estimated_tokens"] for row in items),
                    "estimated_cost_micros": sum(row["effective_estimated_cost_micros"] for row in items),
                    "incomplete_turns": sum(row["incomplete"] for row in items)}
        daily = {}
        zone = timezone(timedelta(hours=8))
        for row in rows:
            day = datetime.fromisoformat(row["created_at"]).astimezone(zone).date().isoformat()
            daily.setdefault(day, []).append(row)
        return {"summary": summary(rows), "users": [
            {"user": user, **summary([row for row in rows if row["user_id"] == user["id"]])} for user in users],
            "daily": [{"date": day, **summary(items)} for day, items in sorted(daily.items())],
            "pricing": self.pricing(), "period": {"days": days, "from": cutoff, "to": self._time()},
            "notice": "费用按每次请求开始时的管理员单价估算；管理员核对金额单独保留来源。"
                      "未知用量继续标记，不伪造 Token；金额账本未接入真实支付。"}

    def audit_log(self, *, limit=100):
        limit = max(1, min(500, int(limit)))
        with self._connect() as db:
            return [{**dict(row), "fields": json.loads(row["fields"])} for row in db.execute(
                "SELECT * FROM account_audit ORDER BY id DESC LIMIT ?", (limit,))]
