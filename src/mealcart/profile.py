"""User profile and exclusion rules (concept D2: easy to customise).

A profile is YAML (see profiles/example.yaml). Exclusions name a concept from data/concepts.yaml and a level:

- `nie_jem` (preference): exclude a recipe when an ingredient line mentions the concept, a product when its
  ingredient text does. Traces are ignored; a product without ingredient text is allowed, flagged "unknown".
- `alergia`: additionally exclude on the OFF allergen tag and on traces, and treat "no ingredient text" as excluded
  (unknown is not safe).

Every plan is checked with `violations()` after it is built, so a rule broken by the planner is an error, not a
warning (concept D8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

CONCEPTS_FILE = Path(__file__).with_name("data") / "concepts.yaml"
Level = Literal["nie_jem", "alergia"]
_SPLIT = re.compile(r"[,;()\[\]:]|\s-\s|\.\s")


# "may contain" statements: traces, not ingredients. Matched up to the end of the sentence.
_TRACES = re.compile(
    r"(produkt\s+)?mo(że|ze)\s+zawiera(ć|c)[^.]*|may\s+contain[^.]*|kann\s+[^.]*?enthalten[^.]*|"
    r"peut\s+contenir[^.]*|spuren\s+von[^.]*|traces?\s+(de|d'|of)[^.]*|(możliwe\s+)?śladowe\s+ilości[^.]*",
    re.IGNORECASE,
)


def split_traces(text: str) -> tuple[str, str]:
    """Return (ingredients without trace statements, the trace statements)."""
    traces = " ".join(m.group(0) for m in _TRACES.finditer(text))
    return _TRACES.sub(" ", text), traces


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    patterns: tuple[re.Pattern[str], ...]
    negatives: tuple[re.Pattern[str], ...]
    off_allergen: str | None

    def matches(self, text: str) -> str | None:
        """Return the matching fragment, or None. Negatives cancel a match only inside the same fragment."""
        for fragment in _SPLIT.split(text):
            if not fragment.strip():
                continue
            cleaned = fragment
            for neg in self.negatives:
                cleaned = neg.sub(" ", cleaned)
            for pat in self.patterns:
                if pat.search(cleaned):
                    return fragment.strip()
        return None


@cache
def concepts(path: Path = CONCEPTS_FILE) -> dict[str, Concept]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, Concept] = {}
    for key, c in raw.items():
        out[key] = Concept(
            key=key,
            label=c.get("label", key),
            patterns=tuple(re.compile(p, re.IGNORECASE) for p in c["patterns"]),
            negatives=tuple(re.compile(p, re.IGNORECASE) for p in c.get("not", [])),
            off_allergen=c.get("off_allergen"),
        )
    return out


class Exclusion(BaseModel):
    skladnik: str = Field(description="concept key from data/concepts.yaml, e.g. orzech_wloski")
    poziom: Level = "nie_jem"

    def concept(self) -> Concept:
        try:
            return concepts()[self.skladnik]
        except KeyError:
            known = ", ".join(sorted(concepts()))
            raise ValueError(f"unknown concept {self.skladnik!r}; known: {known}") from None


class Profile(BaseModel):
    profil: str
    porcje: int = 1
    wyklucz: list[Exclusion] = []
    bialko_min_g_porcja: float | None = None
    kcal_max_porcja: float | None = None
    czas_max_min: int | None = None
    mam: list[str] = []

    @classmethod
    def load(cls, path: Path) -> Profile:
        profile = cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        for e in profile.wyklucz:
            e.concept()  # fail fast on a typo in the concept key
        return profile


@dataclass
class Verdict:
    allowed: bool
    reasons: list[str] = field(default_factory=list)  # why excluded
    unknown: list[str] = field(default_factory=list)  # what could not be checked


def check_recipe(profile: Profile, ingredient_lines: list[str]) -> Verdict:
    v = Verdict(allowed=True)
    for e in profile.wyklucz:
        c = e.concept()
        for line in ingredient_lines:
            if hit := c.matches(line):
                v.allowed = False
                v.reasons.append(f"{c.label}: „{hit}”")
                break
    return v


def check_product(profile: Profile, ingredients_text: str | None, allergens: list[str], traces: list[str]) -> Verdict:
    v = Verdict(allowed=True)
    body, trace_text = split_traces(ingredients_text) if ingredients_text else ("", "")
    for e in profile.wyklucz:
        c = e.concept()
        if body and (hit := c.matches(body)):
            v.allowed = False
            v.reasons.append(f"{c.label}: skład „{hit}”")
            continue
        if e.poziom == "alergia":
            if c.off_allergen and c.off_allergen in allergens:
                v.allowed = False
                v.reasons.append(f"{c.label}: alergen {c.off_allergen}")
            elif (c.off_allergen and c.off_allergen in traces) or (trace_text and c.matches(trace_text)):
                v.allowed = False
                v.reasons.append(f"{c.label}: może zawierać ({c.off_allergen or c.label})")
            elif not ingredients_text:
                v.allowed = False
                v.reasons.append(f"{c.label}: brak składu (alergia = nie ryzykujemy)")
        elif not ingredients_text:
            v.unknown.append(f"{c.label}: brak składu")
    return v
