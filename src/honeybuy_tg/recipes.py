import asyncio
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


@dataclass(frozen=True)
class LearnRecipeRequest:
    name: str
    url: str | None = None
    recipe_text: str | None = None


@dataclass(frozen=True)
class AddRecipeRequest:
    name: str


@dataclass(frozen=True)
class RecipeAliasRequest:
    recipe_name: str
    alias: str


@dataclass(frozen=True)
class RecipeCommand:
    action: Literal["learn_recipe", "add_recipe", "unknown"]
    name: str | None = None
    url: str | None = None
    recipe_text: str | None = None


def recipe_command_from_ai(payload: dict[str, Any]) -> RecipeCommand:
    action = payload.get("action")
    if action not in {"learn_recipe", "add_recipe"}:
        return RecipeCommand(action="unknown")

    recipe_name = payload.get("recipe_name")
    if not isinstance(recipe_name, str) or not recipe_name.strip():
        return RecipeCommand(action="unknown")

    url = clean_optional_recipe_text(payload.get("url"))
    recipe_text = clean_optional_recipe_text(payload.get("recipe_text"))

    return RecipeCommand(
        action=action,
        name=recipe_name.strip(),
        url=url,
        recipe_text=recipe_text,
    )


def parse_learn_recipe_request(text: str) -> LearnRecipeRequest | None:
    pasted_request = parse_pasted_recipe_request(text)
    if pasted_request is not None:
        return pasted_request

    match = URL_RE.search(text)
    if match is None:
        return None

    prefix = text[: match.start()]
    url = match.group(0).rstrip(".,)")
    name = learn_recipe_name_from_command_text(prefix)
    if name is None:
        return None
    return LearnRecipeRequest(name=name, url=url)


def parse_pasted_recipe_request(text: str) -> LearnRecipeRequest | None:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None

    name = learn_recipe_name_from_command_text(lines[0])
    if name is None:
        return None

    recipe_text = text.strip()
    if not looks_like_pasted_recipe_text(recipe_text):
        return None

    if not name and len(lines) > 1 and not is_ingredient_heading(lines[1]):
        candidate_name = lines[1].strip(" -:,.")
        if 1 <= len(candidate_name) <= 80:
            name = candidate_name
    if not name:
        return None

    return LearnRecipeRequest(name=name, recipe_text=recipe_text)


def learn_recipe_name_from_command_text(text: str) -> str | None:
    text = URL_RE.sub("", text)
    normalized = normalize_recipe_command_text(text)
    if not normalized.startswith(
        ("выучи", "запомни", "learn", "remember", "save", "teach")
    ):
        return None

    name = normalized
    for leading in (
        "выучи рецепт",
        "выучи",
        "запомни рецепт",
        "запомни",
        "learn recipe",
        "learn",
        "remember recipe",
        "remember",
        "save recipe",
        "save",
        "teach recipe",
        "teach",
    ):
        if name.startswith(leading):
            name = name[len(leading) :].strip(" -:,.")
            break
    name = name.replace("вот ссылка", "").replace("вот рецепт", "").strip(" -:,.")
    return name


def looks_like_pasted_recipe_text(text: str) -> bool:
    if len(text) < 50 or "\n" not in text:
        return False
    normalized = normalize_recipe_command_text(text)
    if any(
        marker in normalized
        for marker in (
            "ингредиент",
            "ingredients",
            "состав",
            "приготовление",
            "instructions",
            "method",
        )
    ):
        return True
    return (
        sum(1 for line in text.splitlines() if line.strip().startswith(("-", "•"))) >= 2
    )


def is_ingredient_heading(text: str) -> bool:
    normalized = normalize_recipe_command_text(text).strip(" -:,.")
    return normalized in {"ингредиенты", "ingredients", "состав"}


def clean_optional_recipe_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.strip().split())
    return value or None


def parse_add_recipe_request(text: str) -> AddRecipeRequest | None:
    normalized = normalize_recipe_command_text(text)
    prefixes = (
        "добавь все для",
        "добавить все для",
        "купи все для",
        "купить все для",
        "добавь ингредиенты для",
        "добавить ингредиенты для",
        "добавь продукты для",
        "добавить продукты для",
        "добавь рецепт",
        "приготовь",
    )
    for prefix in prefixes:
        if normalized.startswith(prefix + " "):
            name = normalized[len(prefix) :].strip(" -:,.")
            if name:
                return AddRecipeRequest(name=name)
    return None


def parse_recipe_alias_request(text: str) -> RecipeAliasRequest | None:
    normalized = normalize_recipe_command_text(text)
    for prefix in (
        "add recipe alias",
        "recipe alias",
        "alias recipe",
        "add alias",
        "alias",
        "добавь псевдоним",
        "добавить псевдоним",
        "псевдоним",
        "добавь алиас",
        "добавить алиас",
        "алиас",
    ):
        if normalized.startswith(prefix + " "):
            argument = normalized[len(prefix) :]
            if prefix == "add alias":
                alias_first = parse_recipe_alias_to_argument(argument)
                if alias_first is not None:
                    return alias_first
            return parse_recipe_alias_argument(argument)
    return None


def parse_recipe_alias_argument(text: str) -> RecipeAliasRequest | None:
    normalized = normalize_recipe_command_text(text).strip(" -:,.")
    for separator in (" = ", " as ", " to ", " как "):
        recipe_name, found, alias = normalized.partition(separator)
        if not found:
            continue
        recipe_name = recipe_name.strip(" -:,.")
        alias = alias.strip(" -:,.")
        if recipe_name and alias:
            return RecipeAliasRequest(recipe_name=recipe_name, alias=alias)
    return None


def parse_recipe_alias_to_argument(text: str) -> RecipeAliasRequest | None:
    normalized = normalize_recipe_command_text(text).strip(" -:,.")
    alias, found, recipe_name = normalized.partition(" to ")
    if not found:
        return None
    alias = alias.strip(" -:,.")
    recipe_name = recipe_name.strip(" -:,.")
    if recipe_name and alias:
        return RecipeAliasRequest(recipe_name=recipe_name, alias=alias)
    return None


def should_try_ai_recipe_command(text: str) -> bool:
    normalized = normalize_recipe_command_text(text)
    return any(
        marker in normalized
        for marker in (
            "рецепт",
            "ингредиент",
            "ingredient",
            "продукт",
            "recipe",
            "готов",
            "learn",
            "remember",
            "save",
            "teach",
            "выучи",
            "запомни",
            "для ",
            " на ",
        )
    )


def looks_like_recipe_reuse_request(text: str) -> bool:
    normalized = normalize_recipe_command_text(text)
    return any(
        marker in normalized
        for marker in (
            " все для ",
            " для ",
            " на ",
            "ингредиент",
            "продукт",
            "приготов",
        )
    )


def normalize_recipe_command_text(text: str) -> str:
    text = text.casefold().strip().replace("ё", "е")
    text = re.sub(r"[.!?]+$", "", text)
    return " ".join(text.split())


async def fetch_recipe_page_text(url: str, *, timeout: int = 15) -> str:
    return await asyncio.to_thread(fetch_recipe_page_text_sync, url, timeout=timeout)


def fetch_recipe_page_text_sync(url: str, *, timeout: int = 15) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS recipe links are supported")

    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; HoneybuyRecipeBot/0.1; "
                "+https://example.invalid)"
            )
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                raise ValueError("Recipe link did not return readable text")
            charset = response.headers.get_content_charset() or "utf-8"
            raw_html = response.read(1_500_000)
    except URLError as error:
        raise ValueError(f"Could not fetch recipe link: {error}") from error

    html = raw_html.decode(charset, errors="replace")
    return html_to_text(html)


def html_to_text(html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(html)
    return parser.text()


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        text = " ".join(data.split())
        if text:
            self._parts.append(text)

    def text(self) -> str:
        return "\n".join(self._parts)
