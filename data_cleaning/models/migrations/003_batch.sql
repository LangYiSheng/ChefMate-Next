ALTER TABLE standard_ingredients ADD COLUMN usda_query TEXT NOT NULL DEFAULT '';
ALTER TABLE standard_ingredients ADD COLUMN nutrition_candidates_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE standard_ingredients ADD COLUMN nutrition_confidence REAL;
ALTER TABLE standard_ingredients ADD COLUMN nutrition_reason TEXT;
CREATE TABLE batch_runs (
 id TEXT PRIMARY KEY, status TEXT NOT NULL, options_json TEXT NOT NULL, recipe_ids_json TEXT NOT NULL,
 stage TEXT, current_item TEXT, error_message TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_batch ON batch_runs((1)) WHERE status IN ('queued','running','stopping');
CREATE TABLE batch_items (
 run_id TEXT NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
 stage TEXT NOT NULL, entity_id TEXT NOT NULL, version TEXT NOT NULL,
 status TEXT NOT NULL, result_json TEXT, error_message TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(run_id,stage,entity_id)
);
