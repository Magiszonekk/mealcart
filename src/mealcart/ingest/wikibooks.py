"""Polish Wikibooks cookbook ("Książka kucharska") → recipes + ingredient lines.

Two steps, so parsing can be re-run without the network:
1. `fetch()` downloads the wikitext of every page in "Kategoria:Książka kucharska/Wszystkie przepisy" (50 pages per
   API call, polite rate) into a JSONL snapshot with revision ids (CC BY-SA attribution + reproducibility).
2. `parse_page()` turns wikitext into sections: ingredient lines (with variant / group headings), preparation text,
   servings. Templates are rendered by a few explicit rules (measured on a sample: sr, lpg, c, dk, rn, lz, lhg are
   the only ones that occur inside recipe text).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import psycopg

API = "https://pl.wikibooks.org/w/api.php"
CATEGORY = "Kategoria:Książka kucharska/Wszystkie przepisy"
PREFIX = "Książka kucharska/"
USER_AGENT = "mealcart-ingest/0.1 (https://github.com/Magiszonekk/mealcart)"

INGREDIENT_HEADINGS = re.compile(r"^(składniki|skład)\b", re.IGNORECASE)
# "Przepis" alone is a steps heading, but "Przepis klasyczny" names a variant (Bigos)
STEP_HEADINGS = re.compile(
    r"^(przygotowanie|wykonanie|przyrządz\w*|sposób( przyrządzania| przygotowania)?)\b|^przepis$", re.IGNORECASE
)
SKIP_HEADINGS = re.compile(r"^(źródł[oa]|zobacz też|przypisy|uwagi|bibliografia|linki zewnętrzne)\b", re.IGNORECASE)
SERVINGS = re.compile(r"(?:dla|na)\s+(\d+(?:\s*[-–]\s*\d+)?)\s+(osób|osoby|porcj\w*|sztuk\w*)", re.IGNORECASE)
HAS_QUANTITY = re.compile(r"\d|\b(pół|ćwierć|szczypt\w*|łyżk\w*|łyżecz\w*|szklank\w*|garść\w*)\b", re.IGNORECASE)


# ---------------------------------------------------------------- fetch


def _get(client: httpx.Client, **params: Any) -> dict[str, Any]:
    params |= {"format": "json", "formatversion": 2}
    for attempt in range(6):
        r = client.get(API, params=params)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data
    raise RuntimeError(f"Wikibooks API keeps failing: {r.status_code}")


def list_titles(client: httpx.Client) -> list[str]:
    titles: list[str] = []
    cont: dict[str, str] = {}
    while True:
        d = _get(client, action="query", list="categorymembers", cmtitle=CATEGORY, cmlimit=500, cmnamespace=0, **cont)
        titles += [m["title"] for m in d["query"]["categorymembers"]]
        if "continue" not in d:
            return titles
        cont = {"cmcontinue": d["continue"]["cmcontinue"]}


def fetch(out: Path, pause: float = 1.0) -> int:
    """Snapshot every recipe page (title, revid, timestamp, categories, wikitext) into JSONL."""
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        titles = list_titles(client)
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for i in range(0, len(titles), 50):
                d = _get(
                    client,
                    action="query",
                    prop="revisions|categories",
                    rvprop="ids|timestamp|content",
                    rvslots="main",
                    cllimit="max",
                    clshow="!hidden",
                    titles="|".join(titles[i : i + 50]),
                )
                for p in d["query"]["pages"]:
                    if "revisions" not in p:
                        continue
                    rev = p["revisions"][0]
                    f.write(
                        json.dumps(
                            {
                                "title": p["title"],
                                "pageid": p["pageid"],
                                "revid": rev["revid"],
                                "timestamp": rev["timestamp"],
                                "categories": [c["title"].split(":", 1)[1] for c in p.get("categories", [])],
                                "wikitext": rev["slots"]["main"]["content"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    n += 1
                time.sleep(pause)
    return n


# ---------------------------------------------------------------- wikitext → text

_TEMPLATE = re.compile(r"\{\{([^{}]*)\}\}")
_LINK_TEMPLATES = {"sr", "lpg", "lz", "lhg", "lp", "lw", "lwk", "lws"}


@dataclass
class Rendered:
    text: str
    sub_recipes: list[str] = field(default_factory=list)


def _render_template(body: str, sub_recipes: list[str]) -> str:
    parts = [p.strip() for p in body.split("|")]
    name = parts[0].lower()
    positional = [p for p in parts[1:] if "=" not in p]
    named = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
    if name in _LINK_TEMPLATES:
        if name == "sr" and positional:
            sub_recipes.append(positional[0])
        if not positional:
            return ""
        label = positional[1] if len(positional) > 1 else positional[0]
        return label + named.get("p", "")
    if name == "c" and positional:
        return f"{positional[0]} °C"
    if name == "dk":
        return ":"
    if name in {"tt", "kbd", "nowrap"} and positional:
        return positional[0]
    return ""  # images, footnotes (rn/refn), navigation, sort keys: not recipe text


def render(wikitext: str) -> Rendered:
    """Wikitext → plain text, keeping line structure (lists, headings)."""
    subs: list[str] = []
    text = re.sub(r"<!--.*?-->", "", wikitext, flags=re.S)
    text = re.sub(r"<noinclude>.*?</noinclude>", "", text, flags=re.S)
    text = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    for _ in range(5):  # nested templates, innermost first
        new = _TEMPLATE.sub(lambda m: _render_template(m.group(1), subs), text)
        if new == text:
            break
        text = new
    text = re.sub(r"\[\[(?:Plik|File|Grafika|Image|Kategoria|Category):[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[([^|\]]*\|)?([^\]]*)\]\]", r"\2", text)  # [[target|label]] → label
    text = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", text)  # [url label] → label
    text = re.sub(r"'''?", "", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?(small|big|sup|sub|span|div|center|u|i|b|s)[^>]*>", "", text, flags=re.I)
    text = text.replace("&nbsp;", " ").replace("\u00a0", " ")
    return Rendered(text=text, sub_recipes=subs)


# ---------------------------------------------------------------- page → recipe


@dataclass
class IngredientLine:
    position: int
    raw_line: str
    variant: str | None
    group_name: str | None
    sub_recipe: str | None
    has_quantity: bool


@dataclass
class Recipe:
    title: str
    url: str
    revision_id: int
    revision_time: str
    categories: list[str]
    wikitext: str
    steps_text: str | None
    servings_text: str | None
    ingredients: list[IngredientLine]


def _clean_line(line: str) -> str:
    line = re.sub(r"^[*#:;]+\s*", "", line)
    return re.sub(r"\s+", " ", line).strip(" ,;")


def parse_page(page: dict[str, Any]) -> Recipe:
    """Split a recipe page into ingredient lines and preparation text.

    Headings decide the role of the lines under them: an "ingredients" heading opens an ingredient block, a
    "preparation" heading a steps block. Any other heading inside an ingredient block names a group ("Farsz",
    "Ciasto"); outside one it names a recipe variant ("Przepis klasyczny", "Wariant II").

    Pages without any ingredients heading (e.g. "Pascha": one heading per variant, list right under it) get a
    second pass where a variant heading opens an ingredient block. The result is kept only when most lines carry a
    quantity, so index pages ("Kurczak": a list of links) and encyclopedic lists ("Wołowina": cuts of beef) stay out.
    """
    recipe = _parse(page, variant_opens_ingredients=False)
    if not recipe.ingredients:
        fallback = _parse(page, variant_opens_ingredients=True)
        lines = fallback.ingredients
        if len(lines) >= 2 and sum(li.has_quantity for li in lines) * 2 >= len(lines):
            return fallback
    return recipe


def _parse(page: dict[str, Any], variant_opens_ingredients: bool) -> Recipe:
    title = page["title"].removeprefix(PREFIX)
    wikitext = page["wikitext"]
    mode: str | None = None  # "ingredients" | "steps" | "skip" | None
    ing_level = 0
    variant: str | None = None
    group: str | None = None
    ingredients: list[IngredientLine] = []
    steps: list[str] = []
    servings: str | None = None
    for raw in wikitext.splitlines():
        heading = re.match(r"^(=+)\s*(.*?)\s*=+\s*$", raw)
        if heading:
            level, name = len(heading.group(1)), render(heading.group(2)).text.strip()
            if INGREDIENT_HEADINGS.match(name):
                mode, ing_level, group = "ingredients", level, None
            elif STEP_HEADINGS.match(name):
                mode, group = "steps", None
            elif SKIP_HEADINGS.match(name):
                mode = "skip"
            elif mode == "ingredients" and level > ing_level:
                group = name
            elif mode == "steps":
                steps.append(name + ":")  # sub-steps heading ("Farsz", "Przygotowanie ciasta")
            elif variant_opens_ingredients:
                variant, mode, ing_level, group = name, "ingredients", level, None
            else:
                variant, mode, group = name, None, None
            continue
        rendered = render(raw)
        line = _clean_line(rendered.text)
        if not line:
            continue
        if servings is None and (m := SERVINGS.search(line)):
            servings = m.group(0)
        is_list = raw.lstrip().startswith(("*", "#"))
        if mode == "ingredients" and is_list:
            ingredients.append(
                IngredientLine(
                    position=len(ingredients),
                    raw_line=line,
                    variant=variant,
                    group_name=group,
                    sub_recipe=rendered.sub_recipes[0] if rendered.sub_recipes else None,
                    has_quantity=bool(HAS_QUANTITY.search(line)),
                )
            )
        elif mode == "ingredients" and line.endswith(":") and len(line) < 60:
            group = line.rstrip(":")  # "Ciasto:" written as a plain line instead of a heading
        elif mode == "steps":
            steps.append(line)
    return Recipe(
        title=title,
        url="https://pl.wikibooks.org/wiki/" + page["title"].replace(" ", "_"),
        revision_id=page["revid"],
        revision_time=page["timestamp"],
        categories=page.get("categories", []),
        wikitext=wikitext,
        steps_text="\n".join(steps) or None,
        servings_text=servings,
        ingredients=ingredients,
    )


def read_snapshot(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def load(conn: psycopg.Connection, snapshot: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "source_file": snapshot.name,
        "pages": 0,
        "recipes": 0,
        "no_ingredients": [],
        "no_steps": 0,
        "ingredient_lines": 0,
        "lines_with_quantity": 0,
        "recipes_half_quantified": 0,
        "sub_recipe_refs": 0,
        "with_servings": 0,
    }
    with conn.cursor() as cur:
        cur.execute("TRUNCATE recipes RESTART IDENTITY CASCADE")
        for page in read_snapshot(snapshot):
            stats["pages"] += 1
            r = parse_page(page)
            if not r.ingredients:
                stats["no_ingredients"].append(r.title)
                continue
            cur.execute(
                "INSERT INTO recipes (title, url, revision_id, revision_time, categories, wikitext, steps_text, "
                "servings_text) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                [
                    r.title,
                    r.url,
                    r.revision_id,
                    r.revision_time,
                    r.categories,
                    r.wikitext,
                    r.steps_text,
                    r.servings_text,
                ],
            )
            rid = cur.fetchone()[0]  # type: ignore[index]
            with cur.copy(
                "COPY recipe_ingredient_lines (recipe_id, position, variant, group_name, raw_line, sub_recipe, "
                "has_quantity) FROM STDIN"
            ) as copy:
                for li in r.ingredients:
                    copy.write_row(
                        [rid, li.position, li.variant, li.group_name, li.raw_line, li.sub_recipe, li.has_quantity]
                    )
            q = sum(li.has_quantity for li in r.ingredients)
            stats["recipes"] += 1
            stats["no_steps"] += r.steps_text is None
            stats["ingredient_lines"] += len(r.ingredients)
            stats["lines_with_quantity"] += q
            stats["recipes_half_quantified"] += q * 2 >= len(r.ingredients)
            stats["sub_recipe_refs"] += sum(li.sub_recipe is not None for li in r.ingredients)
            stats["with_servings"] += r.servings_text is not None
        stats["no_ingredients_count"] = len(stats["no_ingredients"])
        cur.execute("INSERT INTO ingest_runs (source, stats) VALUES ('wikibooks', %s)", [json.dumps(stats)])
    conn.commit()
    return stats
