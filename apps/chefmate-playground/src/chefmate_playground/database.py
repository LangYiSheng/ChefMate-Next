"""Explicit application databases with SQLite fixtures and PostgreSQL schemas.

The small SQL adapter is restricted to repository-owned statements. It never
accepts SQL from HTTP callers. Mutating aggregate transactions retain the same
serialization on PostgreSQL through a schema-scoped transaction advisory lock.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .sqlite_store import ClosingConnection


_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("数据库 schema 或表名无效。")
    return value


def _parts(statement, delimiter):
    """Split only outside SQL string/identifier literals; repository SQL only."""
    quote, start, index = None, 0, 0
    while index < len(statement):
        char = statement[index]
        if quote:
            if char == quote:
                if index + 1 < len(statement) and statement[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == delimiter:
            yield statement[start:index]
            start = index + 1
        index += 1
    yield statement[start:]


def postgres_statement(statement, *, parameters=False):
    """Translate the intentionally small portable repository SQL subset."""
    result = statement.strip().rstrip(";")
    ignored = bool(re.match(r"INSERT\s+OR\s+IGNORE\b", result, re.IGNORECASE))
    if ignored:
        result = re.sub(r"INSERT\s+OR\s+IGNORE\b", "INSERT", result, count=1, flags=re.IGNORECASE)
        result += " ON CONFLICT DO NOTHING"
    result = re.sub(r"\bBLOB\b", "BYTEA", result, flags=re.IGNORECASE)
    result = re.sub(r"\bINTEGER\b", "BIGINT", result, flags=re.IGNORECASE)
    if parameters:
        # psycopg parses percent placeholders even inside SQL literals.
        result = result.replace("%", "%%")
        result = "%s".join(_parts(result, "?"))
    return result


def _value(value):
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        return int(value)
    return value


class DatabaseRow(tuple):
    """sqlite3.Row-compatible numeric access, mapping conversion, and iteration."""
    def __new__(cls, values, names):
        row = super().__new__(cls, (_value(value) for value in values))
        row._names = tuple(names)
        return row

    def keys(self):
        return list(self._names)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                key = self._names.index(key)
            except ValueError:
                raise IndexError(key) from None
        return super().__getitem__(key)


class _Cursor:
    def __init__(self, cursor, *, mapping=False):
        self.cursor, self.mapping = cursor, mapping

    @property
    def rowcount(self):
        return self.cursor.rowcount

    @property
    def description(self):
        return self.cursor.description

    def _row(self, values):
        if values is None:
            return None
        if self.mapping:
            return DatabaseRow(values, [column.name for column in self.cursor.description])
        return tuple(_value(value) for value in values)

    def fetchone(self):
        return self._row(self.cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        return (self._row(row) for row in self.cursor)


class PostgresConnection:
    def __init__(self, connection, database, *, row_factory=None):
        self.connection, self.database, self.row_factory = connection, database, row_factory

    def execute(self, statement, parameters=None):
        import psycopg
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            statement, parameters = "SELECT pg_advisory_xact_lock(?)", (self.database.lock_key,)
        translated = postgres_statement(statement, parameters=parameters is not None)
        values = tuple(int(value) if type(value) is bool else value for value in parameters) if parameters is not None else None
        try:
            cursor = self.connection.execute(translated, values)
        except psycopg.IntegrityError as error:
            # Existing domain code handles portable duplicate/conflict errors;
            # the surrounding context rolls this failed transaction back.
            raise sqlite3.IntegrityError("数据库约束冲突。") from error
        return _Cursor(cursor, mapping=self.row_factory is not None)

    def executescript(self, script):
        for statement in _parts(script, ";"):
            if statement.strip():
                self.execute(statement)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return self.connection.__exit__(*arguments)


@dataclass
class Database:
    path: Path | str
    url: str = field(default="", repr=False)
    schema: str = "chefmate"
    _initialized: bool = field(default=False, init=False, repr=False)
    _init_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        self.path = Path(self.path)
        _identifier(self.schema)
        if self.url and not self.url.startswith(("postgresql://", "postgres://")):
            raise ValueError("应用数据库需要 PostgreSQL 连接地址。")

    @classmethod
    def coerce(cls, value):
        return value if isinstance(value, cls) else cls(value)

    @property
    def is_postgres(self):
        return bool(self.url)

    @property
    def lock_key(self):
        return int.from_bytes(hashlib.sha256(("chefmate:" + self.schema).encode()).digest()[:8], "big", signed=True)

    @property
    def identity_primary_key(self):
        return "BIGSERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY"

    def _ensure_schema(self):
        if self._initialized:
            return
        import psycopg
        from psycopg import sql
        with self._init_lock:
            if self._initialized:
                return
            with psycopg.connect(self.url, connect_timeout=10) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (self.lock_key,))
                connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            self._initialized = True

    def connect(self, *, row_factory=None, timeout=10):
        if not self.is_postgres:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=timeout, factory=ClosingConnection)
            connection.row_factory = row_factory
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        import psycopg
        from psycopg import sql
        self._ensure_schema()
        connection = psycopg.connect(self.url, connect_timeout=max(1, int(timeout)))
        try:
            connection.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(self.schema)))
            connection.execute("SET statement_timeout='30s'")
            connection.execute("SET lock_timeout='15s'")
        except BaseException:
            connection.close()
            raise
        return PostgresConnection(connection, self, row_factory=row_factory)

    def tables(self, *, connection=None):
        if connection is None:
            with self.connect() as active:
                return self.tables(connection=active)
        if self.is_postgres:
            return [row[0] for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=? AND table_type='BASE TABLE' "
                "ORDER BY table_name", (self.schema,))]
        return [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                    "AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def table_columns(self, table, *, connection=None):
        _identifier(table)
        if connection is None:
            with self.connect() as active:
                return self.table_columns(table, connection=active)
        if self.is_postgres:
            rows = connection.execute("SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns "
                                      "WHERE table_schema=? AND table_name=? ORDER BY ordinal_position", (self.schema, table))
            return [{"name": row[0], "type": row[1], "nullable": row[2] == "YES", "default": row[3]} for row in rows]
        return [{"name": row[1], "type": row[2], "nullable": not bool(row[3]), "default": row[4]}
                for row in connection.execute(f'PRAGMA table_info("{table}")')]

    def json_boolean(self, column, key):
        _identifier(column), _identifier(key)
        if self.is_postgres:
            return f"COALESCE(({column}::jsonb->>'{key}')::boolean::int,0)"
        return f"COALESCE(json_extract({column},'$.{key}'),0)"

    def json_array_length(self, column, key):
        _identifier(column), _identifier(key)
        if self.is_postgres:
            return f"jsonb_array_length({column}::jsonb->'{key}')"
        return f"json_array_length({column},'$.{key}')"

    def rename_schema(self, new_name):
        if not self.is_postgres:
            raise ValueError("SQLite 使用文件归档。")
        _identifier(new_name)
        import psycopg
        from psycopg import sql
        with psycopg.connect(self.url, connect_timeout=10) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (self.lock_key,))
            connection.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
                sql.Identifier(self.schema), sql.Identifier(new_name)))
        self.schema = new_name
        self._initialized = True
