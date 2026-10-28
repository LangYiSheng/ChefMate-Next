"""Private model connections with associated prices and exactly one active entry."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import ValidationError

from .accounts import AccountError, _price
from .settings import ModelSettings, build_agent, persist_settings, probe_model, public_settings, settings_path


MODEL_FIELDS = ("model", "base_url", "api_key", "api_format", "stream_tool_calls", "supports_images", "max_images")
PRICE_FIELDS = ("input_per_million", "cached_input_per_million", "output_per_million")
MAX_PROFILES = 32


def _text(value, name, limit=80):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit or any(ord(char) < 32 for char in value):
        raise AccountError(422, f"{name}需为 1 至 {limit} 字的非空文本。")
    return value.strip()


def _pricing(values, previous=None):
    if not isinstance(values, dict) or set(values) - {*PRICE_FIELDS, "currency", "configured"}:
        raise AccountError(422, "模型计价字段无效。")
    current = {"currency": "CNY", **{field: "0.000000" for field in PRICE_FIELDS}, "configured": False,
               **(previous or {})}
    if values.get("currency", "CNY") != "CNY" or "configured" in values and type(values["configured"]) is not bool:
        raise AccountError(422, "模型计价使用人民币，计价确认必须为布尔值。")
    for field in PRICE_FIELDS:
        if field in values:
            current[field] = _price(values[field])
    if "configured" in values:
        current["configured"] = values["configured"]
    return {field: current[field] for field in ("currency", *PRICE_FIELDS, "configured")}


def _origin(value):
    parsed = urlsplit(value)
    return parsed.scheme, parsed.netloc


def _restore_file(path: Path, content: bytes | None):
    if content is None:
        path.unlink(missing_ok=True)
        return
    temporary = None
    try:
        with NamedTemporaryFile("wb", dir=path.parent, delete=False, suffix=".tmp") as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ModelProfilesRegistry:
    """Canonical private connections live in the account database.

    The active price is mirrored in the existing pricing setting, so every
    reservation continues to retain a single immutable tariff snapshot. The
    mirror remains authoritative for compatible legacy price-only writes.
    File writes are compensated on failure; startup hydrates the canonical
    connection again after a crash between the file and database commits.
    """

    def __init__(self, pool):
        self.pool, self.accounts = pool, pool.accounts
        with self.accounts._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM account_settings WHERE name='model_profiles'").fetchone() is None:
                config = pool.control.state.context.config
                pricing = json.loads(db.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])
                profile_id = uuid4().hex
                instant = self.accounts._time()
                profile = {"id": profile_id, "revision": 1, "name": "默认模型连接", "provider": "默认提供商",
                    **{field: getattr(config, field) for field in MODEL_FIELDS},
                    "pricing": _pricing({field: pricing[field] for field in ("currency", *PRICE_FIELDS, "configured")}),
                    "created_at": instant, "updated_at": instant}
                data = {"format_version": 1, "revision": 1, "active_id": profile_id, "profiles": [profile]}
                db.execute("INSERT INTO account_settings VALUES('model_profiles',?)", (json.dumps(data),))
                self.accounts._audit(db, None, None, "model_profiles.import", ["model", "pricing"], instant)
            data = self._read(db)
            active = self._find(data, data["active_id"])
            self._mirror_price(db, active)
        # No HTTP requests are accepted during application assembly.
        active_config = self._config(active)
        if any(getattr(active_config, key) != getattr(pool.control.state.context.config, key) for key in MODEL_FIELDS):
            replacement, components = self._build(active_config)
            persist_settings(active_config)
            self._install(active_config, replacement, components)

    def _read(self, db):
        row = db.execute("SELECT body FROM account_settings WHERE name='model_profiles'").fetchone()
        try:
            data = json.loads(row[0])
            if data.get("format_version") != 1 or not isinstance(data["profiles"], list):
                raise ValueError
            active = self._find(data, data["active_id"])
            mirror = json.loads(db.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])
            if mirror.get("model_profile_id", active["id"]) == active["id"]:
                active["pricing"] = _pricing({key: mirror[key] for key in ("currency", *PRICE_FIELDS, "configured")})
            return data
        except (TypeError, ValueError, KeyError, IndexError):
            raise AccountError(503, "模型配置暂时无法读取，请检查存储或恢复备份。") from None

    @staticmethod
    def _find(data, profile_id):
        profile = next((item for item in data["profiles"] if item["id"] == profile_id), None)
        if profile is None:
            raise AccountError(404, "模型连接不存在。")
        return profile

    @staticmethod
    def _actor(db, actor_id):
        actor = db.execute("SELECT role,disabled FROM accounts WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor["disabled"] or actor["role"] != "admin":
            raise AccountError(403, "需要管理员权限。")

    @staticmethod
    def _revision(data, revision):
        if type(revision) is not int or revision != data["revision"]:
            raise AccountError(409, "模型配置已变化，请保留草稿并读取最新版本。")

    def _public(self, data, *, db=None):
        tests = {}
        if db is not None:
            stored = db.execute("SELECT body FROM account_settings WHERE name='model_profile_tests'").fetchone()
            tests = json.loads(stored[0]) if stored else {}
        result = []
        for item in data["profiles"]:
            entry = {key: item[key] for key in ("id", "revision", "name", "provider", "model", "base_url", "api_format",
                "stream_tool_calls", "supports_images", "max_images", "pricing", "created_at", "updated_at")}
            entry.update(active=item["id"] == data["active_id"], api_key_set=bool(item["api_key"]), last_test=tests.get(item["id"]))
            if entry["last_test"] is not None:
                entry["last_test"]["outdated"] = entry["last_test"]["profile_revision"] != item["revision"]
            result.append(entry)
        return {"revision": data["revision"], "active_id": data["active_id"], "items": result}

    def list(self, actor_id):
        with self.accounts._connect() as db:
            self._actor(db, actor_id)
            return self._public(self._read(db), db=db)

    def _config(self, profile):
        return replace(self.pool.control.state.context.config, **{field: profile[field] for field in MODEL_FIELDS})

    def _build(self, config):
        context = self.pool.control.state.context
        agent, components = build_agent(config, context.retrieval, memory=context.memory,
            cookbook=context.cookbook, journal=context.journal, preferences=context.preferences)
        return (self.pool.injected_agent if self.pool.injected_agent is not None else agent), components

    def _install(self, config, agent, components):
        context = self.pool.control.state.context
        context.config, context.agent, context.components, context.setup_error = config, agent, components, None
        context.app.state.config, context.app.state.components = config, components
        context.manager.agent = agent
        context.manager.debug_secrets = (config.api_key, config.database_url, config.app_database_url)

    @staticmethod
    def price_snapshot(profile):
        return {**profile["pricing"], "model_profile_id": profile["id"], "model_profile_label": profile["name"]}

    def _mirror_price(self, db, profile, *, force_revision=False):
        current = json.loads(db.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])
        values = self.price_snapshot(profile)
        if force_revision or any(current.get(key) != value for key, value in values.items()):
            values["revision"] = current["revision"] + 1
            db.execute("UPDATE account_settings SET body=? WHERE name='pricing'", (json.dumps(values),))

    def _validated(self, values, previous=None):
        allowed = {"name", "provider", *MODEL_FIELDS, "clear_api_key", "pricing"}
        if not isinstance(values, dict) or not values or set(values) - allowed:
            raise AccountError(422, "模型连接字段无效。")
        current = dict(previous or {"api_key": "", "api_format": "chat_completions", "stream_tool_calls": False,
            "supports_images": False, "max_images": 3})
        current.update({key: value for key, value in values.items() if key not in {"api_key", "clear_api_key", "pricing"}})
        current["name"] = _text(current.get("name"), "连接名称")
        current["provider"] = _text(current.get("provider"), "提供商名称")
        key = values.get("api_key")
        if hasattr(key, "get_secret_value"):
            key = key.get_secret_value()
        if key is not None and (not isinstance(key, str) or len(key) > 4096 or any(char in key for char in "\r\n\x00")):
            raise AccountError(422, "密钥格式无效。")
        key = key.strip() if key else ""
        clear = values.get("clear_api_key", False)
        if type(clear) is not bool or clear and key:
            raise AccountError(422, "清除密钥与填写新密钥不能同时使用。")
        try:
            checked = ModelSettings.model_validate({field: current.get(field) for field in MODEL_FIELDS if field != "api_key"})
        except (ValidationError, ValueError):
            raise AccountError(422, "请填写有效的模型名称、端点、协议和图片能力。") from None
        current.update(checked.model_dump(exclude={"api_key"}))
        if previous and (current["provider"] != previous["provider"] or _origin(current["base_url"]) != _origin(previous["base_url"])) and not key and not clear:
            raise AccountError(422, "更换提供商或端点来源时，请填写专用新密钥，或明确清除旧密钥。")
        current["api_key"] = "" if clear else key or current["api_key"]
        current["pricing"] = _pricing(values.get("pricing", {}), current.get("pricing"))
        return current

    def _mutate(self, actor_id, revision, operation, profile_id=None, values=None):
        context = self.pool.control.state.context
        previous_live = context.config, context.agent, context.components, context.setup_error
        path, original = settings_path(context.config), None
        wrote_file = False
        try:
            with self.accounts._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._actor(db, actor_id)
                data = self._read(db)
                self._revision(data, revision)
                active_changed = False
                instant = self.accounts._time()
                if operation == "create":
                    if len(data["profiles"]) >= MAX_PROFILES:
                        raise AccountError(422, "最多保存 32 个模型连接。")
                    profile = {**self._validated(values), "id": uuid4().hex, "revision": 1,
                               "created_at": instant, "updated_at": instant}
                    profile_id = profile["id"]
                    data["profiles"].append(profile)
                else:
                    profile = self._find(data, profile_id)
                    if operation == "delete":
                        if profile_id == data["active_id"]:
                            raise AccountError(409, "请先切换到其他模型，再删除当前连接。")
                        data["profiles"].remove(profile)
                        stored_tests = db.execute("SELECT body FROM account_settings WHERE name='model_profile_tests'").fetchone()
                        if stored_tests:
                            tests = json.loads(stored_tests[0])
                            tests.pop(profile_id, None)
                            db.execute("UPDATE account_settings SET body=? WHERE name='model_profile_tests'", (json.dumps(tests),))
                    elif operation == "update":
                        updated = self._validated(values, profile)
                        updated.update(revision=profile["revision"] + 1, updated_at=instant)
                        data["profiles"][data["profiles"].index(profile)] = updated
                        profile = updated
                        active_changed = profile_id == data["active_id"]
                    elif operation == "activate":
                        if not profile["model"] or not profile["api_key"]:
                            raise AccountError(422, "请先保存模型名称和专用密钥，再启用这个连接。")
                        data["active_id"] = profile_id
                        active_changed = True
                    else:
                        raise AccountError(422, "模型操作无效。")
                if active_changed:
                    if self.pool.busy():
                        raise AccountError(409, "还有用户请求正在运行，请等待结束后更新当前模型。")
                    config = self._config(profile)
                    replacement, components = self._build(config)
                    original = path.read_bytes() if path.is_file() else None
                    persist_settings(config)
                    wrote_file = True
                    self._mirror_price(db, profile, force_revision=True)
                    self._install(config, replacement, components)
                data["revision"] += 1
                db.execute("UPDATE account_settings SET body=? WHERE name='model_profiles'", (json.dumps(data),))
                self.accounts._audit(db, actor_id, profile_id, "model_profiles." + operation,
                    set(values or {}) - {"api_key"} | ({"api_key"} if values and (values.get("api_key") or values.get("clear_api_key")) else set()), instant)
                result = self._public(data, db=db)
            return result
        except BaseException:
            if wrote_file:
                self._install(*previous_live[:3])
                context.setup_error = previous_live[3]
                _restore_file(path, original)
            raise

    async def change(self, actor_id, revision, operation, profile_id=None, values=None):
        async with self.pool.lock:
            return await asyncio.to_thread(self._mutate, actor_id, revision, operation, profile_id, values)

    async def update_legacy_active(self, actor_id, values, expected_settings_revision):
        """Compatibility hook for the former single-model settings endpoint."""
        async with self.pool.lock:
            current = public_settings(self.pool.control.state.context.config, components=self.pool.control.state.context.components)
            if expected_settings_revision != current["revision"]:
                raise AccountError(409, "设置已变化，请保留草稿并重新读取。")
            with self.accounts._connect() as db:
                data = self._read(db)
            await asyncio.to_thread(self._mutate, actor_id, data["revision"], "update", data["active_id"], values)
            return public_settings(self.pool.control.state.context.config, components=self.pool.control.state.context.components)

    def update_legacy_pricing(self, actor_id, changes):
        """Price-only compatibility writes also advance the model editor CAS.

        They remain allowed during existing turns: each turn already owns its
        frozen tariff, and a price-only update does not replace any agent.
        """
        if not isinstance(changes, dict) or not changes or set(changes) - {*PRICE_FIELDS, "currency", "revision"}:
            raise AccountError(422, "计价设置无效。")
        with self.accounts._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._actor(db, actor_id)
            current = json.loads(db.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])
            if "revision" in changes and (type(changes["revision"]) is not int or changes["revision"] != current["revision"]):
                raise AccountError(409, "计价设置已变化，请保留草稿并读取最新版本。")
            data = self._read(db)
            active = self._find(data, data["active_id"])
            active["pricing"] = _pricing({**{key: value for key, value in changes.items() if key != "revision"},
                                         "configured": True}, active["pricing"])
            active.update(revision=active["revision"] + 1, updated_at=self.accounts._time())
            data["revision"] += 1
            self._mirror_price(db, active, force_revision=True)
            db.execute("UPDATE account_settings SET body=? WHERE name='model_profiles'", (json.dumps(data),))
            self.accounts._audit(db, actor_id, active["id"], "update_pricing", set(changes) - {"revision"}, self.accounts._time())
            return json.loads(db.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])

    async def test(self, actor_id, profile_id, revision=None):
        with self.accounts._connect() as db:
            self._actor(db, actor_id)
            data = self._read(db)
            if revision is not None:
                self._revision(data, revision)
            profile = self._find(data, profile_id)
        if not profile["api_key"] or not profile["model"]:
            raise AccountError(422, "请先保存模型名称和专用密钥，再测试这个连接。")
        candidate, tariff = self._config(profile), self.price_snapshot(profile)
        turn_id = "model-profile-probe-" + uuid4().hex
        try:
            self.accounts.reserve_usage(actor_id, turn_id, pricing_override=tariff)
            cost = int((Decimal(8192) * max(Decimal(tariff["input_per_million"]), Decimal(tariff["cached_input_per_million"]))
                + Decimal(512) * Decimal(tariff["output_per_million"])).to_integral_value(rounding=ROUND_CEILING))
            self.accounts.resize_usage(actor_id, turn_id, tokens=8704, cost_micros=cost)
        except AccountError:
            self.accounts.release_usage(actor_id, turn_id)
            raise
        observed, result = {"attempted_calls": 0, "calls": []}, None
        def observe(usage):
            nonlocal observed
            observed = usage
        try:
            result = await probe_model(candidate, on_usage=observe)
        finally:
            self.accounts.settle_usage(actor_id, turn_id, observed, session_id="model-profile-test:" + profile_id,
                model=candidate.model, status="completed" if result and result.get("ok") else "error")
        from .debug import redact_debug
        safe = redact_debug({key: result[key] for key in ("ok", "message", "api_format", "stream_tool_calls", "http_status") if key in result},
                            (candidate.api_key, candidate.database_url, candidate.app_database_url))
        latest = {**safe, "profile_revision": profile["revision"], "tested_at": self.accounts._time()}
        with self.accounts._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            stored = db.execute("SELECT body FROM account_settings WHERE name='model_profile_tests'").fetchone()
            tests = json.loads(stored[0]) if stored else {}
            if any(item["id"] == profile_id for item in self._read(db)["profiles"]):
                tests[profile_id] = latest
                db.execute("INSERT INTO account_settings VALUES('model_profile_tests',?) ON CONFLICT(name) DO UPDATE SET body=excluded.body",
                           (json.dumps(tests),))
            self.accounts._audit(db, actor_id, profile_id, "model_profiles.test", ["model_profile_id"], self.accounts._time())
        return {**safe, "profile_id": profile_id, "profile_revision": profile["revision"], "pricing": tariff}
