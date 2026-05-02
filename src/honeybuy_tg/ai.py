import json
from pathlib import Path
from typing import Literal, TypeVar

from openai import AsyncOpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from honeybuy_tg.metrics import AIRequestReport, record_ai_request_async
from honeybuy_tg.models import ItemIdentity


ResponseModelT = TypeVar("ResponseModelT", bound="AIResponseModel")


def clean_required_text(value: str) -> str:
    value = " ".join(value.strip().split())
    if not value:
        raise ValueError("Field must not be empty")
    return value


class AIResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class CategorizedItemResponse(AIResponseModel):
    id: int
    category: str = Field(min_length=1)

    @field_validator("category")
    @classmethod
    def clean_category(cls, value: str) -> str:
        return clean_required_text(value)


class CategoryParseResponse(AIResponseModel):
    items: list[CategorizedItemResponse]


class NormalizedItemResponse(AIResponseModel):
    name: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    canonical_key: str = Field(min_length=1)

    @field_validator("name", "canonical_name", "canonical_key")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return clean_required_text(value)


class ItemNormalizationResponse(AIResponseModel):
    items: list[NormalizedItemResponse]


class ShoppingTextParseResponse(AIResponseModel):
    action: Literal[
        "add_items",
        "remove_items",
        "mark_bought",
        "show_list",
        "unknown",
    ]
    items: list[str]
    needs_confirmation: bool
    clarification_question: str | None

    @field_validator("items")
    @classmethod
    def clean_items(cls, value: list[str]) -> list[str]:
        return [" ".join(item.strip().split()) for item in value if item.strip()]

    @model_validator(mode="after")
    def require_items_for_mutating_actions(self) -> "ShoppingTextParseResponse":
        if self.action in {"add_items", "remove_items", "mark_bought"} and not self.items:
            raise ValueError("Mutating shopping action must include at least one item")
        return self

    @field_validator("clarification_question")
    @classmethod
    def clean_clarification(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.strip().split())
        return value or None


class RecipeIngredientResponse(AIResponseModel):
    name: str = Field(min_length=1)
    quantity: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return clean_required_text(value)

    @field_validator("quantity")
    @classmethod
    def clean_quantity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.strip().split())
        return value or None


class RecipeExtractResponse(AIResponseModel):
    name: str = Field(min_length=1)
    ingredients: list[RecipeIngredientResponse] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return clean_required_text(value)


class RecipeCommandParseResponse(AIResponseModel):
    action: Literal["learn_recipe", "add_recipe", "unknown"]
    recipe_name: str | None
    url: str | None

    @field_validator("recipe_name", "url")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.strip().split())
        return value or None

    @model_validator(mode="after")
    def require_action_fields(self) -> "RecipeCommandParseResponse":
        if self.action in {"learn_recipe", "add_recipe"} and self.recipe_name is None:
            raise ValueError("Recipe command action must include recipe_name")
        if self.action == "learn_recipe" and self.url is None:
            raise ValueError("learn_recipe action must include url")
        return self


class VoiceTranscriber:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def transcribe(self, audio_path: Path) -> str:
        async with record_ai_request_async(operation="voice_transcription"):
            with audio_path.open("rb") as audio_file:
                result = await self.client.audio.transcriptions.create(
                    file=audio_file,
                    model=self.model,
                    language="ru",
                    prompt="Shopping list commands in Russian and English.",
                )

        text = getattr(result, "text", None)
        if not text:
            return str(result).strip()
        return text.strip()


class ShoppingItemCategorizer:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def categorize(self, items: list[dict[str, str | int]]) -> dict[int, str]:
        if not items:
            return {}

        async with record_ai_request_async(operation="category_parse") as report:
            result = await self.client.responses.create(
                model=self.model,
                instructions=(
                    "Categorize shopping-list items into concise grocery store "
                    "sections. Choose categories yourself from the item names. "
                    "Use Russian category names. Return only valid JSON with this "
                    'shape: {"items":[{"id":1,"category":"Молочка"}]}.'
                ),
                input=json.dumps({"items": items}, ensure_ascii=False),
                temperature=0,
                max_output_tokens=600,
            )
            parsed = parse_ai_json_response(
                result,
                CategoryParseResponse,
                report=report,
            )
        categories: dict[int, str] = {}
        for item in parsed.items:
            categories[item.id] = item.category
        return categories


class ShoppingItemNormalizer:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def normalize(self, names: list[str]) -> dict[str, ItemIdentity]:
        if not names:
            return {}

        unique_names = list(dict.fromkeys(name for name in names if name.strip()))
        if not unique_names:
            return {}

        async with record_ai_request_async(operation="item_normalize") as report:
            result = await self.client.responses.create(
                model=self.model,
                instructions=(
                    "Normalize grocery shopping-list items for deduplication across "
                    "languages. Ignore quantities, units, packaging size, politeness, "
                    "and filler. Return only valid JSON with this shape: "
                    '{"items":[{"name":"tomato paste, 60 g",'
                    '"canonical_name":"томатная паста",'
                    '"canonical_key":"tomato_paste"}]}. '
                    "canonical_name should be a short Russian grocery name when "
                    "possible. canonical_key must be a stable lowercase English slug "
                    "using a-z, 0-9, and underscores only. Different languages for the "
                    "same product must have the same canonical_key."
                ),
                input=json.dumps({"items": unique_names}, ensure_ascii=False),
                temperature=0,
                max_output_tokens=900,
            )
            parsed = parse_ai_json_response(
                result,
                ItemNormalizationResponse,
                report=report,
            )

        identities: dict[str, ItemIdentity] = {}
        for item in parsed.items:
            identities[item.name] = ItemIdentity(
                raw_name=item.name,
                canonical_name=item.canonical_name,
                canonical_key=item.canonical_key,
            )
        return identities


class ShoppingTextParser:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def parse(self, text: str) -> dict[str, object]:
        async with record_ai_request_async(operation="text_parse") as report:
            result = await self.client.responses.create(
                model=self.model,
                instructions=(
                    "Parse a grocery shopping-list command. Ignore politeness, filler, "
                    "uncertainty, and meta phrases such as 'пожалуйста' or 'что еще'. "
                    "For undo/cancel-last-add commands such as 'отмени', 'не надо', "
                    "or 'удали то что добавил', return action 'remove_items' with "
                    'items ["__last_added__"]. For reply-context commands such as '
                    "'удали это' or 'это куплено', keep the referenced item as 'это' "
                    "or 'this' instead of inventing a product name. "
                    "Return only valid JSON with shape: "
                    '{"action":"add_items|remove_items|mark_bought|show_list|unknown",'
                    '"items":["milk"],"needs_confirmation":false,'
                    '"clarification_question":null}. Preserve item names in the user\'s '
                    "language and include only real shopping items."
                ),
                input=text,
                temperature=0,
                max_output_tokens=700,
            )
            parsed = parse_ai_json_response(
                result,
                ShoppingTextParseResponse,
                report=report,
            )
        return parsed.model_dump()


class RecipeExtractor:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def extract(
        self,
        *,
        requested_name: str,
        source_url: str,
        page_text: str,
    ) -> dict[str, object]:
        async with record_ai_request_async(operation="recipe_extract") as report:
            result = await self.client.responses.create(
                model=self.model,
                instructions=(
                    "Extract a recipe from visible recipe-page text. Return only "
                    "valid JSON with shape: "
                    '{"name":"солянка","ingredients":[{"name":"carrot",'
                    '"quantity":"120 g"}]}. Use the requested name if it is a '
                    "reasonable alias. Ingredients must be grocery items only; omit "
                    "nutrition, ratings, equipment, navigation, and recommendations. "
                    "Preserve useful quantities as short text."
                ),
                input=json.dumps(
                    {
                        "requested_name": requested_name,
                        "source_url": source_url,
                        "page_text": page_text[:20_000],
                    },
                    ensure_ascii=False,
                ),
                temperature=0,
                max_output_tokens=2500,
            )
            parsed = parse_ai_json_response(
                result,
                RecipeExtractResponse,
                report=report,
            )
        return parsed.model_dump()


class RecipeCommandParser:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def parse(self, text: str) -> dict[str, object]:
        async with record_ai_request_async(operation="recipe_command_parse") as report:
            result = await self.client.responses.create(
                model=self.model,
                instructions=(
                    "Parse commands that teach or reuse saved recipes. Return only "
                    "valid JSON with shape: "
                    '{"action":"learn_recipe|add_recipe|unknown",'
                    '"recipe_name":"солянка","url":"https://example.com"}. '
                    "Use null for missing recipe_name or url. "
                    "Use action 'learn_recipe' when the user asks to remember, learn, "
                    "save, or teach a recipe and provides a recipe URL. Use action "
                    "'add_recipe' when the user asks to add/buy ingredients/products "
                    "for a saved recipe, even if phrased loosely, for example "
                    "'добавь для солянки', 'купи на солянку', 'ингредиенты для "
                    "солянки', or 'все для солянки'. Return unknown for ordinary "
                    "single-product shopping commands."
                ),
                input=text,
                temperature=0,
                max_output_tokens=500,
            )
            parsed = parse_ai_json_response(
                result,
                RecipeCommandParseResponse,
                report=report,
            )
        return parsed.model_dump()


def response_text(result: object) -> str:
    text = getattr(result, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return str(result).strip()


def parse_ai_json_response(
    result: object,
    response_model: type[ResponseModelT],
    *,
    report: AIRequestReport | None = None,
) -> ResponseModelT:
    try:
        return response_model.model_validate_json(response_text(result))
    except ValidationError as error:
        if report is not None:
            report.report_status("invalid_response")
        raise ValueError(
            f"AI response did not match {response_model.__name__}"
        ) from error
