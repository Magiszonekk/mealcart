from mealcart.ingest.wikibooks import parse_page, render

BIGOS = """{{Plik|plik=Bigos.jpg|auto=tak|opis=Bigos}}
== Przepis klasyczny ==
=== Składniki ===
* ''2 kg białej kapusty kiszonej''
* ''kilka grzybów leśnych''
=== Przygotowanie ===
{{lpg|kapusta|Kapustę}} dusić 2–3 godziny w {{C|180}}.
== Zobacz też ==
* {{sr|Bigos myśliwski}}
<noinclude>{{SortKuchWolumin|Mięsa}}</noinclude>
"""

PIEROGI = """== Składniki ==
* ''{{sr|Ciasto na pierogi|m=tak}}''
* ''750 g ziemniaków''
* ''łyżka masła''
== Przygotowanie ==
=== Farsz ===
# Ziemniaki ugotować.
"""

PASCHA = """{{Wikipedia|Pascha}}
== Pascha na zimno ==
=== Pascha zwykła ===
* ''3 kg twarogu''
* ''200 g masła''
=== Pascha niesłodka ===
* ''1 kg twarogu''
"""

INDEX = """'''Kurczak''' – drób domowy.
== Przepisy ==
* {{sr|Chrupiący kurczak}}
* {{sr|Kurczak curry}}
"""


def page(title: str, text: str) -> dict[str, object]:
    return {"title": f"Książka kucharska/{title}", "revid": 1, "timestamp": "2026-01-01T00:00:00Z", "wikitext": text}


def test_render_templates() -> None:
    r = render("{{lpg|kapusta|Kapustę}} w {{C|180}}, {{sr|Ciasto na pierogi|ciasta}} [[Plik:x.jpg]] [[a|b]]")
    assert r.text == "Kapustę w 180 °C, ciasta  b"
    assert r.sub_recipes == ["Ciasto na pierogi"]


def test_variant_heading_and_ingredients() -> None:
    r = parse_page(page("Bigos", BIGOS))
    assert [li.raw_line for li in r.ingredients] == ["2 kg białej kapusty kiszonej", "kilka grzybów leśnych"]
    assert {li.variant for li in r.ingredients} == {"Przepis klasyczny"}
    assert [li.has_quantity for li in r.ingredients] == [True, False]
    assert r.steps_text == "Kapustę dusić 2–3 godziny w 180 °C."  # "Zobacz też" is not a step
    assert r.url == "https://pl.wikibooks.org/wiki/Książka_kucharska/Bigos"


def test_sub_recipe_reference_kept() -> None:
    r = parse_page(page("Pierogi ruskie", PIEROGI))
    assert r.ingredients[0].sub_recipe == "Ciasto na pierogi"
    assert r.ingredients[2].has_quantity  # "łyżka" is a quantity
    assert r.steps_text is not None and r.steps_text.startswith("Farsz:")


def test_page_without_ingredient_heading_uses_variant_headings() -> None:
    r = parse_page(page("Pascha", PASCHA))
    assert len(r.ingredients) == 3
    assert r.ingredients[2].group_name == "Pascha niesłodka"


def test_index_page_has_no_ingredients() -> None:
    assert parse_page(page("Kurczak", INDEX)).ingredients == []
