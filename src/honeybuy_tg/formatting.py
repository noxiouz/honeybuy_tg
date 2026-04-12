from honeybuy_tg.models import ShoppingItem


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


def format_items(items: list[ShoppingItem]) -> str:
    if not items:
        return "Shopping list is empty."
    lines = ["Shopping list:"]
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. {format_item(item)}")
    return "\n".join(lines)


def format_added(item: ShoppingItem) -> str:
    return f"Added: {format_item(item)}"


def format_updated(action: str, items: list[ShoppingItem]) -> str:
    if not items:
        return "No matching active items found."
    names = ", ".join(format_item(item) for item in items)
    return f"{action}: {names}"
