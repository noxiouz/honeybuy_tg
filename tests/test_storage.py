import asyncio
import sqlite3

import pytest

from honeybuy_tg.models import ItemIdentity, ItemStatus
from honeybuy_tg.storage import (
    RecipeAliasConflictError,
    RecipeAlreadyExistsError,
    StaleRecipeOverwriteError,
    Storage,
    recipe_state_digest,
)


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
async def test_pending_confirmation_claim_is_single_use(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    confirmation_id = await storage.create_pending_confirmation(
        chat_id=1,
        user_id=10,
        source_message_id=100,
        items_json='["яйца", "масло"]',
    )

    claimed = await storage.claim_pending_confirmation(
        confirmation_id=confirmation_id,
        chat_id=1,
        user_id=10,
        status="claiming_recipe_overwrite",
    )

    assert claimed is not None
    assert claimed["items_json"] == '["яйца", "масло"]'
    assert (
        await storage.get_pending_confirmation(
            confirmation_id=confirmation_id,
            chat_id=1,
        )
        is None
    )
    assert (
        await storage.claim_pending_confirmation(
            confirmation_id=confirmation_id,
            chat_id=1,
            user_id=10,
            status="cancelled_recipe_overwrite",
        )
        is None
    )
    assert not await storage.resolve_pending_confirmation(
        confirmation_id=confirmation_id,
        chat_id=1,
        status="confirmed_add",
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
async def test_save_recipe_requires_explicit_overwrite(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=10,
        ingredients=[("flour", "200 g")],
    )

    with pytest.raises(RecipeAlreadyExistsError) as error:
        await storage.save_recipe(
            chat_id=1,
            name=" pancakes ",
            source_url="https://example.com/pancakes",
            created_by=20,
            ingredients=[("milk", "300 ml")],
        )

    assert error.value.recipe.name == "Pancakes"
    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert loaded is not None
    assert loaded.source_url is None
    assert [
        (ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients
    ] == [("flour", "200 g")]

    overwritten = await storage.save_recipe(
        chat_id=1,
        name=" pancakes ",
        source_url="https://example.com/pancakes",
        created_by=20,
        ingredients=[("milk", "300 ml")],
        overwrite=True,
    )

    assert overwritten.name == "pancakes"
    assert overwritten.source_url == "https://example.com/pancakes"
    assert [
        (ingredient.name, ingredient.quantity_text)
        for ingredient in overwritten.ingredients
    ] == [("milk", "300 ml")]


@pytest.mark.asyncio
async def test_save_recipe_allows_own_loose_alias_on_relearn_and_overwrite(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    original = await storage.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        created_by=10,
        ingredients=[("fresh dill", "8 sprigs")],
    )
    await storage.add_recipe_alias(
        chat_id=1,
        recipe_name="солянка",
        alias="солянки",
        created_by=10,
    )

    with pytest.raises(RecipeAlreadyExistsError) as error:
        await storage.save_recipe(
            chat_id=1,
            name="Солянка",
            source_url=None,
            created_by=20,
            ingredients=[("tomato paste", "60 g")],
        )

    assert error.value.recipe.id == original.id
    assert error.value.recipe.aliases == ("солянки",)

    overwritten = await storage.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        created_by=20,
        ingredients=[("tomato paste", "60 g")],
        overwrite=True,
    )

    assert overwritten.id == original.id
    assert overwritten.aliases == ("солянки",)
    assert [
        (ingredient.name, ingredient.quantity_text)
        for ingredient in overwritten.ingredients
    ] == [("tomato paste", "60 g")]


@pytest.mark.asyncio
async def test_guarded_recipe_overwrite_rejects_same_second_content_change(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "honeybuy_tg.storage.utc_now",
        lambda: "2026-05-03T12:00:00+00:00",
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    original = await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=10,
        ingredients=[("flour", "200 g")],
    )
    original_digest = recipe_state_digest(original)

    await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=20,
        ingredients=[("eggs", "2")],
        overwrite=True,
    )

    with pytest.raises(StaleRecipeOverwriteError):
        await storage.save_recipe(
            chat_id=1,
            name="Pancakes",
            source_url=None,
            created_by=30,
            ingredients=[("milk", "300 ml")],
            overwrite=True,
            expected_recipe_id=original.id,
            expected_normalized_name=original.normalized_name,
            expected_state_digest=original_digest,
        )

    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert loaded is not None
    assert loaded.updated_at == original.updated_at
    assert [
        (ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients
    ] == [("eggs", "2")]


@pytest.mark.asyncio
async def test_recipe_state_digest_ignores_aliases_for_overwrite_guard(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    original = await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=10,
        ingredients=[("flour", "200 g")],
    )
    original_digest = recipe_state_digest(original)

    aliased = await storage.add_recipe_alias(
        chat_id=1,
        recipe_name="pancakes",
        alias="breakfast",
        created_by=10,
    )

    assert aliased is not None
    assert recipe_state_digest(aliased) == original_digest

    overwritten = await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=20,
        ingredients=[("milk", "300 ml")],
        overwrite=True,
        expected_recipe_id=original.id,
        expected_normalized_name=original.normalized_name,
        expected_state_digest=original_digest,
    )

    assert overwritten.aliases == ("breakfast",)
    assert [
        (ingredient.name, ingredient.quantity_text)
        for ingredient in overwritten.ingredients
    ] == [("milk", "300 ml")]


@pytest.mark.asyncio
async def test_delete_recipe_is_scoped_by_chat_and_cascades_ingredients(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    chat_1_recipe = await storage.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        created_by=10,
        ingredients=[("fresh dill", "8 sprigs")],
    )
    await storage.save_recipe(
        chat_id=2,
        name="Солянка",
        source_url=None,
        created_by=20,
        ingredients=[("tomato paste", "60 g")],
    )

    assert await storage.delete_recipe(chat_id=1, name="солянки") is None
    assert await storage.get_recipe(chat_id=1, name="Солянка") is not None

    deleted = await storage.delete_recipe(chat_id=1, name="солянка")

    assert deleted is not None
    assert deleted.name == "Солянка"
    assert await storage.get_recipe(chat_id=1, name="Солянка") is None
    assert await storage.get_recipe(chat_id=2, name="Солянка") is not None
    with sqlite3.connect(storage.database_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        ingredient_count = db.execute(
            "SELECT COUNT(*) FROM recipe_ingredients WHERE recipe_id = ?",
            (chat_1_recipe.id,),
        ).fetchone()[0]
    assert ingredient_count == 0


@pytest.mark.asyncio
async def test_recipe_aliases_are_scoped_and_listed(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()

    await storage.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        created_by=10,
        ingredients=[("fresh dill", "8 sprigs")],
    )
    await storage.save_recipe(
        chat_id=2,
        name="Солянка",
        source_url=None,
        created_by=20,
        ingredients=[("tomato paste", "60 g")],
    )

    recipe = await storage.add_recipe_alias(
        chat_id=1,
        recipe_name="солянка",
        alias="soup",
        created_by=10,
    )
    loaded = await storage.get_recipe(chat_id=1, name="soup")
    other_chat_loaded = await storage.get_recipe(chat_id=2, name="soup")
    recipes = await storage.list_recipes(chat_id=1)

    assert recipe is not None
    assert recipe.aliases == ("soup",)
    assert loaded is not None
    assert loaded.name == "Солянка"
    assert other_chat_loaded is None
    assert recipes[0].aliases == ("soup",)
    assert await storage.delete_recipe(chat_id=1, name="soup") is None
    assert await storage.get_recipe(chat_id=1, name="soup") is not None


@pytest.mark.asyncio
async def test_recipe_alias_reads_ignore_cross_chat_alias_rows(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    pancakes = await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=10,
        ingredients=[("flour", "200 g")],
    )
    await storage.save_recipe(
        chat_id=2,
        name="Waffles",
        source_url=None,
        created_by=20,
        ingredients=[("eggs", "2")],
    )
    with storage.connect() as db:
        db.execute(
            """
            INSERT INTO recipe_aliases (
                chat_id, recipe_id, alias, normalized_alias, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (2, pancakes.id, "breakfast", "breakfast", 20, "2026-05-03T12:00:00+00:00"),
        )
        db.commit()

    assert await storage.get_recipe(chat_id=2, name="breakfast") is None

    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    recipes = await storage.list_recipes(chat_id=1)

    assert loaded is not None
    assert loaded.aliases == ()
    assert recipes[0].aliases == ()


@pytest.mark.asyncio
async def test_recipe_alias_conflicts_with_recipe_names_and_other_aliases(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=10,
        ingredients=[("flour", "200 g")],
    )
    await storage.save_recipe(
        chat_id=1,
        name="Waffles",
        source_url=None,
        created_by=10,
        ingredients=[("flour", "200 g")],
    )

    first = await storage.add_recipe_alias(
        chat_id=1,
        recipe_name="pancakes",
        alias="breakfast",
        created_by=10,
    )
    idempotent = await storage.add_recipe_alias(
        chat_id=1,
        recipe_name="pancakes",
        alias="breakfast",
        created_by=10,
    )

    assert first is not None
    assert idempotent is not None
    assert idempotent.aliases == ("breakfast",)

    with pytest.raises(RecipeAliasConflictError) as alias_error:
        await storage.add_recipe_alias(
            chat_id=1,
            recipe_name="waffles",
            alias="breakfast",
            created_by=10,
        )
    assert alias_error.value.recipe.name == "Pancakes"

    with pytest.raises(RecipeAliasConflictError) as name_error:
        await storage.add_recipe_alias(
            chat_id=1,
            recipe_name="pancakes",
            alias="waffles",
            created_by=10,
        )
    assert name_error.value.recipe.name == "Waffles"

    with pytest.raises(RecipeAliasConflictError):
        await storage.save_recipe(
            chat_id=1,
            name="breakfast",
            source_url=None,
            created_by=10,
            ingredients=[("eggs", "2")],
        )


@pytest.mark.asyncio
async def test_recipe_alias_duplicate_race_converts_to_alias_conflict(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    pancakes = await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=10,
        ingredients=[("flour", "200 g")],
    )
    await storage.save_recipe(
        chat_id=1,
        name="Waffles",
        source_url=None,
        created_by=10,
        ingredients=[("flour", "200 g")],
    )

    db = sqlite3.connect(storage.database_path)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """
            INSERT INTO recipe_aliases (
                chat_id, recipe_id, alias, normalized_alias, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                pancakes.id,
                "breakfast",
                "breakfast",
                20,
                "2026-05-03T12:00:00+00:00",
            ),
        )
        task = asyncio.create_task(
            asyncio.to_thread(
                lambda: asyncio.run(
                    storage.add_recipe_alias(
                        chat_id=1,
                        recipe_name="waffles",
                        alias="breakfast",
                        created_by=30,
                    )
                )
            )
        )
        await asyncio.sleep(0.05)
        db.commit()
    finally:
        db.close()

    with pytest.raises(RecipeAliasConflictError) as error:
        await asyncio.wait_for(task, timeout=2)
    assert error.value.recipe.name == "Pancakes"


@pytest.mark.asyncio
async def test_recipe_alias_conflicts_with_loose_recipe_lookup_names(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        created_by=10,
        ingredients=[("fresh dill", "8 sprigs")],
    )
    await storage.save_recipe(
        chat_id=1,
        name="Борщ",
        source_url=None,
        created_by=10,
        ingredients=[("beetroot", "2")],
    )

    with pytest.raises(RecipeAliasConflictError) as error:
        await storage.add_recipe_alias(
            chat_id=1,
            recipe_name="борщ",
            alias="солянки",
            created_by=10,
        )

    assert error.value.recipe.name == "Солянка"
    loaded = await storage.get_recipe(chat_id=1, name="солянки")
    assert loaded is not None
    assert loaded.name == "Солянка"


@pytest.mark.asyncio
async def test_save_recipe_conflicts_with_loose_alias_lookup_names(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Борщ",
        source_url=None,
        created_by=10,
        ingredients=[("beetroot", "2")],
    )
    await storage.add_recipe_alias(
        chat_id=1,
        recipe_name="борщ",
        alias="солянки",
        created_by=10,
    )

    with pytest.raises(RecipeAliasConflictError) as error:
        await storage.save_recipe(
            chat_id=1,
            name="Солянка",
            source_url=None,
            created_by=10,
            ingredients=[("fresh dill", "8 sprigs")],
        )

    assert error.value.alias == "солянки"
    assert error.value.recipe.name == "Борщ"


@pytest.mark.asyncio
async def test_save_recipe_name_waits_for_alias_write_before_conflict_check(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    borscht = await storage.save_recipe(
        chat_id=1,
        name="Борщ",
        source_url=None,
        created_by=10,
        ingredients=[("beetroot", "2")],
    )

    db = sqlite3.connect(storage.database_path)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """
            INSERT INTO recipe_aliases (
                chat_id, recipe_id, alias, normalized_alias, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                borscht.id,
                "солянки",
                "солянки",
                20,
                "2026-05-03T12:00:00+00:00",
            ),
        )
        task = asyncio.create_task(
            asyncio.to_thread(
                lambda: asyncio.run(
                    storage.save_recipe(
                        chat_id=1,
                        name="Солянка",
                        source_url=None,
                        created_by=30,
                        ingredients=[("fresh dill", "8 sprigs")],
                    )
                )
            )
        )
        await asyncio.sleep(0.05)
        db.commit()
    finally:
        db.close()

    with pytest.raises(RecipeAliasConflictError) as error:
        await asyncio.wait_for(task, timeout=2)

    assert error.value.alias == "солянки"
    assert error.value.recipe.name == "Борщ"
    with storage.connect() as check_db:
        saved_conflict_name = check_db.execute(
            """
            SELECT COUNT(*) FROM recipes
            WHERE chat_id = ? AND normalized_name = ?
            """,
            (1, "солянка"),
        ).fetchone()[0]
    assert saved_conflict_name == 0


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
        items=[(1, "Milk", "Dairy"), (2, "Bread")],
    )
    assert [
        (row["item_id"], row["item_text"], row["category"], row["checked"])
        for row in await storage.get_shop_session_items(chat_id=1, message_id=100)
    ] == [(1, "Milk", "Dairy", 0), (2, "Bread", None, 0)]

    assert await storage.set_shop_session_item_checked(
        chat_id=1,
        message_id=100,
        item_id=1,
        checked=True,
    )
    assert [
        (row["item_id"], row["item_text"], row["category"], row["checked"])
        for row in await storage.get_shop_session_items(chat_id=1, message_id=100)
    ] == [(1, "Milk", "Dairy", 1), (2, "Bread", None, 0)]


@pytest.mark.asyncio
async def test_init_migrates_old_shop_sessions_table_without_category(tmp_path):
    database_path = tmp_path / "test.sqlite3"
    now = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(database_path) as db:
        db.execute(
            """
            CREATE TABLE shop_sessions (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                item_text TEXT NOT NULL,
                checked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, message_id, item_id)
            )
            """
        )
        db.execute(
            """
            INSERT INTO shop_sessions (
                chat_id, message_id, item_id, item_text, checked, created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 100, 1, "Milk", 0, now, now),
        )
        db.commit()

    storage = Storage(database_path)
    await storage.init()

    with storage.connect() as db:
        columns = {
            row["name"] for row in db.execute("PRAGMA table_info(shop_sessions)")
        }
    assert "category" in columns
    assert [
        (row["item_id"], row["item_text"], row["category"], row["checked"])
        for row in await storage.get_shop_session_items(chat_id=1, message_id=100)
    ] == [(1, "Milk", None, 0)]

    await storage.create_shop_session(
        chat_id=1,
        message_id=101,
        items=[(1, "Milk", "Dairy")],
    )
    assert [
        (row["item_id"], row["item_text"], row["category"], row["checked"])
        for row in await storage.get_shop_session_items(chat_id=1, message_id=101)
    ] == [(1, "Milk", "Dairy", 0)]
