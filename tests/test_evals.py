import json
import os
from pathlib import Path

import pytest

from evals.corpus import Corpus, RoutingCase, load_corpus
from evals.grading import ActualResult, grade_case, items_match
import evals.live_compare as live_compare
from honeybuy_tg.prompts import RECIPE_COMMAND_PROMPT, SHOPPING_TEXT_PROMPT
from honeybuy_tg.telegram_bot import strip_bot_mention


SHIPPED_CASES = load_corpus().cases
AI_RECIPE_CASES = [case for case in SHIPPED_CASES if "ai-route" in case.tags]
DETERMINISTIC_RECIPE_CASES = [
    case for case in SHIPPED_CASES if "deterministic" in case.tags
]


def make_case(
    *,
    case_id="case-001",
    text="купи молоко",
    route="shopping",
    action="add_items",
    accepted_items=None,
    recipe_name=None,
    critical=False,
    channel="text",
    reply_text=None,
    ambiguity_reason=None,
):
    expected = {"route": route, "action": action}
    if route == "shopping" and action != "show_list":
        expected["accepted_items"] = accepted_items or [["молоко"]]
    if route == "recipe":
        expected["recipe_name"] = recipe_name or "солянка"
    if route == "ambiguous":
        expected["ambiguity_reason"] = ambiguity_reason or "Нужно уточнение."
    payload = {
        "id": case_id,
        "text": text,
        "locale": "ru",
        "channel": channel,
        "critical": critical,
        "tags": ["ambiguous" if route == "ambiguous" else route, "test"],
        "expected": expected,
    }
    if reply_text is not None:
        payload["reply_text"] = reply_text
    return RoutingCase.model_validate(payload)


def make_corpus(*cases: RoutingCase) -> Corpus:
    return Corpus(
        path=Path("offline-corpus.jsonl"),
        cases=tuple(cases),
        sha256="0" * 64,
    )


class FakeModelResponse:
    def __init__(
        self,
        output_text: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self.output_text = output_text
        self.usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


class FakeResponses:
    def __init__(self, responses):
        self.queued = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.queued:
            raise AssertionError("unexpected model request")
        result = self.queued.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeEvalClient:
    def __init__(self, responses):
        self.responses = FakeResponses(responses)
        self.closed = False

    async def close(self):
        self.closed = True


def model_response(payload, *, input_tokens=0, output_tokens=0):
    return FakeModelResponse(
        json.dumps(payload, ensure_ascii=False),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def corpus_case(case_id: str) -> RoutingCase:
    return next(case for case in SHIPPED_CASES if case.id == case_id)


@pytest.mark.parametrize(
    "case",
    AI_RECIPE_CASES,
    ids=lambda case: case.id,
)
def test_ai_route_corpus_cases_cannot_short_circuit_to_deterministic(case):
    text = live_compare.command_text_for_case(
        case,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )

    assert live_compare.deterministic_recipe_result(text) is None


@pytest.mark.parametrize(
    "case",
    DETERMINISTIC_RECIPE_CASES,
    ids=lambda case: case.id,
)
def test_deterministic_corpus_tags_match_execution_and_expected_result(case):
    text = live_compare.command_text_for_case(
        case,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )

    actual = live_compare.deterministic_recipe_result(text)

    assert actual is not None
    assert actual.route == case.expected.route
    assert actual.action == case.expected.action
    assert grade_case(case, actual).passed


@pytest.mark.parametrize(
    "case",
    [case for case in SHIPPED_CASES if case.channel == "mention"],
    ids=lambda case: case.id,
)
def test_eval_mention_preprocessing_matches_production(case):
    production_text = strip_bot_mention(
        case.text,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )
    if not production_text:
        production_text = case.reply_text or production_text

    assert live_compare.command_text_for_case(
        case,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    ) == production_text


def test_item_grading_uses_normalized_unordered_multiset_alternatives():
    accepted = [
        ["Молоко", "Хлеб"],
        ["Ёжевичное варенье"],
    ]

    assert items_match((" хлеб ", "МОЛОКО"), accepted)
    assert items_match(("  ЕЖЕВИЧНОЕ   ВАРЕНЬЕ ",), accepted)
    assert items_match(("milk", "milk"), [["MILK", " milk "]])
    assert not items_match(("milk",), [["milk", "milk"]])
    assert not items_match(("milk", "bread", "eggs"), [["milk", "bread"]])


def test_actual_signature_ignores_item_order_but_preserves_multiplicity():
    first = ActualResult(
        route="shopping",
        action="add_items",
        items=("Молоко", " хлеб "),
    )
    reordered = ActualResult(
        route="shopping",
        action="add_items",
        items=("ХЛЕБ", "молоко"),
    )
    duplicate = ActualResult(
        route="shopping",
        action="add_items",
        items=("хлеб", "молоко", "молоко"),
    )

    assert first.signature() == reordered.signature()
    assert first.signature() != duplicate.signature()


@pytest.mark.parametrize(
    ("actual", "failed_fields"),
    [
        (
            ActualResult(
                route="recipe",
                action="add_recipe",
                recipe_name="молоко",
            ),
            {"route_pass", "action_pass", "items_pass"},
        ),
        (
            ActualResult(route="shopping", action="remove_items", items=("молоко",)),
            {"action_pass"},
        ),
        (
            ActualResult(route="shopping", action="add_items", items=("хлеб",)),
            {"items_pass"},
        ),
    ],
)
def test_grade_case_reports_incorrect_route_action_and_items(actual, failed_fields):
    case = make_case()

    grade = grade_case(case, actual)

    observed_failed = {
        field
        for field in ("route_pass", "action_pass", "items_pass")
        if getattr(grade, field) is False
    }
    assert observed_failed == failed_fields
    assert not grade.passed


def test_grade_case_accepts_normalized_recipe_name_and_rejects_wrong_name():
    case = make_case(route="recipe", action="add_recipe", recipe_name="Солянка")

    correct = grade_case(
        case,
        ActualResult(
            route="recipe",
            action="add_recipe",
            recipe_name="  СОЛЯНКА ",
        ),
    )
    wrong = grade_case(
        case,
        ActualResult(route="recipe", action="add_recipe", recipe_name="борщ"),
    )

    assert correct.passed
    assert correct.recipe_name_pass is True
    assert wrong.recipe_name_pass is False
    assert not wrong.passed


def test_ambiguous_expectation_maps_only_to_unhandled_unknown():
    case = make_case(
        route="ambiguous",
        action="unknown",
        text="молоко и хлеб",
    )

    unhandled = grade_case(case, ActualResult(route="unhandled", action="unknown"))
    guessed = grade_case(
        case,
        ActualResult(
            route="shopping",
            action="add_items",
            items=("молоко", "хлеб"),
        ),
    )

    assert unhandled.passed
    assert unhandled.items_pass is None
    assert unhandled.recipe_name_pass is None
    assert not guessed.passed


@pytest.mark.parametrize(
    ("argv", "eval_key"),
    [
        (["--model", "test-model"], "eval-key"),
        (["--allow-live-openai"], "eval-key"),
        (["--allow-live-openai", "--model", "test-model"], None),
        (
            [
                "--allow-live-openai",
                "--model",
                "test-model",
                "--repetitions",
                "0",
            ],
            "eval-key",
        ),
    ],
)
def test_cli_guards_do_not_construct_client_or_create_output(
    monkeypatch,
    tmp_path,
    argv,
    eval_key,
):
    output_path = tmp_path / "must-not-exist.json"
    monkeypatch.setenv("OPENAI_API_KEY", "ordinary-key-must-be-ignored")
    if eval_key is None:
        monkeypatch.delenv(live_compare.EVAL_KEY_ENV, raising=False)
    else:
        monkeypatch.setenv(live_compare.EVAL_KEY_ENV, eval_key)

    def fail_client_construction(**kwargs):
        raise AssertionError("guard failure must happen before client construction")

    monkeypatch.setattr(live_compare, "AsyncOpenAI", fail_client_construction)

    result = live_compare.main([*argv, "--output", str(output_path)])

    assert result == 2
    assert not output_path.exists()


@pytest.mark.parametrize(
    ("model", "eval_key", "error"),
    [
        ("", "eval-key", "explicit --model"),
        ("   ", "eval-key", "explicit --model"),
        ("test-model", "", live_compare.EVAL_KEY_ENV),
        ("test-model", "   ", live_compare.EVAL_KEY_ENV),
    ],
)
def test_live_guard_rejects_blank_model_and_eval_key(
    monkeypatch,
    model,
    eval_key,
    error,
):
    monkeypatch.setenv(live_compare.EVAL_KEY_ENV, eval_key)
    args = live_compare.build_parser().parse_args(
        ["--allow-live-openai", "--model", model]
    )

    errors = live_compare.validate_live_guard(args)

    assert any(error in message for message in errors)


@pytest.mark.parametrize("prompt_option", ["--shopping-prompt", "--recipe-prompt"])
def test_cli_rejects_blank_prompt_override_before_client_or_output(
    monkeypatch,
    tmp_path,
    prompt_option,
):
    blank_prompt = tmp_path / "blank.txt"
    blank_prompt.write_text(" \n\t", encoding="utf-8")
    output_path = tmp_path / "must-not-exist.json"
    monkeypatch.setenv(live_compare.EVAL_KEY_ENV, "eval-key")

    def fail_client_construction(**kwargs):
        raise AssertionError("blank prompt must fail before client construction")

    monkeypatch.setattr(live_compare, "AsyncOpenAI", fail_client_construction)

    result = live_compare.main(
        [
            "--allow-live-openai",
            "--model",
            "test-model",
            prompt_option,
            str(blank_prompt),
            "--output",
            str(output_path),
        ]
    )

    assert result == 2
    assert not output_path.exists()


def test_cli_help_does_not_construct_client_or_create_output(
    monkeypatch,
    tmp_path,
    capsys,
):
    output_path = tmp_path / "must-not-exist.json"

    def fail_client_construction(**kwargs):
        raise AssertionError("--help must not construct an API client")

    monkeypatch.setattr(live_compare, "AsyncOpenAI", fail_client_construction)

    with pytest.raises(SystemExit) as exit_info:
        live_compare.main(["--output", str(output_path), "--help"])

    assert exit_info.value.code == 0
    assert "--allow-live-openai" in capsys.readouterr().out
    assert not output_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("argv", "eval_key", "expected_error"),
    [
        (["--model", "test-model"], "eval-key", "--allow-live-openai"),
        (["--allow-live-openai"], "eval-key", "--model"),
        (
            ["--allow-live-openai", "--model", "test-model"],
            None,
            live_compare.EVAL_KEY_ENV,
        ),
    ],
)
async def test_run_guards_before_corpus_load_or_client_construction(
    monkeypatch,
    argv,
    eval_key,
    expected_error,
):
    if eval_key is None:
        monkeypatch.delenv(live_compare.EVAL_KEY_ENV, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-not-authorize-eval")
    else:
        monkeypatch.setenv(live_compare.EVAL_KEY_ENV, eval_key)

    def fail_corpus_load(*args, **kwargs):
        raise AssertionError("live guard must run before corpus loading")

    def fail_client_construction(*args, **kwargs):
        raise AssertionError("live guard must run before client construction")

    monkeypatch.setattr(live_compare, "load_corpus", fail_corpus_load)
    monkeypatch.setattr(live_compare, "AsyncOpenAI", fail_client_construction)
    args = live_compare.build_parser().parse_args(argv)

    with pytest.raises(ValueError, match=expected_error):
        await live_compare.run(args)


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_option", ["--shopping-prompt", "--recipe-prompt"])
async def test_run_rejects_blank_override_before_corpus_or_client(
    monkeypatch,
    tmp_path,
    prompt_option,
):
    blank_prompt = tmp_path / "blank.txt"
    blank_prompt.write_text(" \n\t", encoding="utf-8")
    monkeypatch.setenv(live_compare.EVAL_KEY_ENV, "eval-key")

    def fail_corpus_load(*args, **kwargs):
        raise AssertionError("prompt guard must run before corpus loading")

    def fail_client_construction(*args, **kwargs):
        raise AssertionError("prompt guard must run before client construction")

    monkeypatch.setattr(live_compare, "load_corpus", fail_corpus_load)
    monkeypatch.setattr(live_compare, "AsyncOpenAI", fail_client_construction)
    args = live_compare.build_parser().parse_args(
        [
            "--allow-live-openai",
            "--model",
            "test-model",
            prompt_option,
            str(blank_prompt),
        ]
    )

    with pytest.raises(ValueError, match="must not be blank"):
        await live_compare.run(args)


@pytest.mark.parametrize("prompt_option", ["--shopping-prompt", "--recipe-prompt"])
def test_cli_rejects_unreadable_candidate_prompt_before_client_or_output(
    monkeypatch,
    tmp_path,
    prompt_option,
):
    missing_prompt = tmp_path / "missing.txt"
    output_path = tmp_path / "must-not-exist.json"
    monkeypatch.setenv(live_compare.EVAL_KEY_ENV, "eval-key")

    def fail_client_construction(*args, **kwargs):
        raise AssertionError("prompt guard must run before client construction")

    monkeypatch.setattr(live_compare, "AsyncOpenAI", fail_client_construction)

    result = live_compare.main(
        [
            "--allow-live-openai",
            "--model",
            "test-model",
            prompt_option,
            str(missing_prompt),
            "--output",
            str(output_path),
        ]
    )

    assert result == 2
    assert not output_path.exists()


@pytest.mark.asyncio
async def test_eval_client_uses_dedicated_key_without_ambient_openai_env(
    monkeypatch,
):
    ambient = {
        name: f"ambient-{index}"
        for index, name in enumerate(live_compare.OPENAI_AMBIENT_ENV, start=1)
    }
    for name, value in ambient.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(live_compare.EVAL_KEY_ENV, "dedicated-eval-key")
    client = FakeEvalClient([])
    snapshots = []

    def fake_client_factory(*, api_key):
        snapshots.append(
            {
                "api_key": api_key,
                "ambient_present": {
                    name: os.environ.get(name)
                    for name in live_compare.OPENAI_AMBIENT_ENV
                    if name in os.environ
                },
            }
        )
        return client

    monkeypatch.setattr(live_compare, "AsyncOpenAI", fake_client_factory)
    monkeypatch.setattr(
        live_compare,
        "load_corpus",
        lambda path: make_corpus(corpus_case("ru-recipe-reuse-001")),
    )
    monkeypatch.setattr(live_compare, "current_commit", lambda: "abc123")
    monkeypatch.setattr(
        live_compare,
        "worktree_provenance",
        lambda: {"dirty": False, "status": [], "diff_sha256": "0" * 64},
    )
    args = live_compare.build_parser().parse_args(
        ["--allow-live-openai", "--model", "test-model", "--repetitions", "1"]
    )

    await live_compare.run(args)

    assert snapshots == [
        {"api_key": "dedicated-eval-key", "ambient_present": {}}
    ]
    assert {name: os.environ.get(name) for name in ambient} == ambient
    assert os.environ[live_compare.EVAL_KEY_ENV] == "dedicated-eval-key"
    assert client.closed


@pytest.mark.asyncio
async def test_ambient_openai_env_is_restored_when_client_construction_fails(
    monkeypatch,
):
    ambient = {
        name: f"ambient-{index}"
        for index, name in enumerate(live_compare.OPENAI_AMBIENT_ENV, start=1)
    }
    for name, value in ambient.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(live_compare.EVAL_KEY_ENV, "dedicated-eval-key")
    snapshots = []

    def fail_client_factory(*, api_key):
        snapshots.append(
            {
                "api_key": api_key,
                "ambient_present": {
                    name: os.environ.get(name)
                    for name in live_compare.OPENAI_AMBIENT_ENV
                    if name in os.environ
                },
            }
        )
        raise RuntimeError("offline constructor failure")

    monkeypatch.setattr(live_compare, "AsyncOpenAI", fail_client_factory)
    monkeypatch.setattr(
        live_compare,
        "load_corpus",
        lambda path: make_corpus(corpus_case("ru-recipe-reuse-001")),
    )
    args = live_compare.build_parser().parse_args(
        ["--allow-live-openai", "--model", "test-model", "--repetitions", "1"]
    )

    with pytest.raises(RuntimeError, match="offline constructor failure"):
        await live_compare.run(args)

    assert snapshots == [
        {"api_key": "dedicated-eval-key", "ambient_present": {}}
    ]
    assert {name: os.environ.get(name) for name in ambient} == ambient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    DETERMINISTIC_RECIPE_CASES,
    ids=lambda case: case.id,
)
async def test_every_deterministic_recipe_case_skips_model_and_passes(case):
    client = FakeEvalClient([])

    report = await live_compare.run_case(
        client=client,
        model="test-model",
        case=case,
        repetition=1,
        shopping_prompt=SHOPPING_TEXT_PROMPT,
        recipe_prompt=RECIPE_COMMAND_PROMPT,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )

    assert report["pass"] is True
    assert report["actual"]["route"] == case.expected.route
    assert report["actual"]["action"] == case.expected.action
    assert report["completed_responses"] == 0
    assert report["schema_valid_responses"] == 0
    assert report["latency_ms"] == 0.0
    assert report["usage"] == live_compare.empty_usage()
    assert client.responses.calls == []


@pytest.mark.asyncio
async def test_recipe_unknown_falls_through_to_shopping_with_contract_metadata(
    monkeypatch,
):
    case = corpus_case("ru-recipe-ai-reuse-008")
    client = FakeEvalClient(
        [
            model_response(
                {
                    "action": "unknown",
                    "recipe_name": None,
                    "url": None,
                    "recipe_text": None,
                },
                input_tokens=10,
                output_tokens=2,
            ),
            model_response(
                {
                    "action": "add_items",
                    "items": ["солянка"],
                    "needs_confirmation": False,
                    "clarification_question": None,
                },
                input_tokens=11,
                output_tokens=3,
            ),
        ]
    )
    clock = iter([1.0, 1.01, 2.0, 2.02])
    monkeypatch.setattr(live_compare.time, "perf_counter", lambda: next(clock))

    report = await live_compare.run_case(
        client=client,
        model="test-model",
        case=case,
        repetition=2,
        shopping_prompt=SHOPPING_TEXT_PROMPT,
        recipe_prompt=RECIPE_COMMAND_PROMPT,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )

    assert [call["instructions"] for call in client.responses.calls] == [
        RECIPE_COMMAND_PROMPT.instructions,
        SHOPPING_TEXT_PROMPT.instructions,
    ]
    assert all(call["model"] == "test-model" for call in client.responses.calls)
    assert report["repetition"] == 2
    assert report["completed_responses"] == 2
    assert report["schema_valid_responses"] == 2
    assert report["actual"] == {
        "route": "shopping",
        "action": "add_items",
        "items": ("солянка",),
        "recipe_name": None,
    }
    assert report["latency_ms"] == 30.0
    assert report["usage"] == {
        "input_tokens": 21,
        "output_tokens": 5,
        "total_tokens": 26,
    }
    json.dumps(report, ensure_ascii=False)


@pytest.mark.asyncio
async def test_api_value_errors_are_individual_infra_request_failures():
    pipeline_case = corpus_case("ru-recipe-ai-reuse-008")
    deterministic_case = corpus_case("ru-recipe-reuse-001")
    client = FakeEvalClient(
        [
            ValueError("recipe client rejected request"),
            ValueError("shopping client rejected request"),
        ]
    )

    pipeline_report = await live_compare.run_case(
        client=client,
        model="test-model",
        case=pipeline_case,
        repetition=1,
        shopping_prompt=SHOPPING_TEXT_PROMPT,
        recipe_prompt=RECIPE_COMMAND_PROMPT,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )
    deterministic_report = await live_compare.run_case(
        client=client,
        model="test-model",
        case=deterministic_case,
        repetition=1,
        shopping_prompt=SHOPPING_TEXT_PROMPT,
        recipe_prompt=RECIPE_COMMAND_PROMPT,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )

    assert pipeline_report["failures"] == [
        "infra:recipe_command_parse:ValueError",
        "infra:text_parse:ValueError",
    ]
    assert pipeline_report["attempted_model_requests"] == 2
    assert pipeline_report["failed_model_requests"] == 2
    assert pipeline_report["completed_responses"] == 0
    assert pipeline_report["schema_valid_responses"] == 0
    assert deterministic_report["attempted_model_requests"] == 0
    assert deterministic_report["failed_model_requests"] == 0

    metrics = live_compare.summarize_attempts(
        make_corpus(pipeline_case, deterministic_case),
        [pipeline_report, deterministic_report],
    )
    assert metrics["attempted_model_requests"] == 2
    assert metrics["failed_model_requests"] == 2
    assert metrics["infra_error_rate"] == 1.0


@pytest.mark.asyncio
async def test_completed_invalid_response_is_schema_failure_not_infra_failure():
    case = corpus_case("ru-shopping-add-001")
    client = FakeEvalClient(
        [
            model_response(
                {
                    "action": "unknown",
                    "items": ["guessed milk"],
                    "needs_confirmation": False,
                    "clarification_question": None,
                }
            )
        ]
    )

    report = await live_compare.run_case(
        client=client,
        model="test-model",
        case=case,
        repetition=1,
        shopping_prompt=SHOPPING_TEXT_PROMPT,
        recipe_prompt=RECIPE_COMMAND_PROMPT,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )

    assert report["failures"] == ["schema:text_parse"]
    assert report["attempted_model_requests"] == 1
    assert report["failed_model_requests"] == 0
    assert report["completed_responses"] == 1
    assert report["schema_valid_responses"] == 0


@pytest.mark.asyncio
async def test_valid_shopping_unknown_uses_deterministic_fallback():
    case = corpus_case("ru-shopping-add-001")
    client = FakeEvalClient(
        [
            model_response(
                {
                    "action": "unknown",
                    "items": [],
                    "needs_confirmation": False,
                    "clarification_question": None,
                }
            )
        ]
    )

    report = await live_compare.run_case(
        client=client,
        model="test-model",
        case=case,
        repetition=1,
        shopping_prompt=SHOPPING_TEXT_PROMPT,
        recipe_prompt=RECIPE_COMMAND_PROMPT,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )

    assert report["pass"] is True
    assert report["actual"] == {
        "route": "shopping",
        "action": "add_items",
        "items": ("молоко",),
        "recipe_name": None,
    }
    assert len(client.responses.calls) == 1


@pytest.mark.asyncio
async def test_runner_serializes_request_contract_and_client_closes(
    monkeypatch,
    tmp_path,
):
    corpus_path = tmp_path / "one-case.jsonl"
    case_payload = make_case().model_dump(mode="json")
    corpus_path.write_text(
        json.dumps(case_payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    client = FakeEvalClient(
        [
            model_response(
                {
                    "action": "add_items",
                    "items": ["молоко"],
                    "needs_confirmation": False,
                    "clarification_question": None,
                },
                input_tokens=12,
                output_tokens=4,
            )
        ]
    )
    observed_api_keys = []

    def fake_client_factory(*, api_key):
        observed_api_keys.append(api_key)
        return client

    monkeypatch.setattr(live_compare, "AsyncOpenAI", fake_client_factory)
    monkeypatch.setattr(live_compare, "current_commit", lambda: "abc123")
    expected_worktree = {
        "dirty": True,
        "status": [" M evals/live_compare.py"],
        "diff_sha256": "f" * 64,
    }
    monkeypatch.setattr(
        live_compare,
        "worktree_provenance",
        lambda: expected_worktree,
    )
    clock = iter([3.0, 3.025])
    monkeypatch.setattr(live_compare.time, "perf_counter", lambda: next(clock))
    monkeypatch.setenv(live_compare.EVAL_KEY_ENV, "offline-eval-key")
    args = live_compare.build_parser().parse_args(
        [
            "--allow-live-openai",
            "--model",
            "test-model",
            "--repetitions",
            "1",
            "--corpus",
            str(corpus_path),
        ]
    )
    production_fingerprint = SHOPPING_TEXT_PROMPT.fingerprint

    report = await live_compare.run(args)

    eval_prompt = report["models"][0]["prompts"]["shopping"]
    production_prompt = report["prompt_registry"]["text_parse"]
    assert observed_api_keys == ["offline-eval-key"]
    assert client.closed
    assert client.responses.calls[0]["instructions"] == (
        SHOPPING_TEXT_PROMPT.instructions
    )
    assert client.responses.calls[0]["temperature"] == SHOPPING_TEXT_PROMPT.temperature
    assert (
        client.responses.calls[0]["max_output_tokens"]
        == SHOPPING_TEXT_PROMPT.max_output_tokens
    )
    assert eval_prompt["revision"] == SHOPPING_TEXT_PROMPT.revision
    assert eval_prompt["fingerprint"] == production_fingerprint
    assert "instructions" not in eval_prompt
    assert production_prompt["fingerprint"] == production_fingerprint
    assert "instructions" not in production_prompt
    assert report["models"][0]["prompts"]["recipe"]["fingerprint"] == (
        RECIPE_COMMAND_PROMPT.fingerprint
    )
    assert report["models"][0]["cases"][0]["latency_ms"] == 25.0
    assert report["models"][0]["cases"][0]["usage"] == {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
    }
    assert report["commit"] == "abc123"
    assert report["worktree"] == expected_worktree
    json.dumps(report, ensure_ascii=False)


@pytest.mark.asyncio
async def test_one_run_compares_production_and_candidate_prompt_on_same_model(
    monkeypatch,
    tmp_path,
):
    corpus_path = tmp_path / "one-case.jsonl"
    corpus_path.write_text(
        json.dumps(make_case().model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    candidate_path = tmp_path / "candidate-shopping-prompt.txt"
    candidate_instructions = (
        SHOPPING_TEXT_PROMPT.instructions + "\nCandidate routing clarification."
    )
    candidate_path.write_text(candidate_instructions, encoding="utf-8")
    response = model_response(
        {
            "action": "add_items",
            "items": ["молоко"],
            "needs_confirmation": False,
            "clarification_question": None,
        }
    )
    client = FakeEvalClient([response, response])

    monkeypatch.setattr(
        live_compare,
        "AsyncOpenAI",
        lambda *, api_key: client,
    )
    monkeypatch.setattr(live_compare, "current_commit", lambda: "abc123")
    monkeypatch.setattr(
        live_compare,
        "worktree_provenance",
        lambda: {"dirty": False, "status": [], "diff_sha256": "0" * 64},
    )
    monkeypatch.setenv(live_compare.EVAL_KEY_ENV, "offline-eval-key")
    args = live_compare.build_parser().parse_args(
        [
            "--allow-live-openai",
            "--model",
            "same-model",
            "--model",
            "same-model",
            "--repetitions",
            "1",
            "--corpus",
            str(corpus_path),
            "--shopping-prompt",
            str(candidate_path),
        ]
    )
    production_fingerprint = SHOPPING_TEXT_PROMPT.fingerprint

    report = await live_compare.run(args)

    baseline, candidate = report["models"]
    assert baseline["model"] == candidate["model"] == "same-model"
    assert baseline["baseline"] is True
    assert candidate["baseline"] is False
    assert baseline["prompts"]["shopping"]["fingerprint"] == production_fingerprint
    assert candidate["prompts"]["shopping"]["fingerprint"] != (
        production_fingerprint
    )
    assert [call["instructions"] for call in client.responses.calls] == [
        SHOPPING_TEXT_PROMPT.instructions,
        candidate_instructions,
    ]
    assert report["prompt_registry"]["text_parse"]["fingerprint"] == (
        production_fingerprint
    )
    assert SHOPPING_TEXT_PROMPT.fingerprint == production_fingerprint
    assert client.closed


def test_cli_unqualified_run_is_nonzero_and_writes_nothing_without_output(
    monkeypatch,
    tmp_path,
):
    corpus_path = tmp_path / "one-case.jsonl"
    corpus_path.write_text(
        json.dumps(make_case().model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    response = {
        "action": "add_items",
        "items": ["молоко"],
        "needs_confirmation": False,
        "clarification_question": None,
    }
    client = FakeEvalClient([model_response(response) for _ in range(3)])

    def fake_client_factory(*, api_key):
        assert api_key == "offline-eval-key"
        return client

    monkeypatch.setattr(live_compare, "AsyncOpenAI", fake_client_factory)
    monkeypatch.setattr(live_compare, "current_commit", lambda: "abc123")
    monkeypatch.setenv(live_compare.EVAL_KEY_ENV, "offline-eval-key")
    before = set(tmp_path.iterdir())

    result = live_compare.main(
        [
            "--allow-live-openai",
            "--model",
            "test-model",
            "--repetitions",
            "3",
            "--corpus",
            str(corpus_path),
        ]
    )

    assert result == 1
    assert client.closed
    assert set(tmp_path.iterdir()) == before


def make_attempt(
    case,
    actual,
    *,
    repetition,
    completed=1,
    schema_valid=1,
    failures=None,
    latency_ms=10.0,
):
    return live_compare.case_report(
        case=case,
        repetition=repetition,
        actual=actual,
        grade=grade_case(case, actual),
        failures=failures or [],
        completed_responses=completed,
        schema_valid_responses=schema_valid,
        latency_ms=latency_ms,
        usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    )


def test_summary_uses_attempt_and_model_response_denominators_across_repetitions():
    shopping = make_case(case_id="shopping-001", critical=True)
    unhandled = make_case(
        case_id="unhandled-001",
        text="привет",
        route="unhandled",
        action="unknown",
    )
    correct_shopping = ActualResult(
        route="shopping",
        action="add_items",
        items=("молоко",),
    )
    false_recipe = ActualResult(
        route="recipe",
        action="add_recipe",
        recipe_name="молоко",
    )
    correct_unhandled = ActualResult(route="unhandled", action="unknown")
    attempts = [
        make_attempt(shopping, correct_shopping, repetition=1),
        make_attempt(shopping, false_recipe, repetition=2),
        make_attempt(
            unhandled,
            correct_unhandled,
            repetition=1,
            schema_valid=0,
            failures=["schema:text_parse"],
        ),
        make_attempt(
            unhandled,
            correct_unhandled,
            repetition=2,
            completed=0,
            schema_valid=0,
            failures=["infra:text_parse:TimeoutError"],
            latency_ms=0,
        ),
    ]

    metrics = live_compare.summarize_attempts(
        make_corpus(shopping, unhandled),
        attempts,
    )

    assert metrics["attempts"] == 4
    assert metrics["completed_model_responses"] == 3
    assert metrics["schema_valid_model_responses"] == 2
    assert metrics["schema_valid_rate"] == pytest.approx(2 / 3)
    assert metrics["critical_rate"] == 0.5
    assert metrics["shopping_to_recipe_false_positives"] == 1
    assert metrics["route_action_rate"] == 0.75
    assert metrics["accepted_items_rate"] == 0.5
    assert metrics["consistency_rate"] == 0.5
    assert metrics["schema_failures"] == 1
    assert metrics["infra_failures"] == 1
    assert metrics["infra_error_rate"] == 0.25


def passing_boundary_metrics():
    return {
        "release_qualified": True,
        "overall_pass_rate": 1.0,
        "schema_valid_rate": 1.0,
        "critical_rate": 1.0,
        "shopping_to_recipe_false_positives": 0,
        "route_action_rate": 0.98,
        "accepted_items_rate": 0.98,
        "recipe_name_rate": 0.98,
        "consistency_rate": 0.99,
        "infra_error_rate": 0.02,
    }


def test_aggregate_gates_accept_exact_boundaries():
    result = live_compare.evaluate_gates(passing_boundary_metrics())

    assert result["pass"] is True
    assert all(result["checks"].values())


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("release_qualified", False),
        ("overall_pass_rate", 0.999999),
        ("schema_valid_rate", 0.999999),
        ("critical_rate", 0.999999),
        ("shopping_to_recipe_false_positives", 1),
        ("route_action_rate", 0.979999),
        ("accepted_items_rate", 0.979999),
        ("recipe_name_rate", 0.979999),
        ("consistency_rate", 0.989999),
        ("infra_error_rate", 0.020001),
    ],
)
def test_aggregate_gates_reject_values_outside_boundaries(metric, value):
    metrics = passing_boundary_metrics()
    metrics[metric] = value

    assert live_compare.evaluate_gates(metrics)["pass"] is False


def test_wrong_noncritical_recipe_name_fails_release_gates():
    case = make_case(
        case_id="recipe-001",
        text="купи на солянку",
        route="recipe",
        action="add_recipe",
        recipe_name="солянка",
        critical=False,
    )
    wrong = ActualResult(
        route="recipe",
        action="add_recipe",
        recipe_name="борщ",
    )
    metrics = live_compare.summarize_attempts(
        make_corpus(case),
        [make_attempt(case, wrong, repetition=1)],
    )
    metrics["release_qualified"] = True

    gates = live_compare.evaluate_gates(metrics)

    assert metrics["recipe_name_rate"] == 0.0
    assert metrics["overall_pass_rate"] == 0.0
    assert gates["checks"]["recipe_name_rate_98"] is False
    assert gates["checks"]["overall_pass_rate_100"] is False
    assert gates["pass"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_count", "repetitions", "qualified"),
    [
        (71, 3, False),
        (72, 2, False),
        (72, 3, True),
    ],
)
async def test_release_qualification_requires_corpus_and_repetition_floors(
    monkeypatch,
    case_count,
    repetitions,
    qualified,
):
    base_case = make_case()
    cases = tuple(
        base_case.model_copy(update={"id": f"shopping-{index:03d}"})
        for index in range(case_count)
    )
    corpus = make_corpus(*cases)

    async def passing_run_case(**kwargs):
        case = kwargs["case"]
        actual = ActualResult(
            route="shopping",
            action="add_items",
            items=("молоко",),
        )
        return make_attempt(
            case,
            actual,
            repetition=kwargs["repetition"],
        )

    monkeypatch.setattr(live_compare, "run_case", passing_run_case)

    report = await live_compare.run_model(
        client=FakeEvalClient([]),
        model="test-model",
        corpus=corpus,
        repetitions=repetitions,
        shopping_prompt=SHOPPING_TEXT_PROMPT,
        recipe_prompt=RECIPE_COMMAND_PROMPT,
        bot_username=live_compare.DEFAULT_BOT_USERNAME,
    )

    assert report["metrics"]["release_qualified"] is qualified
    assert report["gates"]["checks"]["release_qualified"] is qualified
    assert report["gates"]["pass"] is qualified


def test_unqualified_model_report_cannot_signal_release_success():
    metrics = passing_boundary_metrics()
    metrics["release_qualified"] = False
    gates = live_compare.evaluate_gates(metrics)
    model_report = {"metrics": metrics, "gates": gates}

    assert gates["pass"] is False
    assert live_compare.model_exit_pass(model_report) is False


def test_baseline_ratio_boundaries_and_zero_denominators():
    models = [
        {
            "metrics": {
                "latency_ms": {"p95": 100.0},
                "usage": {"output_tokens": 100},
            }
        },
        {
            "metrics": {
                "latency_ms": {"p95": 150.0},
                "usage": {"output_tokens": 125},
            }
        },
        {
            "metrics": {
                "latency_ms": {"p95": 150.001},
                "usage": {"output_tokens": 126},
            }
        },
    ]

    live_compare.add_comparison_flags(models)

    exact = models[1]["comparison_to_baseline"]
    exceeded = models[2]["comparison_to_baseline"]
    assert exact == {
        "latency_p95_ratio": 1.5,
        "output_tokens_ratio": 1.25,
        "flags": [],
    }
    assert exceeded["latency_p95_ratio"] == pytest.approx(1.50001)
    assert exceeded["output_tokens_ratio"] == 1.26
    assert exceeded["flags"] == [
        "p95 latency exceeds 1.5x baseline",
        "output tokens exceed 1.25x baseline",
    ]

    zero_models = [
        {
            "metrics": {
                "latency_ms": {"p95": 0},
                "usage": {"output_tokens": 0},
            }
        },
        {
            "metrics": {
                "latency_ms": {"p95": 20},
                "usage": {"output_tokens": 10},
            }
        },
    ]
    live_compare.add_comparison_flags(zero_models)

    assert zero_models[1]["comparison_to_baseline"] == {
        "latency_p95_ratio": None,
        "output_tokens_ratio": None,
        "flags": [],
    }


def test_worktree_provenance_hash_includes_tracked_and_untracked_state(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    untracked = tmp_path / "untracked.txt"
    untracked.write_text("first private payload", encoding="utf-8")
    status = " M tracked.py\n?? untracked.txt"
    tracked_diff = "diff --git a/tracked.py b/tracked.py\n+changed"

    def fake_run_git(args):
        if args[0] == "status":
            return status
        if args[0] == "diff":
            return tracked_diff
        raise AssertionError(f"unexpected git command: {args}")

    monkeypatch.setattr(live_compare, "run_git", fake_run_git)

    first = live_compare.worktree_provenance()
    untracked.write_text("second private payload", encoding="utf-8")
    second = live_compare.worktree_provenance()

    assert first["dirty"] is True
    assert first["status"] == [" M tracked.py", "?? untracked.txt"]
    assert len(first["diff_sha256"]) == 64
    assert first["diff_sha256"] != second["diff_sha256"]
    serialized = json.dumps(first)
    assert "first private payload" not in serialized
    assert "second private payload" not in serialized
