import json
from pathlib import Path
import re
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
from honeybuy_tg.prompts import (
    CATEGORY_PROMPT,
    ITEM_NORMALIZATION_PROMPT,
    RECIPE_COMMAND_PROMPT,
    RECIPE_EXTRACT_PROMPT,
    SHOPPING_TEXT_PROMPT,
    VOICE_TRANSCRIPTION_PROMPT,
)


ResponseModelT = TypeVar("ResponseModelT", bound="AIResponseModel")


def clean_required_text(value: str) -> str:
    value = " ".join(value.strip().split())
    if not value:
        raise ValueError("Field must not be empty")
    return value


class AIResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


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

    @field_validator("canonical_key")
    @classmethod
    def validate_canonical_key(cls, value: str) -> str:
        if re.fullmatch(r"[a-z0-9_]+", value) is None:
            raise ValueError("canonical_key must match [a-z0-9_]+")
        return value


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
        if self.action in {"unknown", "show_list"} and self.items:
            raise ValueError("Unknown and show_list actions must not include items")
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
    recipe_text: str | None = None

    @field_validator("recipe_name", "url", "recipe_text")
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
                    prompt=VOICE_TRANSCRIPTION_PROMPT.instructions,
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
                instructions=CATEGORY_PROMPT.instructions,
                input=json.dumps({"items": items}, ensure_ascii=False),
                temperature=CATEGORY_PROMPT.temperature,
                max_output_tokens=CATEGORY_PROMPT.max_output_tokens,
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
                instructions=ITEM_NORMALIZATION_PROMPT.instructions,
                input=json.dumps({"items": unique_names}, ensure_ascii=False),
                temperature=ITEM_NORMALIZATION_PROMPT.temperature,
                max_output_tokens=ITEM_NORMALIZATION_PROMPT.max_output_tokens,
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
                instructions=SHOPPING_TEXT_PROMPT.instructions,
                input=text,
                temperature=SHOPPING_TEXT_PROMPT.temperature,
                max_output_tokens=SHOPPING_TEXT_PROMPT.max_output_tokens,
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
        source_url: str | None,
        page_text: str,
    ) -> dict[str, object]:
        async with record_ai_request_async(operation="recipe_extract") as report:
            result = await self.client.responses.create(
                model=self.model,
                instructions=RECIPE_EXTRACT_PROMPT.instructions,
                input=json.dumps(
                    {
                        "requested_name": requested_name,
                        "source_url": source_url,
                        "page_text": page_text[:20_000],
                    },
                    ensure_ascii=False,
                ),
                temperature=RECIPE_EXTRACT_PROMPT.temperature,
                max_output_tokens=RECIPE_EXTRACT_PROMPT.max_output_tokens,
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
                instructions=RECIPE_COMMAND_PROMPT.instructions,
                input=text,
                temperature=RECIPE_COMMAND_PROMPT.temperature,
                max_output_tokens=RECIPE_COMMAND_PROMPT.max_output_tokens,
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
