"""Administrator-owned membership presets; applying one never grants a membership."""

from __future__ import annotations

import json
import re
import unicodedata
from uuid import uuid4

from .accounts import AccountError


SETTING_NAME = "membership_templates"
MAX_TEMPLATES = 64
FIELDS = {"name", "label", "duration_days", "weekly_limit_micros", "five_hour_limit_micros"}
_ID = re.compile(r"[0-9a-f]{32}\Z")


def _validated(values):
    if not isinstance(values, dict) or set(values) != FIELDS:
        raise AccountError(422, "会员模板字段不完整或包含未知设置。")
    result = dict(values)
    for field in ("name", "label"):
        value = result[field]
        if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 80
                or any(ord(character) < 32 for character in value)):
            raise AccountError(422, "模板名称与会员名称需为 1 至 80 字的非空文本。")
        result[field] = value.strip()
    if type(result["duration_days"]) is not int or not 1 <= result["duration_days"] <= 3660:
        raise AccountError(422, "会员有效期需为 1 至 3660 天的整数。")
    for field in ("weekly_limit_micros", "five_hour_limit_micros"):
        if type(result[field]) is not int or not 0 <= result[field] <= 10**15:
            raise AccountError(422, "会员额度需为非负整数微元。")
    return result


def _name(value):
    return unicodedata.normalize("NFKC", value).casefold()


class MembershipTemplates:
    def __init__(self, accounts):
        self.accounts = accounts

    @staticmethod
    def _admin(db, actor_id):
        actor = db.execute("SELECT role,disabled FROM accounts WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor["disabled"] or actor["role"] != "admin":
            raise AccountError(403, "需要管理员权限。")

    @staticmethod
    def _read(db):
        row = db.execute("SELECT body FROM account_settings WHERE name=?", (SETTING_NAME,)).fetchone()
        if row is None:
            return {"format_version": 1, "revision": 1, "templates": []}
        try:
            data = json.loads(row[0])
            if (not isinstance(data, dict) or set(data) != {"format_version", "revision", "templates"}
                    or data["format_version"] != 1 or type(data["revision"]) is not int or data["revision"] < 1
                    or not isinstance(data["templates"], list) or len(data["templates"]) > MAX_TEMPLATES):
                raise ValueError
            identities, names = set(), set()
            for item in data["templates"]:
                if (not isinstance(item, dict) or set(item) != FIELDS | {"id", "created_at", "updated_at"}
                        or not isinstance(item["id"], str) or not _ID.fullmatch(item["id"])
                        or any(not isinstance(item[key], str) or not item[key] for key in ("created_at", "updated_at"))):
                    raise ValueError
                _validated({key: item[key] for key in FIELDS})
                normalized = _name(item["name"])
                if item["id"] in identities or normalized in names:
                    raise ValueError
                identities.add(item["id"])
                names.add(normalized)
            return data
        except (ValueError, KeyError, TypeError, AccountError):
            raise AccountError(503, "会员模板配置暂时无法读取，请检查保存的模板资料。") from None

    @staticmethod
    def _public(data):
        return {"revision": data["revision"], "items": data["templates"]}

    def list(self, actor_id):
        with self.accounts._connect() as db:
            self._admin(db, actor_id)
            return self._public(self._read(db))

    def change(self, actor_id, revision, operation, template_id=None, values=None):
        if operation not in {"create", "update", "delete"}:
            raise AccountError(422, "会员模板操作无效。")
        if operation != "create" and (not isinstance(template_id, str) or not _ID.fullmatch(template_id)):
            raise AccountError(404, "会员模板不存在。")
        with self.accounts._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._admin(db, actor_id)
            data = self._read(db)
            if type(revision) is not int or revision != data["revision"]:
                raise AccountError(409, "会员模板已被其他操作修改，请读取最新模板后复核。")
            old = next((item for item in data["templates"] if item["id"] == template_id), None)
            if operation != "create" and old is None:
                raise AccountError(404, "会员模板不存在。")
            instant = self.accounts._time()
            if operation == "delete":
                data["templates"] = [item for item in data["templates"] if item["id"] != template_id]
                changed = FIELDS
            else:
                if operation == "create":
                    if len(data["templates"]) >= MAX_TEMPLATES:
                        raise AccountError(422, f"最多可保存 {MAX_TEMPLATES} 个会员模板。")
                    accepted = _validated(values)
                else:
                    if not isinstance(values, dict) or not values or set(values) - FIELDS:
                        raise AccountError(422, "会员模板修改字段无效。")
                    accepted = _validated({**{key: old[key] for key in FIELDS}, **values})
                if any(item["id"] != template_id and _name(item["name"]) == _name(accepted["name"]) for item in data["templates"]):
                    raise AccountError(409, "这个模板名称已存在，请换一个便于区分的名称。")
                if old and all(old[key] == accepted[key] for key in FIELDS):
                    return self._public(data)
                changed = FIELDS if old is None else {key for key in FIELDS if old[key] != accepted[key]}
                item = {**accepted, "id": old["id"] if old else uuid4().hex,
                        "created_at": old["created_at"] if old else instant, "updated_at": instant}
                template_id = item["id"]
                data["templates"] = [item if entry["id"] == template_id else entry for entry in data["templates"]] if old else [*data["templates"], item]
            data["revision"] += 1
            db.execute("INSERT INTO account_settings(name,body) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET body=excluded.body",
                       (SETTING_NAME, json.dumps(data, ensure_ascii=False)))
            self.accounts._audit(db, actor_id, template_id, f"membership_templates.{operation}", changed, instant)
            return self._public(data)
