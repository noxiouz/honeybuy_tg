import pytest

from honeybuy_tg.models import ItemIdentity, ItemStatus
from honeybuy_tg.storage import Storage


@pytest.mark.asyncio
async def test_items_are_scoped_by_chat(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    await storage.add_item(chat_id=1, name="Milk", created_by=10)
    await storage.add_item(chat_id=2, name="Milk", created_by=20)

    removed = await storage.remove_matching_items(chat_id=1, name="milk")

    chat_1_items = await storage.list_items(chat_id=1, status=ItemStatus.REMOVED)
    chat_2_active = await storage.list_items(chat_id=2)

    assert [item.chat_id for item in removed] == [1]
    assert [item.name for item in chat_1_items] == ["Milk"]
    assert [item.name for item in chat_2_active] == ["Milk"]


@pytest.mark.asyncio
async def test_mark_item_bought_by_id(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    item = await storage.add_item(chat_id=1, name="Bread", created_by=10)
    updated = await storage.mark_item_bought(chat_id=1, item_id=item.id)

    assert updated is not None
    assert updated.status == ItemStatus.BOUGHT
    assert updated.bought_at is not None


@pytest.mark.asyncio
async def test_item_identity_lifecycle(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    item = await storage.add_item(
        chat_id=1,
        name="Tomato paste",
        created_by=10,
        canonical_name="томатная паста",
        canonical_key="tomato_paste",
    )

    assert item.canonical_name == "томатная паста"
    assert item.canonical_key == "tomato_paste"

    updated = await storage.update_item_identity(
        chat_id=1,
        item_id=item.id,
        identity=ItemIdentity(
            raw_name="Tomato paste",
            canonical_name="паста томатная",
            canonical_key="tomato_paste",
        ),
    )

    assert updated is not None
    assert updated.canonical_name == "паста томатная"


@pytest.mark.asyncio
async def test_authorized_chat(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    assert not await storage.is_chat_authorized(100)

    await storage.authorize_chat(
        chat_id=100,
        chat_type="supergroup",
        title="Groceries",
        authorized_by=10,
    )

    assert await storage.is_chat_authorized(100)


@pytest.mark.asyncio
async def test_pending_confirmation_lifecycle(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    confirmation_id = await storage.create_pending_confirmation(
        chat_id=1,
        user_id=10,
        source_message_id=100,
        items_json='["яйца", "масло"]',
    )

    row = await storage.get_pending_confirmation(
        confirmation_id=confirmation_id,
        chat_id=1,
    )
    assert row is not None
    assert row["items_json"] == '["яйца", "масло"]'

    assert await storage.resolve_pending_confirmation(
        confirmation_id=confirmation_id,
        chat_id=1,
        status="confirmed_add",
    )
    assert not await storage.resolve_pending_confirmation(
        confirmation_id=confirmation_id,
        chat_id=1,
        status="confirmed_add",
    )
    assert (
        await storage.get_pending_confirmation(
            confirmation_id=confirmation_id,
            chat_id=1,
        )
        is None
    )


@pytest.mark.asyncio
async def test_bot_message_lookup(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    await storage.save_bot_message(
        chat_id=1,
        message_id=100,
        kind="added",
        item_ids="1,2",
    )

    row = await storage.get_bot_message(chat_id=1, message_id=100)
    latest = await storage.get_latest_bot_message(chat_id=1, kind="added")

    assert row is not None
    assert row["item_ids"] == "1,2"
    assert latest is not None
    assert latest["message_id"] == 100


@pytest.mark.asyncio
async def test_recipe_lifecycle(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    recipe = await storage.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url="https://example.com/solyanka",
        created_by=10,
        ingredients=[
            ("fresh dill", "8 sprigs"),
            ("tomato paste", "60 g"),
        ],
    )

    loaded = await storage.get_recipe(chat_id=1, name="солянки")
    recipes = await storage.list_recipes(chat_id=1)

    assert recipe.name == "Солянка"
    assert loaded is not None
    assert [ingredient.name for ingredient in loaded.ingredients] == [
        "fresh dill",
        "tomato paste",
    ]
    assert [ingredient.quantity_text for ingredient in loaded.ingredients] == [
        "8 sprigs",
        "60 g",
    ]
    assert [recipe.name for recipe in recipes] == ["Солянка"]


@pytest.mark.asyncio
async def test_chat_text_parse_mode_lifecycle(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    assert await storage.get_chat_text_parse_mode(chat_id=1) is None

    await storage.set_chat_text_parse_mode(
        chat_id=1,
        mode="all",
        updated_by=10,
    )

    assert await storage.get_chat_text_parse_mode(chat_id=1) == "all"

    await storage.set_chat_text_parse_mode(
        chat_id=1,
        mode="mention",
        updated_by=10,
    )

    assert await storage.get_chat_text_parse_mode(chat_id=1) == "mention"


@pytest.mark.asyncio
async def test_category_cache_lifecycle(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    assert await storage.get_cached_categories(["Молоко"]) == {}

    await storage.set_cached_categories(
        categories_by_name={"Молоко": "Молочка"},
        ttl_seconds=60,
    )

    assert await storage.get_cached_categories(["молоко"]) == {"молоко": "Молочка"}


@pytest.mark.asyncio
async def test_item_normalization_cache_lifecycle(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    assert await storage.get_cached_item_identities(["tomato paste"]) == {}

    await storage.set_cached_item_identities(
        identities_by_name={
            "tomato paste": ItemIdentity(
                raw_name="tomato paste",
                canonical_name="томатная паста",
                canonical_key="tomato_paste",
            )
        },
        ttl_seconds=60,
    )

    assert await storage.get_cached_item_identities(["Tomato Paste"]) == {
        "tomato paste": ItemIdentity(
            raw_name="tomato paste",
            canonical_name="томатная паста",
            canonical_key="tomato_paste",
        )
    }


@pytest.mark.asyncio
async def test_clear_active_items_is_scoped_by_chat(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    await storage.add_item(chat_id=1, name="Milk", created_by=10)
    await storage.add_item(chat_id=1, name="Bread", created_by=10)
    await storage.add_item(chat_id=2, name="Eggs", created_by=20)

    assert await storage.clear_active_items(chat_id=1) == 2

    assert await storage.list_items(chat_id=1) == []
    assert [item.name for item in await storage.list_items(chat_id=2)] == ["Eggs"]


@pytest.mark.asyncio
async def test_shop_session_lifecycle(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    await storage.create_shop_session(
        chat_id=1,
        message_id=100,
        items=[(1, "Milk"), (2, "Bread")],
    )
    assert [
        (row["item_id"], row["item_text"], row["checked"])
        for row in await storage.get_shop_session_items(chat_id=1, message_id=100)
    ] == [(1, "Milk", 0), (2, "Bread", 0)]

    assert await storage.set_shop_session_item_checked(
        chat_id=1,
        message_id=100,
        item_id=1,
        checked=True,
    )
    assert [
        (row["item_id"], row["item_text"], row["checked"])
        for row in await storage.get_shop_session_items(chat_id=1, message_id=100)
    ] == [(1, "Milk", 1), (2, "Bread", 0)]
