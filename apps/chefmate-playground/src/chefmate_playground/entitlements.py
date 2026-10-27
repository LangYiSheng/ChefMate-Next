"""Currency grants and first-use membership windows with original-scope accounting.

Every mutation runs inside the account repository's serialized transaction. A
reservation is split into model-round portions; refunds stay with those original
grants/windows, including after membership expiration or a window reset.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta


def _errors():
    from .accounts import AccountError, QuotaExceeded
    return AccountError, QuotaExceeded


def _stamp(value):
    from .accounts import _timestamp
    return _timestamp(value)


class EntitlementsLedger:
    def __init__(self, accounts):
        self.accounts = accounts

    @staticmethod
    def create_tables(db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS account_credit_grants (
                id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES accounts(id),
                kind TEXT NOT NULL CHECK(kind IN ('base','topup')),label TEXT NOT NULL,
                amount_micros INTEGER,request_id TEXT NOT NULL,created_at TEXT NOT NULL,
                UNIQUE(user_id,request_id));
            CREATE INDEX IF NOT EXISTS account_credit_grants_user ON account_credit_grants(user_id);
            CREATE TABLE IF NOT EXISTS account_memberships (
                id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES accounts(id),label TEXT NOT NULL,
                starts_at TEXT NOT NULL,expires_at TEXT NOT NULL,duration_days INTEGER NOT NULL,
                weekly_limit_micros INTEGER NOT NULL,five_hour_limit_micros INTEGER NOT NULL,
                revoked_at TEXT,revision INTEGER NOT NULL DEFAULT 1,request_id TEXT NOT NULL,
                created_at TEXT NOT NULL,revocation_reason TEXT,UNIQUE(user_id,request_id));
            CREATE INDEX IF NOT EXISTS account_memberships_user ON account_memberships(user_id);
            CREATE TABLE IF NOT EXISTS account_membership_windows (
                id TEXT PRIMARY KEY,membership_id TEXT NOT NULL REFERENCES account_memberships(id),
                kind TEXT NOT NULL CHECK(kind IN ('week','five_hour')),parent_id TEXT,
                starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,limit_micros INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS account_membership_windows_member ON account_membership_windows(membership_id,kind,starts_at);
            CREATE TABLE IF NOT EXISTS account_budget_turns (
                user_id TEXT NOT NULL REFERENCES accounts(id),turn_id TEXT NOT NULL,
                created_at TEXT NOT NULL,original_allocations TEXT NOT NULL DEFAULT '[]',
                settled INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,turn_id));
            CREATE TABLE IF NOT EXISTS account_budget_portions (
                user_id TEXT NOT NULL,turn_id TEXT NOT NULL,portion_id TEXT NOT NULL,
                candidates TEXT NOT NULL,created_at TEXT NOT NULL,
                PRIMARY KEY(user_id,turn_id,portion_id),
                FOREIGN KEY(user_id,turn_id) REFERENCES account_budget_turns(user_id,turn_id));
            CREATE TABLE IF NOT EXISTS account_budget_allocations (
                id TEXT PRIMARY KEY,user_id TEXT NOT NULL,turn_id TEXT NOT NULL,portion_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,grant_id TEXT,membership_id TEXT,week_id TEXT,five_hour_id TEXT,
                amount_micros INTEGER NOT NULL,estimated_micros INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL CHECK(state IN ('reserved','used')),created_at TEXT NOT NULL,
                FOREIGN KEY(user_id,turn_id) REFERENCES account_budget_turns(user_id,turn_id));
            CREATE INDEX IF NOT EXISTS account_budget_allocations_user ON account_budget_allocations(user_id,state);
            CREATE INDEX IF NOT EXISTS account_budget_allocations_turn ON account_budget_allocations(user_id,turn_id);
            CREATE INDEX IF NOT EXISTS account_budget_allocations_grant ON account_budget_allocations(grant_id,state);
            CREATE INDEX IF NOT EXISTS account_budget_allocations_week ON account_budget_allocations(week_id,state);
            CREATE INDEX IF NOT EXISTS account_budget_allocations_five_hour ON account_budget_allocations(five_hour_id,state);
        """)

    def ensure_account(self, db, row):
        db.execute("INSERT OR IGNORE INTO account_credit_grants VALUES(?,?,?,?,?,?,?)", (
            "base-" + row["id"], row["id"], "base", "新用户基础额度", row["cost_limit_micros"],
            "initial-base", row["created_at"]))
        db.execute("UPDATE account_credit_grants SET amount_micros=? WHERE id=?", (row["cost_limit_micros"], "base-" + row["id"]))

    def migrate_existing(self, db):
        """Map existing money to the same base allowance; do not grant it again."""
        for row in db.execute("SELECT * FROM accounts").fetchall():
            self.ensure_account(db, row)
        from .accounts import _RECONCILIATION_JOIN
        sources = db.execute("SELECT u.*,COALESCE(r.final_cost_micros,u.cost_micros) effective_cost,"
            "CASE WHEN r.revision IS NULL THEN u.estimated_cost_micros ELSE 0 END effective_estimate "
            "FROM account_usage u " + _RECONCILIATION_JOIN).fetchall()
        pending = {row["id"]: row for row in db.execute("SELECT * FROM account_reservations").fetchall()}
        for row in sources:
            if self._turn(db, row["user_id"], row["turn_id"]):
                continue
            self.start_turn(db, row["user_id"], row["turn_id"], row["created_at"])
            reservation = pending.get(row["turn_id"])
            amount = reservation["cost_micros"] if reservation else row["effective_cost"] + row["effective_estimate"]
            source = {"grant_id": "base-" + row["user_id"], "membership_id": None, "week_id": None, "five_hour_id": None}
            self._insert(db, row["user_id"], row["turn_id"], "legacy", source, amount,
                         state="reserved" if reservation else "used",
                         estimated=0 if reservation else row["effective_estimate"])
            db.execute("INSERT OR IGNORE INTO account_budget_portions VALUES(?,?,?,?,?)", (
                row["user_id"], row["turn_id"], "legacy", json.dumps([source]), row["created_at"]))
            if not reservation:
                self._freeze(db, row["user_id"], row["turn_id"])
        for row in pending.values():
            if self._turn(db, row["user_id"], row["id"]):
                continue
            self.start_turn(db, row["user_id"], row["id"], row["created_at"])
            source = {"grant_id": "base-" + row["user_id"], "membership_id": None, "week_id": None, "five_hour_id": None}
            self._insert(db, row["user_id"], row["id"], "turn", source, row["cost_micros"])
            db.execute("INSERT OR IGNORE INTO account_budget_portions VALUES(?,?,?,?,?)", (
                row["user_id"], row["id"], "turn", json.dumps([source]), row["created_at"]))

    @staticmethod
    def _turn(db, user_id, turn_id):
        return db.execute("SELECT * FROM account_budget_turns WHERE user_id=? AND turn_id=?", (user_id, turn_id)).fetchone()

    def start_turn(self, db, user_id, turn_id, instant=None):
        db.execute("INSERT OR IGNORE INTO account_budget_turns(user_id,turn_id,created_at) VALUES(?,?,?)",
                   (user_id, turn_id, instant or self.accounts._time()))

    @staticmethod
    def _amounts(db, column, resource):
        row = db.execute("SELECT COALESCE(SUM(CASE WHEN state='used' THEN amount_micros ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN state='reserved' THEN amount_micros ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN state='used' THEN estimated_micros ELSE 0 END),0) "
            f"FROM account_budget_allocations WHERE {column}=?", (resource,)).fetchone()
        return {"used_micros": row[0], "reserved_micros": row[1], "estimated_micros": row[2]}

    def _window(self, db, membership, kind, now, *, create=False, parent=None):
        instant = _stamp(now)
        row = db.execute("SELECT * FROM account_membership_windows WHERE membership_id=? AND kind=? "
            "AND starts_at<=? AND ends_at>? AND " + ("parent_id=?" if parent else "parent_id IS NULL") +
            " ORDER BY starts_at DESC LIMIT 1", (membership["id"], kind, instant, instant, *([parent["id"]] if parent else []))).fetchone()
        if row or not create:
            return row
        end = min(now + (timedelta(days=7) if kind == "week" else timedelta(hours=5)),
                  datetime.fromisoformat(membership["expires_at"]))
        if parent:
            end = min(end, datetime.fromisoformat(parent["ends_at"]))
        window_id = secrets.token_hex(16)
        limit = membership["weekly_limit_micros"] if kind == "week" else membership["five_hour_limit_micros"]
        db.execute("INSERT INTO account_membership_windows VALUES(?,?,?,?,?,?,?)", (
            window_id, membership["id"], kind, parent["id"] if parent else None, instant, _stamp(end), limit))
        return db.execute("SELECT * FROM account_membership_windows WHERE id=?", (window_id,)).fetchone()

    def _window_public(self, db, window, limit, *, read_amounts=None):
        amounts = (read_amounts or (lambda column, resource: self._amounts(db, column, resource)))(
            "week_id" if window and window["kind"] == "week" else "five_hour_id", window["id"]) if window else {
            "used_micros": 0, "reserved_micros": 0, "estimated_micros": 0}
        return {"starts_at": window["starts_at"] if window else None, "resets_at": window["ends_at"] if window else None,
                "limit_micros": limit, **amounts,
                "remaining_micros": max(0, limit - amounts["used_micros"] - amounts["reserved_micros"])}

    def snapshot(self, db, row):
        now, sources, memberships = self.accounts._now(), [], []
        # One user-scoped scan supplies every source/window meter. A user with
        # many historical grants must not rescan the entire ledger per card.
        aggregates = {}
        columns = ("grant_id", "membership_id", "week_id", "five_hour_id")
        for item in db.execute("SELECT grant_id,membership_id,week_id,five_hour_id,state,"
                "SUM(amount_micros) amount,SUM(estimated_micros) estimate FROM account_budget_allocations "
                "WHERE user_id=? GROUP BY grant_id,membership_id,week_id,five_hour_id,state", (row["id"],)).fetchall():
            for column in columns:
                if item[column] is None:
                    continue
                amounts = aggregates.setdefault((column, item[column]), {"used_micros": 0, "reserved_micros": 0, "estimated_micros": 0})
                amounts["used_micros" if item["state"] == "used" else "reserved_micros"] += item["amount"]
                if item["state"] == "used":
                    amounts["estimated_micros"] += item["estimate"]

        def read_amounts(column, resource):
            return aggregates.get((column, resource), {"used_micros": 0, "reserved_micros": 0, "estimated_micros": 0})

        for grant in db.execute("SELECT * FROM account_credit_grants WHERE user_id=? "
                                "ORDER BY CASE WHEN kind='base' THEN 0 ELSE 1 END,created_at,id", (row["id"],)).fetchall():
            amounts = read_amounts("grant_id", grant["id"])
            remaining = None if grant["amount_micros"] is None else grant["amount_micros"] - amounts["used_micros"] - amounts["reserved_micros"]
            sources.append({**dict(grant), **amounts, "remaining_micros": None if remaining is None else max(0, remaining),
                            "overdrawn_micros": max(0, -remaining) if remaining is not None else 0})
        for member in db.execute("SELECT * FROM account_memberships WHERE user_id=? ORDER BY expires_at,created_at,id", (row["id"],)).fetchall():
            status = "revoked" if member["revoked_at"] else "expired" if member["expires_at"] <= _stamp(now) else "active"
            week = self._window(db, member, "week", now)
            if not week and status != "active":
                week = db.execute("SELECT * FROM account_membership_windows WHERE membership_id=? AND kind='week' "
                                  "ORDER BY starts_at DESC LIMIT 1", (member["id"],)).fetchone()
            five = self._window(db, member, "five_hour", now, parent=week) if week else None
            if not five and week and status != "active":
                five = db.execute("SELECT * FROM account_membership_windows WHERE membership_id=? AND kind='five_hour' "
                    "AND parent_id=? ORDER BY starts_at DESC LIMIT 1", (member["id"], week["id"])).fetchone()
            weekly = self._window_public(db, week, member["weekly_limit_micros"], read_amounts=read_amounts)
            hourly = self._window_public(db, five, member["five_hour_limit_micros"], read_amounts=read_amounts)
            memberships.append({**dict(member), **read_amounts("membership_id", member["id"]),
                "status": status, "week": weekly, "five_hour": hourly,
                "available_micros": min(weekly["remaining_micros"], hourly["remaining_micros"]) if status == "active" else 0})
        unlimited = any(source["amount_micros"] is None for source in sources)
        total = None if unlimited else sum(source["amount_micros"] for source in sources)
        used, reserved = sum(source["used_micros"] for source in sources), sum(source["reserved_micros"] for source in sources)
        remaining = None if unlimited else max(0, total - used - reserved)
        return {"currency": "CNY", "window_mode": "first_use", "deduction_order": ["membership", "base", "topup"],
            "available_micros": None if unlimited else max(0, total - used - reserved + sum(member["available_micros"] for member in memberships)),
            "permanent": {"total_micros": total, "used_micros": used, "reserved_micros": reserved,
                "estimated_micros": sum(source["estimated_micros"] for source in sources), "remaining_micros": remaining,
                "overdrawn_micros": 0 if unlimited else max(0, used + reserved - total), "sources": sources},
            "memberships": memberships}

    def _candidates(self, db, user_id, required):
        now, result = self.accounts._now(), []
        for member in db.execute("SELECT * FROM account_memberships WHERE user_id=? AND revoked_at IS NULL "
            "AND starts_at<=? AND expires_at>? ORDER BY expires_at,created_at,id", (user_id, _stamp(now), _stamp(now))).fetchall():
            if required <= 0:
                break
            if not member["weekly_limit_micros"] or not member["five_hour_limit_micros"]:
                continue
            week = self._window(db, member, "week", now, create=True)
            weekly = self._amounts(db, "week_id", week["id"])
            if weekly["used_micros"] + weekly["reserved_micros"] >= week["limit_micros"]:
                continue
            five = self._window(db, member, "five_hour", now, create=True, parent=week)
            candidate = {"grant_id": None, "membership_id": member["id"], "week_id": week["id"], "five_hour_id": five["id"]}
            capacity = self._capacity(db, candidate)
            if capacity:
                result.append(candidate)
                required -= capacity
        result.extend({"grant_id": grant["id"], "membership_id": None, "week_id": None, "five_hour_id": None}
                      for grant in db.execute("SELECT id FROM account_credit_grants WHERE user_id=? "
                          "ORDER BY CASE WHEN kind='base' THEN 0 ELSE 1 END,created_at,id", (user_id,)).fetchall())
        return result

    def _capacity(self, db, candidate):
        if candidate["grant_id"]:
            grant = db.execute("SELECT amount_micros FROM account_credit_grants WHERE id=?", (candidate["grant_id"],)).fetchone()
            if grant[0] is None:
                return None
            amounts = self._amounts(db, "grant_id", candidate["grant_id"])
            return max(0, grant[0] - amounts["used_micros"] - amounts["reserved_micros"])
        remaining = []
        for column in ("week_id", "five_hour_id"):
            window = db.execute("SELECT limit_micros FROM account_membership_windows WHERE id=?", (candidate[column],)).fetchone()
            amounts = self._amounts(db, column, candidate[column])
            remaining.append(max(0, window[0] - amounts["used_micros"] - amounts["reserved_micros"]))
        return min(remaining)

    def _insert(self, db, user_id, turn_id, portion, source, amount, *, state="reserved", estimated=0):
        if not amount:
            return
        ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM account_budget_allocations "
                             "WHERE user_id=? AND turn_id=?", (user_id, turn_id)).fetchone()[0]
        db.execute("INSERT INTO account_budget_allocations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            secrets.token_hex(16), user_id, turn_id, portion, ordinal, source["grant_id"], source["membership_id"],
            source["week_id"], source["five_hour_id"], amount, estimated, state, self.accounts._time()))

    def _add(self, db, user_id, turn_id, portion, amount, candidates, *, observed=False, state="reserved"):
        for candidate in candidates:
            capacity = self._capacity(db, candidate)
            take = amount if capacity is None else min(amount, capacity)
            self._insert(db, user_id, turn_id, portion, candidate, take, state=state)
            amount -= take
            if not amount:
                return
        if amount and observed:
            # Real incurred costs cannot disappear. An overrun becomes permanent
            # debt, rather than receiving a fresh membership window for free.
            self._insert(db, user_id, turn_id, portion, {"grant_id": "base-" + user_id,
                "membership_id": None, "week_id": None, "five_hour_id": None}, amount, state=state)
        elif amount:
            raise _errors()[1]()

    def resize(self, db, row, turn_id, amount, *, portions=None, observed=False):
        self.start_turn(db, row["id"], turn_id)
        current = db.execute("SELECT COALESCE(SUM(amount_micros),0) FROM account_budget_allocations "
            "WHERE user_id=? AND turn_id=? AND state='reserved'", (row["id"], turn_id)).fetchone()[0]
        funds = self.snapshot(db, row)
        if not observed and funds["available_micros"] is not None and (amount - current > funds["available_micros"] or
                current == 0 and funds["available_micros"] == 0):
            raise _errors()[1]()
        if portions is None:
            # Legacy callers represent one provider operation. Runtime callers
            # supply per-model-round portions so time-bound refunds are exact.
            portions = {"turn": amount}
        if (not isinstance(portions, dict) or any(not isinstance(key, str) or not 1 <= len(key) <= 80 or "\x00" in key
                or type(value) is not int or value < 0 for key, value in portions.items()) or sum(portions.values()) != amount):
            raise _errors()[0](422, "用量分摊无效。")
        old = {item[0] for item in db.execute("SELECT portion_id FROM account_budget_portions WHERE user_id=? AND turn_id=?",
                                           (row["id"], turn_id)).fetchall()}
        existing_by_portion = {}
        for portion in dict.fromkeys([*portions, *sorted(old)]):
            desired = portions.get(portion, 0)
            allocations = db.execute("SELECT * FROM account_budget_allocations WHERE user_id=? AND turn_id=? "
                "AND portion_id=? AND state='reserved' ORDER BY ordinal", (row["id"], turn_id, portion)).fetchall()
            existing = sum(item["amount_micros"] for item in allocations)
            existing_by_portion[portion] = existing
            if desired < existing:
                release = existing - desired
                for item in reversed(allocations):
                    value = min(release, item["amount_micros"])
                    db.execute("UPDATE account_budget_allocations SET amount_micros=amount_micros-? WHERE id=?", (value, item["id"]))
                    release -= value
                    if not release:
                        break
        # Release every shrinking portion before allocating a growing one. This
        # lets an initial whole-turn reservation become round portions without
        # needing a second copy of the same money during the transaction.
        for portion, existing in existing_by_portion.items():
            desired = portions.get(portion, 0)
            if desired > existing:
                stored = db.execute("SELECT candidates FROM account_budget_portions WHERE user_id=? AND turn_id=? AND portion_id=?",
                                    (row["id"], turn_id, portion)).fetchone()
                if observed:
                    # An observation may enlarge an admitted round, but cannot
                    # start a fresh membership window for an unadmitted round.
                    candidates = json.loads(stored[0]) if stored else [{"grant_id": "base-" + row["id"],
                        "membership_id": None, "week_id": None, "five_hour_id": None}]
                else:
                    candidates = self._candidates(db, row["id"], desired - existing)
                if not stored:
                    db.execute("INSERT INTO account_budget_portions VALUES(?,?,?,?,?)", (
                        row["id"], turn_id, portion, json.dumps(candidates), self.accounts._time()))
                self._add(db, row["id"], turn_id, portion, desired - existing, candidates, observed=observed)

    def release(self, db, user_id, turn_id):
        db.execute("DELETE FROM account_budget_allocations WHERE user_id=? AND turn_id=? AND state='reserved'", (user_id, turn_id))
        turn = self._turn(db, user_id, turn_id)
        if turn and not turn["settled"]:
            db.execute("DELETE FROM account_budget_portions WHERE user_id=? AND turn_id=?", (user_id, turn_id))
            db.execute("DELETE FROM account_budget_turns WHERE user_id=? AND turn_id=?", (user_id, turn_id))

    def _freeze(self, db, user_id, turn_id):
        turn = self._turn(db, user_id, turn_id)
        if turn["settled"]:
            return
        origins = [dict(item) for item in db.execute("SELECT * FROM account_budget_allocations "
            "WHERE user_id=? AND turn_id=? ORDER BY ordinal", (user_id, turn_id)).fetchall()]
        db.execute("UPDATE account_budget_turns SET original_allocations=?,settled=1 WHERE user_id=? AND turn_id=?",
                   (json.dumps(origins), user_id, turn_id))

    def settle(self, db, row, turn_id, amount, estimated):
        self.start_turn(db, row["id"], turn_id)
        self._freeze(db, row["id"], turn_id)
        self._replace_used(db, row["id"], turn_id, amount, estimated)

    def _replace_used(self, db, user_id, turn_id, amount, estimated=0):
        turn = self._turn(db, user_id, turn_id)
        origins = json.loads(turn["original_allocations"])
        db.execute("DELETE FROM account_budget_allocations WHERE user_id=? AND turn_id=?", (user_id, turn_id))
        remaining, known = amount, max(0, amount - estimated)
        for origin in origins:
            take = min(remaining, origin["amount_micros"])
            capacity = self._capacity(db, origin) if origin["membership_id"] else None
            assigned = take if capacity is None else min(take, capacity)
            for source, piece in ((origin, assigned), ({"grant_id": "base-" + user_id,
                    "membership_id": None, "week_id": None, "five_hour_id": None}, take - assigned)):
                estimate = max(0, piece - known)
                self._insert(db, user_id, turn_id, origin["portion_id"], source, piece, state="used", estimated=estimate)
                known = max(0, known - piece)
            remaining -= take
            if not remaining:
                break
        if remaining:
            contexts = db.execute("SELECT portion_id,candidates FROM account_budget_portions WHERE user_id=? AND turn_id=? ORDER BY created_at,portion_id",
                                  (user_id, turn_id)).fetchall()
            live_portions = {origin["portion_id"] for origin in origins if origin["amount_micros"] > 0}
            candidates = [candidate for context in contexts if context["portion_id"] in live_portions
                          for candidate in json.loads(context["candidates"])]
            if not candidates:
                candidates = [{"grant_id": "base-" + user_id, "membership_id": None, "week_id": None, "five_hour_id": None}]
            self._add(db, user_id, turn_id, "adjustment", remaining, candidates, observed=True, state="used")
            # Mark any unknown amount on the final allocation fragments.
            left = max(0, estimated - sum(item["estimated_micros"] for item in db.execute(
                "SELECT estimated_micros FROM account_budget_allocations WHERE user_id=? AND turn_id=?", (user_id, turn_id)).fetchall()))
            for item in db.execute("SELECT * FROM account_budget_allocations WHERE user_id=? AND turn_id=? ORDER BY ordinal DESC",
                                   (user_id, turn_id)).fetchall():
                take = min(left, item["amount_micros"] - item["estimated_micros"])
                db.execute("UPDATE account_budget_allocations SET estimated_micros=estimated_micros+? WHERE id=?", (take, item["id"]))
                left -= take
                if not left:
                    break

    @staticmethod
    def _admin(db, actor_id, user_id):
        actor = db.execute("SELECT role,disabled FROM accounts WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor["disabled"] or actor["role"] != "admin":
            raise _errors()[0](403, "需要管理员权限。")
        row = db.execute("SELECT * FROM accounts WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise _errors()[0](404, "用户不存在。")
        return row

    @staticmethod
    def _request(label, request_id):
        if (not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or "\x00" in label
                or not isinstance(request_id, str) or not 1 <= len(request_id) <= 100 or "\x00" in request_id):
            raise _errors()[0](422, "请填写有效名称与请求标识。")

    def grant_credit(self, actor_id, user_id, *, amount_micros, label, request_id):
        self._request(label, request_id)
        if type(amount_micros) is not int or not 1 <= amount_micros <= 10**15:
            raise _errors()[0](422, "充值额度必须为正整数微元。")
        with self.accounts._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._admin(db, actor_id, user_id)
            old = db.execute("SELECT * FROM account_credit_grants WHERE user_id=? AND request_id=?", (user_id, request_id)).fetchone()
            if old:
                if old["kind"] != "topup" or old["label"] != label.strip() or old["amount_micros"] != amount_micros:
                    raise _errors()[0](409, "请求标识已用于不同额度操作。")
            else:
                db.execute("INSERT INTO account_credit_grants VALUES(?,?,?,?,?,?,?)", (
                    secrets.token_hex(16), user_id, "topup", label.strip(), amount_micros, request_id, self.accounts._time()))
                self.accounts._audit(db, actor_id, user_id, "grant_credit", ["amount_micros", "credit_grant"], self.accounts._time())
            return {"user": self.accounts._public(db, row), "entitlements": self.snapshot(db, row)}

    def grant_membership(self, actor_id, user_id, *, label, duration_days, weekly_limit_micros, five_hour_limit_micros, request_id):
        self._request(label, request_id)
        if (type(duration_days) is not int or not 1 <= duration_days <= 3660 or any(type(value) is not int
                or not 0 <= value <= 10**15 for value in (weekly_limit_micros, five_hour_limit_micros))):
            raise _errors()[0](422, "会员时长与金额限制无效。")
        with self.accounts._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._admin(db, actor_id, user_id)
            old = db.execute("SELECT * FROM account_memberships WHERE user_id=? AND request_id=?", (user_id, request_id)).fetchone()
            if old:
                if (old["label"], old["duration_days"], old["weekly_limit_micros"], old["five_hour_limit_micros"]) != (
                        label.strip(), duration_days, weekly_limit_micros, five_hour_limit_micros):
                    raise _errors()[0](409, "请求标识已用于不同会员操作。")
            else:
                instant = self.accounts._now()
                db.execute("INSERT INTO account_memberships(id,user_id,label,starts_at,expires_at,duration_days,"
                    "weekly_limit_micros,five_hour_limit_micros,revoked_at,revision,request_id,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                    secrets.token_hex(16), user_id, label.strip(), _stamp(instant), _stamp(instant + timedelta(days=duration_days)),
                    duration_days, weekly_limit_micros, five_hour_limit_micros, None, 1, request_id, _stamp(instant)))
                self.accounts._audit(db, actor_id, user_id, "grant_membership", ["duration_days", "weekly_limit_micros", "five_hour_limit_micros"], self.accounts._time())
            return {"user": self.accounts._public(db, row), "entitlements": self.snapshot(db, row)}

    def revoke_membership(self, actor_id, user_id, membership_id, *, revision, reason):
        if (type(revision) is not int or revision < 1 or not isinstance(reason, str)
                or not 1 <= len(reason.strip()) <= 500 or "\x00" in reason):
            raise _errors()[0](422, "会员版本与撤销原因无效。")
        with self.accounts._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._admin(db, actor_id, user_id)
            member = db.execute("SELECT * FROM account_memberships WHERE id=? AND user_id=?", (membership_id, user_id)).fetchone()
            if not member:
                raise _errors()[0](404, "会员不存在。")
            if member["revision"] != revision or member["revoked_at"]:
                raise _errors()[0](409, "会员状态已改变，请重新读取后复核。")
            db.execute("UPDATE account_memberships SET revoked_at=?,revocation_reason=?,revision=revision+1 WHERE id=?",
                       (self.accounts._time(), reason.strip(), membership_id))
            self.accounts._audit(db, actor_id, user_id, "revoke_membership", ["membership", "reason"], self.accounts._time())
            return {"user": self.accounts._public(db, row), "entitlements": self.snapshot(db, row)}
