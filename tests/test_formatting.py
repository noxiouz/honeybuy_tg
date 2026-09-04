from datetime import UTC, datetime
from dataclasses import replace
from html import escape
from html.parser import HTMLParser

import pytest

from honeybuy_tg.formatting import (
    format_items,
    format_recipe_list,
    format_shop_mode,
    format_shop_session,
)
from honeybuy_tg.models import ItemStatus, Recipe, RecipeIngredient, ShoppingItem


def item(item_id: int, name: str) -> ShoppingItem:
    now = datetime.now(UTC)
    return ShoppingItem(
        id=item_id,
        chat_id=1,
        name=name,
        normalized_name=name.casefold(),
        quantity=None,
        unit=None,
        note=None,
        due_date=None,
        status=ItemStatus.ACTIVE,
        created_by=1,
        created_at=now,
        updated_at=now,
        bought_at=None,
        removed_at=None,
    )


def recipe(name: str, aliases: tuple[str, ...] = ()) -> Recipe:
    now = datetime.now(UTC)
    return Recipe(
        id=1,
        chat_id=1,
        name=name,
        normalized_name=name.casefold(),
        source_url=None,
        created_by=1,
        created_at=now,
        updated_at=now,
        aliases=aliases,
        ingredients=(),
    )


def test_format_items_groups_by_category():
    text = format_items(
        [
            item(1, "помидоры"),
            item(2, "молоко"),
            item(3, "яйца"),
        ],
        categories_by_item_id={
            1: "Овощи",
            2: "Молочка",
            3: "Бакалея",
        },
    )

    assert text == "\n".join(
        [
            "Shopping list",
            "",
            "Овощи",
            "• помидоры",
            "",
            "Молочка",
            "• молоко",
            "",
            "Бакалея",
            "• яйца",
        ]
    )


def test_format_items_without_categories_is_ungrouped():
    text = format_items([item(1, "помидоры"), item(2, "молоко")])

    assert text == "\n".join(
        [
            "Shopping list",
            "• помидоры",
            "• молоко",
        ]
    )


def test_format_items_can_use_html_for_telegram():
    text = format_items(
        [item(1, "сыр <брынза>")],
        categories_by_item_id={1: "Молочка & сыр"},
        html=True,
    )

    assert text == "\n".join(
        [
            "<b>Shopping list</b>",
            "",
            "<b>Молочка &amp; сыр</b>",
            "• сыр &lt;брынза&gt;",
        ]
    )


def test_format_recipe_list_includes_aliases():
    text = format_recipe_list([recipe("Pancakes", aliases=("breakfast", "brunch"))])

    assert text == "Saved recipes\n• Pancakes (0 ingredients) aliases: breakfast, brunch"


class RecipeCardHTML(HTMLParser):
    """Check each Telegram message independently and collect literal source text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []
        self.code = []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "strong", "i", "em", "u", "s", "code", "pre"}
        assert not attrs
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag

    def handle_data(self, data):
        self.text.append(data)
        if "code" in self.stack:
            self.code.append(data)


def decode_recipe_card(parts):
    assert isinstance(parts, list) and parts
    decoded = []
    source = []
    for part in parts:
        parser = RecipeCardHTML()
        parser.feed(part)
        parser.close()
        assert not parser.stack
        visible = "".join(parser.text)
        assert visible.strip(), "Every continuation must have a visible heading"
        assert len(visible.encode("utf-16-le")) // 2 <= 4096
        decoded.append(visible)
        source.extend(parser.code)
    return decoded, "".join(source)


def test_recipe_card_renders_complete_saved_fields_and_escapes_html():
    from honeybuy_tg.formatting import format_recipe_card

    card = replace(
        recipe('Soup <b>"name"</b> &amp;', aliases=("<breakfast>", 'lunch & "tea"')),
        source_url='https://example.test/<recipe>?a="x"&b=1',
        ingredients=(
            RecipeIngredient(1, 1, "flour <raw>", '2 & 1/2 "cups"', 0),
            RecipeIngredient(2, 1, "salt & pepper", None, 1),
        ),
    )

    parts = format_recipe_card(card)

    visible, source = decode_recipe_card(parts)
    text = "\n".join(visible)
    fields = [
        card.name, *card.aliases,
        card.ingredients[0].name, card.ingredients[0].quantity_text,
        card.ingredients[1].name, card.source_url,
    ]
    positions = [text.index(field) for field in fields]
    assert positions == sorted(positions)
    assert source == card.source_url
    for field in fields:
        assert escape(field) in "\n".join(parts)
    assert "None" not in text


def test_recipe_card_explicitly_identifies_missing_optional_fields():
    from honeybuy_tg.formatting import format_recipe_card

    visible, source = decode_recipe_card(format_recipe_card(recipe("Pancakes")))
    text = "\n".join(visible).casefold()

    assert "pancakes" in text
    for section in ("aliases", "ingredients", "source"):
        assert any(section in line and ("none" in line or "no " in line)
                   for line in text.splitlines())
    assert source == ""


@pytest.mark.parametrize("field", ["name", "alias", "ingredient", "quantity", "source"])
def test_recipe_card_preserves_each_independently_overlong_unicode_field(field):
    from honeybuy_tg.formatting import format_recipe_card

    payload = "Ω🙂" * 3000
    card = replace(
        recipe(payload if field == "name" else "Pancakes",
               aliases=(payload if field == "alias" else "breakfast",)),
        source_url=payload if field == "source" else "https://example.test/recipe",
        ingredients=(RecipeIngredient(
            1, 1, payload if field == "ingredient" else "flour",
            payload if field == "quantity" else "2 cups", 0,
        ),),
    )

    parts = format_recipe_card(card)

    visible, source = decode_recipe_card(parts)
    assert len(parts) > 1
    assert "".join(char for text in visible for char in text if char in "Ω🙂") == payload
    assert source == card.source_url


@pytest.mark.parametrize("source", [
    "https://example.test/?q=<b>&literal=&amp;" + "&<>\"'" * 1500,
    "javascript:alert(1)",
    "tg://user?id=42",
    "file:///private/example",
    'not a URL <a href="https://example.test">link</a>',
    "start" + " \t\n" * 4500 + "end",
], ids=["long-html", "javascript", "telegram", "file", "malformed", "whitespace"])
def test_recipe_card_keeps_entire_source_literal_in_code_on_every_page(source):
    from honeybuy_tg.formatting import format_recipe_card

    parts = format_recipe_card(replace(recipe("Pancakes"), source_url=source))

    _, rendered_source = decode_recipe_card(parts)
    assert rendered_source == source
    assert all("<a" not in part.casefold() for part in parts)


def test_recipe_card_preserves_all_ingredients_and_quantities_across_pages():
    from honeybuy_tg.formatting import format_recipe_card

    ingredients = tuple(
        RecipeIngredient(i, 1, f"ingredient-{i:04d} <raw>", f"{i} cups & more", i)
        for i in range(220)
    )

    parts = format_recipe_card(replace(recipe("Pancakes"), ingredients=ingredients))

    visible, _ = decode_recipe_card(parts)
    assert len(parts) > 1
    text = "\n".join(visible)
    previous = -1
    for ingredient in ingredients:
        position = text.index(ingredient.name)
        assert position > previous
        assert text.index(ingredient.quantity_text, position) > position
        previous = position


def test_format_shop_mode():
    text = format_shop_mode([item(1, "помидоры"), item(2, "молоко")])

    assert text == "\n".join(
        [
            "Shopping mode",
            "Tap an item after it is in the cart.",
            "",
            "☐ помидоры",
            "☐ молоко",
        ]
    )


def test_format_shop_mode_groups_by_category():
    text = format_shop_mode(
        [item(1, "помидоры"), item(2, "молоко"), item(3, "огурцы")],
        categories_by_item_id={
            1: "Овощи",
            2: "Молочка",
            3: "Овощи",
        },
    )

    assert text == "\n".join(
        [
            "Shopping mode",
            "Tap an item after it is in the cart.",
            "",
            "Овощи",
            "☐ помидоры",
            "☐ огурцы",
            "",
            "Молочка",
            "☐ молоко",
        ]
    )


def test_format_shop_session_with_checked_item():
    text = format_shop_session(
        [
            (1, "помидоры", True),
            (2, "молоко", False),
        ]
    )

    assert text == "\n".join(
        [
            "Shopping mode",
            "Tap an item after it is in the cart.",
            "",
            "✅ помидоры",
            "☐ молоко",
        ]
    )


def test_format_shop_session_keeps_category_groups_after_checking():
    text = format_shop_session(
        [
            (1, "помидоры", True, "Овощи"),
            (2, "молоко", False, "Молочка"),
            (3, "огурцы", True, "Овощи"),
        ]
    )

    assert text == "\n".join(
        [
            "Shopping mode",
            "Tap an item after it is in the cart.",
            "",
            "Овощи",
            "✅ помидоры",
            "✅ огурцы",
            "",
            "Молочка",
            "☐ молоко",
        ]
    )
