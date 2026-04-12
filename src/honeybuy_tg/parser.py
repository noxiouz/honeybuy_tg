from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


class ParsedAction(StrEnum):
    ADD_ITEMS = "add_items"
    REMOVE_ITEMS = "remove_items"
    MARK_BOUGHT = "mark_bought"
    SHOW_LIST = "show_list"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParsedCommand:
    action: ParsedAction
    items: tuple[str, ...] = ()
    needs_confirmation: bool = False
    clarification_question: str | None = None


ADD_PREFIXES = (
    "надо купить",
    "нужно купить",
    "купи",
    "купить",
    "добавь",
    "добавить",
    "add",
    "buy",
)

REMOVE_PREFIXES = (
    "удали",
    "удалить",
    "убери",
    "убрать",
    "remove",
    "delete",
)

BOUGHT_PREFIXES = (
    "я купил",
    "я купила",
    "я купили",
    "мы купили",
    "купил",
    "купила",
    "купили",
    "куплено",
    "bought",
)

SHOW_LIST_PHRASES = {
    "список",
    "покажи список",
    "что купить",
    "что надо купить",
    "list",
    "show list",
}

FILLER_ITEMS = {
    "пожалуйста",
    "плиз",
    "please",
    "что еще",
    "что еще?",
}


def parse_shopping_text(
    text: str, *, default_action: ParsedAction | None = None
) -> ParsedCommand:
    normalized = normalize_text(text)
    normalized = strip_leading_filler(normalized)
    if not normalized:
        return unknown_command()

    if normalized in SHOW_LIST_PHRASES:
        return ParsedCommand(action=ParsedAction.SHOW_LIST)

    add_items = strip_prefix(normalized, ADD_PREFIXES)
    if add_items is not None:
        return items_command(ParsedAction.ADD_ITEMS, add_items)

    remove_items = strip_prefix(normalized, REMOVE_PREFIXES)
    if remove_items is not None:
        return items_command(ParsedAction.REMOVE_ITEMS, remove_items)

    bought_items = strip_prefix(normalized, BOUGHT_PREFIXES)
    if bought_items is not None:
        return items_command(ParsedAction.MARK_BOUGHT, bought_items)

    bought_suffix = strip_suffix(
        normalized,
        (
            "куплено",
            "куплены",
            "куплен",
            "куплена",
            "купил",
            "купила",
            "купили",
            "bought",
        ),
    )
    if bought_suffix is not None:
        return items_command(ParsedAction.MARK_BOUGHT, bought_suffix)

    if default_action is not None:
        return items_command(default_action, normalized)

    return unknown_command()


def normalize_text(text: str) -> str:
    text = text.casefold().strip()
    text = text.replace("ё", "е")
    text = re.sub(r"[.!?]+$", "", text)
    return " ".join(text.split())


def strip_leading_filler(text: str) -> str:
    return re.sub(r"^(пожалуйста|плиз|please)[,\s]+", "", text).strip()


def parsed_command_from_ai(payload: dict[str, Any]) -> ParsedCommand:
    try:
        action = ParsedAction(str(payload.get("action", "unknown")))
    except ValueError:
        action = ParsedAction.UNKNOWN

    raw_items = payload.get("items", [])
    items = tuple(
        item.strip() for item in raw_items if isinstance(item, str) and item.strip()
    )
    clarification = payload.get("clarification_question")
    return ParsedCommand(
        action=action,
        items=items,
        needs_confirmation=bool(payload.get("needs_confirmation", False)),
        clarification_question=clarification
        if isinstance(clarification, str)
        else None,
    )


def strip_prefix(text: str, prefixes: tuple[str, ...]) -> str | None:
    for prefix in sorted(prefixes, key=len, reverse=True):
        if text == prefix:
            return ""
        if text.startswith(prefix + " "):
            return text[len(prefix) :].strip(" ,;:")
    return None


def strip_suffix(text: str, suffixes: tuple[str, ...]) -> str | None:
    for suffix in sorted(suffixes, key=len, reverse=True):
        if text.endswith(" " + suffix):
            return text[: -len(suffix)].strip(" ,;:")
    return None


def items_command(action: ParsedAction, raw_items: str) -> ParsedCommand:
    items = split_items(raw_items)
    if not items:
        return ParsedCommand(
            action=ParsedAction.UNKNOWN,
            needs_confirmation=True,
            clarification_question="What item?",
        )
    return ParsedCommand(action=action, items=tuple(items))


def split_items(raw_items: str) -> list[str]:
    raw_items = raw_items.strip(" ,;:")
    if not raw_items:
        return []

    raw_items = re.sub(r"\s+(и|and)\s+", ",", raw_items)
    raw_items = re.sub(r"^(пожалуйста|плиз|please)[,\s]+", "", raw_items)
    return [
        item
        for item in (" ".join(part.strip().split()) for part in raw_items.split(","))
        if item and item not in FILLER_ITEMS
    ]


def unknown_command() -> ParsedCommand:
    return ParsedCommand(
        action=ParsedAction.UNKNOWN,
        needs_confirmation=True,
        clarification_question="I could not understand the shopping command.",
    )
