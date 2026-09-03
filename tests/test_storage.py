import asyncio
import shutil
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from honeybuy_tg.migrations import (
    CURRENT_SCHEMA_VERSION,
    migrate_database_path,
    run_migrations,
)
from honeybuy_tg.models import ItemIdentity, ItemStatus
from honeybuy_tg.storage import (
    RecipeAliasConflictError,
    RecipeAlreadyExistsError,
    StaleRecipeOverwriteError,
    Storage,
    recipe_state_digest,
)


REMOTE_DB_FIXTURE = Path("/tmp/honeybuy_remote.sqlite3")


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
async def test_init_creates_current_schema_version(tmp_path):
    database_path = tmp_path / "test.sqlite3"
    storage = Storage(database_path)

    await storage.init()

    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = _table_names(db)
        assert {
            "shopping_items",
            "category_cache",
            "item_normalization_cache",
            "recipe_aliases",
            "shop_sessions",
        } <= tables
        assert {"canonical_name", "canonical_key"} <= _column_names(
            db, "shopping_items"
        )
        assert {"canonical_name", "canonical_key"} <= _column_names(
            db, "recipe_ingredients"
        )
        assert "category" in _column_names(db, "shop_sessions")


def test_run_migrations_reads_user_version_after_begin_immediate():
    statements: list[str] = []
    with sqlite3.connect(":memory:") as db:
        db.set_trace_callback(statements.append)

        run_migrations(db)

    begin_index = statements.index("BEGIN IMMEDIATE")
    user_version_index = statements.index("PRAGMA user_version")

    assert begin_index < user_version_index


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
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        columns = {
            row["name"] for row in db.execute("PRAGMA table_info(shop_sessions)")
        }
        tables = {
            row["name"]
            for row in db.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
    assert "category" in columns
    assert "recipe_aliases" in tables
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


def test_migrates_old_shopping_items_table_without_canonical_columns(tmp_path):
    database_path = tmp_path / "test.sqlite3"
    now = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(database_path) as db:
        db.execute(
            """
            CREATE TABLE shopping_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                quantity REAL,
                unit TEXT,
                note TEXT,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                bought_at TEXT,
                removed_at TEXT
            )
            """
        )
        db.execute(
            """
            INSERT INTO shopping_items (
                chat_id, name, normalized_name, status, created_by, created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "Milk", "milk", "active", 10, now, now),
        )
        db.commit()

    result = migrate_database_path(database_path)

    assert result.old_version == 0
    assert result.new_version == CURRENT_SCHEMA_VERSION
    assert result.integrity_check == "ok"
    assert result.applied_versions == tuple(range(1, CURRENT_SCHEMA_VERSION + 1))

    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert {"canonical_name", "canonical_key"} <= _column_names(
            db, "shopping_items"
        )
        assert "idx_shopping_items_chat_canonical_key" in _index_names(
            db, "shopping_items"
        )
        rows = db.execute(
            """
            SELECT name, canonical_name, canonical_key
            FROM shopping_items
            """
        ).fetchall()

    assert rows == [("Milk", None, None)]


def test_migrates_copied_remote_database_fixture(tmp_path):
    if not REMOTE_DB_FIXTURE.exists():
        pytest.skip(f"{REMOTE_DB_FIXTURE} is not available")

    database_path = tmp_path / "remote.sqlite3"
    shutil.copy2(REMOTE_DB_FIXTURE, database_path)

    with sqlite3.connect(database_path) as db:
        old_version = db.execute("PRAGMA user_version").fetchone()[0]
        before_counts = _table_counts(db)

    result = migrate_database_path(database_path)

    assert result.old_version == old_version
    assert result.new_version == CURRENT_SCHEMA_VERSION
    assert result.integrity_check == "ok"
    if old_version < CURRENT_SCHEMA_VERSION:
        assert result.changed
        assert result.applied_versions == tuple(
            range(old_version + 1, CURRENT_SCHEMA_VERSION + 1)
        )
    else:
        assert not result.changed
        assert result.applied_versions == ()

    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert _table_counts(db, before_counts.keys()) == before_counts
        tables = _table_names(db)
        assert {
            "recipe_aliases",
            "category_cache",
            "item_normalization_cache",
        } <= tables
        assert {"canonical_name", "canonical_key"} <= _column_names(
            db, "shopping_items"
        )
        assert {"canonical_name", "canonical_key"} <= _column_names(
            db, "recipe_ingredients"
        )
        assert "category" in _column_names(db, "shop_sessions")
        assert "idx_shopping_items_chat_canonical_key" in _index_names(
            db, "shopping_items"
        )


@pytest.mark.asyncio
async def test_inline_capture_fresh_schema_is_version_two(tmp_path):
    database_path = tmp_path / "fresh.sqlite3"
    storage = Storage(database_path)

    await storage.init()

    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "inline_capture_intents" in _table_names(db)
        assert {
            "token_hash",
            "requester_id",
            "target_chat_id",
            "target_kind",
            "item_text",
            "created_at",
            "expires_at",
            "status",
        } <= _column_names(db, "inline_capture_intents")


def test_migrates_v1_database_to_inline_capture_schema_without_data_loss(tmp_path):
    database_path = tmp_path / "v1.sqlite3"
    now = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(database_path) as db:
        db.executescript(
            """
            CREATE TABLE authorized_chats (
                chat_id INTEGER PRIMARY KEY,
                chat_type TEXT NOT NULL,
                title TEXT,
                authorized_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE shopping_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                canonical_name TEXT,
                canonical_key TEXT,
                quantity REAL,
                unit TEXT,
                note TEXT,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                bought_at TEXT,
                removed_at TEXT
            );
            CREATE INDEX idx_shopping_items_chat_status
                ON shopping_items (chat_id, status);
            """
        )
        db.execute(
            """
            INSERT INTO authorized_chats (
                chat_id, chat_type, title, authorized_by, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (-1001, "supergroup", "Household", 42, now),
        )
        db.execute(
            """
            INSERT INTO shopping_items (
                chat_id, name, normalized_name, status, created_by, created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (-1001, "Milk", "milk", "active", 42, now, now),
        )
        db.execute("PRAGMA user_version = 1")
        db.commit()

    first = migrate_database_path(database_path)
    second = migrate_database_path(database_path)

    assert CURRENT_SCHEMA_VERSION == 2
    assert first.old_version == 1
    assert first.new_version == 2
    assert first.applied_versions == (2,)
    assert first.integrity_check == "ok"
    assert not second.changed
    assert second.old_version == second.new_version == 2
    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute(
            "SELECT chat_id, chat_type, title, authorized_by FROM authorized_chats"
        ).fetchall() == [(-1001, "supergroup", "Household", 42)]
        assert db.execute(
            "SELECT chat_id, name, normalized_name, status, created_by "
            "FROM shopping_items"
        ).fetchall() == [(-1001, "Milk", "milk", "active", 42)]
        assert "inline_capture_intents" in _table_names(db)
        columns = _column_names(db, "inline_capture_intents")
        assert {
            "token_hash",
            "requester_id",
            "target_chat_id",
            "target_kind",
            "item_text",
            "created_at",
            "expires_at",
            "status",
        } <= columns
        assert {
            "token",
            "source_chat_id",
            "chat_instance",
            "inline_message_id",
            "conversation",
        }.isdisjoint(columns)


@pytest.mark.asyncio
async def test_inline_capture_intents_store_only_hash_and_enforce_pending_quota(
    tmp_path,
):
    database_path = tmp_path / "test.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    expired_token = "expired-raw-token-that-must-not-be-stored"
    await storage.create_inline_capture_intent(
        token=expired_token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="old milk",
        created_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(minutes=1),
        pending_limit=2,
    )

    live_tokens = [
        "first-raw-token-that-must-not-be-stored",
        "second-raw-token-that-must-not-be-stored",
        "third-raw-token-that-must-not-be-stored",
    ]
    for index, token in enumerate(live_tokens):
        await storage.create_inline_capture_intent(
            token=token,
            requester_id=42,
            target_chat_id=42,
            target_kind="private",
            item_text=f"item {index}",
            created_at=now + timedelta(seconds=index),
            expires_at=now + timedelta(minutes=5),
            pending_limit=2,
        )
    await storage.create_inline_capture_intent(
        token="other-requester-token-that-must-not-be-stored",
        requester_id=7,
        target_chat_id=7,
        target_kind="private",
        item_text="bread",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=2,
    )

    with sqlite3.connect(database_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM inline_capture_intents ORDER BY requester_id, created_at"
        ).fetchall()

    requester_rows = [row for row in rows if row["requester_id"] == 42]
    assert len(requester_rows) == 2
    assert all(row["status"] == "pending" for row in requester_rows)
    assert all(row["item_text"] in {"item 1", "item 2"} for row in requester_rows)
    assert len([row for row in rows if row["requester_id"] == 7]) == 1
    stored_payload = "\n".join(str(value) for row in rows for value in row)
    assert expired_token not in stored_payload
    assert all(token not in stored_payload for token in live_tokens)
    assert all(len(row["token_hash"]) >= 32 for row in rows)


@pytest.mark.asyncio
async def test_inline_capture_apply_is_requester_bound_expiring_and_target_scoped(
    tmp_path,
):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = "requester-bound-inline-token"
    await storage.create_inline_capture_intent(
        token=token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="milk and eggs",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )

    assert (
        await storage.apply_inline_capture_intent(
            token=token,
            requester_id=7,
            now=now,
            canonical_name="milk and eggs",
            canonical_key="milk_and_eggs",
        )
        is None
    )
    assert await storage.get_inline_capture_intent(
        token=token,
        requester_id=42,
        now=now,
    ) is not None

    item = await storage.apply_inline_capture_intent(
        token=token,
        requester_id=42,
        now=now,
        canonical_name="milk and eggs",
        canonical_key="milk_and_eggs",
    )

    assert item is not None
    assert (item.chat_id, item.name, item.created_by) == (42, "milk and eggs", 42)
    assert await storage.list_items(chat_id=7) == []
    with storage.connect() as db:
        assert db.execute(
            "SELECT status FROM inline_capture_intents"
        ).fetchone()[0] == "applied"
    assert (
        await storage.apply_inline_capture_intent(
            token=token,
            requester_id=42,
            now=now,
            canonical_name="milk and eggs",
            canonical_key="milk_and_eggs",
        )
        is None
    )

    expired_token = "expired-inline-token"
    await storage.create_inline_capture_intent(
        token=expired_token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="bread",
        created_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
        pending_limit=10,
    )
    assert (
        await storage.apply_inline_capture_intent(
            token=expired_token,
            requester_id=42,
            now=now,
            canonical_name="bread",
            canonical_key="bread",
        )
        is None
    )
    assert [item.name for item in await storage.list_items(chat_id=42)] == [
        "milk and eggs"
    ]

    wrong_private_token = "wrong-private-target-inline-token"
    await storage.create_inline_capture_intent(
        token=wrong_private_token,
        requester_id=42,
        target_chat_id=7,
        target_kind="private",
        item_text="must not cross private tenants",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )
    assert (
        await storage.apply_inline_capture_intent(
            token=wrong_private_token,
            requester_id=42,
            now=now,
            canonical_name="must not cross private tenants",
            canonical_key="must_not_cross_private_tenants",
        )
        is None
    )
    assert await storage.list_items(chat_id=7) == []


@pytest.mark.asyncio
async def test_inline_capture_group_deauthorization_leaves_intent_retryable(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = "group-inline-token"
    await storage.authorize_chat(
        chat_id=-1001,
        chat_type="supergroup",
        title="Household",
        authorized_by=42,
    )
    await storage.create_inline_capture_intent(
        token=token,
        requester_id=42,
        target_chat_id=-1001,
        target_kind="supergroup",
        item_text="tea",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )
    with storage.connect() as db:
        db.execute("DELETE FROM authorized_chats WHERE chat_id = ?", (-1001,))
        db.commit()

    assert (
        await storage.apply_inline_capture_intent(
            token=token,
            requester_id=42,
            now=now,
            canonical_name="tea",
            canonical_key="tea",
        )
        is None
    )
    assert await storage.get_inline_capture_intent(
        token=token,
        requester_id=42,
        now=now,
    ) is not None

    await storage.authorize_chat(
        chat_id=-1001,
        chat_type="private",
        title="Invalid stored private row",
        authorized_by=42,
    )
    assert (
        await storage.apply_inline_capture_intent(
            token=token,
            requester_id=42,
            now=now,
            canonical_name="tea",
            canonical_key="tea",
        )
        is None
    )
    await storage.authorize_chat(
        chat_id=-1001,
        chat_type="supergroup",
        title="Household",
        authorized_by=42,
    )
    item = await storage.apply_inline_capture_intent(
        token=token,
        requester_id=42,
        now=now,
        canonical_name="tea",
        canonical_key="tea",
    )
    assert item is not None
    assert item.chat_id == -1001


@pytest.mark.asyncio
async def test_inline_capture_apply_rolls_back_claim_when_item_insert_fails(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = "rollback-inline-token"
    await storage.create_inline_capture_intent(
        token=token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="milk",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )
    with storage.connect() as db:
        db.execute(
            """
            CREATE TRIGGER reject_inline_item
            BEFORE INSERT ON shopping_items
            BEGIN
                SELECT RAISE(ABORT, 'injected insert failure');
            END
            """
        )
        db.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected insert failure"):
        await storage.apply_inline_capture_intent(
            token=token,
            requester_id=42,
            now=now,
            canonical_name="milk",
            canonical_key="milk",
        )

    assert await storage.list_items(chat_id=42) == []
    assert await storage.get_inline_capture_intent(
        token=token,
        requester_id=42,
        now=now,
    ) is not None
    with storage.connect() as db:
        status, item_text = db.execute(
            "SELECT status, item_text FROM inline_capture_intents"
        ).fetchone()
        db.execute("DROP TRIGGER reject_inline_item")
        db.commit()
    assert status == "pending"
    assert item_text == "milk"

    item = await storage.apply_inline_capture_intent(
        token=token,
        requester_id=42,
        now=now,
        canonical_name="milk",
        canonical_key="milk",
    )
    assert item is not None
    assert [saved.name for saved in await storage.list_items(chat_id=42)] == ["milk"]


@pytest.mark.asyncio
async def test_inline_capture_apply_is_single_use_under_duplicate_delivery(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = "concurrent-inline-token"
    await storage.create_inline_capture_intent(
        token=token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="eggs",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )
    start = threading.Barrier(2)

    def apply_from_thread():
        start.wait()
        return asyncio.run(
            storage.apply_inline_capture_intent(
                token=token,
                requester_id=42,
                now=now,
                canonical_name="eggs",
                canonical_key="eggs",
            )
        )

    first, second = await asyncio.gather(
        asyncio.to_thread(apply_from_thread),
        asyncio.to_thread(apply_from_thread),
    )

    assert sum(result is not None for result in (first, second)) == 1
    assert [item.name for item in await storage.list_items(chat_id=42)] == ["eggs"]
    assert (
        await storage.apply_inline_capture_intent(
            token=token,
            requester_id=42,
            now=now,
            canonical_name="eggs",
            canonical_key="eggs",
        )
        is None
    )


@pytest.mark.asyncio
async def test_inline_capture_success_redacts_payload_and_remains_single_use(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = "redacted-applied-inline-token"
    await storage.create_inline_capture_intent(
        token=token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="sensitive applied payload",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )

    item = await storage.apply_inline_capture_intent(
        token=token,
        requester_id=42,
        now=now,
        canonical_name="sensitive applied payload",
        canonical_key="sensitive_applied_payload",
    )

    assert item is not None
    with storage.connect() as db:
        status, item_text = db.execute(
            "SELECT status, item_text FROM inline_capture_intents"
        ).fetchone()
    assert status == "applied"
    assert item_text in {None, ""}
    assert (
        await storage.apply_inline_capture_intent(
            token=token,
            requester_id=42,
            now=now,
            canonical_name="sensitive applied payload",
            canonical_key="sensitive_applied_payload",
        )
        is None
    )
    assert len(await storage.list_items(chat_id=42)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "apply"])
async def test_inline_capture_expired_payload_is_removed_or_redacted_when_touched(
    tmp_path,
    operation,
):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = f"expired-{operation}-inline-token"
    secret = f"sensitive expired {operation} payload"
    live_token = f"live-neighbor-{operation}-inline-token"
    await storage.create_inline_capture_intent(
        token=live_token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="live neighboring payload",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )
    await storage.create_inline_capture_intent(
        token=token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text=secret,
        created_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(minutes=1),
        pending_limit=10,
    )
    with storage.connect() as db:
        touched_token_hash = db.execute(
            "SELECT token_hash FROM inline_capture_intents WHERE item_text = ?",
            (secret,),
        ).fetchone()[0]

    if operation == "get":
        result = await storage.get_inline_capture_intent(
            token=token,
            requester_id=42,
            now=now,
        )
    else:
        result = await storage.apply_inline_capture_intent(
            token=token,
            requester_id=42,
            now=now,
            canonical_name="expired item",
            canonical_key="expired_item",
        )
    assert result is None
    with storage.connect() as db:
        touched_row = db.execute(
            "SELECT * FROM inline_capture_intents WHERE token_hash = ?",
            (touched_token_hash,),
        ).fetchone()
    assert touched_row is None or touched_row["item_text"] in {None, ""}
    if touched_row is not None:
        assert secret not in "\n".join(str(value) for value in touched_row)
    live_intent = await storage.get_inline_capture_intent(
        token=live_token,
        requester_id=42,
        now=now,
    )
    assert live_intent is not None
    assert live_intent["item_text"] == "live neighboring payload"
    assert await storage.list_items(chat_id=42) == []


@pytest.mark.asyncio
async def test_inline_capture_batch_rolls_back_on_mid_batch_insert_failure(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    await storage.authorize_chat(
        chat_id=-1001,
        chat_type="supergroup",
        title="Household",
        authorized_by=42,
    )
    existing_token = "existing-before-batch-inline-token"
    await storage.create_inline_capture_intent(
        token=existing_token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="existing pending item",
        created_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )
    with storage.connect() as db:
        db.execute(
            """
            CREATE TRIGGER reject_second_inline_intent
            BEFORE INSERT ON inline_capture_intents
            WHEN NEW.target_chat_id = -1001
            BEGIN
                SELECT RAISE(ABORT, 'injected batch failure');
            END
            """
        )
        db.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected batch failure"):
        await storage.create_inline_capture_intents(
            requester_id=42,
            intents=[
                {
                    "token": "batch-private-inline-token",
                    "target_chat_id": 42,
                    "target_kind": "private",
                    "item_text": "milk",
                },
                {
                    "token": "batch-group-inline-token",
                    "target_chat_id": -1001,
                    "target_kind": "supergroup",
                    "item_text": "milk",
                },
            ],
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            pending_limit=10,
        )

    with storage.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inline_capture_intents"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT item_text FROM inline_capture_intents"
        ).fetchone()[0] == "existing pending item"
    assert await storage.get_inline_capture_intent(
        token=existing_token,
        requester_id=42,
        now=now,
    ) is not None


@pytest.mark.asyncio
async def test_inline_capture_batch_preserves_every_new_intent_near_quota(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    await storage.authorize_chat(
        chat_id=-1001,
        chat_type="supergroup",
        title="Household",
        authorized_by=42,
    )
    old_tokens = ["old-first-inline-token", "old-second-inline-token"]
    for index, token in enumerate(old_tokens):
        await storage.create_inline_capture_intent(
            token=token,
            requester_id=42,
            target_chat_id=42,
            target_kind="private",
            item_text=f"old item {index}",
            created_at=now + timedelta(seconds=index),
            expires_at=now + timedelta(minutes=5),
            pending_limit=3,
        )

    new_tokens = ["new-private-inline-token", "new-group-inline-token"]
    await storage.create_inline_capture_intents(
        requester_id=42,
        intents=[
            {
                "token": new_tokens[0],
                "target_chat_id": 42,
                "target_kind": "private",
                "item_text": "milk",
            },
            {
                "token": new_tokens[1],
                "target_chat_id": -1001,
                "target_kind": "supergroup",
                "item_text": "milk",
            },
        ],
        created_at=now + timedelta(seconds=2),
        expires_at=now + timedelta(minutes=5),
        pending_limit=3,
    )

    for token in new_tokens:
        assert await storage.get_inline_capture_intent(
            token=token,
            requester_id=42,
            now=now + timedelta(seconds=2),
        ) is not None
    with storage.connect() as db:
        assert db.execute(
            """
            SELECT COUNT(*) FROM inline_capture_intents
            WHERE requester_id = ? AND status = 'pending'
            """,
            (42,),
        ).fetchone()[0] == 3


def _table_names(db: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in db.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def _table_counts(
    db: sqlite3.Connection,
    table_names: Iterable[str] | None = None,
) -> dict[str, int]:
    names = sorted(table_names if table_names is not None else _table_names(db))
    return {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in names}


def _column_names(db: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table_name})")}


def _index_names(db: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA index_list({table_name})")}
