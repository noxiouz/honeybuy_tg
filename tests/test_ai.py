import json
import asyncio

import pytest

from honeybuy_tg.ai import (
    AIResponseModel,
    CategorizedItemResponse,
    CategoryParseResponse,
    ItemNormalizationResponse,
    NormalizedItemResponse,
    RecipeCommandParseResponse,
    RecipeExtractResponse,
    RecipeCommandParser,
    RecipeExtractor,
    RecipeIngredientResponse,
    ShoppingItemCategorizer,
    ShoppingItemNormalizer,
    ShoppingTextParseResponse,
    ShoppingTextParser,
    VoiceTranscriber,
    parse_ai_json_response,
)
from honeybuy_tg.metrics import AIRequestReport
from honeybuy_tg.prompts import (
    CATEGORY_PROMPT,
    ITEM_NORMALIZATION_PROMPT,
    PROMPT_SPECS,
    RECIPE_COMMAND_PROMPT,
    RECIPE_EXTRACT_PROMPT,
    SHOPPING_TEXT_PROMPT,
    VOICE_TRANSCRIPTION_PROMPT,
    PromptSpec,
    build_prompt_registry,
)


class FakeResponse:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeTranscriptionResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeResponsesClient:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.output_text)


class FakeTranscriptionsClient:
    def __init__(self, transcription_text: str) -> None:
        self.transcription_text = transcription_text
        self.calls = []

    async def create(self, **kwargs):
        audio_file = kwargs["file"]
        call = {key: value for key, value in kwargs.items() if key != "file"}
        call["file_name"] = audio_file.name
        call["file_bytes"] = audio_file.read()
        self.calls.append(call)
        return FakeTranscriptionResponse(self.transcription_text)


class FakeAudioClient:
    def __init__(self, transcription_text: str) -> None:
        self.transcriptions = FakeTranscriptionsClient(transcription_text)


class FakeOpenAIClient:
    def __init__(self, output_text: str, *, transcription_text: str = "") -> None:
        self.responses = FakeResponsesClient(output_text)
        self.audio = FakeAudioClient(transcription_text)


def wrapper_with_response(wrapper_class, output_text: str):
    wrapper = wrapper_class.__new__(wrapper_class)
    wrapper.client = FakeOpenAIClient(output_text)
    wrapper.model = "test-model"
    return wrapper


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,reason,schema", [
    (None, "validated", "valid"),
    ("invalid", "invalid_schema", "invalid"),
    ("request", "error", "not_validated"),
    ("cancel", "cancelled", "not_validated"),
])
async def test_trace_real_ai_adapter_metadata_and_privacy(failure, reason, schema):
    from honeybuy_tg.tracing import RoutingTrace, current_trace

    response = {"action": "unknown", "items": [], "needs_confirmation": False,
                "clarification_question": None}
    wrapper = wrapper_with_response(ShoppingTextParser, json.dumps(response) if failure != "invalid" else "private-canary-invalid")
    wrapper.model = "private-canary-model"
    if failure in {"request", "cancel"}:
        async def fail(**kwargs):
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("private-canary-exception")
        wrapper.client.responses.create = fail
    trace = RoutingTrace()
    token = current_trace.set(trace)
    try:
        if failure:
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else Exception):
                await wrapper.parse("private-canary-input")
        else:
            assert await wrapper.parse("private-canary-input") == response
    finally:
        current_trace.reset(token)
    event = trace.snapshot()["events"][0]
    assert event["reason"] == reason
    assert event["schema"] == schema
    assert event["operation"] == SHOPPING_TEXT_PROMPT.operation
    assert event["revision"] == SHOPPING_TEXT_PROMPT.revision
    assert event["fingerprint"] == SHOPPING_TEXT_PROMPT.fingerprint
    assert event["model"] == "other"
    assert "private-canary" not in json.dumps(trace.snapshot())


def assert_response_request(wrapper, *, expected_input, prompt_spec):
    assert len(wrapper.client.responses.calls) == 1
    request = wrapper.client.responses.calls[0]
    assert request["model"] == "test-model"
    assert request["input"] == expected_input
    assert request["instructions"] == prompt_spec.instructions
    assert request["temperature"] == prompt_spec.temperature
    assert request["max_output_tokens"] == prompt_spec.max_output_tokens
    return request["instructions"]


@pytest.mark.asyncio
async def test_voice_transcriber_sends_russian_bilingual_request_contract(tmp_path):
    audio_path = tmp_path / "голос.webm"
    audio_path.write_bytes(b"offline-audio")
    transcriber = VoiceTranscriber.__new__(VoiceTranscriber)
    transcriber.client = FakeOpenAIClient(
        "",
        transcription_text="  купи молоко  ",
    )
    transcriber.model = "test-transcribe-model"

    transcript = await transcriber.transcribe(audio_path)

    assert transcript == "купи молоко"
    calls = transcriber.client.audio.transcriptions.calls
    assert calls == [
        {
            "model": "test-transcribe-model",
            "language": "ru",
            "prompt": VOICE_TRANSCRIPTION_PROMPT.instructions,
            "file_name": str(audio_path),
            "file_bytes": b"offline-audio",
        }
    ]
    assert "Russian and English" in calls[0]["prompt"]


def test_prompt_registry_has_six_unique_versioned_operations():
    expected_specs = (
        VOICE_TRANSCRIPTION_PROMPT,
        CATEGORY_PROMPT,
        ITEM_NORMALIZATION_PROMPT,
        SHOPPING_TEXT_PROMPT,
        RECIPE_EXTRACT_PROMPT,
        RECIPE_COMMAND_PROMPT,
    )

    assert len(PROMPT_SPECS) == 6
    assert set(PROMPT_SPECS) == {
        "voice_transcription",
        "category_parse",
        "item_normalize",
        "text_parse",
        "recipe_extract",
        "recipe_command_parse",
    }
    assert tuple(PROMPT_SPECS.values()) == expected_specs
    assert len({spec.operation for spec in expected_specs}) == 6
    assert all(spec.revision.strip() for spec in expected_specs)
    assert all(PROMPT_SPECS[spec.operation] is spec for spec in expected_specs)


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "   "},
        {"revision": "\t"},
        {"instructions": "\n"},
        {"temperature": -0.01},
        {"temperature": float("nan")},
        {"temperature": float("inf")},
        {"max_output_tokens": 0},
        {"max_output_tokens": -1},
        {"max_output_tokens": 1.5},
        {"max_output_tokens": True},
    ],
)
def test_prompt_spec_rejects_blank_fields_and_invalid_controls(change):
    values = {
        "operation": "test_operation",
        "revision": "v1",
        "instructions": "test instructions",
        "temperature": 0,
        "max_output_tokens": 100,
    }
    values.update(change)

    with pytest.raises(ValueError):
        PromptSpec(**values)


def test_prompt_registry_builder_rejects_duplicate_operations():
    with pytest.raises(ValueError, match="Duplicate prompt operation: same"):
        build_prompt_registry(
            (
                PromptSpec("same", "v1", "first"),
                PromptSpec("same", "v2", "second"),
            )
        )


def test_shopping_prompt_versions_speculative_vs_imperative_contract():
    instructions = SHOPPING_TEXT_PROMPT.instructions

    assert SHOPPING_TEXT_PROMPT.revision == "v3"
    assert "imperative command" in instructions
    assert "speculative or uncertain" in instructions
    assert "Return unknown" in instructions


def test_prompt_fingerprints_are_deterministic_sha256_hex():
    for spec in PROMPT_SPECS.values():
        reconstructed = PromptSpec(**spec.canonical_payload())

        assert reconstructed.fingerprint == spec.fingerprint
        assert len(spec.fingerprint) == 64
        assert all(character in "0123456789abcdef" for character in spec.fingerprint)


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "different_operation"},
        {"revision": "v2"},
        {"instructions": SHOPPING_TEXT_PROMPT.instructions + " Clarify ambiguity."},
        {"temperature": 0.25},
        {"max_output_tokens": SHOPPING_TEXT_PROMPT.max_output_tokens + 1},
    ],
)
def test_prompt_fingerprint_changes_with_semantic_or_control_fields(change):
    payload = SHOPPING_TEXT_PROMPT.canonical_payload()
    payload.update(change)

    changed = PromptSpec(**payload)

    assert changed.fingerprint != SHOPPING_TEXT_PROMPT.fingerprint


def test_extra_field_regression_covers_every_concrete_ai_response_model():
    assert set(AIResponseModel.__subclasses__()) == {
        CategorizedItemResponse,
        CategoryParseResponse,
        NormalizedItemResponse,
        ItemNormalizationResponse,
        ShoppingTextParseResponse,
        RecipeIngredientResponse,
        RecipeExtractResponse,
        RecipeCommandParseResponse,
    }


@pytest.mark.parametrize(
    ("response_model", "payload"),
    [
        (AIResponseModel, {}),
        (CategorizedItemResponse, {"id": 1, "category": "Молочка"}),
        (CategoryParseResponse, {"items": []}),
        (
            NormalizedItemResponse,
            {
                "name": "молоко",
                "canonical_name": "молоко",
                "canonical_key": "milk",
            },
        ),
        (ItemNormalizationResponse, {"items": []}),
        (
            ShoppingTextParseResponse,
            {
                "action": "show_list",
                "items": [],
                "needs_confirmation": False,
                "clarification_question": None,
            },
        ),
        (RecipeIngredientResponse, {"name": "картофель", "quantity": None}),
        (
            RecipeExtractResponse,
            {
                "name": "солянка",
                "ingredients": [{"name": "огурцы", "quantity": None}],
            },
        ),
        (
            RecipeCommandParseResponse,
            {
                "action": "unknown",
                "recipe_name": None,
                "url": None,
                "recipe_text": None,
            },
        ),
    ],
)
def test_every_ai_response_model_rejects_undeclared_fields(response_model, payload):
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        response_model.model_validate({**payload, "undeclared": True})


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


@pytest.mark.parametrize("action", ["unknown", "show_list"])
def test_non_mutating_shopping_actions_reject_guessed_items(action):
    with pytest.raises(ValueError, match="must not include items"):
        ShoppingTextParseResponse.model_validate(
            {
                "action": action,
                "items": ["milk"],
                "needs_confirmation": False,
                "clarification_question": None,
            }
        )


@pytest.mark.parametrize(
    "canonical_key",
    ["Milk", "milk-key", "milk key", "молоко", "milk!"],
)
def test_normalized_item_rejects_non_slug_canonical_key(canonical_key):
    with pytest.raises(ValueError, match=r"canonical_key must match \[a-z0-9_\]\+"):
        NormalizedItemResponse.model_validate(
            {
                "name": "milk",
                "canonical_name": "молоко",
                "canonical_key": canonical_key,
            }
        )


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
          "items": [" молоко ", "свежий   хлеб"],
          "needs_confirmation": false,
          "clarification_question": "  "
        }
        """,
    )
    text = "Пожалуйста, купи молоко и свежий хлеб"

    parsed = await parser.parse(text)

    assert parsed == {
        "action": "add_items",
        "items": ["молоко", "свежий хлеб"],
        "needs_confirmation": False,
        "clarification_question": None,
    }
    instructions = assert_response_request(
        parser,
        expected_input=text,
        prompt_spec=SHOPPING_TEXT_PROMPT,
    )
    assert "shopping-list command" in instructions
    assert "Preserve item names in the user's language" in instructions
    assert "__last_added__" in instructions


@pytest.mark.asyncio
async def test_shopping_item_normalizer_returns_item_identities():
    normalizer = wrapper_with_response(
        ShoppingItemNormalizer,
        """
        {
          "items": [
            {
              "name": " томатная паста, 60 г ",
              "canonical_name": " томатная   паста ",
              "canonical_key": " tomato_paste "
            }
          ]
        }
        """,
    )
    names = ["томатная паста, 60 г", "томатная паста, 60 г", "  "]

    identities = await normalizer.normalize(names)

    assert list(identities) == ["томатная паста, 60 г"]
    identity = identities["томатная паста, 60 г"]
    assert identity.raw_name == "томатная паста, 60 г"
    assert identity.canonical_name == "томатная паста"
    assert identity.canonical_key == "tomato_paste"
    expected_input = json.dumps(
        {"items": ["томатная паста, 60 г"]},
        ensure_ascii=False,
    )
    instructions = assert_response_request(
        normalizer,
        expected_input=expected_input,
        prompt_spec=ITEM_NORMALIZATION_PROMPT,
    )
    assert "deduplication across languages" in instructions
    assert "Ignore quantities" in instructions
    assert "same canonical_key" in instructions


@pytest.mark.asyncio
async def test_shopping_item_categorizer_returns_categories_by_item_id():
    categorizer = wrapper_with_response(
        ShoppingItemCategorizer,
        '{"items":[{"id":1,"category":" Молочка "},{"id":2,"category":"Овощи"}]}',
    )

    items = [{"id": 1, "name": "молоко"}, {"id": 2, "name": "помидоры"}]

    categories = await categorizer.categorize(items)

    assert categories == {1: "Молочка", 2: "Овощи"}
    instructions = assert_response_request(
        categorizer,
        expected_input=json.dumps({"items": items}, ensure_ascii=False),
        prompt_spec=CATEGORY_PROMPT,
    )
    assert "grocery store sections" in instructions
    assert "Use Russian category names" in instructions


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
    page_text = "ё" * 20_001

    recipe = await extractor.extract(
        requested_name="солянка",
        source_url="https://example.com/recipe",
        page_text=page_text,
    )

    assert recipe == {
        "name": "солянка",
        "ingredients": [
            {"name": "carrot", "quantity": "120 g"},
            {"name": "onion", "quantity": None},
        ],
    }
    expected_input = json.dumps(
        {
            "requested_name": "солянка",
            "source_url": "https://example.com/recipe",
            "page_text": page_text[:20_000],
        },
        ensure_ascii=False,
    )
    instructions = assert_response_request(
        extractor,
        expected_input=expected_input,
        prompt_spec=RECIPE_EXTRACT_PROMPT,
    )
    request_payload = json.loads(extractor.client.responses.calls[0]["input"])
    assert len(request_payload["page_text"]) == 20_000
    assert "grocery items only" in instructions
    assert "Preserve useful quantities" in instructions


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
    text = "запомни солянку https://example.com/recipe"

    parsed = await parser.parse(text)

    assert parsed == {
        "action": "learn_recipe",
        "recipe_name": "солянка",
        "url": "https://example.com/recipe",
        "recipe_text": None,
    }
    instructions = assert_response_request(
        parser,
        expected_input=text,
        prompt_spec=RECIPE_COMMAND_PROMPT,
    )
    assert "teach or reuse saved recipes" in instructions
    assert "Return unknown for ordinary single-product shopping commands" in instructions
    assert "купи на солянку" in instructions


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
