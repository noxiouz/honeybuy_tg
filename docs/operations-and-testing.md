# Operations And Testing

## Runtime Requirements

- Python 3.13 or newer
- `uv` for locked dependency installation and command execution
- a Telegram bot token
- one configured owner identity
- write access to the configured SQLite path
- outbound Telegram network access
- an OpenAI key and outbound OpenAI access for AI-backed features
- `ffmpeg` for voice-note conversion
- outbound HTTP(S) access for recipe URL learning

Dependencies and the `honeybuy-tg` console script are declared in
[`pyproject.toml`](../pyproject.toml). Exact dependency versions are locked in
[`uv.lock`](../uv.lock).

## Configuration

Normal bot startup loads settings from process environment variables and a
`.env` file in the working directory. Empty values are ignored and unknown
values are allowed. The `migrate` and `healthcheck` commands load only the
database path and do not require Telegram or OpenAI settings.

### Core And Access

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | none; required | Telegram Bot API token |
| `OWNER_USER_ID` | none | Preferred stable Telegram owner ID |
| `OWNER_USERNAME` | none | Alternative owner match by username, with optional leading `@`; at least one owner field is required |
| `ALLOWED_USER_IDS` | empty | Comma-separated IDs allowed to use private chats; a configured `OWNER_USER_ID` is included automatically |
| `DATABASE_PATH` | `./data/honeybuy.sqlite3` | SQLite database path, relative to the working directory when not absolute |
| `TEXT_PARSE_MODE` | `mention` | Default `off`, `mention`, or `all` for chats without an override |
| `LOG_LEVEL` | `INFO` | Python logging level passed through uppercasing |

Prefer `OWNER_USER_ID` after using `/whoami` to discover it. A username can
change, whereas the numeric ID is stable. If both owner settings are present,
the code accepts a user matching either one; keep them synchronized or remove
the username after setup.

### Telegram Inline Mode

Cross-chat item capture requires one manual Telegram-side setting; there is no
environment variable or deployment-file change for it:

1. Open `@BotFather` and send `/setinline`.
2. Select the Honeybuy bot.
3. Set a short placeholder such as `Item to add`.

The bot must also be an administrator in every authorized group that should be
offered as an inline destination. The requester must still be a current member
of that group. Ordinary group authorization with `/authorize` remains required.

Do not rely on `/setinlinefeedback` for correctness. It is unnecessary for this
feature: selecting or sending a result does not mutate a list, and Honeybuy does
not use `chosen_inline_result` as a functional trigger. If Telegram feedback is
enabled independently, Telegram may deliver those updates as telemetry, but
Honeybuy currently ignores them and they have no application state effect.

### OpenAI And Limits

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENAI_API_KEY` | unset | Enables text parsing, categories, identity normalization, recipe commands/extraction, and voice transcription |
| `OPENAI_PARSE_MODEL` | `gpt-5.4-mini` | Shared model for non-transcription AI operations |
| `OPENAI_TRANSCRIBE_MODEL` | `gpt-4o-mini-transcribe` | Voice transcription model |
| `MAX_VOICE_DURATION_SECONDS` | `120` | Reject before download/transcription when Telegram duration is greater |
| `MAX_VOICE_FILE_SIZE_BYTES` | `10000000` | Reject before download when Telegram reports a larger file |
| `MAX_TRANSCRIPT_CHARACTERS` | `4000` | Reject after transcription when text is longer |
| `CATEGORY_CACHE_TTL_SECONDS` | `2592000` | Category cache lifetime, 30 days by default |
| `ITEM_NORMALIZATION_CACHE_TTL_SECONDS` | `7776000` | Canonical item-identity cache lifetime, 90 days by default |

The three limits and two TTLs must be positive. A missing OpenAI key does not
disable the bot: slash commands, local natural-text parsing, saved recipe reuse,
and local-name matching remain available. It does disable voice transcription
and learning new recipes. Categorization can render without AI, while identity
matching uses cached or local fallbacks; a new uncached cross-language match may
therefore be unavailable.

### Metrics

| Variable | Default | Meaning |
| --- | --- | --- |
| `METRICS_ENABLED` | `false` | Start the Prometheus HTTP exporter |
| `METRICS_HOST` | `127.0.0.1` | Exporter bind address |
| `METRICS_PORT` | `9108` | Exporter port, from 1 through 65535 |

Keep the exporter on loopback unless a trusted network boundary or reverse
proxy protects it.

The local and production templates are
[`.env.example`](../.env.example) and
[`deploy/ubuntu/env.example`](../deploy/ubuntu/env.example).

## Local Development

Install dependencies and run all checks from the repository root:

```sh
uv sync
cp .env.example .env
uv run python -m honeybuy_tg
```

Equivalent installed entrypoint:

```sh
uv run honeybuy-tg
```

Run automated checks:

```sh
uv run pytest
uv run ruff check .
```

Most tests create an isolated database under pytest's temporary directory and
do not need the configured runtime database. Telegram tests use a fake aiogram
session; AI tests fake client responses. No live Telegram or OpenAI call should
be needed for the automated suite.

The required CI job runs locked dependency sync, the offline pytest suite,
Ruff, and `git diff --check` for pull requests and `main`. It does not deploy
or use production, Telegram, or OpenAI credentials. Live model evals remain
opt-in operator actions.

## Test Map

| Test module | Main contract |
| --- | --- |
| `test_parser.py` | Deterministic shopping phrases, splitting, filler, and unknown input |
| `test_recipes.py` | Recipe URL/paste/alias/reuse parsing and visible HTML text |
| `test_ai.py` | Strict response models, wrapper payloads, and invalid-response metrics |
| `test_service.py` | Domain validation, inline-item normalization/application, recipe operations, matching, identity deduplication |
| `test_storage.py` | Chat isolation, state transitions, inline-intent atomicity/expiry/quota, transaction/conflict rules, caches, sessions, and migrations |
| `test_formatting.py` | Category grouping, HTML escaping, recipes, and shop rendering |
| `test_telegram_bot.py` | Routing helpers and fake-session integration for inline capture, text, voice context, recipes, and shop mode |
| `test_metrics.py` | AI status/error/cancellation instrumentation |
| `test_config.py` | Owner and positive-limit validation |
| `test_app.py` | Migrate/healthcheck CLI, runtime settings, schema/integrity validation, and offline behavior |
| `test_runtime_assets.py` | Bot service entrypoints, sandbox, and bounded restart behavior |

[`SCENARIOS.md`](../SCENARIOS.md) is the user-visible acceptance checklist.
When behavior changes, update its stable scenario IDs and add the narrowest
module test plus an integration-style Telegram test when routing is involved.

For a focused offline inline-capture check, run:

```sh
uv run pytest -q tests/test_service.py -k inline_capture
uv run pytest -q tests/test_storage.py \
  -k "inline_capture or migrates_v1_database_to_inline_capture_schema"
uv run pytest -q tests/test_telegram_bot.py -k inline
```

These tests use temporary SQLite files and a fake Telegram session. They do not
contact Telegram, OpenAI, or a deployed database. Follow them with the full
`uv run pytest -q`, `uv run ruff check .`, and `git diff --check` gates before
release.

## Text Routing Evals

Text routing has three layers:

- unit and integration tests under `tests/`, always offline;
- a strict JSONL corpus at `evals/cases/text_routing.v1.jsonl`, validated by
  `tests/test_eval_corpus.py`;
- an opt-in live model comparison runner, `python -m evals.live_compare`, for
  release decisions around model or prompt changes.

The corpus records the expected route, action, accepted item variants, accepted
recipe-name alternatives, critical status, and tags for Russian, English,
mixed-language, mention, voice-transcript, shopping, recipe, unhandled, and
ambiguous inputs. Item grading treats accepted variants as unordered normalized
multisets. Recipe-name alternatives must be explicit labels in the corpus, not
hidden stemming. Ambiguous cases must be labeled as `unknown` without guessed
items or recipe names.

Before editing prompts or changing `OPENAI_PARSE_MODEL`, add or review corpus
cases for the behavior being changed, then run the offline suite. For a live
comparison, use a separate evaluation key:

```sh
HONEYBUY_EVAL_OPENAI_API_KEY=... \
  python -m evals.live_compare \
  --allow-live-openai \
  --model gpt-5.4-mini \
  --output evals/results/text-routing.json
```

The live runner ignores ordinary `OPENAI_API_KEY`, strips ambient OpenAI
endpoint/org/project/webhook environment while constructing the client, never
loads `.env`, and refuses to call OpenAI unless `--allow-live-openai`, at least
one nonblank `--model`, and `HONEYBUY_EVAL_OPENAI_API_KEY` are all present. It
uses production prompt fingerprints and response schemas but does not call
Telegram, SQLite, recipe URLs, voice transcription, or production data. Reports
include `HEAD`, a dirty-worktree flag, status lines, and a diff hash to prevent
misattributing results to a clean commit.

Prompt override files are candidate-only. The first `--model` entry always uses
production prompts as the baseline; the second and later entries use any
override files. To compare a prompt change on the same model, pass that model
twice. A run with overrides and fewer than two model entries is rejected before
client construction.

Release gates for each live model are:

- 100% schema-valid completed model responses;
- 100% critical cases;
- zero shopping-to-recipe false positives;
- 100% overall graded cases;
- at least 98% route/action accuracy;
- at least 98% accepted item accuracy where labeled;
- at least 98% recipe-name accuracy where labeled;
- at least 99% consistency across repetitions;
- no more than 2% failed model requests; and
- release qualification with at least 72 cases and at least 3 repetitions.

When comparing models, the first `--model` is the baseline. Candidate reports
flag p95 latency above 1.5x baseline and output tokens above 1.25x baseline.
These gates are deterministic release checks over the maintained corpus, not a
claim of statistical certainty. Smaller custom runs are smoke checks: they can
be useful locally, but the report must show that they are not release-qualified,
and the CLI exits nonzero.

Known coverage gaps remain around live recipe URL fetching: scheme and
content-type rejection, redirects, timeouts, bounded reads, private-network
destinations, and the generic user-facing failure path do not have dedicated
end-to-end tests. Voice eval cases use transcript text and cover
pre-confirmation intent classification only; dispatcher tests own Telegram
confirmation behavior and they do not measure audio transcription quality.

## Database Initialization And Migration

Normal startup calls `Storage.init` before polling, so a new local database is
created and known migrations are applied automatically.

Run the explicit migration path with:

```sh
uv run python -m honeybuy_tg migrate
```

When a migration is applied, the command prints the old and new
`PRAGMA user_version`, applied versions, and the result of
`PRAGMA integrity_check`. An already-current database reports only its current
version and integrity result. The command then exits without loading bot
settings or contacting Telegram.

Never run an older binary against a database advertising a newer schema
version. The migration runner refuses that case by design.

The separate health check needs only `DATABASE_PATH`:

```sh
DATABASE_PATH=./data/honeybuy.sqlite3 \
  uv run python -m honeybuy_tg healthcheck
```

It opens the source read-only, copies it into an in-memory database, and checks
the exact supported `PRAGMA user_version`, required tables and columns,
`PRAGMA integrity_check`, and `PRAGMA foreign_key_check`. It does not load bot
tokens or OpenAI settings, migrate the source, start polling, or make network
requests. Operators can run it before starting a release; the bot service also
uses it as `ExecStartPre`.

## Manual Ubuntu Deployment

The operator deploys releases manually. The repository supplies only
`deploy/systemd/honeybuy-tg.service` and `deploy/ubuntu/env.example`.
A commit, merge, or tag does not trigger a release.

The service template expects an existing `honeybuy` user/group, Python 3.13,
`uv`, `ffmpeg`, and this layout:

- `/opt/honeybuy-tg/releases/<revision>/`: separate release and its `.venv`,
  readable/executable by `honeybuy` and not writable by that runtime user;
- `/opt/honeybuy-tg/current`: symlink to the selected release;
- `/etc/honeybuy-tg/env`: root-owned `0600` runtime configuration, with
  `DATABASE_PATH=/var/lib/honeybuy-tg/honeybuy.sqlite3`;
- `/var/lib/honeybuy-tg/`: runtime-user-owned data directory, with a
  `honeybuy`-owned `0600` SQLite file; and
- a separate private directory for database backups.

For an update on an already configured host:

1. Select a reviewed revision with passing CI and prepare a fresh release
   directory. Run `uv sync --frozen --no-dev --no-editable --python 3.13` there
   as an unprivileged build/operator user. Keep the running release untouched.
   The selected Python interpreter must be accessible to `honeybuy` outside
   `/home` and `/root`, which the service hides with `ProtectHome=true`;
   a virtualenv pointing into the operator's home will not work.
2. Stop `honeybuy-tg.service` and confirm it is inactive. Ensure no other bot
   instance or maintenance process is writing to the database.
3. Create a uniquely named backup with SQLite's backup API or the SQLite CLI
   `.backup` command and validate it with `PRAGMA integrity_check`. Keep it
   outside the release tree. A raw copy of the main database file can omit
   committed WAL data.
4. With the service stopped, use the new release's `.venv/bin/python` to run
   `-m honeybuy_tg migrate` and then `-m honeybuy_tg healthcheck` as
   `honeybuy`, setting only the correct `DATABASE_PATH` for these commands.
   Resolve any failure before proceeding.
5. Make the prepared release root-owned and runtime-readable, switch
   `/opt/honeybuy-tg/current` to it, and start `honeybuy-tg.service`. If the
   service template changed, install it in `/etc/systemd/system/` and run
   `systemctl daemon-reload` before starting.

Check the running service and logs, then run the smoke scenarios below:

```sh
sudo systemctl status honeybuy-tg.service
sudo journalctl -u honeybuy-tg.service -n 100 --no-pager
```

Do not run an older release against a newer schema or restore an old backup
over data that has received newer writes. Keep the service stopped and choose
a compatible forward fix or an explicit, reviewed data recovery procedure.

Removing repository files does not disable automation already installed on a
host. If any deployment automation remains there, disable it before manual
updates so it cannot race the operator. This documentation does not claim that
the production host has been changed.

## Observability

Process logs use timestamp, level, logger name, and message on standard output,
which systemd captures in journald:

```sh
sudo journalctl -u honeybuy-tg -f
```

Useful Prometheus metric families are:

- `honeybuy_info` and `honeybuy_metrics_exporter_up`;
- `honeybuy_bot_starts_total`;
- `honeybuy_telegram_messages_total{kind,status}`;
- `honeybuy_shopping_actions_total{action,source}`;
- `honeybuy_ai_requests_total{operation,status}`;
- `honeybuy_ai_request_seconds{operation}`;
- `honeybuy_voice_rejections_total{reason}`; and
- `honeybuy_voice_transcript_chars`.

Metrics contain no chat or user labels, avoiding unbounded per-user cardinality.
They also do not expose database health, migration state, recipe-import counts,
or queue depth. Combine them with logs and selected `events` rows when
diagnosing a request.

## Post-Deploy Smoke Test

At minimum, verify:

- `/whoami`, private access, group `/authorize`, and an unauthorized case;
- after enabling `@BotFather` `/setinline`, type `@bot_username milk` from an
  unrelated chat, confirm that the private list and only currently eligible
  authorized groups appear, then send one result;
- before tapping `Confirm add`, verify the destination list is unchanged and
  the public card reveals neither its destination nor existing list contents;
- tap `Confirm add`, verify exactly one literal `milk` item appears in the
  selected list, and verify a repeated tap cannot add it again;
- repeat with `milk and eggs` to confirm it is stored as one literal item, then
  spot-check expiry or loss of group capability before confirmation;
- `/add`, `/list`, `/remove`, `/bought`, and `/clear_bought`;
- `/shop` and repeated/stale `Got` callbacks;
- `mention` and `all` natural-text modes plus a mention-only reply;
- a direct voice note, replied voice reanalysis, and bare-item confirmation;
- recipe learning from both URL and paste, reuse, alias, delete, cancel, and
  overwrite;
- `/list`, `/shop`, `/remove`, and `/bought` against legacy rows after identity
  schema or normalization changes; and
- metrics reachability when enabled.

The detailed maintained checklist is at the end of
[`SCENARIOS.md`](../SCENARIOS.md).

## Known Operational Gaps

Database backup and restore are manual operator responsibilities. There is no
general point-in-time restore command, scheduled backup policy, HTTP health
endpoint, log-rotation policy, general state-retention task, voice/recipe
confirmation reaper, inline-intent background reaper, or multi-instance
coordination. Inline intents have logical expiry and opportunistic cleanup;
they do not require a worker for correctness.
