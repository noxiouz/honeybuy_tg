from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from openai import AsyncOpenAI

from evals.corpus import Corpus, RoutingCase, load_corpus
from evals.grading import ActualResult, grade_case
from honeybuy_tg.ai import (
    RecipeCommandParseResponse,
    ShoppingTextParseResponse,
    parse_ai_json_response,
)
from honeybuy_tg.parser import ParsedAction, parse_shopping_text, parsed_command_from_ai
from honeybuy_tg.prompts import (
    PROMPT_SPECS,
    PromptSpec,
    RECIPE_COMMAND_PROMPT,
    SHOPPING_TEXT_PROMPT,
)
from honeybuy_tg.recipes import (
    parse_add_recipe_request,
    parse_learn_recipe_request,
    recipe_command_from_ai,
    should_try_ai_recipe_command,
)


EVAL_KEY_ENV = "HONEYBUY_EVAL_OPENAI_API_KEY"
DEFAULT_BOT_USERNAME = "HoneyBuyBot"
MIN_RELEASE_CASES = 72
MIN_RELEASE_REPETITIONS = 3
OPENAI_AMBIENT_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_WEBHOOK_SECRET",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an opt-in live OpenAI comparison against text routing cases."
    )
    parser.add_argument("--corpus", default=str(Path("evals/cases/text_routing.v1.jsonl")))
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--allow-live-openai", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--shopping-prompt")
    parser.add_argument("--recipe-prompt")
    parser.add_argument("--bot-username", default=DEFAULT_BOT_USERNAME)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    guard_errors = validate_live_guard(args)
    if guard_errors:
        for error in guard_errors:
            print(error, file=sys.stderr)
        return 2
    report = asyncio.run(run(args))
    print_summary(report)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if all(model_exit_pass(model) for model in report["models"]) else 1


def validate_live_guard(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    model_names = normalize_model_names(args.model)
    if not args.allow_live_openai:
        errors.append("refusing live OpenAI calls without --allow-live-openai")
    if not model_names:
        errors.append("at least one explicit --model is required")
    elif len(model_names) != len(args.model):
        errors.append("every explicit --model value must be nonblank")
    if (args.shopping_prompt or args.recipe_prompt) and len(model_names) < 2:
        errors.append("candidate prompt overrides require at least two --model entries")
    if not eval_api_key():
        errors.append(f"{EVAL_KEY_ENV} is required; ordinary OPENAI_API_KEY is ignored")
    if args.repetitions < 1:
        errors.append("--repetitions must be positive")
    errors.extend(validate_prompt_override(args.shopping_prompt, "shopping prompt"))
    errors.extend(validate_prompt_override(args.recipe_prompt, "recipe prompt"))
    return errors


async def run(args: argparse.Namespace) -> dict[str, Any]:
    guard_errors = validate_live_guard(args)
    if guard_errors:
        raise ValueError("; ".join(guard_errors))
    worktree = worktree_provenance()
    corpus = load_corpus(args.corpus)
    candidate_shopping_prompt = prompt_from_args(
        SHOPPING_TEXT_PROMPT,
        args.shopping_prompt,
        label="shopping override",
    )
    candidate_recipe_prompt = prompt_from_args(
        RECIPE_COMMAND_PROMPT,
        args.recipe_prompt,
        label="recipe override",
    )
    with official_openai_env_only():
        client = AsyncOpenAI(api_key=eval_api_key())
    try:
        models = []
        for model_index, model in enumerate(normalize_model_names(args.model)):
            shopping_prompt = (
                SHOPPING_TEXT_PROMPT if model_index == 0 else candidate_shopping_prompt
            )
            recipe_prompt = (
                RECIPE_COMMAND_PROMPT if model_index == 0 else candidate_recipe_prompt
            )
            model_report = await run_model(
                client=client,
                model=model,
                corpus=corpus,
                repetitions=args.repetitions,
                shopping_prompt=shopping_prompt,
                recipe_prompt=recipe_prompt,
                bot_username=args.bot_username,
            )
            if model_index == 0:
                model_report["baseline"] = True
            models.append(model_report)
        add_comparison_flags(models)
        return {
            "commit": current_commit(),
            "worktree": worktree,
            "corpus": {
                "path": str(corpus.path),
                "sha256": corpus.sha256,
                "case_count": len(corpus.cases),
            },
            "prompt_registry": {
                operation: prompt_metadata(spec)
                for operation, spec in PROMPT_SPECS.items()
            },
            "models": models,
        }
    finally:
        await client.close()


def prompt_from_args(spec: PromptSpec, path: str | None, *, label: str) -> PromptSpec:
    if path is None:
        return spec
    override_path = Path(path)
    instructions = override_path.read_text(encoding="utf-8").strip()
    return spec.with_instructions(instructions, revision=label)


async def run_model(
    *,
    client: AsyncOpenAI,
    model: str,
    corpus: Corpus,
    repetitions: int,
    shopping_prompt: PromptSpec,
    recipe_prompt: PromptSpec,
    bot_username: str,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for repetition in range(1, repetitions + 1):
        for case in corpus.cases:
            attempts.append(
                await run_case(
                    client=client,
                    model=model,
                    case=case,
                    repetition=repetition,
                    shopping_prompt=shopping_prompt,
                    recipe_prompt=recipe_prompt,
                    bot_username=bot_username,
                )
            )
    metrics = summarize_attempts(corpus, attempts)
    metrics["release_qualified"] = (
        metrics["case_count"] >= MIN_RELEASE_CASES
        and repetitions >= MIN_RELEASE_REPETITIONS
    )
    return {
        "model": model,
        "baseline": False,
        "repetitions": repetitions,
        "prompts": {
            "shopping": prompt_metadata(shopping_prompt),
            "recipe": prompt_metadata(recipe_prompt),
        },
        "metrics": metrics,
        "gates": evaluate_gates(metrics),
        "cases": attempts,
    }


async def run_case(
    *,
    client: AsyncOpenAI,
    model: str,
    case: RoutingCase,
    repetition: int,
    shopping_prompt: PromptSpec,
    recipe_prompt: PromptSpec,
    bot_username: str,
) -> dict[str, Any]:
    text = command_text_for_case(case, bot_username=bot_username)
    failures: list[str] = []
    completed_responses = 0
    schema_valid_responses = 0
    attempted_model_requests = 0
    failed_model_requests = 0
    total_latency_ms = 0.0
    usage = empty_usage()

    deterministic = deterministic_recipe_result(text)
    if deterministic is not None:
        grade = grade_case(case, deterministic)
        return case_report(
            case=case,
            repetition=repetition,
            actual=deterministic,
            grade=grade,
            failures=failures,
            completed_responses=completed_responses,
            schema_valid_responses=schema_valid_responses,
            attempted_model_requests=attempted_model_requests,
            failed_model_requests=failed_model_requests,
            latency_ms=total_latency_ms,
            usage=usage,
        )

    if should_try_ai_recipe_command(text):
        attempted_model_requests += 1
        try:
            result, latency_ms, response_usage = await call_responses_api(
                client=client,
                model=model,
                prompt=recipe_prompt,
                text=text,
            )
            completed_responses += 1
            total_latency_ms += latency_ms
            add_usage(usage, response_usage)
        except Exception as error:
            failed_model_requests += 1
            failures.append(f"infra:recipe_command_parse:{error.__class__.__name__}")
        else:
            try:
                recipe_payload = parse_ai_json_response(
                    result,
                    RecipeCommandParseResponse,
                )
            except ValueError:
                failures.append("schema:recipe_command_parse")
            else:
                schema_valid_responses += 1
                recipe_command = recipe_command_from_ai(recipe_payload.model_dump())
                if recipe_command.action in {"learn_recipe", "add_recipe"}:
                    actual = ActualResult(
                        route="recipe",
                        action=recipe_command.action,
                        recipe_name=recipe_command.name,
                    )
                    grade = grade_case(case, actual)
                    return case_report(
                        case=case,
                        repetition=repetition,
                        actual=actual,
                        grade=grade,
                        failures=failures,
                        completed_responses=completed_responses,
                        schema_valid_responses=schema_valid_responses,
                        attempted_model_requests=attempted_model_requests,
                        failed_model_requests=failed_model_requests,
                        latency_ms=total_latency_ms,
                        usage=usage,
                    )

    attempted_model_requests += 1
    try:
        result, latency_ms, response_usage = await call_responses_api(
            client=client,
            model=model,
            prompt=shopping_prompt,
            text=text,
        )
        completed_responses += 1
        total_latency_ms += latency_ms
        add_usage(usage, response_usage)
    except Exception as error:
        failed_model_requests += 1
        failures.append(f"infra:text_parse:{error.__class__.__name__}")
        parsed = parse_shopping_text(text)
    else:
        try:
            shopping_payload = parse_ai_json_response(result, ShoppingTextParseResponse)
        except ValueError:
            failures.append("schema:text_parse")
            parsed = parse_shopping_text(text)
        else:
            schema_valid_responses += 1
            parsed = parsed_command_from_ai(shopping_payload.model_dump())
            if parsed.action == ParsedAction.UNKNOWN:
                local_parsed = parse_shopping_text(text)
                if local_parsed.action != ParsedAction.UNKNOWN:
                    parsed = local_parsed

    if parsed.action == ParsedAction.UNKNOWN:
        actual = ActualResult(route="unhandled", action="unknown")
    else:
        actual = ActualResult(
            route="shopping",
            action=parsed.action.value,
            items=parsed.items,
        )
    grade = grade_case(case, actual)
    return case_report(
        case=case,
        repetition=repetition,
        actual=actual,
        grade=grade,
        failures=failures,
        completed_responses=completed_responses,
        schema_valid_responses=schema_valid_responses,
        attempted_model_requests=attempted_model_requests,
        failed_model_requests=failed_model_requests,
        latency_ms=total_latency_ms,
        usage=usage,
    )


async def call_responses_api(
    *,
    client: AsyncOpenAI,
    model: str,
    prompt: PromptSpec,
    text: str,
) -> tuple[object, float, dict[str, int]]:
    kwargs: dict[str, object] = {
        "model": model,
        "instructions": prompt.instructions,
        "input": text,
    }
    if prompt.temperature is not None:
        kwargs["temperature"] = prompt.temperature
    if prompt.max_output_tokens is not None:
        kwargs["max_output_tokens"] = prompt.max_output_tokens
    started = time.perf_counter()
    result = await client.responses.create(**kwargs)
    latency_ms = (time.perf_counter() - started) * 1000
    return result, latency_ms, extract_usage(result)


def deterministic_recipe_result(text: str) -> ActualResult | None:
    learn_request = parse_learn_recipe_request(text)
    if learn_request is not None:
        return ActualResult(
            route="recipe",
            action="learn_recipe",
            recipe_name=learn_request.name,
        )
    add_request = parse_add_recipe_request(text)
    if add_request is not None:
        return ActualResult(
            route="recipe",
            action="add_recipe",
            recipe_name=add_request.name,
        )
    return None


def command_text_for_case(case: RoutingCase, *, bot_username: str) -> str:
    if case.channel != "mention":
        return case.text
    cleaned = strip_eval_bot_mention(case.text, bot_username=bot_username)
    if cleaned:
        return cleaned
    return case.reply_text or cleaned


def strip_eval_bot_mention(text: str, *, bot_username: str) -> str:
    stripped, count = re.subn(
        rf"@{re.escape(bot_username)}\b[,;:.!?]*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if count == 0:
        return text.strip()
    return stripped.strip(" \t\r\n,;:.!?")


def case_report(
    *,
    case: RoutingCase,
    repetition: int,
    actual: ActualResult,
    grade: object,
    failures: list[str],
    completed_responses: int,
    schema_valid_responses: int,
    attempted_model_requests: int | None = None,
    failed_model_requests: int | None = None,
    latency_ms: float,
    usage: dict[str, int],
) -> dict[str, Any]:
    if failed_model_requests is None:
        failed_model_requests = sum(
            1 for failure in failures if failure.startswith("infra:")
        )
    if attempted_model_requests is None:
        attempted_model_requests = completed_responses + failed_model_requests
    return {
        "id": case.id,
        "repetition": repetition,
        "critical": case.critical,
        "tags": case.tags,
        "expected": case.expected.model_dump(),
        "actual": asdict(actual),
        "pass": grade.passed,
        "grade": asdict(grade),
        "failures": failures,
        "attempted_model_requests": attempted_model_requests,
        "failed_model_requests": failed_model_requests,
        "completed_responses": completed_responses,
        "schema_valid_responses": schema_valid_responses,
        "latency_ms": round(latency_ms, 3),
        "usage": usage,
    }


def summarize_attempts(corpus: Corpus, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(attempts)
    attempted_model_requests = sum(
        attempt.get("attempted_model_requests", attempt["completed_responses"])
        for attempt in attempts
    )
    failed_model_requests = sum(
        attempt.get("failed_model_requests", 0) for attempt in attempts
    )
    completed = sum(attempt["completed_responses"] for attempt in attempts)
    schema_valid = sum(attempt["schema_valid_responses"] for attempt in attempts)
    infra_failures = sum(
        1
        for attempt in attempts
        if any(failure.startswith("infra:") for failure in attempt["failures"])
    )
    schema_failures = sum(
        1
        for attempt in attempts
        if any(failure.startswith("schema:") for failure in attempt["failures"])
    )
    critical = [attempt for attempt in attempts if attempt["critical"]]
    shopping_false_recipe = [
        attempt
        for attempt in attempts
        if attempt["expected"]["route"] == "shopping"
        and attempt["actual"]["route"] == "recipe"
    ]
    item_attempts = [
        attempt
        for attempt in attempts
        if attempt["expected"].get("accepted_items") is not None
    ]
    recipe_name_attempts = [
        attempt
        for attempt in attempts
        if attempt["expected"].get("recipe_name") is not None
    ]
    signatures_by_case: dict[str, set[tuple[object, ...]]] = defaultdict(set)
    for attempt in attempts:
        actual = ActualResult(**attempt["actual"])
        signatures_by_case[attempt["id"]].add(actual.signature())
    latencies = [attempt["latency_ms"] for attempt in attempts if attempt["latency_ms"]]
    usage = empty_usage()
    for attempt in attempts:
        add_usage(usage, attempt["usage"])
    return {
        "attempts": total,
        "case_count": len(corpus.cases),
        "attempted_model_requests": attempted_model_requests,
        "failed_model_requests": failed_model_requests,
        "completed_model_responses": completed,
        "schema_valid_model_responses": schema_valid,
        "schema_valid_rate": ratio(schema_valid, completed, empty_value=1.0),
        "critical_rate": ratio(
            sum(1 for attempt in critical if attempt["pass"]),
            len(critical),
            empty_value=1.0,
        ),
        "shopping_to_recipe_false_positives": len(shopping_false_recipe),
        "route_action_rate": ratio(
            sum(
                1
                for attempt in attempts
                if attempt["grade"]["route_pass"] and attempt["grade"]["action_pass"]
            ),
            total,
        ),
        "accepted_items_rate": ratio(
            sum(1 for attempt in item_attempts if attempt["grade"]["items_pass"]),
            len(item_attempts),
            empty_value=1.0,
        ),
        "recipe_name_rate": ratio(
            sum(
                1
                for attempt in recipe_name_attempts
                if attempt["grade"]["recipe_name_pass"]
            ),
            len(recipe_name_attempts),
            empty_value=1.0,
        ),
        "overall_pass_rate": ratio(
            sum(1 for attempt in attempts if attempt["pass"]),
            total,
        ),
        "consistency_rate": ratio(
            sum(1 for signatures in signatures_by_case.values() if len(signatures) == 1),
            len(corpus.cases),
            empty_value=1.0,
        ),
        "quality_failures": sum(1 for attempt in attempts if not attempt["pass"]),
        "schema_failures": schema_failures,
        "infra_failures": infra_failures,
        "infra_error_rate": ratio(failed_model_requests, attempted_model_requests),
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
        "usage": usage,
    }


def evaluate_gates(metrics: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "release_qualified": metrics.get("release_qualified", True),
        "overall_pass_rate_100": metrics.get("overall_pass_rate", 1.0) == 1.0,
        "schema_valid_rate_100": metrics["schema_valid_rate"] == 1.0,
        "critical_rate_100": metrics["critical_rate"] == 1.0,
        "zero_shopping_to_recipe_false_positives": (
            metrics["shopping_to_recipe_false_positives"] == 0
        ),
        "route_action_rate_98": metrics["route_action_rate"] >= 0.98,
        "accepted_items_rate_98": metrics["accepted_items_rate"] >= 0.98,
        "recipe_name_rate_98": metrics.get("recipe_name_rate", 1.0) >= 0.98,
        "consistency_rate_99": metrics["consistency_rate"] >= 0.99,
        "infra_error_rate_max_2": metrics["infra_error_rate"] <= 0.02,
    }
    return {"pass": all(checks.values()), "checks": checks}


def model_exit_pass(model: dict[str, Any]) -> bool:
    return model["gates"]["pass"]


def add_comparison_flags(models: list[dict[str, Any]]) -> None:
    if not models:
        return
    baseline = models[0]["metrics"]
    baseline_p95 = baseline["latency_ms"]["p95"]
    baseline_tokens = baseline["usage"]["output_tokens"]
    for model in models[1:]:
        metrics = model["metrics"]
        flags: list[str] = []
        p95 = metrics["latency_ms"]["p95"]
        output_tokens = metrics["usage"]["output_tokens"]
        if baseline_p95 and p95 and p95 / baseline_p95 > 1.5:
            flags.append("p95 latency exceeds 1.5x baseline")
        if baseline_tokens and output_tokens / baseline_tokens > 1.25:
            flags.append("output tokens exceed 1.25x baseline")
        model["comparison_to_baseline"] = {
            "latency_p95_ratio": ratio(p95, baseline_p95, empty_value=None),
            "output_tokens_ratio": ratio(
                output_tokens,
                baseline_tokens,
                empty_value=None,
            ),
            "flags": flags,
        }


def print_summary(report: dict[str, Any]) -> None:
    print(
        "Corpus "
        f"{report['corpus']['path']} "
        f"cases={report['corpus']['case_count']} "
        f"sha256={report['corpus']['sha256']}"
    )
    print(f"Commit {report['commit'] or 'unknown'}")
    for model in report["models"]:
        metrics = model["metrics"]
        gates = model["gates"]
        print(
            f"{model['model']}: pass={gates['pass']} "
            f"qualified={metrics.get('release_qualified', False)} "
            f"overall={metrics.get('overall_pass_rate', 0):.3f} "
            f"route_action={metrics['route_action_rate']:.3f} "
            f"items={metrics['accepted_items_rate']:.3f} "
            f"recipe_names={metrics.get('recipe_name_rate', 0):.3f} "
            f"critical={metrics['critical_rate']:.3f} "
            f"schema={metrics['schema_valid_rate']:.3f} "
            f"infra={metrics['infra_error_rate']:.3f} "
            f"p95_ms={metrics['latency_ms']['p95']}"
        )
        for prompt_name, prompt in model["prompts"].items():
            print(
                f"  {prompt_name}: revision={prompt['revision']} "
                f"fingerprint={prompt['fingerprint']}"
            )
        flags = model.get("comparison_to_baseline", {}).get("flags", [])
        for flag in flags:
            print(f"  comparison flag: {flag}")
    print("Thresholds are fixed gates, not statistical certainty.")


def prompt_metadata(spec: PromptSpec) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "operation": spec.operation,
        "revision": spec.revision,
        "fingerprint": spec.fingerprint,
        "temperature": spec.temperature,
        "max_output_tokens": spec.max_output_tokens,
    }
    if spec.revision.endswith(" override"):
        metadata["instructions"] = spec.instructions
    return metadata


def current_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def worktree_provenance() -> dict[str, object]:
    status = run_git(["status", "--short", "--untracked-files=all"])
    diff = run_git(["diff", "--binary", "HEAD", "--"])
    untracked_payload = b""
    if status is not None:
        for line in status.splitlines():
            if not line.startswith("?? "):
                continue
            path = Path(line[3:])
            if path.is_file():
                untracked_payload += line.encode("utf-8") + b"\0" + path.read_bytes()
    payload = "\n".join(part for part in (status, diff) if part).encode("utf-8")
    diff_hash = hashlib_sha256(payload + untracked_payload)
    return {
        "dirty": bool(status),
        "status": status.splitlines() if status else [],
        "diff_sha256": diff_hash,
    }


def run_git(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip()


def hashlib_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def normalize_model_names(models: list[str]) -> list[str]:
    return [model.strip() for model in models if model.strip()]


def eval_api_key() -> str:
    return os.environ.get(EVAL_KEY_ENV, "").strip()


def validate_prompt_override(path: str | None, label: str) -> list[str]:
    if path is None:
        return []
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        return [f"{label} override is not readable: {error}"]
    if not content.strip():
        return [f"{label} override must not be blank"]
    return []


@contextmanager
def official_openai_env_only():
    original = {name: os.environ.get(name) for name in OPENAI_AMBIENT_ENV}
    for name in OPENAI_AMBIENT_ENV:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def extract_usage(result: object) -> dict[str, int]:
    usage = getattr(result, "usage", None)
    if usage is None:
        return empty_usage()
    return {
        "input_tokens": int(get_usage_value(usage, "input_tokens") or 0),
        "output_tokens": int(get_usage_value(usage, "output_tokens") or 0),
        "total_tokens": int(get_usage_value(usage, "total_tokens") or 0),
    }


def get_usage_value(usage: object, key: str) -> object:
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def empty_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def add_usage(target: dict[str, int], increment: dict[str, int]) -> None:
    for key in target:
        target[key] += int(increment.get(key, 0))


def ratio(numerator: float, denominator: float, *, empty_value: Any = 0.0) -> Any:
    if denominator == 0:
        return empty_value
    return numerator / denominator


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * quantile))
    return round(ordered[index], 3)


if __name__ == "__main__":
    raise SystemExit(main())
