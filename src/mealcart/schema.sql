-- MealCart schema (M1: raw-but-normalised data; M2 adds extracted ingredient structure and embeddings)
CREATE EXTENSION IF NOT EXISTS vector;

-- Open Food Facts, Polish products. Nutrition per 100 g (or 100 ml), NULL = unknown (never 0 for unknown).
CREATE TABLE IF NOT EXISTS products (
    code              text PRIMARY KEY,           -- barcode
    name              text,                       -- best Polish name, else main
    name_lang         text,
    brands            text,
    categories        text[] NOT NULL DEFAULT '{}',
    ingredients_text  text,                       -- best Polish ingredients text, else main
    ingredients_lang  text,
    allergens         text[] NOT NULL DEFAULT '{}',   -- OFF tags, e.g. en:nuts
    traces            text[] NOT NULL DEFAULT '{}',
    labels            text[] NOT NULL DEFAULT '{}',
    kcal_100g         real,
    protein_100g      real,
    fat_100g          real,
    saturated_fat_100g real,
    carbs_100g        real,
    sugars_100g       real,
    fiber_100g        real,
    salt_100g         real,
    nutriscore        text,                       -- a..e, NULL when unknown
    nova              smallint,
    quantity          text,                       -- as printed, e.g. "200 g"
    quantity_g        real,                       -- parsed grams/ml when possible
    last_modified     timestamptz,
    well_filled       boolean NOT NULL,           -- name + ingredients + kcal
    source            text NOT NULL DEFAULT 'off'
);
CREATE INDEX IF NOT EXISTS products_well_filled ON products (well_filled);
CREATE INDEX IF NOT EXISTS products_categories ON products USING gin (categories);
CREATE INDEX IF NOT EXISTS products_allergens ON products USING gin (allergens);

-- Wikibooks recipes (pl.wikibooks "Książka kucharska"), CC BY-SA. One row per page.
CREATE TABLE IF NOT EXISTS recipes (
    id            serial PRIMARY KEY,
    title         text NOT NULL UNIQUE,           -- page title without the "Książka kucharska/" prefix
    url           text NOT NULL,
    revision_id   bigint NOT NULL,                -- attribution + reproducibility
    revision_time timestamptz,
    categories    text[] NOT NULL DEFAULT '{}',
    wikitext      text NOT NULL,
    steps_text    text,                           -- plain text of the preparation section(s)
    servings_text text,                           -- as written, if the page states it
    license       text NOT NULL DEFAULT 'CC BY-SA 4.0'
);

-- Ingredient lines as written (M2 extracts concept/quantity/unit into extra columns or a new table).
CREATE TABLE IF NOT EXISTS recipe_ingredient_lines (
    recipe_id     integer NOT NULL REFERENCES recipes (id) ON DELETE CASCADE,
    position      integer NOT NULL,
    variant       text,                           -- sub-heading for pages with several variants ("Przepis klasyczny")
    group_name    text,                           -- ingredient group heading ("Farsz", "Ciasto")
    raw_line      text NOT NULL,                  -- plain text, markup removed
    sub_recipe    text,                           -- {{sr|...}} reference to another recipe page
    has_quantity  boolean NOT NULL,
    PRIMARY KEY (recipe_id, position)
);

-- Open Prices, PLN only (the API country filter is ignored; currency is the reliable filter).
CREATE TABLE IF NOT EXISTS prices (
    id            bigint PRIMARY KEY,             -- Open Prices id
    product_code  text,                           -- barcode; NULL for category prices (e.g. loose vegetables)
    category_tag  text,
    price         numeric(10, 2) NOT NULL,
    price_per     text,                           -- KILOGRAM / UNIT / NULL
    currency      text NOT NULL,
    date          date,
    shop_name     text,
    shop_city     text,
    location_osm_id bigint,
    price_is_discounted boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS prices_product ON prices (product_code);

-- Every ingest run, for the README numbers and reproducibility.
CREATE TABLE IF NOT EXISTS ingest_runs (
    id         serial PRIMARY KEY,
    source     text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    stats      jsonb NOT NULL
);
