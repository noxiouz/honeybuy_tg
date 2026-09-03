from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import json
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class PromptSpec:
    operation: str
    revision: str
    instructions: str
    temperature: float | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ValueError("Prompt operation must not be blank")
        if not self.revision.strip():
            raise ValueError("Prompt revision must not be blank")
        if not self.instructions.strip():
            raise ValueError("Prompt instructions must not be blank")
        if (
            self.temperature is not None
            and (
                isinstance(self.temperature, bool)
                or not isinstance(self.temperature, int | float)
                or not math.isfinite(self.temperature)
                or self.temperature < 0
                or self.temperature > 2
            )
        ):
            raise ValueError("Prompt temperature must be a finite number from 0 to 2")
        if (
            self.max_output_tokens is not None
            and (
                isinstance(self.max_output_tokens, bool)
                or not isinstance(self.max_output_tokens, int)
                or self.max_output_tokens < 1
            )
        ):
            raise ValueError("Prompt max_output_tokens must be positive")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "revision": self.revision,
            "instructions": self.instructions,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def with_instructions(self, instructions: str, *, revision: str) -> "PromptSpec":
        return PromptSpec(
            operation=self.operation,
            revision=revision,
            instructions=instructions,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
        )


VOICE_TRANSCRIPTION_PROMPT = PromptSpec(
    operation="voice_transcription",
    revision="v1",
    instructions="Shopping list commands in Russian and English.",
)

CATEGORY_PROMPT = PromptSpec(
    operation="category_parse",
    revision="v1",
    instructions=(
        "Categorize shopping-list items into concise grocery store "
        "sections. Choose categories yourself from the item names. "
        "Use Russian category names. Return only valid JSON with this "
        'shape: {"items":[{"id":1,"category":"Молочка"}]}.'
    ),
    temperature=0,
    max_output_tokens=600,
)

ITEM_NORMALIZATION_PROMPT = PromptSpec(
    operation="item_normalize",
    revision="v1",
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
    temperature=0,
    max_output_tokens=900,
)

SHOPPING_TEXT_PROMPT = PromptSpec(
    operation="text_parse",
    revision="v3",
    instructions=(
        "Parse a grocery shopping-list command. Ignore politeness, filler, "
        "and meta phrases such as 'пожалуйста' or 'что еще' when the user gives "
        "an imperative command. Return unknown for speculative or uncertain "
        "phrases such as 'может быть купить молоко'. "
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
    temperature=0,
    max_output_tokens=700,
)

RECIPE_EXTRACT_PROMPT = PromptSpec(
    operation="recipe_extract",
    revision="v1",
    instructions=(
        "Extract a recipe from visible recipe-page text or pasted recipe "
        "text. Return only valid JSON with shape: "
        '{"name":"солянка","ingredients":[{"name":"carrot",'
        '"quantity":"120 g"}]}. Use the requested name if it is a '
        "reasonable alias. Ingredients must be grocery items only; omit "
        "nutrition, ratings, equipment, navigation, and recommendations. "
        "Preserve useful quantities as short text."
    ),
    temperature=0,
    max_output_tokens=2500,
)

RECIPE_COMMAND_PROMPT = PromptSpec(
    operation="recipe_command_parse",
    revision="v1",
    instructions=(
        "Parse commands that teach or reuse saved recipes. Return only "
        "valid JSON with shape: "
        '{"action":"learn_recipe|add_recipe|unknown",'
        '"recipe_name":"солянка","url":"https://example.com",'
        '"recipe_text":null}. '
        "Use null for missing recipe_name, url, or recipe_text. "
        "Use action 'learn_recipe' when the user asks to remember, learn, "
        "save, or teach a recipe and provides either a recipe URL or "
        "pasted recipe text. Do not copy pasted recipe bodies into "
        "recipe_text; set recipe_text to null unless the recipe content "
        "is very short. Use action 'add_recipe' when the user asks to "
        "add/buy ingredients/products "
        "for a saved recipe, even if phrased loosely, for example "
        "'добавь для солянки', 'купи на солянку', 'ингредиенты для "
        "солянки', or 'все для солянки'. Return unknown for ordinary "
        "single-product shopping commands."
    ),
    temperature=0,
    max_output_tokens=500,
)

def build_prompt_registry(specs: tuple[PromptSpec, ...]) -> Mapping[str, PromptSpec]:
    registry: dict[str, PromptSpec] = {}
    for spec in specs:
        if spec.operation in registry:
            raise ValueError(f"Duplicate prompt operation: {spec.operation}")
        registry[spec.operation] = spec
    return MappingProxyType(registry)


PROMPT_SPECS: Mapping[str, PromptSpec] = build_prompt_registry(
    (
        VOICE_TRANSCRIPTION_PROMPT,
        CATEGORY_PROMPT,
        ITEM_NORMALIZATION_PROMPT,
        SHOPPING_TEXT_PROMPT,
        RECIPE_EXTRACT_PROMPT,
        RECIPE_COMMAND_PROMPT,
    )
)
