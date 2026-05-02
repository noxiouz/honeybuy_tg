import asyncio

import pytest

from honeybuy_tg import metrics


def test_record_ai_request_reports_custom_status(monkeypatch):
    observed = []

    def observe(**kwargs):
        observed.append(kwargs)

    monkeypatch.setattr(metrics, "_observe_ai_request", observe)

    with metrics.record_ai_request(operation="text_parse") as report:
        report.report_status("invalid_json")

    assert len(observed) == 1
    assert observed[0]["operation"] == "text_parse"
    assert observed[0]["status"] == "invalid_json"
    assert observed[0]["duration_seconds"] >= 0


def test_record_ai_request_reports_errors(monkeypatch):
    observed = []

    def observe(**kwargs):
        observed.append(kwargs)

    monkeypatch.setattr(metrics, "_observe_ai_request", observe)

    with pytest.raises(RuntimeError):
        with metrics.record_ai_request(operation="text_parse"):
            raise RuntimeError("request failed")

    assert observed[0]["operation"] == "text_parse"
    assert observed[0]["status"] == "error"


def test_record_ai_request_reports_cancellation(monkeypatch):
    observed = []

    def observe(**kwargs):
        observed.append(kwargs)

    monkeypatch.setattr(metrics, "_observe_ai_request", observe)

    with pytest.raises(asyncio.CancelledError):
        with metrics.record_ai_request(operation="text_parse"):
            raise asyncio.CancelledError

    assert observed[0]["operation"] == "text_parse"
    assert observed[0]["status"] == "cancelled"


def test_record_ai_request_preserves_reported_failure_status(monkeypatch):
    observed = []

    def observe(**kwargs):
        observed.append(kwargs)

    monkeypatch.setattr(metrics, "_observe_ai_request", observe)

    with pytest.raises(ValueError):
        with metrics.record_ai_request(operation="text_parse") as report:
            report.report_status("invalid_response")
            raise ValueError("bad response")

    assert observed[0]["status"] == "invalid_response"


@pytest.mark.asyncio
async def test_record_ai_request_async_reports_status(monkeypatch):
    observed = []

    def observe(**kwargs):
        observed.append(kwargs)

    monkeypatch.setattr(metrics, "_observe_ai_request", observe)

    async with metrics.record_ai_request_async(operation="voice_transcription"):
        pass

    assert observed[0]["operation"] == "voice_transcription"
    assert observed[0]["status"] == "ok"
    assert observed[0]["duration_seconds"] >= 0


@pytest.mark.asyncio
async def test_record_ai_request_async_reports_cancellation(monkeypatch):
    observed = []

    def observe(**kwargs):
        observed.append(kwargs)

    monkeypatch.setattr(metrics, "_observe_ai_request", observe)

    with pytest.raises(asyncio.CancelledError):
        async with metrics.record_ai_request_async(operation="text_parse"):
            raise asyncio.CancelledError

    assert observed[0]["operation"] == "text_parse"
    assert observed[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_record_ai_request_async_preserves_reported_failure_status(monkeypatch):
    observed = []

    def observe(**kwargs):
        observed.append(kwargs)

    monkeypatch.setattr(metrics, "_observe_ai_request", observe)

    with pytest.raises(ValueError):
        async with metrics.record_ai_request_async(operation="text_parse") as report:
            report.report_status("invalid_response")
            raise ValueError("bad response")

    assert observed[0]["operation"] == "text_parse"
    assert observed[0]["status"] == "invalid_response"
