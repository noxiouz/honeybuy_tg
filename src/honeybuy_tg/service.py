import re
from typing import Protocol

from honeybuy_tg.models import ItemIdentity, Recipe, ShoppingItem
from honeybuy_tg.storage import Storage, normalize_item_name


class ItemNormalizer(Protocol):
    async def normalize(self, names: list[str]) -> dict[str, ItemIdentity]: ...


class ShoppingListService:
    def __init__(
        self,
        storage: Storage,
        *,
        item_normalizer: ItemNormalizer | None = None,
        normalization_cache_ttl_seconds: int = 7_776_000,
    ) -> None:
        self.storage = storage
        self.item_normalizer = item_normalizer
        self.normalization_cache_ttl_seconds = normalization_cache_ttl_seconds

    async def add_item(self, *, chat_id: int, name: str, user_id: int) -> ShoppingItem:
        clean_name = " ".join(name.strip().split())
        if not clean_name:
            raise ValueError("Item name is required")
        identity = await self.item_identity(clean_name)
        return await self.storage.add_item(
            chat_id=chat_id,
            name=clean_name,
            created_by=user_id,
            canonical_name=identity.canonical_name,
            canonical_key=identity.canonical_key,
        )

    async def remove_by_name(self, *, chat_id: int, name: str) -> list[ShoppingItem]:
        clean_name = " ".join(name.strip().split())
        if not clean_name:
            raise ValueError("Item name is required")
        return await self.update_matching_items_by_name(
            chat_id=chat_id,
            name=clean_name,
            action="remove",
        )

    async def mark_bought_by_name(
        self, *, chat_id: int, name: str
    ) -> list[ShoppingItem]:
        clean_name = " ".join(name.strip().split())
        if not clean_name:
            raise ValueError("Item name is required")
        return await self.update_matching_items_by_name(
            chat_id=chat_id,
            name=clean_name,
            action="bought",
        )

    async def list_active(self, *, chat_id: int) -> list[ShoppingItem]:
        return await self.storage.list_items(chat_id=chat_id)

    async def list_active_deduplicated(self, *, chat_id: int) -> list[ShoppingItem]:
        items = await self.list_active_with_identities(chat_id=chat_id)
        removed_ids = await self.remove_duplicate_active_items(
            chat_id=chat_id,
            items=items,
        )
        if not removed_ids:
            return items
        return [item for item in items if item.id not in removed_ids]

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

    async def save_recipe(
        self,
        *,
        chat_id: int,
        name: str,
        source_url: str | None,
        user_id: int,
        ingredients: list[tuple[str, str | None]],
    ) -> Recipe:
        clean_name = name.strip()
        clean_ingredients = [
            (ingredient_name.strip(), quantity.strip() if quantity else None)
            for ingredient_name, quantity in ingredients
            if ingredient_name.strip()
        ]
        if not clean_name:
            raise ValueError("Recipe name is required")
        if not clean_ingredients:
            raise ValueError("Recipe must have ingredients")
        identities = await self.item_identities(
            [ingredient_name for ingredient_name, _ in clean_ingredients]
        )
        return await self.storage.save_recipe(
            chat_id=chat_id,
            name=clean_name,
            source_url=source_url,
            created_by=user_id,
            ingredients=[
                (
                    ingredient_name,
                    quantity,
                    identities[ingredient_name].canonical_name,
                    identities[ingredient_name].canonical_key,
                )
                for ingredient_name, quantity in clean_ingredients
            ],
        )

    async def get_recipe(self, *, chat_id: int, name: str) -> Recipe | None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Recipe name is required")
        return await self.storage.get_recipe(chat_id=chat_id, name=clean_name)

    async def list_recipes(self, *, chat_id: int) -> list[Recipe]:
        return await self.storage.list_recipes(chat_id=chat_id)

    async def delete_recipe(self, *, chat_id: int, name: str) -> Recipe | None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Recipe name is required")
        return await self.storage.delete_recipe(chat_id=chat_id, name=clean_name)

    async def add_recipe_ingredients(
        self,
        *,
        chat_id: int,
        recipe: Recipe,
        user_id: int,
    ) -> list[ShoppingItem]:
        added = []
        active_names = set()
        active_keys = set()
        for item in await self.list_active_with_identities(chat_id=chat_id):
            active_names.add(item.normalized_name)
            active_names.add(recipe_ingredient_base_name(item.name))
            if item.canonical_key:
                active_keys.add(item.canonical_key)
        ingredient_names = [
            recipe_ingredient_item_name(
                ingredient.name,
                ingredient.quantity_text,
            )
            for ingredient in recipe.ingredients
            if not ingredient.canonical_key
        ]
        identities = await self.item_identities(ingredient_names)
        for ingredient in recipe.ingredients:
            item_name = recipe_ingredient_item_name(
                ingredient.name,
                ingredient.quantity_text,
            )
            normalized_name = normalize_item_name(item_name)
            base_name = recipe_ingredient_base_name(item_name)
            identity = (
                ItemIdentity(
                    raw_name=ingredient.name,
                    canonical_name=ingredient.canonical_name,
                    canonical_key=ingredient.canonical_key,
                )
                if ingredient.canonical_name and ingredient.canonical_key
                else identities[item_name]
            )
            if (
                normalized_name in active_names
                or base_name in active_names
                or identity.canonical_key in active_keys
            ):
                continue
            added.append(
                await self.add_item(chat_id=chat_id, name=item_name, user_id=user_id)
            )
            active_names.add(normalized_name)
            active_names.add(base_name)
            active_keys.add(identity.canonical_key)
        return added

    async def update_matching_items_by_name(
        self,
        *,
        chat_id: int,
        name: str,
        action: str,
    ) -> list[ShoppingItem]:
        identity = await self.item_identity(name)
        normalized_name = normalize_item_name(name)
        active_items = await self.list_active_with_identities(chat_id=chat_id)
        matched_ids = [
            item.id
            for item in active_items
            if item.normalized_name == normalized_name
            or normalized_name in item.normalized_name
            or item.normalized_name in normalized_name
            or (
                bool(item.canonical_key)
                and item.canonical_key == identity.canonical_key
            )
        ]

        updated = []
        for item_id in matched_ids:
            if action == "remove":
                item = await self.storage.remove_item(chat_id=chat_id, item_id=item_id)
            elif action == "bought":
                item = await self.storage.mark_item_bought(
                    chat_id=chat_id,
                    item_id=item_id,
                )
            else:
                raise ValueError(f"Unknown update action: {action}")
            if item is not None:
                updated.append(item)
        return updated

    async def list_active_with_identities(self, *, chat_id: int) -> list[ShoppingItem]:
        items = await self.list_active(chat_id=chat_id)
        missing_items = [
            item
            for item in items
            if not item.canonical_key
            or (
                self.item_normalizer is not None
                and not is_stable_canonical_key(item.canonical_key)
            )
        ]
        if not missing_items:
            return items

        identities = await self.item_identities([item.name for item in missing_items])
        by_id = {item.id: item for item in items}
        for item in missing_items:
            updated = await self.storage.update_item_identity(
                chat_id=chat_id,
                item_id=item.id,
                identity=identities[item.name],
            )
            if updated is not None:
                by_id[item.id] = updated
        return [by_id[item.id] for item in items]

    async def remove_duplicate_active_items(
        self,
        *,
        chat_id: int,
        items: list[ShoppingItem],
    ) -> set[int]:
        keep_by_key: dict[str, ShoppingItem] = {}
        removed_ids: set[int] = set()
        for item in items:
            if not item.canonical_key:
                continue
            kept = keep_by_key.get(item.canonical_key)
            if kept is None:
                keep_by_key[item.canonical_key] = item
                continue
            if should_replace_duplicate_item(kept=kept, candidate=item):
                removed_ids.add(kept.id)
                keep_by_key[item.canonical_key] = item
            else:
                removed_ids.add(item.id)

        for item_id in removed_ids:
            await self.storage.remove_item(chat_id=chat_id, item_id=item_id)
        return removed_ids

    async def item_identity(self, name: str) -> ItemIdentity:
        clean_name = " ".join(name.strip().split())
        return (await self.item_identities([clean_name]))[clean_name]

    async def item_identities(self, names: list[str]) -> dict[str, ItemIdentity]:
        clean_names = list(dict.fromkeys(" ".join(name.strip().split()) for name in names))
        clean_names = [name for name in clean_names if name]
        if not clean_names:
            return {}

        cached_by_normalized_name = await self.storage.get_cached_item_identities(
            clean_names
        )
        identities: dict[str, ItemIdentity] = {}
        missing_names = []
        for name in clean_names:
            normalized_name = normalize_item_name(name)
            cached = cached_by_normalized_name.get(normalized_name)
            if cached is not None:
                identities[name] = ItemIdentity(
                    raw_name=name,
                    canonical_name=cached.canonical_name,
                    canonical_key=cached.canonical_key,
                )
            else:
                missing_names.append(name)

        if missing_names and self.item_normalizer is not None:
            try:
                normalized = await self.item_normalizer.normalize(missing_names)
            except Exception:
                normalized = {}
            cacheable_names = set()
            normalized_by_name = {
                normalize_item_name(raw_name): identity
                for raw_name, identity in normalized.items()
            }
            for name in missing_names:
                identity = normalized.get(name) or normalized_by_name.get(
                    normalize_item_name(name)
                )
                if identity is not None:
                    identities[name] = clean_item_identity(name, identity)
                    cacheable_names.add(name)
        else:
            cacheable_names = set()

        for name in missing_names:
            identities.setdefault(name, local_item_identity(name))

        await self.storage.set_cached_item_identities(
            identities_by_name={name: identities[name] for name in cacheable_names},
            ttl_seconds=self.normalization_cache_ttl_seconds,
        )
        return identities


def recipe_ingredient_item_name(name: str, quantity_text: str | None) -> str:
    if quantity_text:
        return f"{name}, {quantity_text}"
    return name


def recipe_ingredient_base_name(name: str) -> str:
    return normalize_item_name(name.split(",", 1)[0])


def clean_item_identity(raw_name: str, identity: ItemIdentity) -> ItemIdentity:
    canonical_name = " ".join(identity.canonical_name.strip().split())
    canonical_key = clean_canonical_key(identity.canonical_key)
    if not canonical_name or not canonical_key:
        return local_item_identity(raw_name)
    return ItemIdentity(
        raw_name=raw_name,
        canonical_name=canonical_name,
        canonical_key=canonical_key,
    )


def clean_canonical_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", value.strip().casefold())
    key = re.sub(r"_+", "_", key).strip("_")
    return key


def is_stable_canonical_key(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", value))


def should_replace_duplicate_item(
    *,
    kept: ShoppingItem,
    candidate: ShoppingItem,
) -> bool:
    return duplicate_keep_score(candidate) > duplicate_keep_score(kept)


def duplicate_keep_score(item: ShoppingItem) -> tuple[int, int]:
    score = 0
    if item.quantity is not None:
        score += 4
    if item.unit:
        score += 2
    if re.search(r"\d", item.name):
        score += 4
    if "," in item.name:
        score += 2
    return score, len(item.name)


def local_item_identity(name: str) -> ItemIdentity:
    canonical_name = " ".join(name.split(",", 1)[0].strip().split()) or name.strip()
    canonical_key = clean_canonical_key(normalize_item_name(canonical_name))
    if not canonical_key:
        canonical_key = normalize_item_name(canonical_name)
    return ItemIdentity(
        raw_name=name,
        canonical_name=canonical_name,
        canonical_key=canonical_key,
    )
