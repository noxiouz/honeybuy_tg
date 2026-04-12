import pytest

from honeybuy_tg.service import ShoppingListService
from honeybuy_tg.storage import Storage


@pytest.mark.asyncio
async def test_service_rejects_empty_item(tmp_path):
    service = ShoppingListService(Storage(tmp_path / "test.sqlite3"))
    await service.storage.init()

    with pytest.raises(ValueError, match="Item name is required"):
        await service.add_item(chat_id=1, name="   ", user_id=10)


@pytest.mark.asyncio
async def test_service_lists_active_items(tmp_path):
    service = ShoppingListService(Storage(tmp_path / "test.sqlite3"))
    await service.storage.init()

    await service.add_item(chat_id=1, name="Milk", user_id=10)
    await service.add_item(chat_id=1, name="Bread", user_id=10)

    items = await service.list_active(chat_id=1)

    assert [item.name for item in items] == ["Milk", "Bread"]


@pytest.mark.asyncio
async def test_service_adds_recipe_ingredients(tmp_path):
    service = ShoppingListService(Storage(tmp_path / "test.sqlite3"))
    await service.storage.init()

    recipe = await service.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        user_id=10,
        ingredients=[("fresh dill", "8 sprigs"), ("water", None)],
    )

    added = await service.add_recipe_ingredients(
        chat_id=1,
        recipe=recipe,
        user_id=10,
    )

    assert [item.name for item in added] == ["fresh dill, 8 sprigs", "water"]


@pytest.mark.asyncio
async def test_service_skips_active_recipe_ingredient_duplicates(tmp_path):
    service = ShoppingListService(Storage(tmp_path / "test.sqlite3"))
    await service.storage.init()

    recipe = await service.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        user_id=10,
        ingredients=[("fresh dill", "8 sprigs"), ("water", None)],
    )
    await service.add_recipe_ingredients(chat_id=1, recipe=recipe, user_id=10)

    added_again = await service.add_recipe_ingredients(
        chat_id=1,
        recipe=recipe,
        user_id=10,
    )

    assert added_again == []
    assert [item.name for item in await service.list_active(chat_id=1)] == [
        "fresh dill, 8 sprigs",
        "water",
    ]
