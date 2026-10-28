from pathlib import Path

import pytest

from mealcart.profile import Exclusion, Profile, check_product, check_recipe, concepts, split_traces

ROOT = Path(__file__).resolve().parents[1]


def prof(level: str = "nie_jem", key: str = "orzech_wloski") -> Profile:
    return Profile(profil="t", wyklucz=[Exclusion(skladnik=key, poziom=level)])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "10 dag orzechów włoskich",
        "Orzechy włoskie łuskane",
        "posiekanymi orzechami włoskimi",
        "walnuts 25%",
        "Walnusskerne",
        "Walnüsse",
        "cerneaux de noix",
        "noci",
    ],
)
def test_walnut_matches_all_forms(text: str) -> None:
    assert concepts()["orzech_wloski"].matches(text)


@pytest.mark.parametrize(
    "text",
    [
        "orzechy laskowe",
        "noix de coco",
        "noix de cajou",
        "gałka muszkatołowa",
        "pieczeń na noce",
    ],
)
def test_walnut_does_not_match_other_things(text: str) -> None:
    assert concepts()["orzech_wloski"].matches(text) is None


def test_nuts_negatives_are_local_to_the_fragment() -> None:
    nuts = concepts()["orzechy"]
    assert nuts.matches("orzeszki ziemne") is None
    assert nuts.matches("wiórki kokosowe") is None
    assert nuts.matches("orzeszki ziemne, migdały")  # the almond fragment still matches


def test_recipe_exclusion() -> None:
    v = check_recipe(prof(), ["500 g mąki", "10 dag orzechów włoskich"])
    assert not v.allowed and "orzech włoski" in v.reasons[0]
    assert check_recipe(prof(), ["2 kg kapusty", "orzechy laskowe"]).allowed


def test_preference_ignores_traces_and_unknown() -> None:
    text = "mleko, cukier. Produkt może zawierać orzechy włoskie."
    assert split_traces(text)[1].startswith("Produkt może zawierać")
    assert check_product(prof(), text, [], []).allowed
    v = check_product(prof(), None, [], [])
    assert v.allowed and v.unknown


def test_allergy_is_strict() -> None:
    a = prof("alergia")
    assert not check_product(a, "mleko, cukier. Produkt może zawierać orzechy włoskie.", [], []).allowed
    assert not check_product(a, "mleko", [], ["en:nuts"]).allowed
    assert not check_product(a, None, [], []).allowed  # unknown is not safe
    assert check_product(a, "mleko, cukier", [], []).allowed


def test_unknown_concept_fails_fast() -> None:
    with pytest.raises(ValueError, match="unknown concept"):
        prof(key="orzech_wlosky").wyklucz[0].concept()


def test_example_profile_loads() -> None:
    p = Profile.load(ROOT / "profiles" / "example.yaml")
    assert p.wyklucz[0].skladnik == "orzech_wloski"
