import pytest

from evals.corpus import load_corpus
from evals.live_compare import DEFAULT_BOT_USERNAME, command_text_for_case
from honeybuy_tg.recipes import (
    html_to_text,
    parse_add_recipe_request,
    parse_recipe_alias_argument,
    parse_recipe_alias_request,
    parse_learn_recipe_request,
    looks_like_recipe_reuse_request,
    recipe_command_from_ai,
    should_try_ai_recipe_command,
)


CORPUS_CASES = load_corpus().cases
RECIPE_FALSE_POSITIVE_CASES = [
    case for case in CORPUS_CASES if "recipe-false-positive" in case.tags
]
AI_RECIPE_ROUTE_CASES = [case for case in CORPUS_CASES if "ai-route" in case.tags]
DETERMINISTIC_RECIPE_REUSE_CASES = [
    case
    for case in CORPUS_CASES
    if case.expected.route == "recipe"
    and "reuse" in case.tags
    and "deterministic" in case.tags
]

RECIPE_CASCADE_PHRASES = (
    "купи ингредиенты для солянки",
    "купи продукты для солянки",
    "пожалуйста, купи на солянку",
    "пожалуйста, добавь всё для солянки",
    "can you buy ingredients for chili",
)

NON_RECIPE_SHOPPING_PHRASES = (
    "купи на завтра молоко",
    "купи молоко на завтра",
    "добавь сыр для завтрака",
    "buy candles for tomorrow",
    "купи молоко",
    "buy milk",
)


def test_parse_learn_recipe_request():
    parsed = parse_learn_recipe_request(
        "Выучи солянку - вот ссылка https://cookidoo.co.uk/recipes/recipe/en-GB/r769287"
    )

    assert parsed is not None
    assert parsed.name == "солянку"
    assert parsed.url == "https://cookidoo.co.uk/recipes/recipe/en-GB/r769287"
    assert parsed.recipe_text is None


def test_parse_pasted_recipe_learning_request():
    parsed = parse_learn_recipe_request(
        """
        Запомни рецепт блины
        Ингредиенты:
        - flour 200 g
        - milk 300 ml
        Приготовление:
        Mix and fry.
        """
    )

    assert parsed is not None
    assert parsed.name == "блины"
    assert parsed.url is None
    assert "flour 200 g" in parsed.recipe_text


def test_parse_pasted_recipe_learning_request_with_url_prefers_text():
    parsed = parse_learn_recipe_request(
        """
        Save recipe pancakes
        Source: https://example.com/pancakes
        Ingredients:
        - flour 200 g
        - milk 300 ml
        Method:
        Mix and fry.
        """
    )

    assert parsed is not None
    assert parsed.name == "pancakes"
    assert parsed.url is None
    assert parsed.recipe_text is not None
    assert "Source: https://example.com/pancakes" in parsed.recipe_text
    assert "- flour 200 g" in parsed.recipe_text


def test_parse_pasted_recipe_learning_request_uses_title_line():
    parsed = parse_learn_recipe_request(
        """
        Выучи рецепт
        Pancakes
        Ingredients:
        - flour 200 g
        - milk 300 ml
        Method:
        Mix and fry.
        """
    )

    assert parsed is not None
    assert parsed.name == "Pancakes"
    assert parsed.url is None


def test_parse_english_pasted_recipe_learning_request():
    parsed = parse_learn_recipe_request(
        """
        Save recipe pancakes
        Ingredients:
        - flour 200 g
        - milk 300 ml
        Method:
        Mix and fry.
        """
    )

    assert parsed is not None
    assert parsed.name == "pancakes"
    assert parsed.url is None


def test_parse_add_recipe_request():
    parsed = parse_add_recipe_request("добавь все для солянки")

    assert parsed is not None
    assert parsed.name == "солянки"


def test_parse_recipe_alias_request():
    parsed = parse_recipe_alias_request("alias recipe pancakes as breakfast")

    assert parsed is not None
    assert parsed.recipe_name == "pancakes"
    assert parsed.alias == "breakfast"

    natural = parse_recipe_alias_request("add alias breakfast to pancakes")
    assert natural is not None
    assert natural.recipe_name == "pancakes"
    assert natural.alias == "breakfast"

    command_arg = parse_recipe_alias_argument("солянка = суп")
    assert command_arg is not None
    assert command_arg.recipe_name == "солянка"
    assert command_arg.alias == "суп"


def test_ai_recipe_command_detection_is_selective():
    assert should_try_ai_recipe_command("купи на солянку")
    assert should_try_ai_recipe_command("ингредиенты для солянки")
    assert should_try_ai_recipe_command("save recipe pancakes")
    assert not should_try_ai_recipe_command("купи молоко")
    assert looks_like_recipe_reuse_request("купи на солянку")
    assert not looks_like_recipe_reuse_request("купи молоко")


@pytest.mark.parametrize("text", RECIPE_CASCADE_PHRASES)
def test_explicit_and_polite_recipe_phrases_reach_recipe_cascade(text):
    deterministic = parse_add_recipe_request(text)

    assert deterministic is not None or should_try_ai_recipe_command(text)
    assert should_try_ai_recipe_command(text)
    assert looks_like_recipe_reuse_request(text)


@pytest.mark.parametrize("text", NON_RECIPE_SHOPPING_PHRASES)
def test_temporal_purpose_and_single_product_phrases_avoid_recipe_routing(text):
    assert parse_add_recipe_request(text) is None
    assert not should_try_ai_recipe_command(text)
    assert not looks_like_recipe_reuse_request(text)


@pytest.mark.parametrize(
    "case",
    RECIPE_FALSE_POSITIVE_CASES,
    ids=lambda case: case.id,
)
def test_corpus_recipe_false_positives_are_not_recipe_candidates(case):
    text = command_text_for_case(case, bot_username=DEFAULT_BOT_USERNAME)

    assert not should_try_ai_recipe_command(text)
    assert not looks_like_recipe_reuse_request(text)


@pytest.mark.parametrize(
    "case",
    AI_RECIPE_ROUTE_CASES,
    ids=lambda case: case.id,
)
def test_all_corpus_ai_recipe_routes_reach_recipe_prefilter(case):
    text = command_text_for_case(case, bot_username=DEFAULT_BOT_USERNAME)

    assert should_try_ai_recipe_command(text)
    assert looks_like_recipe_reuse_request(text)


@pytest.mark.parametrize(
    "case",
    DETERMINISTIC_RECIPE_REUSE_CASES,
    ids=lambda case: case.id,
)
def test_documented_deterministic_recipe_reuse_phrases_remain_candidates(case):
    text = command_text_for_case(case, bot_username=DEFAULT_BOT_USERNAME)

    assert parse_add_recipe_request(text) is not None
    assert should_try_ai_recipe_command(text)
    assert looks_like_recipe_reuse_request(text)


def test_recipe_command_from_ai():
    parsed = recipe_command_from_ai(
        {"action": "add_recipe", "recipe_name": "солянка", "url": None}
    )

    assert parsed.action == "add_recipe"
    assert parsed.name == "солянка"
    assert parsed.url is None
    assert parsed.recipe_text is None
    learned = recipe_command_from_ai(
        {
            "action": "learn_recipe",
            "recipe_name": "блины",
            "url": None,
            "recipe_text": "Ingredients: flour, milk",
        }
    )
    assert learned.action == "learn_recipe"
    assert learned.recipe_text == "Ingredients: flour, milk"
    learned_without_body = recipe_command_from_ai(
        {"action": "learn_recipe", "recipe_name": "блины", "url": None}
    )
    assert learned_without_body.action == "learn_recipe"
    assert learned_without_body.recipe_text is None
    assert recipe_command_from_ai({"action": "add_recipe"}).action == "unknown"


def test_html_to_text_omits_script_and_keeps_visible_text():
    text = html_to_text(
        """
        <html>
          <body>
            <h1>Solyanka</h1>
            <script>hidden()</script>
            <p>fresh dill</p>
          </body>
        </html>
        """
    )

    assert "Solyanka" in text
    assert "fresh dill" in text
    assert "hidden" not in text
