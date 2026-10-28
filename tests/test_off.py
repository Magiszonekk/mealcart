from mealcart.ingest.off import clean_ingredients, nutrients, parse_quantity_g, pick_lang, to_product


def test_pick_lang_prefers_polish_then_main() -> None:
    items = [{"lang": "main", "text": "Eau, jus"}, {"lang": "fr", "text": "Eau, jus"}, {"lang": "pl", "text": "Woda"}]
    assert pick_lang(items, "fr") == ("Woda", "pl")
    assert pick_lang(items[:2], "fr") == ("Eau, jus", "fr")
    assert pick_lang([], "pl") == (None, None)
    assert pick_lang([{"lang": "de", "text": "  "}], "de") == (None, None)


def test_parse_quantity() -> None:
    assert parse_quantity_g("250 g") == 250
    assert parse_quantity_g("1,5 l") == 1500
    assert parse_quantity_g("4 x 125 g") == 500
    assert parse_quantity_g("0.33 L") == 330
    assert parse_quantity_g("6 sztuk") is None
    assert parse_quantity_g(None) is None


def test_nutrients_missing_is_none_and_kcal_from_kj() -> None:
    rows = [{"name": "energy", "100g": 418.4, "unit": "kJ"}, {"name": "proteins", "100g": 15.0}]
    out, issues = nutrients(rows)
    assert out["protein_100g"] == 15.0
    assert out["sugars_100g"] is None  # unknown, never 0
    assert out["kcal_100g"] == 100.0 and "kcal_from_kj" in issues


def test_nutrients_drop_implausible_values() -> None:
    out, issues = nutrients([{"name": "sugars", "100g": 250.0}, {"name": "energy-kcal", "100g": 3500.0}])
    assert out["sugars_100g"] is None and out["kcal_100g"] is None
    assert "implausible:sugars_100g" in issues


def test_clean_ingredients_markup() -> None:
    raw = 'Mąka _pszenna_ 66%, <span class="allergen">mleko</span>  w proszku'
    assert clean_ingredients(raw) == "Mąka pszenna 66%, mleko w proszku"
    assert clean_ingredients("snake_case_ok") == "snake_case_ok"


def test_well_filled_needs_name_ingredients_kcal() -> None:
    base = {
        "code": "1",
        "product_name": [{"lang": "pl", "text": "Twaróg"}],
        "lang": "pl",
        "ingredients_text": [{"lang": "pl", "text": "mleko"}],
        "nutriments": [{"name": "energy-kcal", "100g": 120.0}],
    }
    assert to_product(base).well_filled is True
    assert to_product(base | {"nutriments": []}).well_filled is False
    assert to_product(base | {"ingredients_text": None}).well_filled is False
