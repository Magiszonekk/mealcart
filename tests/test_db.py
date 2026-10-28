"""Acceptance checks on the loaded database (run after `mealcart-ingest all`). Skipped without a database."""

import pytest

from mealcart import db

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def conn():  # type: ignore[no-untyped-def]
    try:
        c = db.connect()
    except Exception as e:
        pytest.skip(f"no database: {e}")
    if c.execute("SELECT count(*) FROM products").fetchone()[0] == 0:  # type: ignore[index]
        pytest.skip("database is empty; run the ingest first")
    yield c
    c.close()


def one(conn, sql: str):  # type: ignore[no-untyped-def]
    return conn.execute(sql).fetchone()[0]


def test_product_counts(conn) -> None:  # type: ignore[no-untyped-def]
    assert one(conn, "SELECT count(*) FROM products") > 35_000
    assert one(conn, "SELECT count(*) FROM products WHERE well_filled") > 10_000


def test_no_markup_or_impossible_values(conn) -> None:  # type: ignore[no-untyped-def]
    assert one(conn, "SELECT count(*) FROM products WHERE ingredients_text LIKE '%<span%'") == 0
    assert one(conn, "SELECT count(*) FROM products WHERE protein_100g > 100 OR sugars_100g > 100") == 0
    assert one(conn, "SELECT count(*) FROM products WHERE well_filled AND kcal_100g IS NULL") == 0


def test_known_products(conn) -> None:  # type: ignore[no-untyped-def]
    row = conn.execute("SELECT name, quantity_g, allergens FROM products WHERE code = '5900334000255'").fetchone()
    assert row[0] == "tymbark jabłko mięta" and row[1] == 500


def test_recipes(conn) -> None:  # type: ignore[no-untyped-def]
    assert one(conn, "SELECT count(*) FROM recipes") > 950
    bigos = conn.execute(
        "SELECT array_agg(raw_line ORDER BY position) FROM recipe_ingredient_lines l JOIN recipes r ON r.id = "
        "l.recipe_id WHERE r.title = 'Bigos' AND l.variant = 'Przepis klasyczny'"
    ).fetchone()[0]
    assert "2 kg białej kapusty kiszonej" in bigos
    assert (
        one(
            conn,
            "SELECT count(*) FROM recipes r WHERE NOT EXISTS "
            "(SELECT 1 FROM recipe_ingredient_lines l WHERE l.recipe_id = r.id)",
        )
        == 0
    )


def test_prices(conn) -> None:  # type: ignore[no-untyped-def]
    assert one(conn, "SELECT count(*) FROM prices") > 2000
    assert one(conn, "SELECT count(*) FROM prices WHERE currency <> 'PLN'") == 0
