"""PostgreSQL migrations, atomic dataset replacement, and coherent read APIs."""

from contextlib import contextmanager
from importlib.resources import files
import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .importer import load_export
from .queries import IngredientSelector, NUTRIENTS, RecipeFilter, RecipeSort, order_clause, where_clause

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


class SeedConflictError(ValueError):
    """Automatic initialization never replaces an existing, different dataset."""


class RecipeStore:
    """Each method opens its own connection; no hidden service, pool, or deployment."""

    def __init__(self, dsn: str, *, schema: str = "chefmate", connect_timeout: int = 5,
                 read_statement_timeout_ms: int = 15000):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("A PostgreSQL DSN is required")
        if not isinstance(schema, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("schema must be a lowercase PostgreSQL identifier (maximum 63 characters)")
        if schema in ("public", "information_schema") or schema.startswith("pg_"):
            raise ValueError("Use a dedicated application schema")
        if type(connect_timeout) is not int or not 1 <= connect_timeout <= 120:
            raise ValueError("connect_timeout must be an integer from 1 to 120")
        if type(read_statement_timeout_ms) is not int or not 100 <= read_statement_timeout_ms <= 120000:
            raise ValueError("read_statement_timeout_ms must be an integer from 100 to 120000")
        self._dsn = dsn
        self.schema = schema
        self.connect_timeout = connect_timeout
        self.read_statement_timeout_ms = read_statement_timeout_ms

    @contextmanager
    def _connection(self, *, read: bool = False):
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=self.connect_timeout,
                             application_name="chefmate-storage") as connection:
            if connection.info.server_version < 180000:
                raise RuntimeError("chefmate-storage requires PostgreSQL 18 or newer")
            if read:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                connection.execute("SELECT pg_catalog.set_config('statement_timeout', %s, true)",
                                   (str(self.read_statement_timeout_ms),))
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
                self._insert_rows(connection, table, snapshot.tables[table])
            self._write_metadata(connection, snapshot)
            result = self._metadata(connection)
        return result

    @staticmethod
    def _insert_rows(connection, table, rows):
        if not rows:
            return
        columns = tuple(rows[0])
        if any(tuple(row) != columns for row in rows):
            raise ValueError(f"Inconsistent normalized columns in {table}")
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(sql.Placeholder() for _ in columns))
        with connection.cursor() as cursor:
            cursor.executemany(statement, [tuple(row[column] for column in columns) for row in rows])

    @staticmethod
    def _write_metadata(connection, snapshot):
        connection.execute("""INSERT INTO dataset_metadata
            (id,data_version,export_schema_version,exported_at) VALUES(1,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET data_version=excluded.data_version,
            export_schema_version=excluded.export_schema_version,exported_at=excluded.exported_at,
            imported_at=CURRENT_TIMESTAMP""",
            (snapshot.data_version, snapshot.export_schema_version, snapshot.exported_at))

    def seed_from_export(self, directory, *, data_version: str, allow_unapproved: bool = False) -> dict:
        """Seed empty tables or verify the identical dataset; never replace data.

        Admission and row verification use the same exclusive advisory lock as
        explicit imports. Temporary typed tables let PostgreSQL compare values
        after its normal numeric conversion, including nullable columns.
        """
        snapshot = load_export(directory, data_version=data_version, allow_unapproved=allow_unapproved)
        with self._connection() as connection:
            self._require_migrated(connection)
            existing = self._metadata(connection)
            if existing and (existing["data_version"] != snapshot.data_version
                             or existing["export_schema_version"] != snapshot.export_schema_version
                             or existing["exported_at"] != snapshot.exported_at):
                raise SeedConflictError("Existing dataset differs; automatic seed preserves it without replacement")
            counts = {table: connection.execute(sql.SQL("SELECT count(*) AS count FROM {}").format(
                sql.Identifier(table))).fetchone()["count"] for table in TABLE_ORDER}
            if not existing:
                if any(counts.values()):
                    raise SeedConflictError("Existing rows have no dataset identity; automatic seed preserves them")
                for table in TABLE_ORDER:
                    self._insert_rows(connection, table, snapshot.tables[table])
                self._write_metadata(connection, snapshot)
            for table in TABLE_ORDER:
                temporary = "seed_check_" + table
                connection.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE {}) ON COMMIT DROP").format(
                    sql.Identifier(temporary), sql.Identifier(table)))
                self._insert_rows(connection, temporary, snapshot.tables[table])
                different = connection.execute(sql.SQL("""SELECT EXISTS (
                    (SELECT * FROM {actual} EXCEPT ALL SELECT * FROM {expected}) UNION ALL
                    (SELECT * FROM {expected} EXCEPT ALL SELECT * FROM {actual})
                    ) AS different""").format(actual=sql.Identifier(table),
                                              expected=sql.Identifier(temporary))).fetchone()["different"]
                if different:
                    raise SeedConflictError("Existing dataset contents differ; automatic seed preserves all rows")
            return {**self._metadata(connection), "status": "verified" if existing else "seeded",
                    "recipe_count": snapshot.recipe_count,
                    "table_counts": {table: len(snapshot.tables[table]) for table in TABLE_ORDER}}

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

    @contextmanager
    def read_session(self):
        """One coherent, read-only dataset for multiple queries. Session expires on exit."""
        with self._connection(read=True) as connection:
            self._require_migrated(connection)
            session = RecipeReadSession(connection, self._metadata(connection))
            try:
                yield session
            finally:
                session._closed = True

    def get_recipe(self, recipe_id: str) -> dict | None:
        with self.read_session() as session:
            return session.get_recipe(recipe_id)


class RecipeReadSession:
    """Read operations bound to one repeatable-read transaction; created by read_session."""

    def __init__(self, connection, metadata):
        self._connection = connection
        self._metadata = metadata
        self._closed = False

    def _active(self):
        if self._closed:
            raise RuntimeError("Read session is closed")
        return self._connection

    def metadata(self) -> dict | None:
        self._active()
        return dict(self._metadata) if self._metadata else None

    def selector_exists(self, ingredient_id: str, canonical_name: str | None = None) -> bool:
        connection = self._active()
        statement = "SELECT 1 FROM recipe_ingredients WHERE ingredient_id=%s"
        args = [ingredient_id]
        if canonical_name is not None:
            statement += " AND canonical_name=%s"
            args.append(canonical_name)
        return connection.execute(statement + " LIMIT 1", args).fetchone() is not None

    def missing_selectors(self, selectors: tuple[IngredientSelector, ...]) -> list[dict]:
        """Validate a whole request in one bound query instead of one round trip per ID."""
        connection = self._active()
        if not isinstance(selectors, (tuple, list)) or any(not isinstance(value, IngredientSelector)
                                                         for value in selectors):
            raise ValueError("selectors must contain IngredientSelector values")
        selectors = tuple(dict.fromkeys(selectors))
        if not selectors:
            return []
        return connection.execute("""WITH requested AS (
            SELECT * FROM unnest(%s::text[],%s::text[]) AS s(ingredient_id,canonical_name)
        ) SELECT s.ingredient_id,s.canonical_name FROM requested s WHERE NOT EXISTS (
            SELECT 1 FROM recipe_ingredients i WHERE i.ingredient_id=s.ingredient_id
            AND (s.canonical_name IS NULL OR i.canonical_name=s.canonical_name))""",
            ([value.ingredient_id for value in selectors], [value.canonical_name for value in selectors])).fetchall()

    def search_options(self) -> dict:
        connection = self._active()
        labels = connection.execute("SELECT kind,value,count(DISTINCT recipe_id) AS recipe_count "
                                    "FROM recipe_labels GROUP BY kind,value ORDER BY kind,value").fetchall()
        result = {kind: [] for kind in ("taste", "cooking_method", "tool", "nutrition", "difficulty",
                                        "category", "allergen")}
        for row in labels:
            result[row["kind"]].append({"code": row["value"], "recipe_count": row["recipe_count"]})
        for kind, statement in (
            ("difficulty", "SELECT difficulty AS code,count(*) AS recipe_count FROM recipes "
                           "WHERE difficulty IS NOT NULL GROUP BY difficulty ORDER BY difficulty"),
            ("category", "SELECT category AS code,count(DISTINCT recipe_id) AS recipe_count "
                         "FROM recipe_ingredients GROUP BY category ORDER BY category"),
            ("allergen", "SELECT allergen AS code,count(DISTINCT recipe_id) AS recipe_count "
                         "FROM ingredient_allergens GROUP BY allergen ORDER BY allergen"),
        ):
            result[kind] = connection.execute(statement).fetchall()
        return result

    def lookup_ingredients(self, queries: tuple[str, ...] = (), fdc_ids: tuple[str, ...] = ()) -> list[dict]:
        """Lookup the current catalogue; names/aliases are evidence, FDC is reference only."""
        connection = self._active()
        clauses, args = [], []
        for query in queries:
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append("(i.ingredient_id ILIKE %s ESCAPE '\\' OR i.canonical_name ILIKE %s ESCAPE '\\' "
                           "OR EXISTS (SELECT 1 FROM ingredient_aliases a WHERE a.recipe_id=i.recipe_id "
                           "AND a.ordinal=i.ordinal AND a.alias ILIKE %s ESCAPE '\\'))")
            args.extend((pattern, pattern, pattern))
        if fdc_ids:
            clauses.append("EXISTS (SELECT 1 FROM recipe_ingredient_nutrition d WHERE d.recipe_id=i.recipe_id "
                           "AND d.ordinal=i.ordinal AND d.fdc_id=ANY(%s))")
            args.append(list(fdc_ids))
        if not clauses:
            return []
        statement = """WITH selected AS (
            SELECT DISTINCT i.ingredient_id,i.canonical_name FROM recipe_ingredients i WHERE """ + (
                " OR ".join(clauses)) + """
            ) SELECT i.ingredient_id,i.canonical_name,array_agg(DISTINCT i.category ORDER BY i.category) AS categories,
                count(DISTINCT i.recipe_id) AS recipe_count,
                count(DISTINCT i.recipe_id) FILTER (WHERE r.ready_for_recommendation) AS recommendable_recipe_count,
                ARRAY(SELECT DISTINCT a.alias FROM ingredient_aliases a JOIN recipe_ingredients ai
                      ON ai.recipe_id=a.recipe_id AND ai.ordinal=a.ordinal
                      WHERE ai.ingredient_id=i.ingredient_id AND ai.canonical_name=i.canonical_name
                      ORDER BY a.alias) AS aliases,
                ARRAY(SELECT DISTINCT d.fdc_id FROM recipe_ingredient_nutrition d JOIN recipe_ingredients di
                      ON di.recipe_id=d.recipe_id AND di.ordinal=d.ordinal
                      WHERE di.ingredient_id=i.ingredient_id AND di.canonical_name=i.canonical_name
                      AND d.fdc_id IS NOT NULL ORDER BY d.fdc_id) AS fdc_ids,
                (SELECT json_agg(e) FROM (SELECT ei.recipe_id,er.name AS recipe_name,ei.raw_name,ei.raw_text,
                        ei.grams,ei.estimated_grams,ed.fdc_id,ed.estimated AS nutrition_estimated
                      FROM recipe_ingredients ei JOIN recipes er ON er.recipe_id=ei.recipe_id
                      JOIN recipe_ingredient_nutrition ed ON ed.recipe_id=ei.recipe_id AND ed.ordinal=ei.ordinal
                      WHERE ei.ingredient_id=i.ingredient_id AND ei.canonical_name=i.canonical_name
                      ORDER BY ei.recipe_id,ei.ordinal LIMIT 3) e) AS examples
            FROM selected s JOIN recipe_ingredients i USING (ingredient_id,canonical_name)
            JOIN recipes r ON r.recipe_id=i.recipe_id
            GROUP BY i.ingredient_id,i.canonical_name ORDER BY i.ingredient_id,i.canonical_name"""
        return connection.execute(statement, args).fetchall()

    def search(self, filters: RecipeFilter | None = None, *, limit: int = 20, offset: int = 0,
               sort: RecipeSort | None = None) -> dict:
        """Filter, count, order and paginate in SQL, then attach bounded page evidence."""
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("limit must be 1..200 and offset a nonnegative integer")
        if filters is not None and not isinstance(filters, RecipeFilter):
            raise ValueError("filters must be RecipeFilter")
        connection = self._active()
        where, args = where_clause(filters or RecipeFilter())
        order = order_clause(sort or RecipeSort())
        source = " FROM recipes r JOIN recipe_nutrition n ON n.recipe_id=r.recipe_id "
        total = connection.execute("SELECT count(*) AS count" + source + "WHERE " + where, args).fetchone()["count"]
        rows = connection.execute("SELECT r.*,n.*" + source + f"WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s",
                                  [*args, limit, offset]).fetchall()
        recipe_ids = [row["recipe_id"] for row in rows]
        if not rows:
            return {"total": total, "items": []}
        labels = connection.execute("SELECT * FROM recipe_labels WHERE recipe_id=ANY(%s) ORDER BY kind,value",
                                    (recipe_ids,)).fetchall()
        notes = connection.execute("SELECT recipe_id,kind,description FROM recipe_notes WHERE recipe_id=ANY(%s) "
                                   "ORDER BY kind,ordinal", (recipe_ids,)).fetchall()
        ingredients = connection.execute("""SELECT i.*,d.fdc_id,d.estimated AS nutrition_estimated,
            ARRAY(SELECT a.alias FROM ingredient_aliases a WHERE a.recipe_id=i.recipe_id AND a.ordinal=i.ordinal
                  ORDER BY a.alias) AS aliases,
            ARRAY(SELECT a.allergen FROM ingredient_allergens a WHERE a.recipe_id=i.recipe_id AND a.ordinal=i.ordinal
                  ORDER BY a.allergen) AS allergens FROM recipe_ingredients i JOIN recipe_ingredient_nutrition d
            ON d.recipe_id=i.recipe_id AND d.ordinal=i.ordinal WHERE i.recipe_id=ANY(%s)
            ORDER BY i.recipe_id,i.ordinal""", (recipe_ids,)).fetchall()
        recipe_columns = ("recipe_id", "name", "description", "servings", "original_servings", "prep_time_min",
                          "cook_time_min", "total_time_min", "difficulty", "source", "ready_for_recommendation",
                          "estimated_servings")
        items = []
        for row in rows:
            recipe = {key: row[key] for key in recipe_columns}
            related_labels = [label for label in labels if label["recipe_id"] == row["recipe_id"]]
            recipe["nutrition"] = {
                "status": row["status"], "estimated": row["estimated"],
                "total": _values(row, "total_"), "per_serving": _values(row, "per_serving_"),
                "estimated_total_fields": _estimated_fields(row, "estimated_total_"),
                "estimated_per_serving_fields": _estimated_fields(row, "estimated_per_serving_"),
                "estimated_servings": row["estimated_servings"],
                "tags": [label["value"] for label in related_labels if label["kind"] == "nutrition"],
                "tags_estimated": any(label["estimated"] for label in related_labels if label["kind"] == "nutrition"),
            }
            for kind, key in (("taste", "taste_tags"), ("cooking_method", "cooking_methods"), ("tool", "required_tools")):
                recipe[key] = [label["value"] for label in related_labels if label["kind"] == kind]
            for kind, key in (("warning", "warnings"), ("blocking_reason", "blocking_reasons")):
                recipe[key] = [note["description"] for note in notes
                               if note["recipe_id"] == row["recipe_id"] and note["kind"] == kind]
            recipe["ingredients"] = [{key: value for key, value in ingredient.items() if key != "recipe_id"}
                                     for ingredient in ingredients if ingredient["recipe_id"] == row["recipe_id"]]
            recipe["allergens"] = sorted({allergen for ingredient in recipe["ingredients"]
                                          for allergen in ingredient["allergens"]})
            items.append(recipe)
        return {"total": total, "items": items}

    def get_recipe(self, recipe_id: str) -> dict | None:
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            raise ValueError("recipe_id must be nonempty text")
        connection = self._active()
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
