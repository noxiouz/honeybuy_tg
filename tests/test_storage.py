import pytest

from honeybuy_tg.models import ItemStatus
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
