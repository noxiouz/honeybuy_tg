import hashlib
import json
from collections import Counter
import re

import pytest

from evals.corpus import (
    DEFAULT_CORPUS_PATH,
    RoutingCase,
    load_corpus,
    normalize_eval_text,
)


def valid_case_payload() -> dict[str, object]:
    return {
        "id": "ru-shopping-test-001",
        "text": "купи молоко",
        "locale": "ru",
        "channel": "text",
        "critical": True,
        "tags": ["shopping", "test"],
        "expected": {
            "route": "shopping",
            "action": "add_items",
            "accepted_items": [["молоко"]],
        },
    }


def valid_recipe_case_payload() -> dict[str, object]:
    return {
        "id": "ru-recipe-test-001",
        "text": "купи на солянку",
        "locale": "ru",
        "channel": "text",
        "critical": True,
        "tags": ["recipe", "test"],
        "expected": {
            "route": "recipe",
            "action": "add_recipe",
            "recipe_name": "солянка",
        },
    }


def write_jsonl(path, *payloads: object) -> None:
    path.write_text(
        "".join(json.dumps(payload, ensure_ascii=False) + "\n" for payload in payloads),
        encoding="utf-8",
    )


def test_production_loader_loads_shipped_corpus_and_stable_hash():
    corpus = load_corpus()
    reloaded = load_corpus(DEFAULT_CORPUS_PATH)

    assert corpus.path == DEFAULT_CORPUS_PATH
    assert len(corpus.cases) == 78
    assert len({case.id for case in corpus.cases}) == 78
    assert re.fullmatch(r"[0-9a-f]{64}", corpus.sha256)
    assert corpus.sha256 == hashlib.sha256(DEFAULT_CORPUS_PATH.read_bytes()).hexdigest()
    assert reloaded.sha256 == corpus.sha256
    assert reloaded.cases == corpus.cases


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("{not-json}\n", "invalid JSON at line 1"),
        ("\n", "blank JSONL record at line 1"),
    ],
)
def test_production_loader_rejects_malformed_jsonl(tmp_path, content, error):
    path = tmp_path / "invalid.jsonl"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        load_corpus(path)


@pytest.mark.parametrize("extra_location", ["case", "expected"])
def test_production_loader_rejects_undeclared_fields(tmp_path, extra_location):
    payload = valid_case_payload()
    if extra_location == "case":
        payload["surprise"] = True
    else:
        payload["expected"]["surprise"] = True
    path = tmp_path / "extra.jsonl"
    write_jsonl(path, payload)

    with pytest.raises(ValueError, match="invalid case at line 1") as error:
        load_corpus(path)
    assert "surprise" in str(error.value)


def test_production_loader_rejects_duplicate_ids_with_line_number(tmp_path):
    payload = valid_case_payload()
    path = tmp_path / "duplicate.jsonl"
    write_jsonl(path, payload, payload)

    with pytest.raises(
        ValueError,
        match="duplicate case id at line 2: ru-shopping-test-001",
    ):
        load_corpus(path)


@pytest.mark.parametrize(
    ("route", "action", "error"),
    [
        ("shopping", "add_recipe", "shopping route requires a shopping action"),
        ("recipe", "show_list", "recipe route requires a recipe action"),
        ("unhandled", "add_items", "unhandled route requires unknown action"),
        ("ambiguous", "show_list", "ambiguous route requires unknown action"),
    ],
)
def test_routing_case_rejects_invalid_route_action_pairs(route, action, error):
    payload = valid_case_payload()
    payload["tags"] = [route, "test"]
    payload["expected"] = {"route": route, "action": action}

    with pytest.raises(ValueError, match=error):
        RoutingCase.model_validate(payload)


@pytest.mark.parametrize(
    ("accepted_items", "error"),
    [
        ([[]], "each accepted_items variant needs nonblank items"),
        ([["  "]], "each accepted_items variant needs nonblank items"),
        ([["Ёжевика", "  ежевика  "]], "contains duplicate items"),
    ],
)
def test_routing_case_rejects_blank_or_normalized_duplicate_items(
    accepted_items,
    error,
):
    payload = valid_case_payload()
    payload["expected"]["accepted_items"] = accepted_items

    with pytest.raises(ValueError, match=error):
        RoutingCase.model_validate(payload)


def test_routing_case_rejects_duplicate_normalized_item_variants():
    payload = valid_case_payload()
    payload["expected"]["accepted_items"] = [
        ["Ёжевичное варенье"],
        ["  ежевичное   варенье "],
    ]

    with pytest.raises(ValueError, match="duplicate accepted_items variant"):
        RoutingCase.model_validate(payload)


@pytest.mark.parametrize(
    ("accepted_recipe_names", "error"),
    [
        ([], "must contain at least one name"),
        (["   "], "must contain nonblank names"),
        (["Солянки", "  солянки  "], "duplicate accepted_recipe_names"),
    ],
)
def test_recipe_expectation_rejects_invalid_accepted_name_variants(
    accepted_recipe_names,
    error,
):
    payload = valid_recipe_case_payload()
    payload["expected"]["accepted_recipe_names"] = accepted_recipe_names

    with pytest.raises(ValueError, match=error):
        RoutingCase.model_validate(payload)


def test_recipe_expectation_rejects_canonical_name_as_accepted_alternative():
    payload = valid_recipe_case_payload()
    payload["expected"]["accepted_recipe_names"] = ["  СОЛЯНКА "]

    with pytest.raises(ValueError, match="duplicates recipe_name"):
        RoutingCase.model_validate(payload)


def test_recipe_expectation_accepts_distinct_normalized_name_alternatives():
    payload = valid_recipe_case_payload()
    payload["expected"]["accepted_recipe_names"] = ["солянки", "солянку"]

    case = RoutingCase.model_validate(payload)

    assert case.expected.accepted_recipe_names == ["солянки", "солянку"]


def test_shopping_expectation_rejects_recipe_name_alternatives():
    payload = valid_case_payload()
    payload["expected"]["accepted_recipe_names"] = ["молока"]

    with pytest.raises(ValueError, match="recipe/ambiguity"):
        RoutingCase.model_validate(payload)


def test_text_routing_corpus_meets_coverage_floor():
    cases = load_corpus().cases
    route_counts = Counter(case.expected.route for case in cases)
    channel_counts = Counter(case.channel for case in cases)

    assert route_counts["shopping"] >= 30
    assert route_counts["recipe"] >= 15
    assert route_counts["unhandled"] + route_counts["ambiguous"] >= 10
    assert channel_counts["mention"] + channel_counts["voice_transcript"] >= 5
    assert {case.locale for case in cases} == {"ru", "en", "mixed"}
    required_tags = {
        "politeness",
        "typo",
        "punctuation",
        "temporal-modifier",
        "purpose-modifier",
        "multi-item",
    }
    present_tags = {tag for case in cases for tag in case.tags}
    assert required_tags <= present_tags
    assert sum("deterministic" in case.tags for case in cases) >= 13


@pytest.mark.parametrize(
    ("text", "route", "action", "required_tag"),
    [
        ("купи молоко", "shopping", "add_items", "ai-unknown-fallback"),
        ("купи продукты", "shopping", "add_items", "recipe-false-positive"),
        (
            "купи молоко на завтра",
            "shopping",
            "add_items",
            "recipe-false-positive",
        ),
        (
            "добавь сыр для завтрака",
            "shopping",
            "add_items",
            "recipe-false-positive",
        ),
        ("добавь всё для солянки", "recipe", "add_recipe", "deterministic"),
        ("купи на солянку", "recipe", "add_recipe", "ai-route"),
        ("ингредиенты для солянки", "recipe", "add_recipe", "ai-route"),
        (
            "купи ингредиенты для солянки",
            "recipe",
            "add_recipe",
            "explicit-ingredients",
        ),
        (
            "купи продукты для солянки",
            "recipe",
            "add_recipe",
            "explicit-products",
        ),
        (
            "пожалуйста, купи на солянку",
            "recipe",
            "add_recipe",
            "politeness",
        ),
        (
            "пожалуйста, добавь всё для солянки",
            "recipe",
            "add_recipe",
            "politeness",
        ),
        (
            "can you buy ingredients for chili",
            "recipe",
            "add_recipe",
            "politeness",
        ),
        (
            "купи на завтра молоко",
            "shopping",
            "add_items",
            "recipe-false-positive",
        ),
        ("@HoneyBuyBot, купи молоко", "shopping", "add_items", "mention"),
    ],
)
def test_text_routing_corpus_contains_critical_regressions(
    text: str,
    route: str,
    action: str,
    required_tag: str,
):
    matching = [case for case in load_corpus().cases if case.text == text]

    assert len(matching) == 1
    case = matching[0]
    assert case.critical
    assert case.expected.route == route
    assert case.expected.action == action
    assert required_tag in case.tags


def test_ambiguous_cases_are_labeled_without_guessed_semantics():
    ambiguous = [
        case
        for case in load_corpus().cases
        if case.expected.route == "ambiguous"
    ]

    assert ambiguous
    assert all(case.expected.action == "unknown" for case in ambiguous)
    assert all(case.expected.ambiguity_reason for case in ambiguous)
    assert all(case.expected.accepted_items is None for case in ambiguous)
    assert all(case.expected.recipe_name is None for case in ambiguous)


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        ("  СЫР   для   завтрака  ", "сыр для завтрака"),
        ("ЁЖЕВИЧНОЕ   ВАРЕНЬЕ", "ежевичное варенье"),
        ("Oat   MILK", "oat milk"),
    ],
)
def test_production_eval_text_normalization(value: str, normalized: str):
    assert normalize_eval_text(value) == normalized
