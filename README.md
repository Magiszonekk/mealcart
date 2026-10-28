# MealCart

Meal planning and a shopping list on open data: pick recipes that fit a profile (exclusions, protein, time,
what is already in the fridge), turn them into a shopping list of real products with allergen warnings and an
approximate price.

The point of the project is the parts a plain "chat with recipes" skips:

- **hard constraints next to semantic search**: exclusions and nutrition targets are filters on structured data,
  vectors only rank what passes them;
- **numbers are computed, never generated**: macros, quantities and prices come from code; every plan is checked
  by a deterministic validator, so "no walnuts" is a guarantee, not a prompt;
- **honest unknowns**: a missing allergen tag is "unknown", not "absent"; a recipe without quantities has no macros.

Status: **M1 — data** (ingest + acceptance tests, no LLM). Next: ingredient extraction and matching to products (M2),
planner + validator (M3), demo (M4).

## Data (snapshot 2026-10-28)

| source | license | loaded |
|---|---|---|
| [Open Food Facts](https://world.openfoodfacts.org), products sold in Poland | ODbL (contents DbCL, images CC BY-SA) | 39 235 products, **11 084 well filled** (name + ingredients + kcal) |
| [pl.wikibooks "Książka kucharska"](https://pl.wikibooks.org/wiki/Książka_kucharska) | CC BY-SA 4.0 | 1013 pages → **999 recipes**, 8906 ingredient lines |
| [Open Prices](https://prices.openfoodfacts.org), PLN | ODbL | 2152 prices (53 duplicates skipped), 1689 products, 1058 of them in the product table |

Measured on the loaded data:

- Ingredient text language among well-filled products: Polish 9981, English 477, French 190, German 163, other 273.
  Exclusion patterns therefore cover other languages too (walnut / Walnuss / noix).
- Nutrition values outside physical limits (e.g. 250 g sugar per 100 g) are dropped, not clipped: 52 values.
- Recipes: 66.7% of ingredient lines carry a quantity; 811 of 999 recipes have a quantity on at least half of their
  lines (the rest: "sól, pieprz", "kilka grzybów"). Only 85 recipes state the number of servings.
- 14 category pages are not recipes (index pages such as "Kurczak", a list of beef cuts) and are skipped.
- Prices: Lidl 260, Żabka 256, Carrefour Market 251, Biedronka 236, Kaufland 232, Carrefour 223; 2019-04 to 2026-10.
  Enough for "approximate basket cost when known", not for comparing shops.

## Profiles and exclusions

A profile is YAML ([`profiles/example.yaml`](profiles/example.yaml)). An exclusion names a concept from
[`src/mealcart/data/concepts.yaml`](src/mealcart/data/concepts.yaml) and a level:

| level | recipe | product |
|---|---|---|
| `nie_jem` (preference) | excluded when an ingredient line mentions it | excluded when the ingredient text mentions it; "may contain" ignored; no ingredient text = allowed, flagged unknown |
| `alergia` | same | also the OFF allergen tag, "may contain" statements and trace tags; **no ingredient text = excluded** |

Adding a concept is one YAML entry with inflection-aware patterns and optional negatives (e.g. "orzechy" excludes
peanuts, coconut and nutmeg). On the loaded data, "orzech włoski" as a preference excludes 28 recipes and
39 well-filled products; as an allergy 2230 products, because OFF's `en:nuts` tag covers every tree nut and many
products state "may contain nuts".

## Run

```bash
cp .env.example .env            # set a password
docker compose up -d db         # Postgres 16 + pgvector on 127.0.0.1:5437
uv sync
uv run mealcart-ingest extract-off   # 8 GB world dump → Polish products (bounded memory); or reuse a snapshot
uv run mealcart-ingest fetch-all     # Wikibooks + Open Prices snapshots
uv run mealcart-ingest load-all
uv run pytest                        # unit tests + acceptance checks on the loaded database
```

## Attribution

Product data © Open Food Facts contributors, ODbL. Recipes from Polish Wikibooks, © their authors, CC BY-SA 4.0;
each recipe keeps its page URL and revision id. Prices © Open Prices contributors, ODbL.
