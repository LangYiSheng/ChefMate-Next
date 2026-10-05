-- Initial current-dataset schema. search_path is set by the migration runner.
CREATE TABLE dataset_metadata (
    id smallint PRIMARY KEY CHECK (id = 1),
    data_version text NOT NULL CHECK (length(data_version) > 0),
    export_schema_version integer NOT NULL CHECK (export_schema_version > 0),
    exported_at timestamptz NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE recipes (
    recipe_id text PRIMARY KEY CHECK (length(recipe_id) > 0),
    name text NOT NULL CHECK (length(name) > 0),
    description text,
    servings double precision CHECK (servings > 0 AND servings < 'Infinity'::double precision),
    original_servings text,
    prep_time_min double precision CHECK (prep_time_min >= 0 AND prep_time_min < 'Infinity'::double precision),
    cook_time_min double precision CHECK (cook_time_min >= 0 AND cook_time_min < 'Infinity'::double precision),
    total_time_min double precision CHECK (total_time_min >= 0 AND total_time_min < 'Infinity'::double precision),
    difficulty text,
    source text,
    ready_for_recommendation boolean NOT NULL,
    estimated_servings boolean NOT NULL CHECK (NOT estimated_servings OR servings IS NOT NULL)
);
CREATE INDEX recipes_ready_time ON recipes(ready_for_recommendation, total_time_min, recipe_id);
CREATE TABLE ingredients (
    ingredient_id text PRIMARY KEY CHECK (length(ingredient_id) > 0)
);
CREATE TABLE recipe_ingredients (
    recipe_id text NOT NULL REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    ingredient_id text NOT NULL REFERENCES ingredients(ingredient_id),
    canonical_name text NOT NULL CHECK (length(canonical_name) > 0),
    category text NOT NULL CHECK (length(category) > 0),
    raw_name text NOT NULL CHECK (length(raw_name) > 0),
    raw_amount text,
    raw_unit text,
    raw_text text NOT NULL CHECK (length(raw_text) > 0),
    grams double precision CHECK (grams >= 0 AND grams < 'Infinity'::double precision),
    scalable boolean NOT NULL,
    estimated_grams boolean NOT NULL CHECK (NOT estimated_grams OR grams IS NOT NULL),
    PRIMARY KEY (recipe_id, ordinal)
);
CREATE INDEX recipe_ingredients_by_identity ON recipe_ingredients(ingredient_id, recipe_id);
CREATE TABLE ingredient_aliases (
    recipe_id text NOT NULL,
    ordinal integer NOT NULL,
    alias text NOT NULL CHECK (length(alias) > 0),
    PRIMARY KEY (recipe_id, ordinal, alias),
    FOREIGN KEY (recipe_id, ordinal) REFERENCES recipe_ingredients(recipe_id, ordinal) ON DELETE CASCADE
);
CREATE INDEX ingredient_aliases_lookup ON ingredient_aliases(alias, recipe_id);
CREATE TABLE ingredient_allergens (
    recipe_id text NOT NULL,
    ordinal integer NOT NULL,
    allergen text NOT NULL CHECK (length(allergen) > 0),
    PRIMARY KEY (recipe_id, ordinal, allergen),
    FOREIGN KEY (recipe_id, ordinal) REFERENCES recipe_ingredients(recipe_id, ordinal) ON DELETE CASCADE
);
CREATE INDEX ingredient_allergens_lookup ON ingredient_allergens(allergen, recipe_id);
CREATE TABLE recipe_steps (
    recipe_id text NOT NULL REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    description text NOT NULL CHECK (length(description) > 0),
    PRIMARY KEY (recipe_id, ordinal)
);
CREATE TABLE recipe_labels (
    recipe_id text NOT NULL REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('taste', 'cooking_method', 'tool', 'nutrition')),
    value text NOT NULL CHECK (length(value) > 0),
    estimated boolean NOT NULL,
    PRIMARY KEY (recipe_id, kind, value),
    CHECK (kind = 'nutrition' OR NOT estimated)
);
CREATE INDEX recipe_labels_lookup ON recipe_labels(kind, value, recipe_id);
CREATE TABLE recipe_notes (
    recipe_id text NOT NULL REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('warning', 'blocking_reason')),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    description text NOT NULL CHECK (length(description) > 0),
    PRIMARY KEY (recipe_id, kind, ordinal)
);
CREATE TABLE recipe_references (
    recipe_id text NOT NULL REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    title text NOT NULL CHECK (length(title) > 0),
    url text NOT NULL,
    PRIMARY KEY (recipe_id, ordinal)
);
CREATE TABLE recipe_nutrition (
    recipe_id text PRIMARY KEY REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('complete', 'estimated', 'incomplete')),
    estimated boolean NOT NULL,
    total_calories_kcal double precision CHECK (total_calories_kcal >= 0 AND total_calories_kcal < 'Infinity'::double precision),
    total_protein_g double precision CHECK (total_protein_g >= 0 AND total_protein_g < 'Infinity'::double precision),
    total_fat_g double precision CHECK (total_fat_g >= 0 AND total_fat_g < 'Infinity'::double precision),
    total_carbohydrate_g double precision CHECK (total_carbohydrate_g >= 0 AND total_carbohydrate_g < 'Infinity'::double precision),
    total_sugar_g double precision CHECK (total_sugar_g >= 0 AND total_sugar_g < 'Infinity'::double precision),
    total_sodium_mg double precision CHECK (total_sodium_mg >= 0 AND total_sodium_mg < 'Infinity'::double precision),
    per_serving_calories_kcal double precision CHECK (per_serving_calories_kcal >= 0 AND per_serving_calories_kcal < 'Infinity'::double precision),
    per_serving_protein_g double precision CHECK (per_serving_protein_g >= 0 AND per_serving_protein_g < 'Infinity'::double precision),
    per_serving_fat_g double precision CHECK (per_serving_fat_g >= 0 AND per_serving_fat_g < 'Infinity'::double precision),
    per_serving_carbohydrate_g double precision CHECK (per_serving_carbohydrate_g >= 0 AND per_serving_carbohydrate_g < 'Infinity'::double precision),
    per_serving_sugar_g double precision CHECK (per_serving_sugar_g >= 0 AND per_serving_sugar_g < 'Infinity'::double precision),
    per_serving_sodium_mg double precision CHECK (per_serving_sodium_mg >= 0 AND per_serving_sodium_mg < 'Infinity'::double precision),
    estimated_total_calories_kcal boolean NOT NULL,
    estimated_total_protein_g boolean NOT NULL,
    estimated_total_fat_g boolean NOT NULL,
    estimated_total_carbohydrate_g boolean NOT NULL,
    estimated_total_sugar_g boolean NOT NULL,
    estimated_total_sodium_mg boolean NOT NULL,
    estimated_per_serving_calories_kcal boolean NOT NULL,
    estimated_per_serving_protein_g boolean NOT NULL,
    estimated_per_serving_fat_g boolean NOT NULL,
    estimated_per_serving_carbohydrate_g boolean NOT NULL,
    estimated_per_serving_sugar_g boolean NOT NULL,
    estimated_per_serving_sodium_mg boolean NOT NULL
);
CREATE TABLE recipe_ingredient_nutrition (
    recipe_id text NOT NULL,
    ordinal integer NOT NULL,
    estimated boolean NOT NULL,
    fdc_id text,
    contribution_calories_kcal double precision CHECK (contribution_calories_kcal >= 0 AND contribution_calories_kcal < 'Infinity'::double precision),
    contribution_protein_g double precision CHECK (contribution_protein_g >= 0 AND contribution_protein_g < 'Infinity'::double precision),
    contribution_fat_g double precision CHECK (contribution_fat_g >= 0 AND contribution_fat_g < 'Infinity'::double precision),
    contribution_carbohydrate_g double precision CHECK (contribution_carbohydrate_g >= 0 AND contribution_carbohydrate_g < 'Infinity'::double precision),
    contribution_sugar_g double precision CHECK (contribution_sugar_g >= 0 AND contribution_sugar_g < 'Infinity'::double precision),
    contribution_sodium_mg double precision CHECK (contribution_sodium_mg >= 0 AND contribution_sodium_mg < 'Infinity'::double precision),
    per_100g_calories_kcal double precision CHECK (per_100g_calories_kcal >= 0 AND per_100g_calories_kcal < 'Infinity'::double precision),
    per_100g_protein_g double precision CHECK (per_100g_protein_g >= 0 AND per_100g_protein_g < 'Infinity'::double precision),
    per_100g_fat_g double precision CHECK (per_100g_fat_g >= 0 AND per_100g_fat_g < 'Infinity'::double precision),
    per_100g_carbohydrate_g double precision CHECK (per_100g_carbohydrate_g >= 0 AND per_100g_carbohydrate_g < 'Infinity'::double precision),
    per_100g_sugar_g double precision CHECK (per_100g_sugar_g >= 0 AND per_100g_sugar_g < 'Infinity'::double precision),
    per_100g_sodium_mg double precision CHECK (per_100g_sodium_mg >= 0 AND per_100g_sodium_mg < 'Infinity'::double precision),
    estimated_calories_kcal boolean NOT NULL,
    estimated_protein_g boolean NOT NULL,
    estimated_fat_g boolean NOT NULL,
    estimated_carbohydrate_g boolean NOT NULL,
    estimated_sugar_g boolean NOT NULL,
    estimated_sodium_mg boolean NOT NULL,
    PRIMARY KEY (recipe_id, ordinal),
    FOREIGN KEY (recipe_id, ordinal) REFERENCES recipe_ingredients(recipe_id, ordinal) ON DELETE CASCADE
);
CREATE INDEX nutrition_total_calories_kcal ON recipe_nutrition(total_calories_kcal, recipe_id);
CREATE INDEX nutrition_total_protein_g ON recipe_nutrition(total_protein_g, recipe_id);
CREATE INDEX nutrition_total_sodium_mg ON recipe_nutrition(total_sodium_mg, recipe_id);
CREATE INDEX nutrition_per_serving_calories_kcal ON recipe_nutrition(per_serving_calories_kcal, recipe_id);
CREATE INDEX nutrition_per_serving_protein_g ON recipe_nutrition(per_serving_protein_g, recipe_id);
CREATE INDEX nutrition_per_serving_sodium_mg ON recipe_nutrition(per_serving_sodium_mg, recipe_id);
