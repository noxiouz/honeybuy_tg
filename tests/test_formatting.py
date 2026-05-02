from datetime import UTC, datetime

from honeybuy_tg.formatting import format_items, format_shop_mode, format_shop_session
from honeybuy_tg.models import ItemStatus, ShoppingItem


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
