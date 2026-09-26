CREATE TABLE converted_recipes (
 recipe_id TEXT PRIMARY KEY REFERENCES recipes(recipe_id) ON DELETE CASCADE,
 source_hash TEXT NOT NULL, data_json TEXT NOT NULL,
 nutrition_json TEXT NOT NULL DEFAULT '{}', prompt TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending_review', error_message TEXT,
 review_note TEXT NOT NULL DEFAULT '', reviewed_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
