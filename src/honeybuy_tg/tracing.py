"""Bounded, content-free diagnostics. Context carries correlation, never authority."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
import json
import re
import secrets
from time import monotonic

from honeybuy_tg.prompts import PROMPT_SPECS

RULE_REVISION = "routing-v1"
MAX_EVENTS = 32
MAX_BYTES = 16384
MAX_RECORDS = 1000


class Stage(StrEnum):
    ROUTING = "routing"
    AUTH = "auth"
    RECIPE = "recipe"
    SHOPPING = "shopping"
    REPLY = "reply"
    VOICE = "voice"
    AI = "ai"
    ACTION = "action"
    IDENTITY = "identity"
    CATEGORY = "category"


class Reason(StrEnum):
    RECEIVED = "received"
    COMMAND_MATCHED = "command_matched"
    UNSUPPORTED_CONTENT = "unsupported_content"
    MISSING_SENDER = "missing_sender"
    UNAUTHORIZED = "unauthorized"
    TEXT_MODE_OFF = "text_mode_off"
    MENTION_MISSING = "mention_missing"
    SLASH_COMMAND_IGNORED = "slash_command_ignored"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    ERROR = "error"
    CANCELLED = "cancelled"
    LOCAL_ACTION = "local_action"
    LOCAL_UNKNOWN = "local_unknown"
    AI_UNAVAILABLE = "ai_unavailable"
    DETERMINISTIC_MATCH = "deterministic_match"
    MARKER_ABSENT = "marker_absent"
    HANDLED = "handled"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    CONFIRMATION_PENDING = "confirmation_pending"
    DURATION_LIMIT = "duration_limit"
    FILE_SIZE_LIMIT = "file_size_limit"
    TRANSCRIPT_LENGTH_LIMIT = "transcript_length_limit"
    SELECTED = "selected"
    COMPLETED = "completed"
    INVALID_SCHEMA = "invalid_schema"
    VALIDATED = "validated"
    CACHE_HIT = "cache_hit"
    LOCAL_FALLBACK = "local_fallback"


class Outcome(StrEnum):
    PROCESSED = "processed"
    IGNORED = "ignored"
    REJECTED = "rejected"
    CONFIRMATION_PENDING = "confirmation_pending"
    FAILED = "failed"
    CANCELLED = "cancelled"


MODELS = frozenset(
    {"gpt-5.4-mini", "gpt-4o-mini-transcribe", "gpt-4o-mini", "gpt-4o", "other"}
)
ACTIONS = frozenset(
    {"add_items", "remove_items", "mark_bought", "show_list", "unknown",
     "learn_recipe", "add_recipe", "recipe_alias", "clear_bought", "delete_recipe"}
)


COMMANDS = frozenset({
    "whoami", "start", "help", "authorize", "list", "shop", "add", "remove",
    "bought", "clear_bought", "clear", "recipes", "recipe_alias", "delete_recipe",
    "text_parse_mode", "trace",
})


def clean_event(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Invalid event")
    result = {
        "stage": Stage(value["stage"]).value,
        "reason": Reason(value["reason"]).value,
    }
    for key in ("elapsed_ms", "duration_ms"):
        if key in value:
            elapsed = value[key]
            if type(elapsed) is not int or not 0 <= elapsed <= 86400000:
                raise ValueError("Invalid timing")
            result[key] = elapsed
    if "action" in value and value["action"] in ACTIONS:
        result["action"] = value["action"]
    if "command" in value:
        if value["command"] not in COMMANDS:
            raise ValueError("Invalid command")
        result["command"] = value["command"]
    if "operation" in value:
        spec = PROMPT_SPECS.get(value["operation"])
        if spec is None:
            raise ValueError("Invalid operation")
        revision = value.get("revision")
        fingerprint = value.get("fingerprint")
        if (
            not isinstance(revision, str)
            or re.fullmatch(r"v[0-9]{1,6}", revision) is None
        ):
            raise ValueError("Invalid prompt revision")
        if (
            not isinstance(fingerprint, str)
            or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None
        ):
            raise ValueError("Invalid prompt fingerprint")
        result.update(
            operation=spec.operation, revision=revision, fingerprint=fingerprint
        )
        result["model"] = (
            value.get("model") if value.get("model") in MODELS else "other"
        )
        result["schema"] = (
            value.get("schema")
            if value.get("schema")
            in {"valid", "invalid", "not_applicable", "not_validated"}
            else "not_validated"
        )
    return result


def clean_trace(value: dict) -> dict:
    correlation = value["correlation_id"]
    if (
        not isinstance(correlation, str)
        or re.fullmatch(r"[a-f0-9]{32}", correlation) is None
    ):
        raise ValueError("Invalid correlation")
    if value["rule_revision"] != RULE_REVISION:
        raise ValueError("Invalid revision")
    events = value["events"]
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("Invalid events")
    result = dict(
        correlation_id=correlation,
        rule_revision=RULE_REVISION,
        outcome=Outcome(value["outcome"]).value,
        events=[clean_event(event) for event in events],
    )
    if len(json.dumps(result).encode()) > MAX_BYTES:
        raise ValueError("Trace too large")
    return result


@dataclass
class RoutingTrace:
    correlation_id: str = field(default_factory=lambda: secrets.token_hex(16))
    events: list[dict] = field(default_factory=list)
    outcome: Outcome = Outcome.PROCESSED
    started: float = field(default_factory=monotonic)
    closed: bool = False

    def append(self, stage: Stage, reason: Reason, **metadata) -> None:
        if not self.closed and len(self.events) < MAX_EVENTS:
            event = clean_event(
                dict(
                    stage=stage,
                    reason=reason,
                    elapsed_ms=min(
                        86400000, max(0, int((monotonic() - self.started) * 1000))
                    ),
                    **metadata,
                )
            )
            candidate = self.events + [event]
            if len(json.dumps(candidate).encode()) < MAX_BYTES - 512:
                self.events.append(event)

    def snapshot(self) -> dict:
        return clean_trace(
            dict(
                correlation_id=self.correlation_id,
                rule_revision=RULE_REVISION,
                outcome=self.outcome,
                events=self.events,
            )
        )


current_trace: ContextVar[RoutingTrace | None] = ContextVar(
    "routing_trace", default=None
)


def record(
    stage: Stage, reason: Reason, *, outcome: Outcome | None = None, **metadata
) -> None:
    try:
        trace = current_trace.get()
        if trace is not None and not trace.closed:
            if outcome is not None:
                trace.outcome = Outcome(outcome)
            trace.append(stage, reason, **metadata)
    except Exception:
        pass


def render_trace(value: dict) -> str:
    trace = clean_trace(value)
    lines = [
        f"Trace {trace['correlation_id']}",
        f"Rules: {trace['rule_revision']}",
        f"Outcome: {trace['outcome']}",
    ]
    for event in trace["events"]:
        line = f"{event['stage']}: {event['reason']}"
        for key in (
            "command",
            "action",
            "operation",
            "model",
            "revision",
            "schema",
            "elapsed_ms",
            "duration_ms",
        ):
            if key in event:
                line += f" {key}={event[key]}"
        if "fingerprint" in event:
            line += f" fingerprint={event['fingerprint']}"
        lines.append(line)
    return "\n".join(lines)[:4096]
