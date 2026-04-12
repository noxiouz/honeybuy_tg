from honeybuy_tg.models import ShoppingItem
from honeybuy_tg.storage import Storage


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
