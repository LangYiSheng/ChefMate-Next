"""Read-only, paginated administrator history across both application databases."""

import json
import re
from datetime import date, datetime, time, timedelta, timezone

from .accounts import AccountError


LOCAL_ZONE = timezone(timedelta(hours=8))
ACTION_LABELS = {
    "bootstrap_admin": "初始化管理员", "register": "注册账户", "change_password": "修改密码",
    "update_user": "修改用户与权限", "reset_password": "重置用户密码", "update_policy": "修改注册设置",
    "update_pricing": "修改模型计价", "reconcile_usage": "核对用量金额", "archive_reset_data": "备份并重置用户数据",
    "grant_credit": "增加永久额度", "grant_membership": "开通会员", "revoke_membership": "终止会员",
    "migrate_monetary_budgets": "迁移金额额度", "model_profiles.import": "导入模型连接",
    "model_profiles.create": "新增模型连接", "model_profiles.update": "修改模型连接",
    "model_profiles.activate": "切换使用模型", "model_profiles.delete": "删除模型连接",
    "model_profiles.test": "测试模型连接",
}
FIELD_LABELS = {
    "disabled": "账号状态", "developer_allowed": "开发者权限", "role": "用户角色",
    "cost_limit_micros": "金额额度", "legacy_token_budget": "旧额度", "default_cost_limit_micros": "新用户额度",
    "registration_enabled": "开放注册", "currency": "币种", "input_per_million": "输入单价",
    "output_per_million": "输出单价", "cached_input_per_million": "缓存输入单价", "configured": "计价确认",
    "final_cost_micros": "核对金额", "usage_reconciliation": "金额核对记录", "personal_data": "厨房资料",
    "amount_micros": "增加金额", "credit_grant": "额度来源", "duration_days": "会员时长",
    "weekly_limit_micros": "每周额度", "five_hour_limit_micros": "每五小时额度", "membership": "会员状态",
    "reason": "备注", "name": "连接名称", "provider": "服务商", "model": "模型名称",
    "base_url": "服务地址", "api_key": "连接密钥", "clear_api_key": "清除密钥", "api_format": "调用协议",
    "supports_images": "图片支持", "max_images": "图片上限", "stream_tool_calls": "流式工具调用",
    "pricing": "模型计价", "model_profile_id": "模型连接",
}
_JOINS = """FROM account_audit a
    LEFT JOIN accounts actor ON actor.id=a.actor_id
    LEFT JOIN accounts target ON target.id=a.target_id"""


def _text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise AccountError(422, "操作记录筛选条件无效。")
    return value.strip()


def _literal(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").lower() + "%"


def _day(value, *, exclusive=False):
    if not value:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        parsed = date.fromisoformat(value) + timedelta(days=int(exclusive))
        return datetime.combine(parsed, time.min, LOCAL_ZONE).astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise AccountError(422, "请选择有效的起止日期。") from None


def _models(db):
    row = db.execute("SELECT body FROM account_settings WHERE name='model_profiles'").fetchone()
    try:
        profiles = json.loads(row[0])["profiles"] if row else []
        # Extract only labels. API keys and all other private connection values stay in the database.
        return {item["id"]: item["name"] for item in profiles
                if isinstance(item.get("id"), str) and isinstance(item.get("name"), str)}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {}


def _person(row, prefix, models):
    identity = row[prefix + "_id"]
    username = row.get(prefix + "_username")
    display_name = row.get(prefix + "_display_name")
    return {"value": identity or "system", "label": (
        f"{display_name} (@{username})" if username and display_name and display_name != username
        else f"@{username}" if username else models.get(identity) or ("系统" if identity is None else identity))}


def audit_report(accounts, *, q="", action="", actor="", target="", date_from="", date_to="", page=1, page_size=20):
    """Search audit metadata without reading or exposing affected private values.

    Date inputs are inclusive calendar days in UTC+8. LIKE metacharacters in
    search text are literal; all user input is bound as SQL parameters.
    """
    if type(page) is not int or type(page_size) is not int or not 1 <= page <= 1_000_000 or not 1 <= page_size <= 100:
        raise AccountError(422, "页码应大于零，每页可显示 1 到 100 条记录。")
    q, action, actor, target = (_text(value, size) for value, size in ((q, 200), (action, 100), (actor, 128), (target, 128)))
    date_from, date_to = _text(date_from, 10), _text(date_to, 10)
    start, stop = _day(date_from), _day(date_to, exclusive=True)
    if start and stop and start >= stop:
        raise AccountError(422, "开始日期不能晚于结束日期。")
    where, arguments = [], []
    for name, value in (("action", action), ("actor_id", actor), ("target_id", target)):
        if value:
            if name != "action" and value == "system":
                where.append(f"a.{name} IS NULL")
            else:
                where.append(f"a.{name}=?")
                arguments.append(value)
    if start:
        where.append("a.created_at>=?")
        arguments.append(start)
    if stop:
        where.append("a.created_at<?")
        arguments.append(stop)
    with accounts._connect() as db:
        models = _models(db)
        if q:
            columns = ("a.action", "a.fields", "a.actor_id", "a.target_id", "actor.username", "actor.display_name",
                       "target.username", "target.display_name")
            search = [f"LOWER(COALESCE({column},'')) LIKE ? ESCAPE '\\'" for column in columns]
            arguments.extend([_literal(q)] * len(columns))
            for column, labels in (("action", ACTION_LABELS), ("target_id", models)):
                matches = [key for key, label in labels.items() if q.casefold() in label.casefold()]
                if matches:
                    search.append(f"a.{column} IN ({','.join('?' for _ in matches)})")
                    arguments.extend(matches)
            where.append("(" + " OR ".join(search) + ")")
        condition = " WHERE " + " AND ".join(where) if where else ""
        total = db.execute("SELECT COUNT(*) " + _JOINS + condition, arguments).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        # Clamp stale page links after a filter or deletion, without ever returning a blank last page.
        page = min(page, pages)
        rows = db.execute("SELECT a.*,actor.username AS actor_username,actor.display_name AS actor_display_name,"
            "target.username AS target_username,target.display_name AS target_display_name " + _JOINS + condition
            + " ORDER BY a.created_at DESC,a.id DESC LIMIT ? OFFSET ?", [*arguments, page_size, (page - 1) * page_size])
        items = []
        for raw in rows:
            row = dict(raw)
            try:
                fields = json.loads(row["fields"])
            except (ValueError, TypeError):
                fields = []
            row.update(fields=[field for field in fields if isinstance(field, str)] if isinstance(fields, list) else [],
                       action_label=ACTION_LABELS.get(row["action"], row["action"]))
            row["field_labels"] = [FIELD_LABELS.get(field, field) for field in row["fields"]]
            row["actor_label"] = _person(row, "actor", models)["label"]
            row["target_label"] = _person(row, "target", models)["label"]
            items.append(row)
        actions = [{"value": row[0], "label": ACTION_LABELS.get(row[0], row[0])} for row in db.execute(
            "SELECT DISTINCT action FROM account_audit ORDER BY action")]
        people = {}
        for prefix in ("actor", "target"):
            rows = db.execute(f"SELECT DISTINCT a.{prefix}_id,{prefix}.username AS {prefix}_username,"
                f"{prefix}.display_name AS {prefix}_display_name " + _JOINS)
            people[prefix + "s"] = sorted((_person(dict(row), prefix, models) for row in rows), key=lambda item: item["label"])
        return {"items": items, "total": total, "page": page, "page_size": page_size, "total_pages": pages,
                "actions": actions, **people, "timezone": "Asia/Shanghai"}
