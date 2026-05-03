from html import escape

from honeybuy_tg.models import Recipe, RecipeIngredient, ShoppingItem

ShopSessionItem = tuple[int, str, bool] | tuple[int, str, bool, str | None]


def format_item(item: ShoppingItem) -> str:
    parts = [item.name]
    if item.quantity is not None:
        quantity = (
            int(item.quantity)
            if isinstance(item.quantity, float) and item.quantity.is_integer()
            else item.quantity
        )
        parts.append(str(quantity))
    if item.unit:
        parts.append(item.unit)
    if item.due_date:
        parts.append(f"by {item.due_date}")
    return " ".join(parts)


def format_items(
    items: list[ShoppingItem],
    *,
    categories_by_item_id: dict[int, str] | None = None,
    html: bool = False,
) -> str:
    if not items:
        return "Shopping list is empty.\n\nSend /add milk or say: купи молоко"
    title = "<b>Shopping list</b>" if html else "Shopping list"
    lines = [title]
    if not categories_by_item_id:
        for item in items:
            lines.append(f"• {format_item_for_output(item, html=html)}")
        return "\n".join(lines)

    for category, category_items in group_items_by_category(
        items,
        categories_by_item_id=categories_by_item_id,
    ):
        if category:
            lines.append("")
            lines.append(f"<b>{escape(category)}</b>" if html else category)
        for item in category_items:
            lines.append(f"• {format_item_for_output(item, html=html)}")
    return "\n".join(lines)


def format_added(item: ShoppingItem) -> str:
    return f"Added\n• {format_item(item)}"


def format_item_for_output(item: ShoppingItem, *, html: bool) -> str:
    text = format_item(item)
    if html:
        return escape(text)
    return text


def format_updated(action: str, items: list[ShoppingItem]) -> str:
    if not items:
        return "No matching active items."
    lines = [action]
    lines.extend(f"• {format_item(item)}" for item in items)
    return "\n".join(lines)


def format_shop_mode(
    items: list[ShoppingItem],
    *,
    categories_by_item_id: dict[int, str] | None = None,
) -> str:
    if not items:
        return "Shopping checklist is empty."
    return format_shop_session(
        [
            (
                item.id,
                format_item(item),
                False,
                categories_by_item_id.get(item.id) if categories_by_item_id else None,
            )
            for item in items
        ]
    )


def format_shop_session(items: list[ShopSessionItem]) -> str:
    if not items:
        return "Shopping checklist is empty."
    lines = ["Shopping mode", "Tap an item after it is in the cart.", ""]
    if not has_shop_categories(items):
        for item in items:
            _, item_text, checked = item[:3]
            marker = "✅" if checked else "☐"
            lines.append(f"{marker} {item_text}")
        return "\n".join(lines)

    for category, category_items in group_shop_session_items(items):
        lines.append(category)
        for item in category_items:
            _, item_text, checked = item[:3]
            marker = "✅" if checked else "☐"
            lines.append(f"{marker} {item_text}")
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def format_recipe_ingredient(ingredient: RecipeIngredient) -> str:
    if ingredient.quantity_text:
        return f"{ingredient.name}, {ingredient.quantity_text}"
    return ingredient.name


def format_recipe_saved(recipe: Recipe) -> str:
    lines = [
        "Saved recipe",
        recipe.name,
        "",
        f"Ingredients: {len(recipe.ingredients)}",
    ]
    lines.extend(f"• {format_recipe_ingredient(ingredient)}" for ingredient in recipe.ingredients)
    return "\n".join(lines)


def format_recipe_list(recipes: list[Recipe]) -> str:
    if not recipes:
        return "No saved recipes yet."
    lines = ["Saved recipes"]
    for recipe in recipes:
        alias_text = f" aliases: {', '.join(recipe.aliases)}" if recipe.aliases else ""
        lines.append(
            f"• {recipe.name} ({len(recipe.ingredients)} ingredients){alias_text}"
        )
    return "\n".join(lines)


def group_items_by_category(
    items: list[ShoppingItem],
    *,
    categories_by_item_id: dict[int, str],
) -> list[tuple[str, list[ShoppingItem]]]:
    groups: dict[str, list[ShoppingItem]] = {}
    for item in items:
        category = categories_by_item_id.get(item.id, "Другое")
        groups.setdefault(category, []).append(item)

    return list(groups.items())


def has_shop_categories(items: list[ShopSessionItem]) -> bool:
    return any(len(item) > 3 and bool(item[3]) for item in items)


def group_shop_session_items(
    items: list[ShopSessionItem],
) -> list[tuple[str, list[ShopSessionItem]]]:
    groups: dict[str, list[ShopSessionItem]] = {}
    for item in items:
        category = item[3] if len(item) > 3 and item[3] else "Другое"
        groups.setdefault(category, []).append(item)
    return list(groups.items())
