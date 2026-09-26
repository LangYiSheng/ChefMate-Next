CREATE TABLE source_versions (
    recipe_id TEXT NOT NULL, revision INTEGER NOT NULL, source TEXT NOT NULL,
    content_hash TEXT NOT NULL, raw_bytes BLOB NOT NULL, raw_text TEXT,
    created_at TEXT NOT NULL, PRIMARY KEY(recipe_id, revision)
);
CREATE TABLE recipes (
    recipe_id TEXT PRIMARY KEY, source TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL, name TEXT NOT NULL, extracted_json TEXT,
    extract_status TEXT NOT NULL DEFAULT 'pending', extract_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending', error_message TEXT, review_note TEXT,
    reviewed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    FOREIGN KEY(recipe_id, revision) REFERENCES source_versions(recipe_id, revision)
);
CREATE TABLE raw_ingredients (
    id INTEGER PRIMARY KEY, raw_name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending', canonical_ingredient_id TEXT,
    error_message TEXT, review_note TEXT, reviewed_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE recipe_ingredients (
    recipe_id TEXT NOT NULL REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, raw_ingredient_id INTEGER NOT NULL REFERENCES raw_ingredients(id),
    data_json TEXT NOT NULL, PRIMARY KEY(recipe_id, ordinal)
);
CREATE INDEX ingredient_usage ON recipe_ingredients(raw_ingredient_id);
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY, recipe_id TEXT, action TEXT NOT NULL,
    data_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE enum_options (
    kind TEXT NOT NULL, code TEXT NOT NULL, label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved', PRIMARY KEY(kind, code)
);
