import pytest

from honeybuy_tg.ai import (
    CategoryParseResponse,
    ItemNormalizationResponse,
    RecipeCommandParseResponse,
    RecipeExtractResponse,
    RecipeCommandParser,
    RecipeExtractor,
    ShoppingItemCategorizer,
    ShoppingItemNormalizer,
    ShoppingTextParseResponse,
    ShoppingTextParser,
    parse_ai_json_response,
)
from honeybuy_tg.metrics import AIRequestReport


class FakeResponse:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeResponsesClient:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text

    async def create(self, **kwargs):
        return FakeResponse(self.output_text)


class FakeOpenAIClient:
    def __init__(self, output_text: str) -> None:
        self.responses = FakeResponsesClient(output_text)


def wrapper_with_response(wrapper_class, output_text: str):
    wrapper = wrapper_class.__new__(wrapper_class)
    wrapper.client = FakeOpenAIClient(output_text)
    wrapper.model = "test-model"
    return wrapper


def test_shopping_text_response_validates_and_cleans_items():
    parsed = ShoppingTextParseResponse.model_validate_json(
        """
        {
          "action": "add_items",
          "items": [" milk ", "  ", "fresh   bread"],
          "needs_confirmation": false,
          "clarification_question": null
        }
        """
    )

    assert parsed.model_dump() == {
        "action": "add_items",
        "items": ["milk", "fresh bread"],
        "needs_confirmation": False,
        "clarification_question": None,
    }


@pytest.mark.parametrize("action", ["add_items", "remove_items", "mark_bought"])
@pytest.mark.parametrize("items", [[], ["  ", "\t\n"]])
def test_shopping_text_response_rejects_empty_mutating_items(
    action: str,
    items: list[str],
):
    with pytest.raises(ValueError):
        ShoppingTextParseResponse.model_validate(
            {
                "action": action,
                "items": items,
                "needs_confirmation": False,
                "clarification_question": None,
            }
        )


def test_ai_response_models_require_declared_format():
    with pytest.raises(ValueError):
        parse_ai_json_response(
            FakeResponse('{"action":"add_items","items":["milk"]}'),
            ShoppingTextParseResponse,
        )


def test_parse_ai_json_response_reports_invalid_response_status():
    report = AIRequestReport()

    with pytest.raises(ValueError):
        parse_ai_json_response(
            FakeResponse('{"items":[{"id":1,"category":"   "}]}'),
            CategoryParseResponse,
            report=report,
        )

    assert report.status == "invalid_response"


@pytest.mark.asyncio
async def test_shopping_text_parser_returns_validated_payload():
    parser = wrapper_with_response(
        ShoppingTextParser,
        """
        {
          "action": "add_items",
          "items": [" milk ", "fresh   bread"],
          "needs_confirmation": false,
          "clarification_question": "  "
        }
        """,
    )

    parsed = await parser.parse("buy milk and bread")

    assert parsed == {
        "action": "add_items",
        "items": ["milk", "fresh bread"],
        "needs_confirmation": False,
        "clarification_question": None,
    }


@pytest.mark.asyncio
async def test_shopping_item_normalizer_returns_item_identities():
    normalizer = wrapper_with_response(
        ShoppingItemNormalizer,
        """
        {
          "items": [
            {
              "name": " tomato paste, 60 g ",
              "canonical_name": " томатная   паста ",
              "canonical_key": " tomato_paste "
            }
          ]
        }
        """,
    )

    identities = await normalizer.normalize(["tomato paste, 60 g"])

    assert list(identities) == ["tomato paste, 60 g"]
    assert identities["tomato paste, 60 g"].raw_name == "tomato paste, 60 g"
    assert identities["tomato paste, 60 g"].canonical_name == "томатная паста"
    assert identities["tomato paste, 60 g"].canonical_key == "tomato_paste"


@pytest.mark.asyncio
async def test_shopping_item_categorizer_returns_categories_by_item_id():
    categorizer = wrapper_with_response(
        ShoppingItemCategorizer,
        '{"items":[{"id":1,"category":" Молочка "},{"id":2,"category":"Овощи"}]}',
    )

    categories = await categorizer.categorize(
        [{"id": 1, "name": "milk"}, {"id": 2, "name": "tomatoes"}]
    )

    assert categories == {1: "Молочка", 2: "Овощи"}


@pytest.mark.asyncio
async def test_recipe_extractor_returns_validated_payload():
    extractor = wrapper_with_response(
        RecipeExtractor,
        """
        {
          "name": " солянка ",
          "ingredients": [
            {"name": " carrot ", "quantity": " 120   g "},
            {"name": "onion", "quantity": null}
          ]
        }
        """,
    )

    recipe = await extractor.extract(
        requested_name="солянка",
        source_url="https://example.com/recipe",
        page_text="recipe page",
    )

    assert recipe == {
        "name": "солянка",
        "ingredients": [
            {"name": "carrot", "quantity": "120 g"},
            {"name": "onion", "quantity": None},
        ],
    }


@pytest.mark.asyncio
async def test_recipe_command_parser_returns_validated_payload():
    parser = wrapper_with_response(
        RecipeCommandParser,
        """
        {
          "action": "learn_recipe",
          "recipe_name": " солянка ",
          "url": " https://example.com/recipe "
        }
        """,
    )

    parsed = await parser.parse("запомни солянку https://example.com/recipe")

    assert parsed == {
        "action": "learn_recipe",
        "recipe_name": "солянка",
        "url": "https://example.com/recipe",
        "recipe_text": None,
    }


@pytest.mark.asyncio
async def test_ai_wrapper_reports_invalid_response_metric(monkeypatch):
    observed = []

    def observe(**kwargs):
        observed.append(kwargs)

    monkeypatch.setattr("honeybuy_tg.metrics._observe_ai_request", observe)
    parser = wrapper_with_response(
        ShoppingTextParser,
        '{"action":"add_items","items":[],"needs_confirmation":false,'
        '"clarification_question":null}',
    )

    with pytest.raises(ValueError):
        await parser.parse("buy")

    assert observed[0]["operation"] == "text_parse"
    assert observed[0]["status"] == "invalid_response"


def test_ai_response_models_do_not_coerce_wrong_field_types():
    with pytest.raises(ValueError):
        parse_ai_json_response(
            FakeResponse('{"items":[{"id":"1","category":"Молочка"}]}'),
            CategoryParseResponse,
        )


def test_item_normalization_response_validates_shape():
    parsed = ItemNormalizationResponse.model_validate_json(
        """
        {
          "items": [
            {
              "name": " tomato paste, 60 g ",
              "canonical_name": " томатная   паста ",
              "canonical_key": " tomato_paste "
            }
          ]
        }
        """
    )

    assert parsed.items[0].name == "tomato paste, 60 g"
    assert parsed.items[0].canonical_name == "томатная паста"
    assert parsed.items[0].canonical_key == "tomato_paste"


def test_recipe_response_models_validate_shape():
    recipe = RecipeExtractResponse.model_validate_json(
        """
        {
          "name": " солянка ",
          "ingredients": [{"name": " carrot ", "quantity": " 120   g "}]
        }
        """
    )
    command = RecipeCommandParseResponse.model_validate_json(
        """
        {
          "action": "add_recipe",
          "recipe_name": " солянка ",
          "url": null
        }
        """
    )
    unknown_command = RecipeCommandParseResponse.model_validate(
        {"action": "unknown", "recipe_name": None, "url": None}
    )

    assert recipe.name == "солянка"
    assert recipe.ingredients[0].quantity == "120 g"
    assert command.recipe_name == "солянка"
    assert command.url is None
    assert command.recipe_text is None
    assert unknown_command.model_dump() == {
        "action": "unknown",
        "recipe_name": None,
        "url": None,
        "recipe_text": None,
    }


def test_recipe_command_response_accepts_pasted_recipe_text():
    command = RecipeCommandParseResponse.model_validate(
        {
            "action": "learn_recipe",
            "recipe_name": "блины",
            "url": None,
            "recipe_text": " Ingredients: flour, milk ",
        }
    )

    assert command.model_dump() == {
        "action": "learn_recipe",
        "recipe_name": "блины",
        "url": None,
        "recipe_text": "Ingredients: flour, milk",
    }


def test_recipe_command_response_accepts_learn_recipe_without_body_echo():
    command = RecipeCommandParseResponse.model_validate(
        {"action": "learn_recipe", "recipe_name": "блины", "url": None}
    )

    assert command.action == "learn_recipe"
    assert command.recipe_name == "блины"
    assert command.url is None
    assert command.recipe_text is None


def test_recipe_extract_response_rejects_empty_ingredients_as_invalid_response():
    report = AIRequestReport()

    with pytest.raises(ValueError):
        parse_ai_json_response(
            FakeResponse('{"name":"солянка","ingredients":[]}'),
            RecipeExtractResponse,
            report=report,
        )

    assert report.status == "invalid_response"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "add_recipe", "recipe_name": None, "url": None},
        {
            "action": "learn_recipe",
            "recipe_name": None,
            "url": "https://example.com/recipe",
        },
        {
            "action": "learn_recipe",
            "recipe_name": "   ",
            "url": "https://example.com/recipe",
        },
    ],
)
def test_recipe_command_response_rejects_invalid_action_payloads(
    payload: dict[str, object],
):
    with pytest.raises(ValueError):
        RecipeCommandParseResponse.model_validate(payload)
