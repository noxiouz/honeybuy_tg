import json
from pathlib import Path
from time import perf_counter

from openai import AsyncOpenAI

from honeybuy_tg.metrics import record_ai_request


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


def response_text(result: object) -> str:
    text = getattr(result, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return str(result).strip()
