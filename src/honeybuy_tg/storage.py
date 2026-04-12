import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from honeybuy_tg.models import ItemStatus, Recipe, RecipeIngredient, ShoppingItem


def normalize_item_name(name: str) -> str:
    return " ".join(name.casefold().strip().split())


def normalize_recipe_lookup_name(name: str) -> str:
    normalized = normalize_item_name(name)
    if len(normalized) > 3 and normalized[-1] in {"а", "е", "и", "у", "ы", "ю", "я"}:
        return normalized[:-1]
    return normalized


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def init(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS authorized_chats (
                    chat_id INTEGER PRIMARY KEY,
                    chat_type TEXT NOT NULL,
                    title TEXT,
                    authorized_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shopping_items (
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
                );

                CREATE INDEX IF NOT EXISTS idx_shopping_items_chat_status
                    ON shopping_items (chat_id, status);

                CREATE INDEX IF NOT EXISTS idx_shopping_items_chat_name
                    ON shopping_items (chat_id, normalized_name);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    telegram_message_id INTEGER,
                    input_type TEXT NOT NULL,
                    raw_text TEXT,
                    ai_result_json TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_messages (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    item_ids TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, message_id)
                );

                CREATE TABLE IF NOT EXISTS pending_confirmations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    items_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_pending_confirmations_chat_status
                    ON pending_confirmations (chat_id, status);

                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    text_parse_mode TEXT NOT NULL,
                    updated_by INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS category_cache (
                    normalized_name TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shop_sessions (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    item_text TEXT NOT NULL,
                    checked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, message_id, item_id)
                );

                CREATE TABLE IF NOT EXISTS recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    source_url TEXT,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (chat_id, normalized_name)
                );

                CREATE TABLE IF NOT EXISTS recipe_ingredients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipe_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    quantity_text TEXT,
                    position INTEGER NOT NULL,
                    FOREIGN KEY (recipe_id) REFERENCES recipes (id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_recipes_chat_name
                    ON recipes (chat_id, normalized_name);
                """
            )
            db.commit()

    async def authorize_chat(
        self,
        *,
        chat_id: int,
        chat_type: str,
        title: str | None,
        authorized_by: int,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO authorized_chats (
                    chat_id, chat_type, title, authorized_by, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_type = excluded.chat_type,
                    title = excluded.title,
                    authorized_by = excluded.authorized_by
                """,
                (chat_id, chat_type, title, authorized_by, utc_now()),
            )
            db.commit()

    async def is_chat_authorized(self, chat_id: int) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "SELECT 1 FROM authorized_chats WHERE chat_id = ?",
                (chat_id,),
            )
            row = cursor.fetchone()
        return row is not None

    async def add_item(
        self,
        *,
        chat_id: int,
        name: str,
        created_by: int,
        quantity: float | None = None,
        unit: str | None = None,
        note: str | None = None,
        due_date: str | None = None,
    ) -> ShoppingItem:
        now = utc_now()
        clean_name = " ".join(name.strip().split())
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO shopping_items (
                    chat_id, name, normalized_name, quantity, unit, note, due_date,
                    status, created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    clean_name,
                    normalize_item_name(clean_name),
                    quantity,
                    unit,
                    note,
                    due_date,
                    ItemStatus.ACTIVE.value,
                    created_by,
                    now,
                    now,
                ),
            )
            db.commit()
            item_id = cursor.lastrowid

        item = await self.get_item(chat_id=chat_id, item_id=item_id)
        if item is None:
            raise RuntimeError("Inserted item could not be loaded")
        return item

    async def get_item(self, *, chat_id: int, item_id: int) -> ShoppingItem | None:
        with self.connect() as db:
            cursor = db.execute(
                """
                SELECT * FROM shopping_items
                WHERE chat_id = ? AND id = ?
                """,
                (chat_id, item_id),
            )
            row = cursor.fetchone()
        return row_to_item(row) if row is not None else None

    async def list_items(
        self,
        *,
        chat_id: int,
        status: ItemStatus = ItemStatus.ACTIVE,
    ) -> list[ShoppingItem]:
        with self.connect() as db:
            cursor = db.execute(
                """
                SELECT * FROM shopping_items
                WHERE chat_id = ? AND status = ?
                ORDER BY due_date IS NULL, due_date, created_at, id
                """,
                (chat_id, status.value),
            )
            rows = cursor.fetchall()
        return [row_to_item(row) for row in rows]

    async def mark_matching_bought(
        self, *, chat_id: int, name: str
    ) -> list[ShoppingItem]:
        return await self._update_matching_items(
            chat_id=chat_id,
            name=name,
            new_status=ItemStatus.BOUGHT,
        )

    async def remove_matching_items(
        self, *, chat_id: int, name: str
    ) -> list[ShoppingItem]:
        return await self._update_matching_items(
            chat_id=chat_id,
            name=name,
            new_status=ItemStatus.REMOVED,
        )

    async def mark_item_bought(
        self, *, chat_id: int, item_id: int
    ) -> ShoppingItem | None:
        return await self._update_item_by_id(
            chat_id=chat_id,
            item_id=item_id,
            new_status=ItemStatus.BOUGHT,
        )

    async def remove_item(self, *, chat_id: int, item_id: int) -> ShoppingItem | None:
        return await self._update_item_by_id(
            chat_id=chat_id,
            item_id=item_id,
            new_status=ItemStatus.REMOVED,
        )

    async def clear_bought(self, *, chat_id: int) -> int:
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE shopping_items
                SET status = ?, removed_at = ?, updated_at = ?
                WHERE chat_id = ? AND status = ?
                """,
                (
                    ItemStatus.REMOVED.value,
                    utc_now(),
                    utc_now(),
                    chat_id,
                    ItemStatus.BOUGHT.value,
                ),
            )
            db.commit()
            return cursor.rowcount

    async def clear_active_items(self, *, chat_id: int) -> int:
        now = utc_now()
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE shopping_items
                SET status = ?, removed_at = ?, updated_at = ?
                WHERE chat_id = ? AND status = ?
                """,
                (
                    ItemStatus.REMOVED.value,
                    now,
                    now,
                    chat_id,
                    ItemStatus.ACTIVE.value,
                ),
            )
            db.commit()
            return cursor.rowcount

    async def save_recipe(
        self,
        *,
        chat_id: int,
        name: str,
        source_url: str | None,
        created_by: int,
        ingredients: list[tuple[str, str | None]],
    ) -> Recipe:
        now = utc_now()
        clean_name = " ".join(name.strip().split())
        normalized_name = normalize_item_name(clean_name)
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO recipes (
                    chat_id, name, normalized_name, source_url,
                    created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, normalized_name) DO UPDATE SET
                    name = excluded.name,
                    source_url = excluded.source_url,
                    updated_at = excluded.updated_at
                RETURNING id
                """,
                (
                    chat_id,
                    clean_name,
                    normalized_name,
                    source_url,
                    created_by,
                    now,
                    now,
                ),
            )
            recipe_id = cursor.fetchone()["id"]
            db.execute(
                "DELETE FROM recipe_ingredients WHERE recipe_id = ?",
                (recipe_id,),
            )
            db.executemany(
                """
                INSERT INTO recipe_ingredients (
                    recipe_id, name, quantity_text, position
                )
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        recipe_id,
                        " ".join(ingredient_name.strip().split()),
                        " ".join(quantity.strip().split()) if quantity else None,
                        position,
                    )
                    for position, (ingredient_name, quantity) in enumerate(
                        ingredients,
                        start=1,
                    )
                    if ingredient_name.strip()
                ],
            )
            db.commit()

        recipe = await self.get_recipe(chat_id=chat_id, name=clean_name)
        if recipe is None:
            raise RuntimeError("Saved recipe could not be loaded")
        return recipe

    async def get_recipe(self, *, chat_id: int, name: str) -> Recipe | None:
        with self.connect() as db:
            cursor = db.execute(
                """
                SELECT * FROM recipes
                WHERE chat_id = ? AND normalized_name = ?
                """,
                (chat_id, normalize_item_name(name)),
            )
            row = cursor.fetchone()
            if row is None:
                row = self._find_recipe_by_lookup_name(
                    db=db,
                    chat_id=chat_id,
                    name=name,
                )
                if row is None:
                    return None
            ingredients = db.execute(
                """
                SELECT * FROM recipe_ingredients
                WHERE recipe_id = ?
                ORDER BY position, id
                """,
                (row["id"],),
            ).fetchall()
        return row_to_recipe(row, ingredients)

    def _find_recipe_by_lookup_name(
        self,
        *,
        db: sqlite3.Connection,
        chat_id: int,
        name: str,
    ) -> sqlite3.Row | None:
        lookup_name = normalize_recipe_lookup_name(name)
        cursor = db.execute(
            """
            SELECT * FROM recipes
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        for row in cursor.fetchall():
            if normalize_recipe_lookup_name(row["name"]) == lookup_name:
                return row
        return None

    async def list_recipes(self, *, chat_id: int) -> list[Recipe]:
        with self.connect() as db:
            cursor = db.execute(
                """
                SELECT * FROM recipes
                WHERE chat_id = ?
                ORDER BY name
                """,
                (chat_id,),
            )
            rows = cursor.fetchall()
            recipe_ids = [row["id"] for row in rows]
            if not recipe_ids:
                return []
            placeholders = ",".join("?" for _ in recipe_ids)
            ingredient_rows = db.execute(
                f"""
                SELECT * FROM recipe_ingredients
                WHERE recipe_id IN ({placeholders})
                ORDER BY recipe_id, position, id
                """,
                recipe_ids,
            ).fetchall()

        ingredients_by_recipe_id: dict[int, list[sqlite3.Row]] = {}
        for ingredient_row in ingredient_rows:
            ingredients_by_recipe_id.setdefault(ingredient_row["recipe_id"], []).append(
                ingredient_row
            )
        return [
            row_to_recipe(row, ingredients_by_recipe_id.get(row["id"], []))
            for row in rows
        ]

    async def save_bot_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        kind: str,
        item_ids: str | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO bot_messages (
                    chat_id, message_id, kind, item_ids, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, message_id, kind, item_ids, utc_now()),
            )
            db.commit()

    async def get_bot_message(
        self,
        *,
        chat_id: int,
        message_id: int,
    ) -> sqlite3.Row | None:
        with self.connect() as db:
            cursor = db.execute(
                """
                SELECT * FROM bot_messages
                WHERE chat_id = ? AND message_id = ?
                """,
                (chat_id, message_id),
            )
            return cursor.fetchone()

    async def get_latest_bot_message(
        self,
        *,
        chat_id: int,
        kind: str,
    ) -> sqlite3.Row | None:
        with self.connect() as db:
            cursor = db.execute(
                """
                SELECT * FROM bot_messages
                WHERE chat_id = ? AND kind = ?
                ORDER BY created_at DESC, message_id DESC
                LIMIT 1
                """,
                (chat_id, kind),
            )
            return cursor.fetchone()

    async def log_event(
        self,
        *,
        chat_id: int,
        user_id: int,
        input_type: str,
        status: str,
        telegram_message_id: int | None = None,
        raw_text: str | None = None,
        ai_result_json: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO events (
                    chat_id, user_id, telegram_message_id, input_type, raw_text,
                    ai_result_json, status, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    user_id,
                    telegram_message_id,
                    input_type,
                    raw_text,
                    ai_result_json,
                    status,
                    error,
                    utc_now(),
                ),
            )
            db.commit()

    async def create_pending_confirmation(
        self,
        *,
        chat_id: int,
        user_id: int,
        source_message_id: int,
        items_json: str,
    ) -> int:
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO pending_confirmations (
                    chat_id, user_id, source_message_id, items_json, status, created_at
                )
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (chat_id, user_id, source_message_id, items_json, utc_now()),
            )
            db.commit()
            return cursor.lastrowid

    async def get_pending_confirmation(
        self,
        *,
        confirmation_id: int,
        chat_id: int,
    ) -> sqlite3.Row | None:
        with self.connect() as db:
            cursor = db.execute(
                """
                SELECT * FROM pending_confirmations
                WHERE id = ? AND chat_id = ? AND status = 'pending'
                """,
                (confirmation_id, chat_id),
            )
            return cursor.fetchone()

    async def resolve_pending_confirmation(
        self,
        *,
        confirmation_id: int,
        chat_id: int,
        status: str,
    ) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE pending_confirmations
                SET status = ?, resolved_at = ?
                WHERE id = ? AND chat_id = ? AND status = 'pending'
                """,
                (status, utc_now(), confirmation_id, chat_id),
            )
            db.commit()
            return cursor.rowcount > 0

    async def set_chat_text_parse_mode(
        self,
        *,
        chat_id: int,
        mode: str,
        updated_by: int,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO chat_settings (
                    chat_id, text_parse_mode, updated_by, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    text_parse_mode = excluded.text_parse_mode,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (chat_id, mode, updated_by, utc_now()),
            )
            db.commit()

    async def get_chat_text_parse_mode(self, *, chat_id: int) -> str | None:
        with self.connect() as db:
            cursor = db.execute(
                """
                SELECT text_parse_mode FROM chat_settings
                WHERE chat_id = ?
                """,
                (chat_id,),
            )
            row = cursor.fetchone()
        return row["text_parse_mode"] if row is not None else None

    async def get_cached_categories(self, names: list[str]) -> dict[str, str]:
        normalized_names = sorted({normalize_item_name(name) for name in names if name})
        if not normalized_names:
            return {}

        placeholders = ",".join("?" for _ in normalized_names)
        with self.connect() as db:
            cursor = db.execute(
                f"""
                SELECT normalized_name, category FROM category_cache
                WHERE normalized_name IN ({placeholders}) AND expires_at > ?
                """,
                (*normalized_names, utc_now()),
            )
            rows = cursor.fetchall()
        return {row["normalized_name"]: row["category"] for row in rows}

    async def set_cached_categories(
        self,
        *,
        categories_by_name: dict[str, str],
        ttl_seconds: int,
    ) -> None:
        if not categories_by_name:
            return

        now = datetime.now(UTC)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat(
            timespec="seconds"
        )
        with self.connect() as db:
            db.executemany(
                """
                INSERT INTO category_cache (
                    normalized_name, category, expires_at, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(normalized_name) DO UPDATE SET
                    category = excluded.category,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        normalize_item_name(name),
                        category,
                        expires_at,
                        now.isoformat(timespec="seconds"),
                    )
                    for name, category in categories_by_name.items()
                ],
            )
            db.commit()

    async def create_shop_session(
        self,
        *,
        chat_id: int,
        message_id: int,
        items: list[tuple[int, str]],
    ) -> None:
        now = utc_now()
        with self.connect() as db:
            db.executemany(
                """
                INSERT OR REPLACE INTO shop_sessions (
                    chat_id, message_id, item_id, item_text, checked,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                [
                    (chat_id, message_id, item_id, item_text, now, now)
                    for item_id, item_text in items
                ],
            )
            db.commit()

    async def get_shop_session_items(
        self,
        *,
        chat_id: int,
        message_id: int,
    ) -> list[sqlite3.Row]:
        with self.connect() as db:
            cursor = db.execute(
                """
                SELECT item_id, item_text, checked FROM shop_sessions
                WHERE chat_id = ? AND message_id = ?
                ORDER BY rowid
                """,
                (chat_id, message_id),
            )
            return cursor.fetchall()

    async def set_shop_session_item_checked(
        self,
        *,
        chat_id: int,
        message_id: int,
        item_id: int,
        checked: bool,
    ) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE shop_sessions
                SET checked = ?, updated_at = ?
                WHERE chat_id = ? AND message_id = ? AND item_id = ?
                """,
                (1 if checked else 0, utc_now(), chat_id, message_id, item_id),
            )
            db.commit()
            return cursor.rowcount > 0

    async def _update_matching_items(
        self,
        *,
        chat_id: int,
        name: str,
        new_status: ItemStatus,
    ) -> list[ShoppingItem]:
        normalized_name = normalize_item_name(name)
        active_items = await self.list_items(chat_id=chat_id)
        matched_ids = [
            item.id
            for item in active_items
            if item.normalized_name == normalized_name
            or normalized_name in item.normalized_name
            or item.normalized_name in normalized_name
        ]
        updated: list[ShoppingItem] = []
        for item_id in matched_ids:
            item = await self._update_item_by_id(
                chat_id=chat_id,
                item_id=item_id,
                new_status=new_status,
            )
            if item is not None:
                updated.append(item)
        return updated

    async def _update_item_by_id(
        self,
        *,
        chat_id: int,
        item_id: int,
        new_status: ItemStatus,
    ) -> ShoppingItem | None:
        now = utc_now()
        bought_at = now if new_status == ItemStatus.BOUGHT else None
        removed_at = now if new_status == ItemStatus.REMOVED else None
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE shopping_items
                SET status = ?,
                    bought_at = COALESCE(?, bought_at),
                    removed_at = COALESCE(?, removed_at),
                    updated_at = ?
                WHERE chat_id = ? AND id = ? AND status = ?
                """,
                (
                    new_status.value,
                    bought_at,
                    removed_at,
                    now,
                    chat_id,
                    item_id,
                    ItemStatus.ACTIVE.value,
                ),
            )
            db.commit()
            if cursor.rowcount == 0:
                return None
        return await self.get_item(chat_id=chat_id, item_id=item_id)


def row_to_item(row: sqlite3.Row) -> ShoppingItem:
    values: dict[str, Any] = dict(row)
    return ShoppingItem(
        id=values["id"],
        chat_id=values["chat_id"],
        name=values["name"],
        normalized_name=values["normalized_name"],
        quantity=values["quantity"],
        unit=values["unit"],
        note=values["note"],
        due_date=values["due_date"],
        status=ItemStatus(values["status"]),
        created_by=values["created_by"],
        created_at=datetime.fromisoformat(values["created_at"]),
        updated_at=datetime.fromisoformat(values["updated_at"]),
        bought_at=parse_dt(values["bought_at"]),
        removed_at=parse_dt(values["removed_at"]),
    )


def row_to_recipe(
    row: sqlite3.Row,
    ingredient_rows: list[sqlite3.Row],
) -> Recipe:
    values: dict[str, Any] = dict(row)
    return Recipe(
        id=values["id"],
        chat_id=values["chat_id"],
        name=values["name"],
        normalized_name=values["normalized_name"],
        source_url=values["source_url"],
        created_by=values["created_by"],
        created_at=datetime.fromisoformat(values["created_at"]),
        updated_at=datetime.fromisoformat(values["updated_at"]),
        ingredients=tuple(row_to_recipe_ingredient(row) for row in ingredient_rows),
    )


def row_to_recipe_ingredient(row: sqlite3.Row) -> RecipeIngredient:
    values: dict[str, Any] = dict(row)
    return RecipeIngredient(
        id=values["id"],
        recipe_id=values["recipe_id"],
        name=values["name"],
        quantity_text=values["quantity_text"],
        position=values["position"],
    )
