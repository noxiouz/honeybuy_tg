import sqlite3
from dataclasses import dataclass
from pathlib import Path


CURRENT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MigrationResult:
    database_path: Path | None
    old_version: int
    new_version: int
    applied_versions: tuple[int, ...]
    integrity_check: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.applied_versions)


SCHEMA_SQL = """
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

CREATE INDEX IF NOT EXISTS idx_shopping_items_chat_status
    ON shopping_items (chat_id, status);

CREATE INDEX IF NOT EXISTS idx_shopping_items_chat_name
    ON shopping_items (chat_id, normalized_name);

CREATE INDEX IF NOT EXISTS idx_shopping_items_chat_canonical_key
    ON shopping_items (chat_id, canonical_key);

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

CREATE TABLE IF NOT EXISTS item_normalization_cache (
    raw_normalized_name TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shop_sessions (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    item_text TEXT NOT NULL,
    category TEXT,
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
    canonical_name TEXT,
    canonical_key TEXT,
    position INTEGER NOT NULL,
    FOREIGN KEY (recipe_id) REFERENCES recipes (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS recipe_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    recipe_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (chat_id, normalized_alias),
    FOREIGN KEY (recipe_id) REFERENCES recipes (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recipes_chat_name
    ON recipes (chat_id, normalized_name);

CREATE INDEX IF NOT EXISTS idx_recipe_aliases_recipe
    ON recipe_aliases (recipe_id);
"""

SCHEMA_STATEMENTS = tuple(
    statement.strip() for statement in SCHEMA_SQL.split(";") if statement.strip()
)


def migrate_database_path(database_path: Path) -> MigrationResult:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        result = run_migrations(db, database_path=database_path)
        integrity_check = _scalar(db.execute("PRAGMA integrity_check").fetchone())
        if integrity_check != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity_check}")
        return MigrationResult(
            database_path=result.database_path,
            old_version=result.old_version,
            new_version=result.new_version,
            applied_versions=result.applied_versions,
            integrity_check=str(integrity_check),
        )


def run_migrations(
    db: sqlite3.Connection,
    *,
    database_path: Path | None = None,
) -> MigrationResult:
    applied_versions: list[int] = []
    started_transaction = not db.in_transaction
    if started_transaction:
        db.execute("BEGIN IMMEDIATE")
    try:
        old_version = get_user_version(db)
        if old_version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                "Database schema version "
                f"{old_version} is newer than supported version {CURRENT_SCHEMA_VERSION}"
            )

        if old_version < 1:
            _migrate_to_1(db)
            _set_user_version(db, 1)
            applied_versions.append(1)

        new_version = get_user_version(db)
        if started_transaction:
            db.commit()
    except Exception:
        if started_transaction:
            db.rollback()
        raise

    return MigrationResult(
        database_path=database_path,
        old_version=old_version,
        new_version=new_version,
        applied_versions=tuple(applied_versions),
    )


def get_user_version(db: sqlite3.Connection) -> int:
    return int(_scalar(db.execute("PRAGMA user_version").fetchone()))


def _migrate_to_1(db: sqlite3.Connection) -> None:
    _ensure_column_if_table_exists(db, "shopping_items", "canonical_name", "TEXT")
    _ensure_column_if_table_exists(db, "shopping_items", "canonical_key", "TEXT")
    for statement in SCHEMA_STATEMENTS:
        db.execute(statement)
    _ensure_column(db, "shopping_items", "canonical_name", "TEXT")
    _ensure_column(db, "shopping_items", "canonical_key", "TEXT")
    _ensure_column(db, "recipe_ingredients", "canonical_name", "TEXT")
    _ensure_column(db, "recipe_ingredients", "canonical_key", "TEXT")
    _ensure_column(db, "shop_sessions", "category", "TEXT")


def _ensure_column(
    db: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = {
        _row_value(row, "name", 1)
        for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        db.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )


def _ensure_column_if_table_exists(
    db: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    if _table_exists(db, table_name):
        _ensure_column(db, table_name, column_name, column_definition)


def _table_exists(db: sqlite3.Connection, table_name: str) -> bool:
    return db.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone() is not None


def _set_user_version(db: sqlite3.Connection, version: int) -> None:
    if version < 0:
        raise ValueError("SQLite user_version cannot be negative")
    db.execute(f"PRAGMA user_version = {version}")


def _scalar(row: sqlite3.Row | tuple[object, ...] | None) -> object:
    if row is None:
        raise RuntimeError("SQLite pragma returned no rows")
    return _row_value(row, 0, 0)


def _row_value(
    row: sqlite3.Row | tuple[object, ...],
    key: str | int,
    index: int,
) -> object:
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row[index]
