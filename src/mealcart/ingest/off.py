"""Open Food Facts → products table (Polish products only).

Input: a Parquet file already filtered to `countries_tags ∋ en:poland` (see `extract_poland`), so the ingest
never needs the 8 GB world dump in memory.

Rules (from measuring the data, 2026-10-28):
- Name and ingredients come in several languages; prefer Polish, then the product's main language. The chosen
  language is stored, because ~10% of Polish products carry French/German ingredient text.
- Missing nutrient = NULL, never 0. A missing allergen tag is "unknown", not "absent".
- energy-kcal is sometimes missing while energy (kJ) is present: derive kcal = kJ / 4.184.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import psycopg

WORLD_PARQUET_URL = "https://huggingface.co/datasets/openfoodfacts/product-database/resolve/main/food.parquet"

NUTRIENTS = {
    "energy-kcal": "kcal_100g",
    "proteins": "protein_100g",
    "fat": "fat_100g",
    "saturated-fat": "saturated_fat_100g",
    "carbohydrates": "carbs_100g",
    "sugars": "sugars_100g",
    "fiber": "fiber_100g",
    "salt": "salt_100g",
}
# plausibility caps per 100 g; values above are data-entry errors (e.g. per-package values)
NUTRIENT_MAX = {"kcal_100g": 950.0, "salt_100g": 100.0}
NUTRIENT_MAX_DEFAULT = 100.0

COLUMNS = (
    "code",
    "name",
    "name_lang",
    "brands",
    "categories",
    "ingredients_text",
    "ingredients_lang",
    "allergens",
    "traces",
    "labels",
    *NUTRIENTS.values(),
    "nutriscore",
    "nova",
    "quantity",
    "quantity_g",
    "last_modified",
    "well_filled",
)


@dataclass
class Product:
    code: str
    name: str | None
    name_lang: str | None
    brands: str | None
    categories: list[str]
    ingredients_text: str | None
    ingredients_lang: str | None
    allergens: list[str]
    traces: list[str]
    labels: list[str]
    kcal_100g: float | None = None
    protein_100g: float | None = None
    fat_100g: float | None = None
    saturated_fat_100g: float | None = None
    carbs_100g: float | None = None
    sugars_100g: float | None = None
    fiber_100g: float | None = None
    salt_100g: float | None = None
    nutriscore: str | None = None
    nova: int | None = None
    quantity: str | None = None
    quantity_g: float | None = None
    last_modified: datetime | None = None
    well_filled: bool = False
    issues: list[str] = field(default_factory=list)  # not stored; counted in ingest stats


def extract_poland(world_parquet: str, out: Path) -> int:
    """Filter the world dump to Polish products with bounded memory (projection + streaming COPY)."""
    con = duckdb.connect()
    con.execute("SET memory_limit='1500MB'; SET threads=2; SET preserve_insertion_order=false")
    cols = (
        "code, product_name, generic_name, brands, brands_tags, categories_tags, ingredients_text, allergens_tags, "
        "traces_tags, nutriments, nutriscore_grade, nutriscore_score, nova_group, labels_tags, quantity, "
        "product_quantity, last_modified_t, lang, countries_tags, completeness, popularity_key"
    )
    con.execute(
        f"COPY (SELECT {cols} FROM read_parquet(?) WHERE list_contains(countries_tags, 'en:poland')) "
        f"TO '{out}' (FORMAT PARQUET)",
        [world_parquet],
    )
    n: int = con.execute("SELECT count(*) FROM read_parquet(?)", [str(out)]).fetchone()[0]  # type: ignore[index]
    return n


_HTML_TAG = re.compile(r"<[^>]+>")
_ALLERGEN_MARK = re.compile(r"(?<!\w)_+([^_]+?)_+(?!\w)")  # OFF marks allergens as _pszenna_


def clean_ingredients(text: str | None) -> str | None:
    """Strip the markup OFF keeps in ingredient text (<span class="allergen">, _underscores_), squeeze spaces."""
    if not text:
        return None
    text = _HTML_TAG.sub("", text)
    text = _ALLERGEN_MARK.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def pick_lang(items: list[dict[str, Any]] | None, main_lang: str | None) -> tuple[str | None, str | None]:
    """Choose text from OFF's [{lang, text}] list: Polish first, then the product's main language, then 'main'."""
    if not items:
        return None, None
    by_lang: dict[str, str] = {}
    for it in items:
        text = (it.get("text") or "").strip()
        if text and it.get("lang") not in by_lang:
            by_lang[it["lang"]] = text
    if "pl" in by_lang:
        return by_lang["pl"], "pl"
    if "main" in by_lang:
        return by_lang["main"], main_lang
    if main_lang and main_lang in by_lang:
        return by_lang[main_lang], main_lang
    if by_lang:
        lang, text = next(iter(by_lang.items()))
        return text, lang
    return None, None


_QTY = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|mg|l|ml|cl|dl)\b", re.IGNORECASE)
_MULTI = re.compile(r"(\d+)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l|cl)\b", re.IGNORECASE)
_UNIT_G = {"kg": 1000.0, "g": 1.0, "mg": 0.001, "l": 1000.0, "ml": 1.0, "cl": 10.0, "dl": 100.0}


def parse_quantity_g(text: str | None) -> float | None:
    """'200 g' → 200, '1,5 l' → 1500, '4 x 125 g' → 500. Liquids count 1 ml = 1 g (good enough for shopping)."""
    if not text:
        return None
    m = _MULTI.search(text)
    if m:
        return int(m.group(1)) * float(m.group(2).replace(",", ".")) * _UNIT_G[m.group(3).lower()]
    m = _QTY.search(text)
    if m:
        value = float(m.group(1).replace(",", ".")) * _UNIT_G[m.group(2).lower()]
        return value if value > 0 else None
    return None


def nutrients(rows: list[dict[str, Any]] | None) -> tuple[dict[str, float | None], list[str]]:
    out: dict[str, float | None] = dict.fromkeys(NUTRIENTS.values())
    issues: list[str] = []
    kj: float | None = None
    for r in rows or []:
        v = r.get("100g")
        if v is None:
            continue
        name = r.get("name")
        if name == "energy-kj" or (name == "energy" and (r.get("unit") or "").lower() == "kj"):
            kj = float(v)
        col = NUTRIENTS.get(name or "")
        if col is None:
            continue
        v = float(v)
        cap = NUTRIENT_MAX.get(col, NUTRIENT_MAX_DEFAULT)
        if v < 0 or v > cap:
            issues.append(f"implausible:{col}")
            continue
        out[col] = round(v, 3)
    if out["kcal_100g"] is None and kj is not None and 0 < kj <= NUTRIENT_MAX["kcal_100g"] * 4.184:
        out["kcal_100g"] = round(kj / 4.184, 1)
        issues.append("kcal_from_kj")
    return out, issues


def to_product(row: dict[str, Any]) -> Product:
    main_lang = row.get("lang")
    name, name_lang = pick_lang(row.get("product_name"), main_lang)
    ingredients, ingredients_lang = pick_lang(row.get("ingredients_text"), main_lang)
    ingredients = clean_ingredients(ingredients)
    nut, issues = nutrients(row.get("nutriments"))
    grade = (row.get("nutriscore_grade") or "").lower()
    ts = row.get("last_modified_t")
    p = Product(
        code=row["code"],
        name=name,
        name_lang=name_lang,
        brands=(row.get("brands") or None),
        categories=list(row.get("categories_tags") or []),
        ingredients_text=ingredients,
        ingredients_lang=ingredients_lang,
        allergens=list(row.get("allergens_tags") or []),
        traces=list(row.get("traces_tags") or []),
        labels=list(row.get("labels_tags") or []),
        nutriscore=grade if grade in {"a", "b", "c", "d", "e"} else None,
        nova=row.get("nova_group"),
        quantity=row.get("quantity") or None,
        quantity_g=parse_quantity_g(row.get("quantity")),
        last_modified=datetime.fromtimestamp(ts, UTC) if ts else None,
        issues=issues,
    )
    for col, value in nut.items():
        setattr(p, col, value)
    p.well_filled = bool(p.name and p.ingredients_text and p.kcal_100g is not None)
    return p


def read_products(parquet: Path) -> Iterator[Product]:
    con = duckdb.connect()
    con.execute("SET memory_limit='1000MB'; SET threads=2")
    cur = con.execute(
        "SELECT code, product_name, brands, categories_tags, ingredients_text, allergens_tags, traces_tags, "
        "labels_tags, nutriments, nutriscore_grade, nova_group, quantity, last_modified_t, lang "
        "FROM read_parquet(?) WHERE code IS NOT NULL",
        [str(parquet)],
    )
    names = [d[0] for d in cur.description]
    while batch := cur.fetchmany(2000):
        for values in batch:
            yield to_product(dict(zip(names, values, strict=True)))


def load(conn: psycopg.Connection, parquet: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {"source_file": parquet.name, "products": 0, "well_filled": 0, "duplicates": 0}
    issue_counts: dict[str, int] = {}
    lang_counts: dict[str, int] = {}
    seen: set[str] = set()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE products")
        with cur.copy(f"COPY products ({', '.join(COLUMNS)}) FROM STDIN") as copy:
            for p in read_products(parquet):
                if p.code in seen:  # the dump has a handful of repeated barcodes; keep the first
                    stats["duplicates"] += 1
                    continue
                seen.add(p.code)
                row = asdict(p)
                copy.write_row([row[c] for c in COLUMNS])
                stats["products"] += 1
                stats["well_filled"] += p.well_filled
                for i in p.issues:
                    issue_counts[i] = issue_counts.get(i, 0) + 1
                if p.well_filled:
                    key = p.ingredients_lang or "?"
                    lang_counts[key] = lang_counts.get(key, 0) + 1
        stats["issues"] = issue_counts
        stats["well_filled_ingredients_lang"] = dict(sorted(lang_counts.items(), key=lambda kv: -kv[1])[:8])
        cur.execute("INSERT INTO ingest_runs (source, stats) VALUES ('off', %s)", [json.dumps(stats)])
    conn.commit()
    return stats
