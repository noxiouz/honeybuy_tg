from html import escape

from honeybuy_tg.models import Recipe, RecipeIngredient, ShoppingItem


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


def format_shop_mode(items: list[ShoppingItem]) -> str:
    if not items:
        return "Shopping checklist is empty."
    return format_shop_session([(item.id, format_item(item), False) for item in items])


def format_shop_session(items: list[tuple[int, str, bool]]) -> str:
    if not items:
        return "Shopping checklist is empty."
    lines = ["Shopping mode", "Tap an item after it is in the cart.", ""]
    for _, item_text, checked in items:
        marker = "✅" if checked else "☐"
        lines.append(f"{marker} {item_text}")
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
        lines.append(f"• {recipe.name} ({len(recipe.ingredients)} ingredients)")
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
