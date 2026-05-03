from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ItemStatus(StrEnum):
    ACTIVE = "active"
    BOUGHT = "bought"
    REMOVED = "removed"


@dataclass(frozen=True)
class ShoppingItem:
    id: int
    chat_id: int
    name: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    note: str | None
    due_date: str | None
    status: ItemStatus
    created_by: int
    created_at: datetime
    updated_at: datetime
    bought_at: datetime | None
    removed_at: datetime | None
    canonical_name: str | None = None
    canonical_key: str | None = None


@dataclass(frozen=True)
class ItemIdentity:
    raw_name: str
    canonical_name: str
    canonical_key: str


@dataclass(frozen=True)
class RecipeIngredient:
    id: int
    recipe_id: int
    name: str
    quantity_text: str | None
    position: int
    canonical_name: str | None = None
    canonical_key: str | None = None


@dataclass(frozen=True)
class Recipe:
    id: int
    chat_id: int
    name: str
    normalized_name: str
    source_url: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime
    aliases: tuple[str, ...] = ()
    ingredients: tuple[RecipeIngredient, ...] = ()
