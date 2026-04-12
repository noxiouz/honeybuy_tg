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
