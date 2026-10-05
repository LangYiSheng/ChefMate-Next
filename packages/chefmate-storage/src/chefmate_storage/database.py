"""PostgreSQL migrations, atomic dataset replacement, and coherent read APIs."""

from contextlib import contextmanager
from importlib.resources import files
import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .importer import load_export
from .queries import NUTRIENTS, RecipeFilter, where_clause

SCHEMA_VERSION = 1
TABLE_ORDER = (
    "ingredients", "recipes", "recipe_ingredients", "ingredient_aliases", "ingredient_allergens",
    "recipe_steps", "recipe_labels", "recipe_notes", "recipe_references", "recipe_nutrition",
    "recipe_ingredient_nutrition",
)


def _values(row: dict, prefix: str) -> dict:
    return {name: row[prefix + name] for name in NUTRIENTS}


def _estimated_fields(row: dict, prefix: str) -> list[str]:
    return sorted(name for name in NUTRIENTS if row[prefix + name])


class RecipeStore:
    """Each method opens its own connection; no hidden service, pool, or deployment."""

    def __init__(self, dsn: str, *, schema: str = "chefmate", connect_timeout: int = 5):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("A PostgreSQL DSN is required")
        if not isinstance(schema, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("schema must be a lowercase PostgreSQL identifier (maximum 63 characters)")
        if schema in ("public", "information_schema") or schema.startswith("pg_"):
            raise ValueError("Use a dedicated application schema")
        if type(connect_timeout) is not int or not 1 <= connect_timeout <= 120:
            raise ValueError("connect_timeout must be an integer from 1 to 120")
        self._dsn = dsn
        self.schema = schema
        self.connect_timeout = connect_timeout

    @contextmanager
    def _connection(self, *, read: bool = False):
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=self.connect_timeout,
                             application_name="chefmate-storage") as connection:
            if connection.info.server_version < 180000:
                raise RuntimeError("chefmate-storage requires PostgreSQL 18 or newer")
            if read:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            connection.execute("SET LOCAL lock_timeout = '30s'")
            connection.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(self.schema)))
            lock = "pg_advisory_xact_lock_shared" if read else "pg_advisory_xact_lock"
            connection.execute(f"SELECT pg_catalog.{lock}(hashtext('chefmate-storage'), hashtext(%s))", (self.schema,))
            yield connection

    @staticmethod
    def _require_migrated(connection):
        try:
            row = connection.execute("SELECT version FROM schema_migrations WHERE id=1").fetchone()
        except psycopg.errors.UndefinedTable:
            raise RuntimeError("Database is not initialized; run chefmate-storage migrate first") from None
        if not row or row["version"] != SCHEMA_VERSION:
            raise RuntimeError("Database schema version does not match this package; run migrations")

    def migrate(self) -> int:
        """Apply packaged migrations transactionally; never drops a schema or database."""
        with self._connection() as connection:
            connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                id smallint PRIMARY KEY CHECK (id=1), version integer NOT NULL CHECK (version>=0),
                applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            row = connection.execute("SELECT version FROM schema_migrations WHERE id=1").fetchone()
            version = row["version"] if row else 0
            if version > SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this package")
            for target in range(version + 1, SCHEMA_VERSION + 1):
                migration = next((p for p in files("chefmate_storage").joinpath("migrations").iterdir()
                                  if p.name.startswith(f"{target:03d}_") and p.name.endswith(".sql")), None)
                if migration is None:
                    raise RuntimeError(f"Missing packaged migration {target}")
                connection.execute(migration.read_text(encoding="utf-8"), prepare=False)
                connection.execute("""INSERT INTO schema_migrations(id,version) VALUES(1,%s)
                    ON CONFLICT(id) DO UPDATE SET version=excluded.version, applied_at=CURRENT_TIMESTAMP""", (target,))
        return SCHEMA_VERSION

    @staticmethod
    def _metadata(connection) -> dict | None:
        return connection.execute("""SELECT d.data_version,d.export_schema_version,d.exported_at,d.imported_at,
            m.version AS db_schema_version FROM dataset_metadata d CROSS JOIN schema_migrations m
            WHERE d.id=1 AND m.id=1""").fetchone()

    def metadata(self) -> dict | None:
        with self._connection(read=True) as connection:
            self._require_migrated(connection)
            return self._metadata(connection)

    def replace_from_export(self, directory, *, data_version: str | None = None, allow_unapproved: bool = False,
                            allow_empty: bool = False) -> dict:
        """Validate first, then replace only this package's current dataset in one transaction."""
        snapshot = load_export(directory, data_version=data_version, allow_unapproved=allow_unapproved,
                               allow_empty=allow_empty)
        with self._connection() as connection:
            self._require_migrated(connection)
            # DELETE preserves MVCC snapshots and honors foreign keys from external applications.
            connection.execute("DELETE FROM recipes")
            connection.execute("DELETE FROM ingredients")
            for table in TABLE_ORDER:
                rows = snapshot.tables[table]
                if not rows:
                    continue
                columns = tuple(rows[0])
                if any(tuple(row) != columns for row in rows):
                    raise ValueError(f"Inconsistent normalized columns in {table}")
                statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() for _ in columns))
                with connection.cursor() as cursor:
                    cursor.executemany(statement, [tuple(row[column] for column in columns) for row in rows])
            connection.execute("""INSERT INTO dataset_metadata
                (id,data_version,export_schema_version,exported_at) VALUES(1,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET data_version=excluded.data_version,
                export_schema_version=excluded.export_schema_version,exported_at=excluded.exported_at,
                imported_at=CURRENT_TIMESTAMP""",
                (snapshot.data_version, snapshot.export_schema_version, snapshot.exported_at))
            result = self._metadata(connection)
        return result

    def search(self, filters: RecipeFilter | None = None, *, limit: int = 20, offset: int = 0) -> dict:
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("limit must be 1..200 and offset a nonnegative integer")
        if filters is not None and not isinstance(filters, RecipeFilter):
            raise ValueError("filters must be RecipeFilter")
        where, params = where_clause(filters or RecipeFilter())
        with self._connection(read=True) as connection:
            self._require_migrated(connection)
            total = connection.execute("SELECT count(*) AS count FROM recipes r JOIN recipe_nutrition n "
                                       f"ON n.recipe_id=r.recipe_id WHERE {where}", params).fetchone()["count"]
            items = connection.execute("SELECT r.*,n.status AS nutrition_status,n.estimated AS nutrition_estimated "
                                       "FROM recipes r JOIN recipe_nutrition n ON n.recipe_id=r.recipe_id "
                                       f"WHERE {where} ORDER BY r.recipe_id LIMIT %s OFFSET %s",
                                       [*params, limit, offset]).fetchall()
            return {"total": total, "items": items}

    def get_recipe(self, recipe_id: str) -> dict | None:
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            raise ValueError("recipe_id must be nonempty text")
        with self._connection(read=True) as connection:
            self._require_migrated(connection)
            recipe = connection.execute("SELECT * FROM recipes WHERE recipe_id=%s", (recipe_id,)).fetchone()
            if recipe is None:
                return None
            args = (recipe_id,)
            recipe["steps"] = [r["description"] for r in connection.execute(
                "SELECT description FROM recipe_steps WHERE recipe_id=%s ORDER BY ordinal", args)]
            labels = connection.execute("SELECT kind,value,estimated FROM recipe_labels WHERE recipe_id=%s "
                                        "ORDER BY kind,value", args).fetchall()
            for kind, key in (("taste", "taste_tags"), ("cooking_method", "cooking_methods"), ("tool", "required_tools")):
                recipe[key] = [r["value"] for r in labels if r["kind"] == kind]
            notes = connection.execute("SELECT kind,description FROM recipe_notes WHERE recipe_id=%s "
                                       "ORDER BY kind,ordinal", args).fetchall()
            for kind, key in (("warning", "warnings"), ("blocking_reason", "blocking_reasons")):
                recipe[key] = [r["description"] for r in notes if r["kind"] == kind]
            recipe["references"] = connection.execute("SELECT title,url FROM recipe_references WHERE recipe_id=%s "
                                                       "ORDER BY ordinal", args).fetchall()
            nutrition = connection.execute("SELECT * FROM recipe_nutrition WHERE recipe_id=%s", args).fetchone()
            recipe["nutrition"] = {
                "status": nutrition["status"], "estimated": nutrition["estimated"],
                "total": _values(nutrition, "total_"), "per_serving": _values(nutrition, "per_serving_"),
                "estimated_total_fields": _estimated_fields(nutrition, "estimated_total_"),
                "estimated_per_serving_fields": _estimated_fields(nutrition, "estimated_per_serving_"),
                "estimated_servings": recipe["estimated_servings"],
                "tags": [r["value"] for r in labels if r["kind"] == "nutrition"],
                "tags_estimated": any(r["estimated"] for r in labels if r["kind"] == "nutrition"),
            }
            recipe["ingredients"] = connection.execute(
                "SELECT * FROM recipe_ingredients WHERE recipe_id=%s ORDER BY ordinal", args).fetchall()
            aliases = connection.execute("SELECT ordinal,alias FROM ingredient_aliases WHERE recipe_id=%s "
                                         "ORDER BY ordinal,alias", args).fetchall()
            allergens = connection.execute("SELECT ordinal,allergen FROM ingredient_allergens WHERE recipe_id=%s "
                                           "ORDER BY ordinal,allergen", args).fetchall()
            details = {r["ordinal"]: r for r in connection.execute(
                "SELECT * FROM recipe_ingredient_nutrition WHERE recipe_id=%s", args)}
            for item in recipe["ingredients"]:
                ordinal = item["ordinal"]
                item.pop("recipe_id")
                item["aliases"] = [r["alias"] for r in aliases if r["ordinal"] == ordinal]
                item["allergens"] = [r["allergen"] for r in allergens if r["ordinal"] == ordinal]
                detail = details[ordinal]
                item["nutrition"] = {"contribution": _values(detail, "contribution_"),
                                     "per_100g": _values(detail, "per_100g_"), "fdc_id": detail["fdc_id"],
                                     "estimated": detail["estimated"],
                                     "estimated_nutrients": _estimated_fields(detail, "estimated_")}
            recipe["allergens"] = sorted({r["allergen"] for r in allergens})
            return recipe
