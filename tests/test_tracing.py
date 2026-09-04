"""Persistence-facing routing trace contract; all checks stay offline."""

import json
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from honeybuy_tg.storage import Storage
from honeybuy_tg.tracing import (
    MAX_BYTES, MAX_EVENTS, MAX_RECORDS, Reason, RoutingTrace, Stage,
    clean_trace, current_trace, record, render_trace,
)


@pytest.mark.asyncio
async def test_routing_trace_missing_is_none(tmp_path):
    storage = Storage(tmp_path / "traces.sqlite3")
    await storage.init()

    assert await storage.get_routing_trace(chat_id=1, message_id=10) is None


def test_trace_closed_catalog_bounded_and_fail_open(monkeypatch):
    trace = RoutingTrace()
    token = current_trace.set(trace)
    try:
        record(Stage.ROUTING, Reason.RECEIVED, secret="privacy-canary")
        record("privacy-canary", Reason.RECEIVED)
        for _ in range(100):
            record(Stage.SHOPPING, Reason.LOCAL_ACTION, action="add_items")
        value = trace.snapshot()
        assert len(value["events"]) == MAX_EVENTS
        assert len(json.dumps(value).encode()) <= MAX_BYTES
        assert "privacy-canary" not in json.dumps(value)
        assert len(render_trace(value)) <= 4096
        trace.closed = True
        record(Stage.ROUTING, Reason.ERROR)
        assert trace.snapshot() == value
    finally:
        current_trace.reset(token)


@pytest.mark.asyncio
async def test_trace_ttl_boundary_and_global_cap_ties(tmp_path):
    storage = Storage(tmp_path / "traces.sqlite3")
    await storage.init()
    now = datetime(2026, 9, 5, tzinfo=UTC)
    trace = RoutingTrace().snapshot()
    for number in range(MAX_RECORDS + 1):
        await storage.save_routing_trace(
            chat_id=number % 2, message_id=number, trace=trace, now=now
        )
    assert await storage.get_routing_trace(chat_id=0, message_id=0, now=now) is None
    assert await storage.get_routing_trace(chat_id=1, message_id=1, now=now) == trace
    before = now + timedelta(hours=24) - timedelta(microseconds=1)
    assert await storage.get_routing_trace(chat_id=1, message_id=1, now=before) == trace
    assert await storage.get_routing_trace(
        chat_id=1, message_id=1, now=now + timedelta(hours=24)
    ) is None
    with storage.connect() as db:
        assert db.execute("SELECT count(*) FROM routing_traces").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["not-json", "x" * (MAX_BYTES + 1), "[]"])
async def test_trace_corrupt_storage_is_unavailable(tmp_path, payload):
    storage = Storage(tmp_path / "traces.sqlite3")
    await storage.init()
    await storage.save_routing_trace(chat_id=1, message_id=1, trace=RoutingTrace().snapshot())
    with storage.connect() as db:
        db.execute("UPDATE routing_traces SET trace_json = ?", (payload,))
        db.commit()
    assert await storage.get_routing_trace(chat_id=1, message_id=1) is None


def test_trace_unknown_fields_and_unapproved_model_are_redacted():
    from honeybuy_tg.prompts import SHOPPING_TEXT_PROMPT

    trace = RoutingTrace()
    trace.append(
        Stage.AI, Reason.VALIDATED,
        operation=SHOPPING_TEXT_PROMPT.operation,
        revision=SHOPPING_TEXT_PROMPT.revision,
        fingerprint=SHOPPING_TEXT_PROMPT.fingerprint,
        model="private-canary-model", schema="valid", text="private-canary-text",
    )
    value = trace.snapshot()
    value["secret"] = "private-canary-envelope"
    cleaned = clean_trace(value)
    assert cleaned["events"][0]["model"] == "other"
    assert "private-canary" not in json.dumps(cleaned)


def test_trace_metadata_does_not_expand_metric_labels():
    from honeybuy_tg import metrics

    expected = {
        "TELEGRAM_MESSAGES": ("kind", "status"),
        "SHOPPING_ACTIONS": ("action", "source"),
        "AI_REQUESTS": ("operation", "status"),
        "AI_REQUEST_SECONDS": ("operation",),
        "VOICE_REJECTIONS": ("reason",),
    }
    for name, labels in expected.items():
        assert getattr(metrics, name)._labelnames == labels


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "exception"])
async def test_trace_context_interleaving_and_failure_reset(tmp_path, failure):
    from aiogram.types import Chat, Message, User
    from honeybuy_tg.telegram_bot import RoutingTraceMiddleware

    storage = Storage(tmp_path / "traces.sqlite3")
    await storage.init()
    middleware = RoutingTraceMiddleware(storage)
    entered = asyncio.Event()
    release = asyncio.Event()
    seen = []
    async def slow(event, data):
        seen.append(current_trace.get())
        record(Stage.SHOPPING, Reason.LOCAL_ACTION, action="add_items")
        entered.set()
        await release.wait()
        raise RuntimeError("private-canary")
    async def fast(event, data):
        seen.append(current_trace.get())
        record(Stage.RECIPE, Reason.DETERMINISTIC_MATCH, action="add_recipe")
        return "ok"
    def message(number):
        return Message(message_id=number, date=datetime.now(UTC), chat=Chat(id=1, type="private"),
                       from_user=User(id=42, is_bot=False, first_name="Owner"))
    task = asyncio.create_task(middleware(slow, message(1), {}))
    await entered.wait()
    assert await middleware(fast, message(2), {}) == "ok"
    assert current_trace.get() is None
    if failure == "cancel":
        task.cancel()
    else:
        release.set()
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError):
        await task
    first = await storage.get_routing_trace(chat_id=1, message_id=1)
    second = await storage.get_routing_trace(chat_id=1, message_id=2)
    assert first["outcome"] == ("cancelled" if failure == "cancel" else "failed")
    assert second["outcome"] == "processed"
    assert first["correlation_id"] != second["correlation_id"]
    assert all(event.get("action") != "add_recipe" for event in first["events"])
    assert all(event.get("action") != "add_items" for event in second["events"])
    assert all(trace.closed for trace in seen)
    assert current_trace.get() is None
