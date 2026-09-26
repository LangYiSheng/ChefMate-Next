CREATE TABLE standard_ingredients (
 ingredient_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL UNIQUE, category TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending_review', allergens_json TEXT NOT NULL DEFAULT '[]',
 nutrition_source TEXT, nutrition_source_id TEXT, nutrition_source_name TEXT,
 nutrition_match_status TEXT NOT NULL DEFAULT 'pending', nutrition_json TEXT,
 nutrition_error TEXT, nutrition_review_note TEXT, nutrition_reviewed_at TEXT,
 error_message TEXT, review_note TEXT, reviewed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE ingredient_aliases (
 alias TEXT PRIMARY KEY, ingredient_id TEXT NOT NULL REFERENCES standard_ingredients(ingredient_id),
 created_at TEXT NOT NULL
);
CREATE TABLE fixed_mappings (
 raw_name TEXT PRIMARY KEY, ingredient_id TEXT NOT NULL REFERENCES standard_ingredients(ingredient_id),
 status TEXT NOT NULL DEFAULT 'approved', review_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
ALTER TABLE raw_ingredients ADD COLUMN candidates_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE raw_ingredients ADD COLUMN confidence REAL;
ALTER TABLE raw_ingredients ADD COLUMN match_method TEXT;
ALTER TABLE raw_ingredients ADD COLUMN match_reason TEXT;
CREATE TABLE recipe_profiles (
 recipe_id TEXT PRIMARY KEY REFERENCES recipes(recipe_id) ON DELETE CASCADE,
 source_updated_at TEXT NOT NULL, data_json TEXT NOT NULL, status TEXT NOT NULL,
 error_message TEXT, review_note TEXT, reviewed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE usda_cache (
 fdc_id TEXT PRIMARY KEY, data_json TEXT NOT NULL, fetched_at TEXT NOT NULL
);
CREATE TABLE service_calls (
 id INTEGER PRIMARY KEY, service TEXT NOT NULL, purpose TEXT NOT NULL, entity_id TEXT,
 status TEXT NOT NULL, request_json TEXT NOT NULL, response_json TEXT, error_message TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
