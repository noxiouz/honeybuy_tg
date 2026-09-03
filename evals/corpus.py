from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_CORPUS_PATH = Path(__file__).resolve().parent / "cases" / "text_routing.v1.jsonl"

ShoppingAction = Literal["add_items", "remove_items", "mark_bought", "show_list"]
Route = Literal["shopping", "recipe", "unhandled", "ambiguous"]
Action = Literal[
    "add_items",
    "remove_items",
    "mark_bought",
    "show_list",
    "learn_recipe",
    "add_recipe",
    "unknown",
]


def normalize_eval_text(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


class ExpectedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    route: Route
    action: Action
    accepted_items: list[list[str]] | None = None
    recipe_name: str | None = None
    accepted_recipe_names: list[str] | None = None
    ambiguity_reason: str | None = None

    @field_validator("accepted_items")
    @classmethod
    def validate_accepted_items(
        cls,
        variants: list[list[str]] | None,
    ) -> list[list[str]] | None:
        if variants is None:
            return None
        if not variants:
            raise ValueError("accepted_items must contain at least one variant")
        for variant in variants:
            if not variant or any(not item.strip() for item in variant):
                raise ValueError("each accepted_items variant needs nonblank items")
            normalized = tuple(sorted(normalize_eval_text(item) for item in variant))
            if len(normalized) != len(set(normalized)):
                raise ValueError("an accepted_items variant contains duplicate items")
        normalized_variants = {
            tuple(sorted(normalize_eval_text(item) for item in variant))
            for variant in variants
        }
        if len(normalized_variants) != len(variants):
            raise ValueError("duplicate accepted_items variant")
        return variants

    @field_validator("accepted_recipe_names")
    @classmethod
    def validate_accepted_recipe_names(
        cls,
        names: list[str] | None,
    ) -> list[str] | None:
        if names is None:
            return None
        if not names:
            raise ValueError("accepted_recipe_names must contain at least one name")
        normalized = [normalize_eval_text(name) for name in names if name.strip()]
        if len(normalized) != len(names):
            raise ValueError("accepted_recipe_names must contain nonblank names")
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate accepted_recipe_names value")
        return names

    @model_validator(mode="after")
    def validate_route_contract(self) -> "ExpectedResult":
        shopping_actions: set[str] = {
            "add_items",
            "remove_items",
            "mark_bought",
            "show_list",
        }
        if self.route == "shopping":
            if self.action not in shopping_actions:
                raise ValueError("shopping route requires a shopping action")
            if self.action == "show_list" and self.accepted_items is not None:
                raise ValueError("show_list must not declare accepted_items")
            if self.action != "show_list" and self.accepted_items is None:
                raise ValueError("mutating shopping actions require accepted_items")
            if (
                self.recipe_name is not None
                or self.accepted_recipe_names is not None
                or self.ambiguity_reason is not None
            ):
                raise ValueError("shopping result contains recipe/ambiguity fields")
        elif self.route == "recipe":
            if self.action not in {"learn_recipe", "add_recipe"}:
                raise ValueError("recipe route requires a recipe action")
            if self.recipe_name is None or not self.recipe_name.strip():
                raise ValueError("recipe route requires recipe_name")
            if self.accepted_items is not None or self.ambiguity_reason is not None:
                raise ValueError("recipe result contains shopping/ambiguity fields")
            if self.accepted_recipe_names is not None:
                canonical = normalize_eval_text(self.recipe_name)
                alternatives = {
                    normalize_eval_text(name) for name in self.accepted_recipe_names
                }
                if canonical in alternatives:
                    raise ValueError("accepted_recipe_names duplicates recipe_name")
        elif self.route == "unhandled":
            if self.action != "unknown":
                raise ValueError("unhandled route requires unknown action")
            if any(
                value is not None
                for value in (
                    self.accepted_items,
                    self.recipe_name,
                    self.accepted_recipe_names,
                    self.ambiguity_reason,
                )
            ):
                raise ValueError("unhandled result must not infer extra semantics")
        else:
            if self.action != "unknown":
                raise ValueError("ambiguous route requires unknown action")
            if self.ambiguity_reason is None or not self.ambiguity_reason.strip():
                raise ValueError("ambiguous route requires an explicit reason")
            if (
                self.accepted_items is not None
                or self.recipe_name is not None
                or self.accepted_recipe_names is not None
            ):
                raise ValueError("ambiguous result must not guess items or recipe")
        return self


class RoutingCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    text: str = Field(min_length=1)
    reply_text: str | None = None
    locale: Literal["ru", "en", "mixed"]
    channel: Literal["text", "mention", "voice_transcript"]
    critical: bool
    tags: list[str] = Field(min_length=1)
    expected: ExpectedResult

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @field_validator("reply_text")
    @classmethod
    def reject_blank_reply_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("reply_text must not be blank")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: list[str]) -> list[str]:
        if any(not tag or normalize_eval_text(tag) != tag for tag in tags):
            raise ValueError("tags must be normalized nonblank labels")
        if len(tags) != len(set(tags)):
            raise ValueError("tags must be unique within a case")
        return tags

    @model_validator(mode="after")
    def validate_context(self) -> "RoutingCase":
        if self.reply_text is not None and self.channel != "mention":
            raise ValueError("reply_text is only valid for mention cases")
        route_tag = (
            "ambiguous" if self.expected.route == "ambiguous" else self.expected.route
        )
        if route_tag not in self.tags:
            raise ValueError(f"case must include its route tag: {route_tag}")
        return self


@dataclass(frozen=True)
class Corpus:
    path: Path
    cases: tuple[RoutingCase, ...]
    sha256: str


def load_corpus(path: str | Path = DEFAULT_CORPUS_PATH) -> Corpus:
    corpus_path = Path(path)
    content = corpus_path.read_bytes()
    cases: list[RoutingCase] = []
    ids: set[str] = set()
    for line_number, raw_line in enumerate(
        content.decode("utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            raise ValueError(f"blank JSONL record at line {line_number}")
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON at line {line_number}: {error}") from error
        try:
            case = RoutingCase.model_validate(payload)
        except ValueError as error:
            raise ValueError(f"invalid case at line {line_number}: {error}") from error
        if case.id in ids:
            raise ValueError(f"duplicate case id at line {line_number}: {case.id}")
        ids.add(case.id)
        cases.append(case)
    return Corpus(
        path=corpus_path,
        cases=tuple(cases),
        sha256=hashlib.sha256(content).hexdigest(),
    )
