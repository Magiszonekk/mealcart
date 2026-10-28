"""Open Prices (prices.openfoodfacts.org) → prices table, PLN only.

The API ignores every country filter we tried (location_osm_address_country_code etc. return the global total), so
`currency=PLN` is the filter. Duplicates flagged by Open Prices (`duplicate_of`) are skipped. Discounted prices are
kept with `price_is_discounted`, but the "typical price" for a basket uses only regular prices (see prices_for).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import psycopg

API = "https://prices.openfoodfacts.org/api/v1/prices"
USER_AGENT = "mealcart-ingest/0.1 (https://github.com/Magiszonekk/mealcart)"


def fetch(out: Path, currency: str = "PLN", pause: float = 0.5) -> int:
    items: list[dict[str, Any]] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        page = 1
        while True:
            r = client.get(API, params={"currency": currency, "size": 100, "page": page, "order_by": "id"})
            r.raise_for_status()
            d = r.json()
            items += d["items"]
            if page >= d.get("pages", 1):
                break
            page += 1
            time.sleep(pause)
    out.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")
    return len(items)


def load(conn: psycopg.Connection, snapshot: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines() if line.strip()]
    stats: dict[str, Any] = {"source_file": snapshot.name, "records": len(rows), "loaded": 0, "duplicates": 0}
    shops: Counter[str] = Counter()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE prices")
        for p in rows:
            if p.get("duplicate_of"):
                stats["duplicates"] += 1
                continue
            loc = p.get("location") or {}
            shop = loc.get("osm_brand") or loc.get("osm_name")
            cur.execute(
                "INSERT INTO prices (id, product_code, category_tag, price, price_per, currency, date, shop_name, "
                "shop_city, location_osm_id, price_is_discounted) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    p["id"],
                    p.get("product_code"),
                    p.get("category_tag"),
                    p["price"],
                    p.get("price_per"),
                    p["currency"],
                    p.get("date"),
                    shop,
                    loc.get("osm_address_city"),
                    p.get("location_osm_id"),
                    bool(p.get("price_is_discounted")),
                ],
            )
            stats["loaded"] += 1
            shops[shop or "?"] += 1
        stats["top_shops"] = dict(shops.most_common(10))
        cur.execute(
            "SELECT count(DISTINCT pr.product_code), "
            "count(DISTINCT pr.product_code) FILTER (WHERE p.code IS NOT NULL), "
            "min(date), max(date), count(*) FILTER (WHERE date >= now() - interval '1 year') "
            "FROM prices pr LEFT JOIN products p ON p.code = pr.product_code"
        )
        distinct, matched, first, last, last_year = cur.fetchone()  # type: ignore[misc]
        stats |= {
            "distinct_products": distinct,
            "matched_to_products": matched,
            "first": str(first),
            "last": str(last),
            "last_12_months": last_year,
        }
        cur.execute("INSERT INTO ingest_runs (source, stats) VALUES ('open_prices', %s)", [json.dumps(stats)])
    conn.commit()
    return stats
