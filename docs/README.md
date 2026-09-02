# Honeybuy Developer Documentation

Honeybuy is a private, chat-scoped Telegram shopping-list bot. It accepts slash
commands, selected natural-language messages, voice notes, inline-button
callbacks, and recipe text or links. SQLite is the source of truth; OpenAI is an
optional capability provider for everything except voice transcription and new
recipe extraction, where it is required.

This directory describes how the implemented system fits together. It
complements, rather than replaces, the root documentation:

- [README](../README.md) is the user and operator quick start.
- [SCENARIOS](../SCENARIOS.md) is the behavior-level functional specification.
- [PLAN](../PLAN.md) records current status, deployment work, and backlog.

There is no generic file, Telegram-document, CSV, or JSON import subsystem in
the current code. See [Ingestion And Recipes](ingestion-and-recipes.md) for the
input paths that do exist and their exact constraints.

## Reading Guide

- [Architecture](architecture.md) explains process startup, module boundaries,
  dependencies, and external systems.
- [Request Lifecycle](request-lifecycle.md) traces authorization, shopping
  commands, natural text, voice, reply context, callbacks, and shopping mode.
- [Ingestion And Recipes](ingestion-and-recipes.md) documents recipe URL and
  pasted-text ingestion, extraction, overwrite protection, and unsupported
  import formats.
- [Persistence](persistence.md) describes the SQLite schema, state transitions,
  transaction boundaries, caches, migrations, and retention behavior.
- [Operations And Testing](operations-and-testing.md) collects configuration,
  local development, deployment, observability, and verification guidance.

## System Invariants

Keep these properties intact when changing the project:

- Telegram `chat_id` is the tenant boundary for lists, recipes, aliases,
  settings, confirmations, shop sessions, and tracked bot-message context.
- Category and item-identity caches are deliberately global by normalized item
  name; they contain reusable classification data rather than chat state.
- Direct item actions transition active rows to `bought` or `removed`, and
  clearing bought history transitions bought rows to `removed`. Normal
  shopping-list cleanup is a soft state transition, not row deletion.
- An authorized group has one shared list for all its members. Owner-only
  operations remain owner-only inside that group.
- Deterministic parsing and core slash commands continue to work without an
  OpenAI key. Voice transcription and learning a new recipe do not.
- A recipe header and its replacement ingredient set are saved atomically.
  Adding all ingredients from a saved recipe is a sequence of independent item
  inserts and can therefore be partially applied if processing fails midway.
- `/list` and `/shop` are not strictly read-only: they may backfill item
  identities and soft-remove equivalent active rows during deduplication.
- The `events` table is selective diagnostic history, not a complete audit log.

## Repository Map

```text
src/honeybuy_tg/
  __init__.py       console-script entrypoint
  __main__.py       python -m entrypoint
  app.py            settings, migrate command, process startup
  config.py         environment-backed settings and validation
  telegram_bot.py   aiogram composition root, routes, callbacks, voice I/O
  service.py        shopping and recipe domain orchestration
  storage.py        SQLite repository and persistence rules
  migrations.py     schema version and migrations
  models.py         immutable domain data objects
  parser.py         deterministic shopping-text parser
  recipes.py        recipe command parsing and URL-to-text fetching
  ai.py             OpenAI adapters and response validation
  formatting.py     Telegram-facing text rendering
  metrics.py        Prometheus instrumentation

tests/               module-aligned unit and integration-style tests
deploy/              Ubuntu installer, environment template, systemd unit
README.md            quick start and operator guide
SCENARIOS.md         user-visible functional contract
PLAN.md              project status and backlog
```

## Architectural Shortcuts To Know About

The code is intentionally small and optimized for one household bot process:

- `build_dispatcher` in `telegram_bot.py` is both the delivery adapter and the
  composition root. Its handlers call `ShoppingListService` for domain actions
  and `Storage` directly for transport-related state such as authorization,
  caches, confirmations, events, and message context.
- `Storage` exposes `async` methods but uses synchronous `sqlite3` calls and
  normally opens a fresh connection for each operation. Large or parallel batch
  workloads would require a different persistence strategy.
- The deployed topology is one long-polling process and one local SQLite file.
  The repository has no HTTP application API, job queue, or multi-node
  coordination.

These are current implementation facts, not guarantees that future versions
must preserve.

## Agentic Coding Configuration

The repository defines a Codex-oriented autonomous development setup:

- root `AGENTS.md` defines project boundaries, safety rules, verification, and
  the review/fix/retest loop;
- `.codex/agents/` defines `coder`, `test_engineer`, `telegram_expert`, and
  `reviewer` roles.

For nontrivial changes, the primary keeps a path-ownership ledger. For behavior
changes, the test engineer establishes the regression contract before the coder
implements it. Telegram-facing work waits for the specialist's constraints, and
the independent reviewer must return `VERDICT: APPROVE` before handoff.

Outside the two role-local `sandbox_mode = "read-only"` defaults, the repository
intentionally does not set `agents.enabled`, concurrency, model, credentials,
plugins, MCP servers, parent-session permissions, sandboxing, or network access.
Contributor-level configuration remains authoritative.
