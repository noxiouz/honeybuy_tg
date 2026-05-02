import json
from pathlib import Path
from time import perf_counter

from openai import AsyncOpenAI

from honeybuy_tg.metrics import record_ai_request
from honeybuy_tg.models import ItemIdentity


class VoiceTranscriber:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def transcribe(self, audio_path: Path) -> str:
        started_at = perf_counter()
        try:
            with audio_path.open("rb") as audio_file:
                result = await self.client.audio.transcriptions.create(
                    file=audio_file,
                    model=self.model,
                    language="ru",
                    prompt="Shopping list commands in Russian and English.",
                )
        except Exception:
            record_ai_request(
                operation="voice_transcription",
                status="error",
                duration_seconds=perf_counter() - started_at,
            )
            raise
        record_ai_request(
            operation="voice_transcription",
            status="ok",
            duration_seconds=perf_counter() - started_at,
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

        started_at = perf_counter()
        try:
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
        except Exception:
            record_ai_request(
                operation="category_parse",
                status="error",
                duration_seconds=perf_counter() - started_at,
            )
            raise
        record_ai_request(
            operation="category_parse",
            status="ok",
            duration_seconds=perf_counter() - started_at,
        )
        payload = json.loads(response_text(result))
        categorized_items = payload.get("items", [])
        categories: dict[int, str] = {}
        for item in categorized_items:
            item_id = item.get("id")
            category = item.get("category")
            if isinstance(item_id, int) and isinstance(category, str) and category:
                categories[item_id] = category.strip()
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

        started_at = perf_counter()
        try:
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
        except Exception:
            record_ai_request(
                operation="item_normalize",
                status="error",
                duration_seconds=perf_counter() - started_at,
            )
            raise
        record_ai_request(
            operation="item_normalize",
            status="ok",
            duration_seconds=perf_counter() - started_at,
        )

        payload = json.loads(response_text(result))
        normalized_items = payload.get("items", [])
        identities: dict[str, ItemIdentity] = {}
        if not isinstance(normalized_items, list):
            return identities
        for item in normalized_items:
            if not isinstance(item, dict):
                continue
            raw_name = item.get("name")
            canonical_name = item.get("canonical_name")
            canonical_key = item.get("canonical_key")
            if (
                isinstance(raw_name, str)
                and raw_name.strip()
                and isinstance(canonical_name, str)
                and canonical_name.strip()
                and isinstance(canonical_key, str)
                and canonical_key.strip()
            ):
                identities[raw_name] = ItemIdentity(
                    raw_name=raw_name.strip(),
                    canonical_name=canonical_name.strip(),
                    canonical_key=canonical_key.strip(),
                )
        return identities


class ShoppingTextParser:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def parse(self, text: str) -> dict[str, object]:
        started_at = perf_counter()
        try:
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
        except Exception:
            record_ai_request(
                operation="text_parse",
                status="error",
                duration_seconds=perf_counter() - started_at,
            )
            raise
        record_ai_request(
            operation="text_parse",
            status="ok",
            duration_seconds=perf_counter() - started_at,
        )
        payload = json.loads(response_text(result))
        if not isinstance(payload, dict):
            raise ValueError("AI parser returned non-object JSON")
        return payload


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
        started_at = perf_counter()
        try:
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
        except Exception:
            record_ai_request(
                operation="recipe_extract",
                status="error",
                duration_seconds=perf_counter() - started_at,
            )
            raise
        record_ai_request(
            operation="recipe_extract",
            status="ok",
            duration_seconds=perf_counter() - started_at,
        )
        payload = json.loads(response_text(result))
        if not isinstance(payload, dict):
            raise ValueError("AI recipe extractor returned non-object JSON")
        return payload


class RecipeCommandParser:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def parse(self, text: str) -> dict[str, object]:
        started_at = perf_counter()
        try:
            result = await self.client.responses.create(
                model=self.model,
                instructions=(
                    "Parse commands that teach or reuse saved recipes. Return only "
                    "valid JSON with shape: "
                    '{"action":"learn_recipe|add_recipe|unknown",'
                    '"recipe_name":"солянка","url":"https://example.com"}. '
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
        except Exception:
            record_ai_request(
                operation="recipe_command_parse",
                status="error",
                duration_seconds=perf_counter() - started_at,
            )
            raise
        record_ai_request(
            operation="recipe_command_parse",
            status="ok",
            duration_seconds=perf_counter() - started_at,
        )
        payload = json.loads(response_text(result))
        if not isinstance(payload, dict):
            raise ValueError("AI recipe command parser returned non-object JSON")
        return payload


def response_text(result: object) -> str:
    text = getattr(result, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return str(result).strip()
