import json
from pathlib import Path

from openai import AsyncOpenAI


class VoiceTranscriber:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def transcribe(self, audio_path: Path) -> str:
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
        result = await self.client.responses.create(
            model=self.model,
            instructions=(
                "Parse a grocery shopping-list command. Ignore politeness, filler, "
                "uncertainty, and meta phrases such as 'пожалуйста' or 'что еще'. "
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
        payload = json.loads(response_text(result))
        if not isinstance(payload, dict):
            raise ValueError("AI parser returned non-object JSON")
        return payload


def response_text(result: object) -> str:
    text = getattr(result, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return str(result).strip()
