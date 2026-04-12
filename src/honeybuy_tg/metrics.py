import logging

from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)

_server_started = False

INFO = Gauge(
    "honeybuy_info",
    "Static Honeybuy bot process info.",
    ["version"],
)
METRICS_EXPORTER_UP = Gauge(
    "honeybuy_metrics_exporter_up",
    "Whether the Prometheus metrics exporter was started.",
)
BOT_STARTS = Counter(
    "honeybuy_bot_starts_total",
    "Bot process starts.",
)
TELEGRAM_MESSAGES = Counter(
    "honeybuy_telegram_messages_total",
    "Telegram messages and callback queries handled by the bot.",
    ["kind", "status"],
)
SHOPPING_ACTIONS = Counter(
    "honeybuy_shopping_actions_total",
    "Shopping-list mutations applied by the bot.",
    ["action", "source"],
)
AI_REQUESTS = Counter(
    "honeybuy_ai_requests_total",
    "OpenAI API requests made by the bot.",
    ["operation", "status"],
)
AI_REQUEST_SECONDS = Histogram(
    "honeybuy_ai_request_seconds",
    "OpenAI API request duration.",
    ["operation"],
)
VOICE_REJECTIONS = Counter(
    "honeybuy_voice_rejections_total",
    "Voice messages rejected before transcription.",
    ["reason"],
)
VOICE_TRANSCRIPT_CHARS = Histogram(
    "honeybuy_voice_transcript_chars",
    "Voice transcript length in characters.",
    buckets=(0, 20, 50, 100, 200, 500, 1000, 2000, 4000, 8000),
)


def start_metrics_exporter(*, host: str, port: int) -> None:
    global _server_started
    if _server_started:
        return
    start_http_server(port, addr=host)
    INFO.labels(version="0.1.0").set(1)
    METRICS_EXPORTER_UP.set(1)
    _server_started = True
    logger.info("Prometheus metrics exporter listening on %s:%s", host, port)


def record_bot_start() -> None:
    BOT_STARTS.inc()


def record_message(*, kind: str, status: str = "ok") -> None:
    TELEGRAM_MESSAGES.labels(kind=kind, status=status).inc()


def record_shopping_action(*, action: str, source: str, count: int = 1) -> None:
    if count > 0:
        SHOPPING_ACTIONS.labels(action=action, source=source).inc(count)


def record_ai_request(*, operation: str, status: str, duration_seconds: float) -> None:
    AI_REQUESTS.labels(operation=operation, status=status).inc()
    AI_REQUEST_SECONDS.labels(operation=operation).observe(duration_seconds)


def record_voice_rejection(reason: str) -> None:
    VOICE_REJECTIONS.labels(reason=reason).inc()


def observe_voice_transcript_chars(length: int) -> None:
    VOICE_TRANSCRIPT_CHARS.observe(length)
