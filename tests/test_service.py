import pytest

from honeybuy_tg.models import ItemIdentity
from honeybuy_tg.service import ShoppingListService
from honeybuy_tg.storage import Storage


class FakeItemNormalizer:
    async def normalize(self, names):
        identities = {}
        for name in names:
            if name in {"tomato paste", "томатная паста", "tomato paste, 60 g"}:
                identities[name] = ItemIdentity(
                    raw_name=name,
                    canonical_name="томатная паста",
                    canonical_key="tomato_paste",
                )
            elif name in {"green beans", "зелёная фасоль"}:
                identities[name] = ItemIdentity(
                    raw_name=name,
                    canonical_name="зелёная фасоль",
                    canonical_key="green_beans",
                )
            elif name in {"water", "вода", "water, 1000 g"}:
                identities[name] = ItemIdentity(
                    raw_name=name,
                    canonical_name="вода",
                    canonical_key="water",
                )
        return identities


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
async def test_service_saves_pasted_recipe_without_source_url(tmp_path):
    service = ShoppingListService(Storage(tmp_path / "test.sqlite3"))
    await service.storage.init()

    recipe = await service.save_recipe(
        chat_id=1,
        name=" Pasted   pancakes ",
        source_url=None,
        user_id=10,
        ingredients=[(" flour ", " 200   g "), (" ", "bad")],
    )

    assert recipe.name == "Pasted pancakes"
    assert recipe.source_url is None
    assert [(ingredient.name, ingredient.quantity_text) for ingredient in recipe.ingredients] == [
        ("flour", "200 g")
    ]


@pytest.mark.asyncio
async def test_service_deletes_recipe(tmp_path):
    service = ShoppingListService(Storage(tmp_path / "test.sqlite3"))
    await service.storage.init()
    await service.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        user_id=10,
        ingredients=[("flour", "200 g")],
    )

    deleted = await service.delete_recipe(chat_id=1, name="pancakes")

    assert deleted is not None
    assert deleted.name == "Pancakes"
    assert await service.get_recipe(chat_id=1, name="pancakes") is None


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


@pytest.mark.asyncio
async def test_service_skips_recipe_ingredient_when_base_item_exists(tmp_path):
    service = ShoppingListService(Storage(tmp_path / "test.sqlite3"))
    await service.storage.init()

    await service.add_item(chat_id=1, name="tomato paste", user_id=10)
    recipe = await service.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        user_id=10,
        ingredients=[("tomato paste", "60 g"), ("lemon", "6 slices")],
    )

    added = await service.add_recipe_ingredients(
        chat_id=1,
        recipe=recipe,
        user_id=10,
    )

    assert [item.name for item in added] == ["lemon, 6 slices"]
    assert [item.name for item in await service.list_active(chat_id=1)] == [
        "tomato paste",
        "lemon, 6 slices",
    ]


@pytest.mark.asyncio
async def test_service_skips_recipe_ingredient_with_cross_language_identity(tmp_path):
    service = ShoppingListService(
        Storage(tmp_path / "test.sqlite3"),
        item_normalizer=FakeItemNormalizer(),
    )
    await service.storage.init()

    await service.add_item(chat_id=1, name="томатная паста", user_id=10)
    recipe = await service.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        user_id=10,
        ingredients=[("tomato paste", "60 g"), ("green beans", None)],
    )

    added = await service.add_recipe_ingredients(
        chat_id=1,
        recipe=recipe,
        user_id=10,
    )

    assert [item.name for item in added] == ["green beans"]
    assert [item.name for item in await service.list_active(chat_id=1)] == [
        "томатная паста",
        "green beans",
    ]


@pytest.mark.asyncio
async def test_service_marks_bought_by_cross_language_identity(tmp_path):
    service = ShoppingListService(
        Storage(tmp_path / "test.sqlite3"),
        item_normalizer=FakeItemNormalizer(),
    )
    await service.storage.init()

    await service.add_item(chat_id=1, name="green beans", user_id=10)

    bought = await service.mark_bought_by_name(chat_id=1, name="зелёная фасоль")

    assert [item.name for item in bought] == ["green beans"]
    assert await service.list_active(chat_id=1) == []


@pytest.mark.asyncio
async def test_service_deduplicates_existing_cross_language_active_items(tmp_path):
    service = ShoppingListService(
        Storage(tmp_path / "test.sqlite3"),
        item_normalizer=FakeItemNormalizer(),
    )
    await service.storage.init()

    await service.storage.add_item(chat_id=1, name="water", created_by=10)
    await service.storage.add_item(chat_id=1, name="вода", created_by=10)
    await service.storage.add_item(chat_id=1, name="water, 1000 g", created_by=10)

    items = await service.list_active_deduplicated(chat_id=1)

    assert [item.name for item in items] == ["water, 1000 g"]
    assert [item.name for item in await service.list_active(chat_id=1)] == [
        "water, 1000 g"
    ]
