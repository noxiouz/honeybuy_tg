from __future__ import annotations

from dataclasses import dataclass

from evals.corpus import RoutingCase, normalize_eval_text


@dataclass(frozen=True)
class ActualResult:
    route: str
    action: str
    items: tuple[str, ...] = ()
    recipe_name: str | None = None

    def signature(self) -> tuple[object, ...]:
        return (
            self.route,
            self.action,
            tuple(sorted(normalize_eval_text(item) for item in self.items)),
            normalize_eval_text(self.recipe_name) if self.recipe_name else None,
        )


@dataclass(frozen=True)
class CaseGrade:
    case_id: str
    route_pass: bool
    action_pass: bool
    items_pass: bool | None
    recipe_name_pass: bool | None

    @property
    def passed(self) -> bool:
        return all(
            result is not False
            for result in (
                self.route_pass,
                self.action_pass,
                self.items_pass,
                self.recipe_name_pass,
            )
        )


def grade_case(case: RoutingCase, actual: ActualResult) -> CaseGrade:
    expected_route = (
        "unhandled" if case.expected.route == "ambiguous" else case.expected.route
    )
    route_pass = actual.route == expected_route
    action_pass = actual.action == case.expected.action
    items_pass = None
    recipe_name_pass = None
    if case.expected.accepted_items is not None:
        items_pass = items_match(actual.items, case.expected.accepted_items)
    if case.expected.recipe_name is not None:
        accepted_names = {normalize_eval_text(case.expected.recipe_name)}
        if case.expected.accepted_recipe_names is not None:
            accepted_names.update(
                normalize_eval_text(name)
                for name in case.expected.accepted_recipe_names
            )
        recipe_name_pass = actual.recipe_name is not None and (
            normalize_eval_text(actual.recipe_name) in accepted_names
        )
    return CaseGrade(
        case_id=case.id,
        route_pass=route_pass,
        action_pass=action_pass,
        items_pass=items_pass,
        recipe_name_pass=recipe_name_pass,
    )


def items_match(items: tuple[str, ...], accepted_items: list[list[str]]) -> bool:
    normalized_items = tuple(sorted(normalize_eval_text(item) for item in items))
    return any(
        normalized_items
        == tuple(sorted(normalize_eval_text(item) for item in variant))
        for variant in accepted_items
    )
