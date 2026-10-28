"""`mealcart-ingest`: fetch snapshots and load them into Postgres.

    uv run mealcart-ingest fetch-all          # download Wikibooks + Open Prices snapshots into data/raw/
    uv run mealcart-ingest load-all           # load every snapshot found in data/raw/ (no network)
    uv run mealcart-ingest stats              # print the latest stats per source

Open Food Facts: the world dump is 8 GB; `extract-off <url-or-path>` filters it to Polish products (~12 MB) with
bounded memory. Snapshots carry the date in the file name so a run is reproducible.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from mealcart import db
from mealcart.ingest import off, prices, wikibooks

RAW = Path(__file__).resolve().parents[3] / "data" / "raw"


def latest(pattern: str) -> Path:
    files = sorted(RAW.glob(pattern))
    if not files:
        raise SystemExit(f"no snapshot matching data/raw/{pattern}; run fetch first")
    return files[-1]


def main() -> None:
    ap = argparse.ArgumentParser(prog="mealcart-ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract-off")
    ex.add_argument("source", nargs="?", default=off.WORLD_PARQUET_URL)
    sub.add_parser("fetch-all")
    sub.add_parser("load-all")
    sub.add_parser("stats")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    if args.cmd == "extract-off":
        out = RAW / f"off_poland_{today}.parquet"
        print(f"{off.extract_poland(args.source, out)} products → {out}")
    elif args.cmd == "fetch-all":
        print("wikibooks:", wikibooks.fetch(RAW / f"wikibooks_pl_{today}.jsonl"))
        print("open prices:", prices.fetch(RAW / f"open_prices_pln_{today}.jsonl"))
    elif args.cmd == "load-all":
        conn = db.connect()
        db.init_schema(conn)
        for name, fn, pattern in (
            ("off", off.load, "off_poland_*.parquet"),
            ("wikibooks", wikibooks.load, "wikibooks_pl_*.jsonl"),
            ("open_prices", prices.load, "open_prices_pln_*.jsonl"),  # after products: counts matches
        ):
            stats = fn(conn, latest(pattern))
            stats.pop("no_ingredients", None)
            print(name, json.dumps(stats, ensure_ascii=False))
    elif args.cmd == "stats":
        conn = db.connect()
        rows = conn.execute(
            "SELECT DISTINCT ON (source) source, started_at, stats FROM ingest_runs ORDER BY source, started_at DESC"
        ).fetchall()
        for source, started, stats in rows:
            print(source, started.isoformat(timespec="seconds"), json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
