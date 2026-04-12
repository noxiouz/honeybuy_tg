from honeybuy_tg.models import Recipe, ShoppingItem
from honeybuy_tg.storage import Storage, normalize_item_name


class ShoppingListService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    async def add_item(self, *, chat_id: int, name: str, user_id: int) -> ShoppingItem:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Item name is required")
        return await self.storage.add_item(
            chat_id=chat_id,
            name=clean_name,
            created_by=user_id,
        )

    async def remove_by_name(self, *, chat_id: int, name: str) -> list[ShoppingItem]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Item name is required")
        return await self.storage.remove_matching_items(
            chat_id=chat_id, name=clean_name
        )

    async def mark_bought_by_name(
        self, *, chat_id: int, name: str
    ) -> list[ShoppingItem]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Item name is required")
        return await self.storage.mark_matching_bought(chat_id=chat_id, name=clean_name)

    async def list_active(self, *, chat_id: int) -> list[ShoppingItem]:
        return await self.storage.list_items(chat_id=chat_id)

    async def mark_bought_by_id(
        self,
        *,
        chat_id: int,
        item_id: int,
    ) -> ShoppingItem | None:
        return await self.storage.mark_item_bought(chat_id=chat_id, item_id=item_id)

    async def remove_by_id(self, *, chat_id: int, item_id: int) -> ShoppingItem | None:
        return await self.storage.remove_item(chat_id=chat_id, item_id=item_id)

    async def clear_bought(self, *, chat_id: int) -> int:
        return await self.storage.clear_bought(chat_id=chat_id)

    async def clear_active(self, *, chat_id: int) -> int:
        return await self.storage.clear_active_items(chat_id=chat_id)

    async def save_recipe(
        self,
        *,
        chat_id: int,
        name: str,
        source_url: str | None,
        user_id: int,
        ingredients: list[tuple[str, str | None]],
    ) -> Recipe:
        clean_name = name.strip()
        clean_ingredients = [
            (ingredient_name.strip(), quantity.strip() if quantity else None)
            for ingredient_name, quantity in ingredients
            if ingredient_name.strip()
        ]
        if not clean_name:
            raise ValueError("Recipe name is required")
        if not clean_ingredients:
            raise ValueError("Recipe must have ingredients")
        return await self.storage.save_recipe(
            chat_id=chat_id,
            name=clean_name,
            source_url=source_url,
            created_by=user_id,
            ingredients=clean_ingredients,
        )

    async def get_recipe(self, *, chat_id: int, name: str) -> Recipe | None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Recipe name is required")
        return await self.storage.get_recipe(chat_id=chat_id, name=clean_name)

    async def list_recipes(self, *, chat_id: int) -> list[Recipe]:
        return await self.storage.list_recipes(chat_id=chat_id)

    async def add_recipe_ingredients(
        self,
        *,
        chat_id: int,
        recipe: Recipe,
        user_id: int,
    ) -> list[ShoppingItem]:
        added = []
        active_names = set()
        for item in await self.list_active(chat_id=chat_id):
            active_names.add(item.normalized_name)
            active_names.add(recipe_ingredient_base_name(item.name))
        for ingredient in recipe.ingredients:
            item_name = recipe_ingredient_item_name(
                ingredient.name,
                ingredient.quantity_text,
            )
            normalized_name = normalize_item_name(item_name)
            base_name = recipe_ingredient_base_name(item_name)
            if normalized_name in active_names or base_name in active_names:
                continue
            added.append(
                await self.add_item(chat_id=chat_id, name=item_name, user_id=user_id)
            )
            active_names.add(normalized_name)
            active_names.add(base_name)
        return added


def recipe_ingredient_item_name(name: str, quantity_text: str | None) -> str:
    if quantity_text:
        return f"{name}, {quantity_text}"
    return name


def recipe_ingredient_base_name(name: str) -> str:
    return normalize_item_name(name.split(",", 1)[0])
