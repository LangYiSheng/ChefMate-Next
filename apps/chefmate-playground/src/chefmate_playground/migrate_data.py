"""Explicit, offline SQLite-to-PostgreSQL migration with staging schemas.

Source databases are opened read-only. Destination schemas must not exist;
tables are staged and checked before the final namespace switch. Files are
copied separately into a new runtime directory, never removed from the source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from .accounts import AccountsRepository
from .cookbook import CookbookRepository
from .database import Database
from .journal import JournalRepository
from .memory import MemoryRepository
from .sessions import SessionRepository
from .usage import UsageRepository
from .settings import PERSONAL_PERSISTED
from .sqlite_store import ClosingConnection


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _initialize_workspace(database):
    SessionRepository(database)
    CookbookRepository(database)
    journal = JournalRepository(database)
    MemoryRepository(database, journal=journal)
    UsageRepository(database)


def _copy_database(source, target):
    if source.is_symlink():
        raise ValueError("源数据库不能是符号链接。")
    result = {}
    def fingerprint(rows):
        total = 0
        for row in rows:
            normalized = [bytes(value).hex() if isinstance(value, (bytes, memoryview)) else value for value in row]
            payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            total = (total + int.from_bytes(hashlib.sha256(payload).digest(), "big")) % (1 << 256)
        return format(total, "064x")

    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, factory=ClosingConnection) as original:
        original.execute("BEGIN")
        source_tables = [row[0] for row in original.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        target_tables = target.tables()
        if set(source_tables) - set(target_tables):
            raise ValueError("源库包含未知表，已停止迁移：" + ", ".join(sorted(set(source_tables) - set(target_tables))))
        # Foreign keys only exist in the identity registry; identities precede
        # sessions, reservations and usage. All copied values remain parameters.
        dependencies = {name: index for index, name in enumerate(("accounts", "account_credit_grants",
            "account_memberships", "account_membership_windows", "account_budget_turns",
            "account_budget_portions", "account_budget_allocations"))}
        ordered = sorted(source_tables, key=lambda table: (dependencies.get(table, 100), table))
        target_columns = {table: {item["name"] for item in target.table_columns(table)} for table in source_tables}
        with psycopg.connect(target.url) as destination:
            if source_tables:
                destination.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(sql.SQL(", ").join(
                    sql.Identifier(target.schema, table) for table in source_tables)))
            for table in ordered:
                columns = [row[1] for row in original.execute("PRAGMA table_info(" + _quote(table) + ")")]
                known = target_columns[table]
                if set(columns) - known:
                    raise ValueError("源表包含未知字段，已停止迁移：" + table)
                rows = original.execute("SELECT " + ",".join(_quote(column) for column in columns)
                                        + " FROM " + _quote(table)).fetchall()
                if rows:
                    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(target.schema, table), sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns))
                    with destination.cursor() as cursor:
                        cursor.executemany(statement, rows)
                copied = destination.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(
                    sql.Identifier(target.schema, table))).fetchone()[0]
                if copied != len(rows):
                    raise ValueError("迁移数量核对失败：" + table)
                saved = destination.execute(sql.SQL("SELECT {} FROM {}").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(target.schema, table))).fetchall()
                if fingerprint(rows) != fingerprint(saved):
                    raise ValueError("迁移内容指纹核对失败：" + table)
                result[table] = copied
            # New defaults introduced since the SQLite source version remain
            # present even when an old source has no account_settings table.
            if "account_settings" in target_tables:
                destination.execute(sql.SQL("INSERT INTO {} VALUES('pricing',%s) ON CONFLICT(name) DO NOTHING").format(
                    sql.Identifier(target.schema, "account_settings")), (json.dumps({"currency": "CNY",
                    "input_per_million": "0.000000", "output_per_million": "0.000000",
                    "cached_input_per_million": "0.000000", "revision": 1}),))
            if "account_audit" in target_tables:
                sequence = destination.execute("SELECT pg_get_serial_sequence(%s,'id')",
                    (target.schema + ".account_audit",)).fetchone()[0]
                maximum = destination.execute(sql.SQL("SELECT MAX(id) FROM {}").format(
                    sql.Identifier(target.schema, "account_audit"))).fetchone()[0]
                if sequence and maximum is not None:
                    destination.execute("SELECT setval(%s,%s,true)", (sequence, maximum))
    return result


def migrate_runtime(source_dir, target_dir, url, *, schema_prefix="chefmate", legacy_owner=None,
                    admin_username="", admin_password="", default_cost_limit_micros=5_000_000):
    for candidate in (Path(source_dir), Path(target_dir)):
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError("源目录和目标目录不能是符号链接或目录联接。")
    source, target = Path(source_dir).resolve(), Path(target_dir).resolve()
    if not source.is_dir() or source == target or source in target.parents or target in source.parents:
        raise ValueError("请选择互不包含的源目录和新目标目录。")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,24}", schema_prefix):
        raise ValueError("schema 前缀需为 1 至 25 位小写字母、数字或下划线，并以字母开头。")
    if target.exists() and any(target.iterdir()):
        raise ValueError("目标运行目录必须为空，避免覆盖现有图片和设置。")
    if any(path.is_symlink() or path.is_junction() for path in source.rglob("*")):
        raise ValueError("源目录包含符号链接或目录联接，需先人工核对。")
    if any(path.name.endswith((".db-wal", ".db-shm", ".db-journal")) for path in source.rglob("*")):
        raise ValueError("源 SQLite 存在未收拢的日志。请停止旧服务并完成 checkpoint 后再迁移。")
    if not (source / "accounts.db").is_file() and not (source / "sessions.db").is_file():
        raise ValueError("源目录没有可迁移的 SQLite 身份或会话数据库。")
    staging = "cm_migrate_" + uuid4().hex[:12]
    staged_schemas, mapping, counts = [], {}, {}
    target.mkdir(parents=True, exist_ok=True)
    files = target / (".migration-" + uuid4().hex)
    try:
        with psycopg.connect(url) as db:
            if db.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s OR schema_name LIKE %s",
                          (schema_prefix + "_accounts", schema_prefix + r"\_user\_%")).fetchone():
                raise ValueError("目标应用 schema 已存在。请使用新数据库或不同前缀，不合并现有账号。")
        account_db = Database(files / "accounts.db", url=url, schema=staging + "_accounts")
        staged_schemas.append(account_db.schema)
        accounts = AccountsRepository(account_db, default_cost_limit_micros=default_cost_limit_micros)
        if (source / "accounts.db").is_file():
            counts["accounts"] = _copy_database(source / "accounts.db", account_db)
            with accounts._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                accounts.entitlements.migrate_existing(db)
        else:
            if not legacy_owner or not admin_username or not admin_password or legacy_owner.lower() != admin_username.lower():
                raise ValueError("旧单用户库需显式 --legacy-owner，并配置同名管理员初始化账号与密码。")
            legacy_user = accounts.bootstrap_admin(admin_username, admin_password)
            # The explicitly named owner is an existing single-user operator.
            # Preserve direct access without inventing a new kitchen profile
            # or inferring consent to send that profile to a model provider.
            with accounts._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                instant = accounts._time()
                db.execute("UPDATE accounts SET onboarding_completed=1,updated_at=? WHERE id=?",
                           (instant, legacy_user["id"]))
                accounts._audit(db, legacy_user["id"], legacy_user["id"], "import_legacy_data",
                                ["onboarding_completed", "legacy_owner"], instant)
        mapping[account_db.schema] = schema_prefix + "_accounts"
        users = accounts.list_users()
        if any(not isinstance(user["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", user["id"]) for user in users):
            raise ValueError("源账号 ID 无效，已停止迁移，需人工核对。")
        known_ids = {user["id"] for user in users}
        user_root = source / "users"
        if user_root.is_dir() and any(path.is_dir() and path.name not in known_ids for path in user_root.iterdir()):
            raise ValueError("源目录存在未登记用户的文件，请先人工核对归属。")
        owner = next((user for user in users if user["username"] == (legacy_owner or "").lower()), None)
        if legacy_owner and owner is None:
            raise ValueError("指定的旧数据归属账号不存在。")
        if (source / "sessions.db").is_file() and not legacy_owner:
            # New multi-user runtimes can also contain an unused control DB.
            with sqlite3.connect((source / "sessions.db").as_uri() + "?mode=ro", uri=True, factory=ClosingConnection) as db:
                table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
                if table and db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]:
                    raise ValueError("旧单用户库包含对话，请用 --legacy-owner 明确归属。")
        for user in users:
            user_id = user["id"]
            source_db = source / "users" / user_id / "sessions.db"
            if owner and user_id == owner["id"] and (source / "sessions.db").is_file():
                if source_db.is_file():
                    raise ValueError("同一用户同时有旧库和独立库，需先人工决定归属，不能自动合并。")
                source_db = source / "sessions.db"
            if not source_db.is_file():
                continue
            workspace = Database(files / "users" / user_id / "sessions.db", url=url, schema=staging + "_u_" + user_id)
            staged_schemas.append(workspace.schema)
            _initialize_workspace(workspace)
            counts[user_id] = _copy_database(source_db, workspace)
            mapping[workspace.schema] = schema_prefix + "_user_" + user_id
        # Only non-database runtime files are copied. The original tree remains
        # untouched; SQL databases never masquerade as active SQLite files.
        files.mkdir(parents=True, exist_ok=True)
        if (source / "settings.json").is_file():
            shutil.copy2(source / "settings.json", files / "settings.json")
        for user in users:
            user_id = user["id"]
            origin = source / "users" / user_id
            own_files = files / "users" / user_id
            own_files.mkdir(parents=True, exist_ok=True)
            if (origin / "images").is_dir():
                shutil.copytree(origin / "images", own_files / "images")
            settings_file = origin / "settings.json"
            if owner and user_id == owner["id"] and not settings_file.is_file():
                settings_file = source / "settings.json"
            if settings_file.is_file():
                values = json.loads(settings_file.read_text(encoding="utf-8"))
                (own_files / "settings.json").write_text(json.dumps({key: values[key] for key in PERSONAL_PERSISTED
                    if key in values}, ensure_ascii=False, indent=2), encoding="utf-8")
        if owner and (source / "images").is_dir():
            own_images = files / "users" / owner["id"] / "images"
            own_images.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source / "images", own_images)
        with psycopg.connect(url) as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("chefmate-migration:" + schema_prefix,))
            for old, new in mapping.items():
                if db.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (new,)).fetchone():
                    raise ValueError("目标 schema 在迁移期间被创建，已停止，源数据不变。")
                db.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(sql.Identifier(old), sql.Identifier(new)))
            for item in files.iterdir():
                item.rename(target / item.name)
        try:
            files.rmdir()
        except OSError:
            pass  # Empty staging directory cleanup cannot invalidate a committed migration.
        return {"ok": True, "storage": "postgresql", "users": len(users), "counts": counts,
                "schemas": list(mapping.values()), "source_preserved": True}
    except Exception:
        # Drop only the exact randomly-created staging schemas, never a user's
        # existing/final namespace. Files and source are preserved for review.
        with psycopg.connect(url) as db:
            for schema in staged_schemas:
                db.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        raise


def main():
    parser = argparse.ArgumentParser(description="将离线 SQLite 运行目录迁移到全新 PostgreSQL 应用命名空间")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--schema-prefix", default="chefmate")
    parser.add_argument("--legacy-owner")
    parser.add_argument("--apply", action="store_true", help="确认旧服务已停止且目标数据库为空")
    args = parser.parse_args()
    if not args.apply:
        parser.error("需先停止旧服务并备份，再显式传入 --apply")
    url = os.environ.get("CHEFMATE_APP_DATABASE_URL", "")
    if not url:
        parser.error("请通过 CHEFMATE_APP_DATABASE_URL 环境变量配置目标，避免把密码放在命令参数中")
    try:
        result = migrate_runtime(args.source_dir, args.target_dir, url, schema_prefix=args.schema_prefix,
            legacy_owner=args.legacy_owner, admin_username=os.environ.get("CHEFMATE_ADMIN_USERNAME", ""),
            admin_password=os.environ.get("CHEFMATE_ADMIN_PASSWORD", ""),
            default_cost_limit_micros=int(os.environ.get("CHEFMATE_NEW_USER_COST_LIMIT_MICROS", "5000000")))
    except Exception:
        parser.exit(1, "迁移未完成。源数据保留；请核对源格式、目标为空、连接与权限。连接详情不写入日志。\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
