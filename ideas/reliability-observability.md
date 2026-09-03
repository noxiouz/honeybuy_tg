# Reliability and Observability Ideas

Status: unranked brainstorm. These are options for later comparison, not an
implementation plan or a commitment to build every item.

Honeybuy currently runs as one aiogram long-polling process with one local
SQLite database. `MetricsMiddleware` counts broad Telegram outcomes,
`metrics.py` exposes a small low-cardinality Prometheus surface, journald holds
plain-text process logs, and the `events` table records selected paths with
potentially sensitive raw content. The ideas below preserve `chat_id` as the
tenant boundary and use the current module boundaries described in
`docs/architecture.md`.

Cross-cutting constraints for all ideas:

- A reason code is a stable machine-readable value such as
  `route.text.mode_off`; user-facing wording may change independently.
- Prometheus labels must come from bounded enums. Trace IDs, chat IDs, user IDs,
  message IDs, recipe names, item names, URLs, exception strings, and model
  output must never be metric labels.
- Request text, transcripts, recipe bodies, item lists, tokens, usernames,
  callback payloads, and fetched page content must not be logged by default.
- Diagnostic persistence needs an explicit retention period, size limit, and
  deletion behavior. Backups inherit the sensitivity of the source database.
- A process-local context variable may carry correlation state within one
  async update, but it is not durable business state. It must be set and reset
  at the update boundary so concurrent tasks cannot inherit one another's
  identity.
- Observability must be best-effort and bounded: a broken diagnostic sink must
  not block a shopping-list mutation or turn an otherwise handled Telegram
  update into a user-visible failure.

## RO-001 — End-to-end request decision trace

**Category:** Routing diagnostics

**Short description:** Record a bounded sequence of routing decisions for each
handled Telegram update under a random correlation ID.

**Potential value:** Makes “why did the bot treat this as a recipe, shopping
command, reply action, or nothing?” answerable without reconstructing the
control flow from scattered logs.

**Detailed explanation:** The trace would represent decisions already made in
`telegram_bot.py`: update kind, authorization result, effective text mode,
mention stripping, reply-source selection, deterministic recipe match, AI
recipe attempt, AI shopping attempt, deterministic fallback, context
resolution, selected action, and delivery result. Every step would contain a
stable stage name and reason code, plus safe bounded attributes such as route,
action, source, parser kind, schema-valid flag, and item count. The trace should
record that text existed and its length bucket, not the text itself. A final
outcome must be written even for ignored or unknown input.

**Lightweight architecture:** Telegram update → middleware creates trace ID and
request context → routing helpers append bounded decision steps → finalizer
emits one structured summary to logs and, when sampled, a short-lived trace
store.

**Constraints and failure modes:** Reset context at the end of every update,
including cancellation and exceptions. Cap step count and attribute sizes.
Never store bot tokens, raw messages, transcripts, recipe content, URLs,
OpenAI request/response bodies, or exception dumps in the trace. If the trace
store fails, emit one rate-limited health signal and continue normal handling.

## RO-002 — Owner-visible “why?” explainer

**Category:** Operator diagnostics / Telegram UX

**Short description:** Let the owner retrieve a sanitized explanation of the
most recent decision for a message or correlation ID.

**Potential value:** Turns routing failures reported in Telegram into
actionable evidence without requiring SSH access or exposing internal prompts.

**Detailed explanation:** The explanation could say, for example: text parsing
was enabled in `mention` mode; the mention was found; no deterministic recipe
form matched; the recipe classifier returned `unknown`; the shopping parser
returned `unknown`; local fallback also returned `unknown`; therefore no reply
was sent. It should display stable reason codes and coarse facts such as action,
route, fallback, validation status, and count, while keeping model
chain-of-thought, prompts, raw input, and secrets hidden. Access should be
owner-only and
chat-scoped; a group member must not inspect another user's diagnostic record.

**Lightweight architecture:** Sampled request trace → sanitized explainer view →
owner-only Telegram command or reply action → formatted explanation with trace
ID and expiry time.

**Constraints and failure modes:** A missing/expired trace should produce a
clear `trace.not_found_or_expired` result. Explanations must not claim certainty
about model reasoning; they describe observable routing decisions only. Apply a
short retention window and a per-owner rate limit, and never echo the original
message unless the owner explicitly supplies it again.

## RO-003 — Stable reason-code catalog

**Category:** Diagnostic contracts

**Short description:** Define one versioned taxonomy for routing, access,
fallback, delivery, storage, and lifecycle outcomes.

**Potential value:** Aligns logs, traces, metrics, tests, and operator docs so a
single failure has the same name everywhere.

**Detailed explanation:** Current statuses include values such as `ok`,
`error`, `ignored`, `invalid_response`, and voice-specific rejection reasons,
while many branches have no explicit reason. A catalog could use namespaces:
`access.*`, `route.*`, `parse.*`, `context.*`, `confirm.*`, `telegram.*`,
`openai.*`, `storage.*`, `recipe_fetch.*`, and `lifecycle.*`. Each entry would
define meaning, terminal/nonterminal status, allowed attributes, metric mapping,
and whether it is safe to show to the owner. Codes remain stable; wording and
implementation details do not become API contracts.

**Lightweight architecture:** Versioned reason-code registry → validation in
trace/event emitters → shared use by Prometheus aggregation, tests, dashboards,
and operator explanations.

**Constraints and failure modes:** Do not generate codes dynamically from
exceptions, callback data, model output, or URLs. Unknown internal errors map to
a bounded `internal.unclassified` code while the exception remains in a
separately protected log. Removing or changing semantics requires a catalog
version change; otherwise historical dashboards silently become misleading.

## RO-004 — Sampled tracing with temporary diagnostic windows

**Category:** Observability controls

**Short description:** Keep aggregate outcomes for every request but persist
full sanitized decision traces only by sampling or during an owner-enabled
debug window.

**Potential value:** Provides deep evidence for intermittent problems without
unbounded database growth or permanent surveillance of household messages.

**Detailed explanation:** Normal mode could retain only terminal reason,
duration, and safe counts. A time-boxed diagnostic window could increase the
sample rate for one chat after explicit owner activation. Sampling decisions
should be made once at trace start so all stages of a request are either kept or
dropped together. The active mode, expiry, and capture policy should be visible
to the owner.

**Lightweight architecture:** Request context → deterministic sampler →
in-memory stage collector → aggregate metric for all traces; sampled sanitized
trace persisted with automatic expiry.

**Constraints and failure modes:** Do not use `chat_id` as a Prometheus label or
write raw text even in debug mode. Cap traces per chat and globally. Diagnostic
mode must expire automatically across restarts using durable expiry state, and
an invalid/expired setting should fail closed to normal low-detail capture.

## RO-005 — Structured journald logging with correlation

**Category:** Logging

**Short description:** Replace ad hoc prose-only log records with structured
fields and a request correlation ID while retaining readable messages.

**Potential value:** Makes production incidents searchable by release, stage,
reason, and trace without parsing changing English sentences.

**Detailed explanation:** Logs could carry fixed fields for service version,
trace ID, update kind, component, reason code, outcome, duration bucket, model
operation, and retry attempt. Existing voice lifecycle, reaction failure,
recipe failure, and startup messages would share the same envelope. Sensitive
identifiers can be represented by short-lived keyed hashes only when correlation
across records is essential; most events need no user or chat identifier.

**Lightweight architecture:** Logging adapter reads the current request context
→ produces JSON or journald-native fields → systemd captures output → saved
queries group by trace/release/reason.

**Constraints and failure modes:** Never log environment values, Telegram file
IDs, callback payloads, text, transcripts, recipes, item names, URLs, OpenAI
payloads, or raw exception messages that may contain them. Bound every field.
If structured serialization fails, fall back to a minimal safe message rather
than serializing the original object.

## RO-006 — Privacy-preserving diagnostic event schema

**Category:** Privacy / data model

**Short description:** Separate bounded diagnostic metadata from raw user
content instead of storing both in the current general-purpose `events` rows.

**Potential value:** Retains useful failure evidence while reducing the impact
of database or backup exposure.

**Detailed explanation:** A new diagnostic record could hold trace ID, reason
code, route, action, parser source, model/prompt fingerprint, length bucket,
item count, status, and timestamps. Optional content capture would be a separate
explicitly enabled facility with tighter access, encryption, and much shorter
retention. Existing `raw_text`, `ai_result_json`, and `error` values should not
be treated as a reliable audit contract; migration planning would decide
whether to redact, expire, or leave old rows until backup rotation completes.

**Lightweight architecture:** Sanitizer at event boundary → metadata table for
routine diagnostics; optional protected content store behind an explicit policy
→ independent cleanup schedules.

**Constraints and failure modes:** JSON must be schema-validated and
size-bounded before insertion. Do not persist raw Telegram text, voice transcripts,
recipe bodies, item arrays, source URLs, exception strings, model responses, or
personal identifiers by default. A sanitizer failure drops optional diagnostic
detail, not the user operation.

## RO-007 — Retention, deletion, and backup privacy lifecycle

**Category:** Data lifecycle

**Short description:** Assign explicit TTLs and deletion semantics to events,
traces, confirmations, bot-message context, shop sessions, caches, and backups.

**Potential value:** Prevents indefinite growth and makes privacy expectations
predictable after a recipe, list, or chat is removed.

**Detailed explanation:** The project currently has no cleanup worker, and a
deleted recipe can remain in `events.raw_text` and backups. A policy matrix
could distinguish business records from diagnostic and ephemeral state. Cleanup
would be incremental, observable, and safe to retry. An owner-level “forget
chat diagnostics” operation could remove only chat-scoped diagnostics without
damaging shared global classification caches. Backup retention must be handled
separately because deleting live rows does not erase old copies.

**Lightweight architecture:** Retention policy registry → periodic bounded
cleanup transaction → tombstone/count metrics → backup expiry job and documented
restore implications.

**Constraints and failure modes:** Never delete active shopping or recipe state
through a diagnostic cleanup path. Use small batches to avoid long SQLite write
locks. Report counts only with low-cardinality record type labels. A failed run
must resume safely and must not continually restart the bot.

## RO-008 — Low-cardinality routing and fallback metrics

**Category:** Metrics

**Short description:** Add counters for routing decisions, parser sources,
fallbacks, unknown outcomes, confirmations, and terminal reason codes.

**Potential value:** Reveals whether Russian text is increasingly unrecognized,
whether recipe routing steals shopping messages, and where users abandon
confirmation flows.

**Detailed explanation:** Useful bounded dimensions include route
(`shopping`, `recipe`, `voice`, `reply_context`, `unhandled`), parser
(`deterministic`, `ai`, `fallback`), action enum, terminal status, and a curated
reason family. Metrics should distinguish “not eligible for parsing” from
“eligible but unknown,” and AI failure from schema-invalid output. A counter for
shopping-to-recipe routing is particularly useful alongside the existing eval
gate.

**Lightweight architecture:** Stable trace terminal event → metric mapper →
Prometheus counters/histograms → dashboards and alerts based on ratios over
time.

**Constraints and failure modes:** Never label by trace, text, chat, user,
message, item, recipe, URL, exception, model-generated string, or full model ID
if the model name is operator-controlled. Pre-initialize or validate allowed
label values so a bug cannot create an unbounded series set.

## RO-009 — Request latency phase breakdown

**Category:** Performance observability

**Short description:** Measure bounded durations for authorization, routing,
AI calls, recipe fetch, SQLite work, ffmpeg, Telegram download, and Telegram
delivery.

**Potential value:** Distinguishes slow OpenAI or Telegram calls from local
SQLite/event-loop stalls and makes regressions visible before users report them.

**Detailed explanation:** Existing metrics time AI operations only. A request
trace could record nested phase durations using a fixed stage enum, plus the
terminal end-to-end duration. Histograms can show p50/p95/p99 per stage. For
multi-item operations, measure total operation time and item count separately;
do not create one metric series per item or request.

**Lightweight architecture:** Monotonic timers around fixed boundaries → trace
stage durations → low-cardinality histograms → latency budget dashboard.

**Constraints and failure modes:** Timers must close on cancellation and errors.
Avoid precise user-identifying timestamps in long-lived trace exports. Metrics
must not include chat/message IDs, URLs, item names, or trace IDs. Excessive
instrumentation around synchronous SQLite calls should not itself increase
event-loop blocking.

## RO-010 — AI outcome, prompt, and cost observability

**Category:** AI reliability

**Short description:** Attribute AI outcomes to an operation, model family,
prompt fingerprint, schema result, fallback result, token counts, and latency.

**Potential value:** Connects live eval findings to production behavior and
shows whether a prompt/model change increases unknowns, invalid responses,
latency, or cost.

**Detailed explanation:** `ai.py` already has strict response validation and
operation-specific metrics. Extend the metadata conceptually with bounded
prompt-contract fingerprints already used by evals, model aliases from an
allowlist, usage counts, request attempt count, and outcome reason. Production
content should never be copied into an eval corpus automatically; suspected
failures should require deliberate redacted curation.

**Lightweight architecture:** AI adapter → common request report → safe usage
and contract metadata → metrics/trace summary → release dashboard compared with
offline and opt-in live eval reports.

**Constraints and failure modes:** Never log prompts, inputs, outputs, API keys,
organization/project IDs, request headers, or raw validation errors containing
model text. Model strings and error classes must be normalized to bounded
values. Missing usage metadata should become `usage.unavailable`, not a failed
user request.

## RO-011 — Explicit AI resilience envelope

**Category:** External dependency reliability

**Short description:** Define operation-specific timeouts, retry rules,
concurrency limits, and circuit behavior instead of relying on SDK defaults.

**Potential value:** Prevents one degraded OpenAI dependency from consuming all
update-processing capacity or making Telegram interactions hang unpredictably.

**Detailed explanation:** Shopping parse, categorization, normalization, recipe
extraction, recipe command parsing, and transcription have different latency
budgets and fallback capabilities. Idempotent requests may retry on a narrow set
of transport/rate-limit failures with jitter and a total deadline. A small
per-operation semaphore can protect the event loop. A circuit may temporarily
skip optional AI paths while deterministic shopping commands continue; voice
and recipe extraction need clear user-visible degradation because no local
equivalent exists.

**Lightweight architecture:** AI operation policy registry → deadline and
concurrency gate → adapter call/retry → bounded outcome reason → deterministic
fallback or explicit unavailable response.

**Constraints and failure modes:** Never retry schema-invalid responses
indefinitely, and never allow a retry storm after process restart. Keep reason
codes bounded, such as `openai.timeout`, `openai.rate_limited`, and
`openai.circuit_open`; do not expose provider error bodies. Circuit state may be
process-local because the topology is single-instance, but must fail safely on
restart.

## RO-012 — Telegram delivery reliability envelope

**Category:** External dependency reliability

**Short description:** Classify Telegram API failures and make delivery retries,
fallbacks, and partial outcomes observable.

**Potential value:** Separates successful domain mutations from failed replies
or edits, reducing silent cases where the list changed but the user saw no
confirmation.

**Detailed explanation:** A request can mutate SQLite, then fail while sending,
editing, reacting, answering a callback, or downloading voice. Each boundary
could report whether the call is safely retryable and whether retrying might
duplicate a message. Nonessential reaction failure should remain nonblocking;
failure to acknowledge a callback has different UX impact from failure to send
the mutation result. The trace terminal state should support
`mutation_succeeded_delivery_failed` rather than flattening everything to
`error`.

**Lightweight architecture:** Telegram call wrapper → bounded failure
classifier and retry budget → delivery outcome attached to request trace →
operator metric/alert for persistent degradation.

**Constraints and failure modes:** Do not log tokens, file IDs, message text,
callback payloads, or Telegram response bodies. Flood-wait values should be
capped/bucketed. Retrying sends can duplicate messages, so only known-idempotent
calls retry automatically; mutation state must not be rolled back merely
because the response could not be delivered.

## RO-013 — Telegram update idempotency ledger

**Category:** Correctness / recovery

**Short description:** Remember processed Telegram update identities and their
terminal outcomes for a bounded time.

**Potential value:** Prevents duplicate item additions or repeated destructive
actions if an update is redelivered after a crash, timeout, or polling restart.

**Detailed explanation:** The ledger would claim an update before mutation and
finish it with a stable terminal reason. A duplicate can either reuse the prior
outcome or decline to repeat the mutation. Because callbacks already enforce
active-state and requester checks in several paths, this adds a uniform outer
boundary for text, commands, voice, and callbacks. Entries need a bounded TTL
longer than realistic Telegram redelivery windows.

**Lightweight architecture:** Update arrives → transactional claim keyed by
bot identity plus update ID → handler runs → terminal result saved → duplicate
lookup short-circuits mutation.

**Constraints and failure modes:** Do not store update bodies. A crash after
claim but before completion creates an `in_progress` entry; recovery needs an
expiry and a rule distinguishing retryable reads from uncertain mutations. The
ledger must remain bot-instance scoped if one database ever serves more than
one bot token, without persisting the token itself.

## RO-014 — Durable operation journal for multi-item mutations

**Category:** Data integrity

**Short description:** Track multi-item operations such as adding recipe
ingredients or parsed item batches as one recoverable unit.

**Potential value:** Makes partial application visible and offers deterministic
resume or compensation after an exception midway through independent inserts.

**Detailed explanation:** `add_recipe_ingredients` currently performs a
sequence of individually committed item inserts. A journal record could capture
an operation ID, chat, operation type, expected count, completed count, and
terminal state without storing raw ingredient text in diagnostic fields. The
business rows can reference the operation ID so recovery discovers exactly what
was applied. A later design choice could make the batch atomic in one SQLite
transaction or deliberately resumable; the journal supports either decision.

**Lightweight architecture:** Service opens operation record → storage applies
items transactionally or marks per-item progress → operation finalizes → stale
operation reconciler reports/resumes safe cases.

**Constraints and failure modes:** Preserve chat scoping and idempotency. Never
retry an uncertain operation by blindly adding the full list. Journal retention
must outlive recovery and backup windows but remain bounded. Metrics expose
operation type/status only, never recipe or item names.

## RO-015 — Confirmation expiry and stranded-claim recovery

**Category:** State lifecycle / recovery

**Short description:** Expire pending confirmations and reconcile recipe
overwrite records stranded in `claiming_recipe_overwrite`.

**Potential value:** Avoids stale buttons, unbounded ephemeral state, and
ambiguous outcomes after a crash between recipe save and confirmation finalize.

**Detailed explanation:** Confirmation types should define TTL, terminal
states, and recovery semantics. Ordinary voice confirmations can safely expire.
Recipe overwrite claims need comparison against the stored target digest and
the proposed result to determine whether the recipe was saved, remained
unchanged, or is uncertain. Stale callbacks should return a clear stable reason
instead of looking like authorization or parsing failures.

**Lightweight architecture:** Confirmation policy by type → conditional claim →
domain action → terminal update; startup/periodic reaper expires pending rows
and reconciles claiming rows using stored version evidence.

**Constraints and failure modes:** Do not replay recipe writes without checking
the expected state digest. Cleanup uses bounded batches. Payloads currently
contain ingredient data, so they require shorter retention and must not be
copied into logs or metrics. Recovery ambiguity should be surfaced as
`confirm.outcome_uncertain`, never guessed.

## RO-016 — Read-only database invariant doctor

**Category:** Data integrity / operator tooling

**Short description:** Provide a safe diagnostic command that checks
Honeybuy-specific invariants beyond SQLite's physical integrity check.

**Potential value:** Detects silent logical corruption, migration drift, and
cross-table inconsistencies before they become user-visible.

**Detailed explanation:** Checks could cover `PRAGMA integrity_check`,
`foreign_key_check`, supported `user_version`, required tables/indexes/columns,
valid item and confirmation states, timestamp consistency, recipe ingredient
positions, alias chat ownership, orphaned shop-session items, malformed item ID
lists, and unexpected cross-chat references. The default mode reports counts
and stable reason codes; any future repair mode should be a separate explicitly
authorized operation with backup prerequisites.

**Lightweight architecture:** Schema/invariant registry → read-only SQLite
inspection → bounded finding summary and exit status → deployment preflight and
operator report.

**Constraints and failure modes:** Do not print row content, names, chat IDs, or
user IDs. Cap example evidence to non-sensitive row identifiers only when
needed. Long scans can block writers in rollback-journal mode, so support a
deadline and clear `doctor.incomplete` result rather than claiming health.

## RO-017 — SQLite concurrency and event-loop isolation

**Category:** Performance / availability

**Short description:** Make SQLite lock handling explicit and move blocking
database work off the async event-loop path.

**Potential value:** Reduces latency spikes and `database is locked` failures as
message volume or multi-item work grows.

**Detailed explanation:** `Storage` is async at the interface but uses
synchronous `sqlite3` operations, usually with a new connection each time and
no configured WAL or busy policy. Options include a single serialized database
executor for the current one-process topology, short bounded busy timeouts,
WAL after deployment validation, and explicit transaction duration metrics.
The design should preserve existing `BEGIN IMMEDIATE` conflict semantics for
recipe writes and confirmations.

**Lightweight architecture:** Async handlers → bounded storage work queue or
dedicated executor → SQLite connection policy → completion returned to handler;
lock wait and queue depth exported as bounded metrics.

**Constraints and failure modes:** A queue introduces backpressure and shutdown
drain concerns; it must be bounded. WAL changes backup requirements because
sidecar files can contain committed data. Never label metrics by SQL text or
table-derived content. Busy retries need a total deadline, not indefinite
blocking.

## RO-018 — Database health and capacity telemetry

**Category:** Storage observability

**Short description:** Export safe gauges for database size, schema version,
age of last successful check, row-count buckets, and stale ephemeral records.

**Potential value:** Warns before disk exhaustion, runaway event growth, or a
stuck cleanup/recovery process affects the bot.

**Detailed explanation:** The operations guide explicitly notes that current
metrics omit database health and migration state. Safe measures include file
size, page/freelist counts, expected/current schema version, last integrity
check status, active/bought/removed item counts, pending/claiming confirmation
counts, and oldest ephemeral record age. Counts should be global or grouped by
a fixed record/status enum, not by chat.

**Lightweight architecture:** Periodic low-priority health sampler → short
read-only queries and filesystem metadata → Prometheus gauges → capacity and
staleness alerts.

**Constraints and failure modes:** Do not run a full integrity check on every
scrape; cache expensive health results. Do not expose database paths, chat IDs,
names, or user activity timestamps. Sampling failure should set a freshness/
status gauge and preserve the last known value with clear staleness semantics.

## RO-019 — Migration preflight and postflight contract

**Category:** Deployment safety

**Short description:** Turn schema deployment into an explicit preflight,
migrate, verify, and record sequence.

**Potential value:** Catches unsupported binaries, missing backup space,
permission errors, schema drift, and foreign-key violations before service
restart.

**Detailed explanation:** Preflight could verify the target database path,
owner/mode, free disk headroom, current and supported versions, service stopped
state, required schema objects, and backup destination. Postflight would run
both integrity and foreign-key checks and record migration versions, release
identity, backup manifest, and duration. It should be usable without requiring
a valid Telegram token, unlike the current settings-loaded migration entrypoint.

**Lightweight architecture:** Deployment gate → read-only preflight → verified
backup → forward migration transaction → postflight doctor → immutable release
record → service start.

**Constraints and failure modes:** Never print environment secrets or database
content. A failed postflight keeps the service stopped and points to the exact
backup; it must not attempt a lossy reverse migration. Stable reasons distinguish
`migration.unsupported_version`, `migration.backup_missing`, and
`migration.integrity_failed`.

## RO-020 — Automated consistent backup with manifest

**Category:** Recovery

**Short description:** Produce verified, timestamped SQLite backups with a
machine-readable manifest instead of relying on an unverified plain copy.

**Potential value:** Reduces data-loss risk and makes it obvious which backup
matches a release and schema version.

**Detailed explanation:** For the current stopped-writer deployment, a backup
job can copy the database and then verify it. A future online path should use
SQLite's backup API rather than copying an active database, especially if WAL
is enabled. The manifest can hold UTC time, schema version, application commit,
file size, checksum, check results, and retention class. Optional encrypted
off-host replication improves host-loss recovery.

**Lightweight architecture:** Backup coordinator → consistent snapshot →
integrity/foreign-key validation → checksum and manifest → permission-restricted
local archive → optional encrypted remote archive → retention job.

**Constraints and failure modes:** Backups contain shopping history, recipes,
user/chat identifiers, and diagnostics; never expose them in logs or CI
artifacts. A checksum verifies transfer, not logical correctness. Backup failure
must block schema deployment, and cleanup must never delete the last known-good
copy.

## RO-021 — Restore drill and rollback rehearsal

**Category:** Recovery validation

**Short description:** Regularly restore a backup into an isolated location and
verify that the application can read it before treating backups as trustworthy.

**Potential value:** Converts “a backup file exists” into evidence that recovery
will work under pressure.

**Detailed explanation:** A drill would select an eligible backup, validate its
manifest/checksum, restore to a temporary permission-restricted path, run the
database doctor and migration compatibility check, and exercise offline storage
reads with content suppressed. The production database, bot process, Telegram,
and OpenAI must not be touched. The result becomes a freshness signal for the
operator.

**Lightweight architecture:** Scheduled/manual drill → isolated temp directory
→ restore and checks with production-like binary → sanitized success/failure
record → alert when no recent successful drill exists.

**Constraints and failure modes:** Never load production `.env`, start polling,
or print restored rows. Securely delete temporary copies after the drill. A
newer-schema backup tested with an older binary should report
`restore.binary_incompatible`, not be migrated backward or marked corrupt.

## RO-022 — Release identity and deployment markers

**Category:** Deployment observability

**Short description:** Make the running commit, dirty-state policy, schema
version, prompt fingerprints, and deployment time visible in startup logs and
safe metrics.

**Potential value:** Answers “what is actually running?” on a copy-deployed
host where `git pull` and `.git` metadata are intentionally absent.

**Detailed explanation:** The local release process could generate a manifest
before `rsync`, and the service would read it at startup. A deployment marker
would record start, migration, service restart, and smoke outcome under a
release ID. This connects incidents and AI behavior to the exact artifact,
instead of relying on the hard-coded `honeybuy_info` version currently set to
`0.1.0`.

**Lightweight architecture:** Clean source tree → release manifest embedded in
copied artifact → startup validation → bounded info metric and structured log →
deployment record/smoke result.

**Constraints and failure modes:** Do not include repository paths, usernames,
remote URLs, environment values, or secrets. Prometheus labels must remain
bounded; if raw commit hashes would create excessive historical series, expose
the hash in an info endpoint/log and keep metrics to a deployment generation.
A missing manifest should be explicit, not silently reported as the latest.

## RO-023 — Isolated staging and canary release path

**Category:** Deployment safety

**Short description:** Validate releases against a separate bot identity,
database, chat, and service before replacing the single production process.

**Potential value:** Finds Telegram integration, environment, ffmpeg, recipe
fetch, and model issues that offline tests cannot reproduce.

**Detailed explanation:** A staging instance would use a dedicated Telegram bot
token and nonproduction chat, a separate SQLite path, distinct metrics port,
and restricted recipe/network policy. A release artifact that passes offline
tests and live evals is deployed there first, then a bounded smoke set runs
before production promotion. This is a deployment concept, not a second replica
sharing the production SQLite file.

**Lightweight architecture:** One immutable release artifact → staging systemd
unit with isolated config/data → operator or synthetic smoke → promotion of the
same artifact → production restart and smoke.

**Constraints and failure modes:** Never point staging at production data or
reuse the production bot token. Synthetic inputs must contain no real household
content. Promotion must require an explicit green result; staging failure must
not modify production. Two production pollers for the same bot token are not a
valid canary strategy.

## RO-024 — Post-deploy synthetic smoke sentinel

**Category:** Availability verification

**Short description:** Run a small deterministic end-to-end check after deploy
using isolated synthetic state and report a release-scoped result.

**Potential value:** Detects that the process is running but cannot receive,
mutate, reply, edit callbacks, reach OpenAI, convert voice, or expose metrics.

**Detailed explanation:** The broad manual checklist in
`docs/operations-and-testing.md` should remain the release acceptance source.
A sentinel can cover a narrow safe subset repeatedly: bot reachability,
authorization behavior, add/list/remove in a dedicated chat, callback handling,
metrics freshness, and optional AI probe with a synthetic phrase. Destructive
cleanup must target only sentinel-created rows tagged by an isolated tenant or
fixture identity.

**Lightweight architecture:** Deployment marker → external smoke runner using
staging/sentinel credentials → bounded scenario IDs → cleanup → signed result
associated with release → alert on failure.

**Constraints and failure modes:** Automated repository tests remain offline;
the sentinel is an explicitly configured operational job. Never run against an
ordinary household chat, leak tokens into output, or leave test items behind.
Rate-limit probes and distinguish Telegram/OpenAI outages from application
regressions with stable reasons.

## RO-025 — Service-level objectives and actionable alerts

**Category:** Reliability management

**Short description:** Define a small set of user-centered indicators and alert
only on sustained, actionable failures.

**Potential value:** Focuses operations on failed user outcomes rather than raw
process uptime or noisy one-off exceptions.

**Detailed explanation:** Candidate indicators include handled-update success,
eligible-text unknown rate, mutation success with delivery success, AI optional
fallback success, voice/recipe required-AI availability, p95 response time,
backup freshness, restore-drill freshness, restart rate, and database capacity.
Different capabilities can have different objectives: deterministic list
commands should remain available during an OpenAI outage, while voice has a
separate dependency-aware objective.

**Lightweight architecture:** Low-cardinality production metrics → recording
rules over rolling windows → dashboard by capability → burn-rate or sustained
threshold alerts → runbook links keyed by reason family.

**Constraints and failure modes:** Avoid alerting on tiny denominators; show
sample volume. Do not label by users/chats or treat every `unknown` text as a
failure when parse mode is `all`. Alerts need deployment annotations and a
clear owner action; otherwise they become noise and get ignored.

## RO-026 — Secure recipe-fetch boundary with explainable failures

**Category:** Security / external dependency reliability

**Short description:** Add explicit destination policy, timeout/redirect/size
budgets, and reason-coded telemetry around recipe URL ingestion.

**Potential value:** Reduces server-side request forgery and hanging fetch risk
while telling the owner whether a recipe failed because of scheme, host,
redirect, content type, size, timeout, or extraction.

**Detailed explanation:** The current fetcher accepts HTTP/HTTPS and bounded
content, but documented test gaps include redirects, timeouts, private-network
destinations, and end-to-end failure paths. Resolve and validate every redirect
target, reject loopback/private/link-local/reserved destinations, cap redirects,
apply connect/read/total deadlines, and preserve the bounded read. The trace
stores only policy outcome, content-type class, size bucket, and duration.

**Lightweight architecture:** URL parser → DNS/IP destination policy → bounded
redirect-aware fetcher → content validator → visible-text extractor → recipe AI
adapter; each boundary emits a stable reason code.

**Constraints and failure modes:** Never log full URLs, query strings,
credentials, page text, response bodies, DNS answers tied to a user request, or
raw network exceptions. DNS rebinding and redirects require validation at the
actual connection target, not only initial parsing. Metrics use bounded outcome
codes only.

## RO-027 — Separate security audit trail for authorization changes

**Category:** Security observability

**Short description:** Record security-relevant state changes separately from
the selective diagnostic `events` table.

**Potential value:** Provides reliable evidence for who authorized a chat,
changed parse mode, cleared data, or attempted an owner-only action without
pretending ordinary request events are a complete audit log.

**Detailed explanation:** Audit event types could cover chat authorization,
bot-added rejection, owner-only setting changes, destructive confirmations,
repeated unauthorized attempts, and future credential/config rotations. Each
record needs actor class, tenant, operation, outcome, release, and timestamp,
with immutable stable types. Access to the trail is restricted to the operator;
it is not exposed to group members.

**Lightweight architecture:** Security decision boundary → append-only bounded
audit event → restricted local store → retention/export policy → anomaly
counters with coarse event type/outcome labels.

**Constraints and failure modes:** Do not store usernames, message text,
callback data, item/recipe names, tokens, or raw IP/network data. Numeric user
and chat IDs are personal data; if durable attribution is necessary, protect
and retain them under an explicit policy rather than placing them in metrics.
Audit-write failure should be highly visible but must not accidentally grant
access.

## RO-028 — Sanitized failure inbox for unresolved requests

**Category:** Product feedback / diagnostics

**Short description:** Collect a bounded queue of unknown, failed, or
ambiguous request summaries for deliberate operator review and eval curation.

**Potential value:** Creates a repeatable path from “the bot misunderstood
солянку” to a reviewed regression case without continuously storing household
conversation.

**Detailed explanation:** Each inbox entry could contain trace ID, route,
reason, parser/model/prompt fingerprint, language/script class, text length
bucket, counts, and timing. The owner can optionally resubmit or explicitly
attach a redacted phrase when creating an eval candidate. Nothing should
automatically promote production messages into the JSONL eval corpus; curation
must define expected route/action and remove personal data.

**Lightweight architecture:** Terminal trace with selected reason families →
sanitized bounded inbox → owner review → explicit redaction/labeling workflow →
separate offline eval-corpus proposal.

**Constraints and failure modes:** No raw text, transcript, recipe, item list,
URL, model output, or exception by default. Enforce per-reason and total quotas,
deduplicate similar metadata, expire entries, and show dropped-count metrics.
The inbox must not become an implicit long-term message archive.

## RO-029 — Startup and crash-loop diagnostic capsule

**Category:** Process reliability

**Short description:** Emit a safe startup readiness summary and retain the
last bounded fatal-stage result across systemd restarts.

**Potential value:** Distinguishes bad config, migration failure, missing
ffmpeg, database permission issues, Telegram initialization failure, and
polling termination in a restart loop.

**Detailed explanation:** Startup is currently a sequence of config validation,
metrics setup, storage migration, Telegram client setup, command registration,
and polling. A lifecycle state machine could mark each stage, release identity,
duration, and stable failure reason. A tiny local capsule survives restart long
enough to count repeated identical failures and suggest the correct runbook.
Readiness means migrations and mandatory dependencies succeeded, not merely
that the process exists.

**Lightweight architecture:** Startup stage tracker → safe structured logs and
last-failure capsule → systemd restart → repeated-failure detector → metric/
notification with release and stage.

**Constraints and failure modes:** Never persist validation error text that may
contain secret values, environment contents, tokens, database content, or
Telegram responses. Bound the capsule to one/few records and atomic replace.
Failure to write diagnostics must not mask the original exit code.

## RO-030 — Unified ephemeral-state maintenance service

**Category:** Lifecycle / maintenance

**Short description:** Run one bounded maintenance loop for expired caches,
stale bot-message context, shop sessions, confirmations, traces, events, and
idempotency records.

**Potential value:** Prevents database growth and stale behavior while avoiding
a separate scheduler or job system for a small single-process installation.

**Detailed explanation:** Each record family defines eligibility, batch size,
minimum retention, and safe deletion order. The loop runs after startup with
jitter and a time budget, records last success and deleted counts, then yields
to normal update processing. Maintenance actions should use storage-layer
methods and short transactions. A manual dry-run mode could report aggregate
counts before policy changes.

**Lightweight architecture:** Policy registry → periodic single-process
maintenance coordinator → per-table bounded cleanup/reconciliation → database
commit → freshness/status metrics and safe summary log.

**Constraints and failure modes:** Only one maintenance pass may run at once.
Never infer that business data is ephemeral; active lists, recipes, aliases,
and authorization state are excluded unless a separate user-requested deletion
policy exists. Do not log deleted row contents or tenant IDs. Repeated failures
back off and alert without crashing or starving the bot.

## Notes for later comparison

The ideas deliberately overlap at interfaces but solve different problems. For
example, RO-001 defines the request evidence, RO-003 defines its vocabulary,
RO-004 controls capture, RO-006 controls persistence, RO-008 aggregates it, and
RO-002 presents it to an owner. Likewise, backup creation (RO-020) and restore
proof (RO-021) are distinct reliability guarantees. A later ranking exercise
can group dependencies, estimate effort, identify mutually exclusive options,
and select a coherent first slice without treating this brainstorm order as
priority.
