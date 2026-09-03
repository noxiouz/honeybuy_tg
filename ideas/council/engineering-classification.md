# Engineering and Operations Complexity Classification

Scope: `EO-001`–`EO-030`, plus `RA-016`, `RA-017`, `RA-018`,
`RA-021`, and `RA-022` (35 ideas). This is a complexity classification, not a
value ranking.

Complexity estimates cover a production-ready implementation against Honeybuy's
current baseline: one Python process, Telegram long polling, synchronous SQLite,
systemd, and manual copy-deploy. They include tests, migrations, rollback,
privacy, observability, and operator burden implied by each idea. An optional
extension is treated as a dependency rather than silently folded into the estimate
when the idea can deliver its stated outcome without it.

- **XS:** documentation, presentation, or configuration with no new persistence.
- **S:** a contained change at an existing boundary.
- **M:** coordinated multi-module work or an additive schema change.
- **L:** a durable state machine or scheduler, an integration or security
  boundary, an irreversible operation, or a material migration.
- **XL:** a topology, tenant, identity, or platform shift, or several high-risk
  boundaries at once.

Confidence describes confidence in the size class, not confidence that the idea
should be implemented.

## XS

| ID | Idea | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| RA-021 | Architecture decision ledger for irreversible forks | Data & application architecture | governance, ADRs | XS | The core deliverable is a numbered Markdown template, status convention, and linkage policy; it adds no runtime path or persistence. Optional CI link/ID linting would be a later S-sized addition. | High |

## S

| ID | Idea | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| EO-006 | Герметичный контрактный стенд для recipe URL fetching | Testing & evals | recipe fetch, network contracts | S | Contained test infrastructure around the existing fetch boundary: an ephemeral loopback server, deterministic failure endpoints, and pure address-policy cases. It does not itself implement runtime SSRF protection; that is EO-012. | High |
| EO-007 | Stateful/property-based тесты жизненного цикла списка | Testing & evals | invariants, SQLite | S | A test-only reference model, generated command sequences, and temporary SQLite exercise existing service/storage APIs. The main cost is keeping the model deliberately smaller than production; no product schema or runtime flow changes. | High |
| EO-015 | Systemd sandboxing для runtime процесса | Privacy & security | systemd, host hardening | S | The core change is iterative hardening of the existing unit plus smoke validation of known file, DNS, temp, and ffmpeg needs. It depends on an explicit runtime write/network contract; artifact immutability from EO-017 and secret lifecycle from RA-022 are complementary rather than part of this contained unit change. | Medium |

## M

| ID | Idea | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| EO-001 | Объяснимый trace решения для каждого входящего сообщения | Reliability & observability | routing, AI diagnostics, privacy | M | Requires outer Telegram middleware/finalization, a bounded typed event schema, instrumentation across routing and AI fallbacks, redaction tests, and log/optional short-lived persistence projections. A stable reason-code catalog, sampling, and retention policy should precede the owner-facing explainer; it must remain separate from audit and domain history. | Medium |
| EO-002 | Конвейер «ошибка в эксплуатации → новый eval-кейс» | Testing & evals | production feedback, curation | M | Adds a trace exporter, quarantine format, sanitization/minimization workflow, corpus validator, and review provenance across diagnostics and eval tooling. It depends on EO-001 and deliberately keeps labeling and promotion into the versioned corpus manual. | High |
| EO-003 | Разделить evals по продуктовым способностям | Testing & evals | AI quality, corpora | M | Several versioned corpora, capability-specific graders, adapters, accepted-variant rules, reports, and gates must be added around the shared runner. It depends on reviewed gold labels and must keep category, identity, recipe extraction, and user-facing-error results separate. | High |
| EO-004 | Контролируемый voice/audio eval-набор | Testing & evals | voice, ffmpeg, live AI | M | Introduces consented or synthetic binary fixtures, a manifest, offline conversion smoke, opt-in transcription runner, route grading, and tool/model fingerprints. It depends on a separate eval credential and the shared capability-eval/reporting infrastructure from EO-003/EO-011. | High |
| EO-005 | Replay-harness для Telegram update-сценариев | Testing & evals | Telegram, scenario DSL | M | Typed update/callback/AI builders, a restrained scenario DSL, recording Bot API fake, database assertions, and resource cleanup span dispatcher integration tests and support code. It reuses the existing fake seam and must not become a second router. | High |
| EO-008 | Матрица миграций из исторических SQLite fixtures | Testing & evals | migrations, release safety | M | Requires frozen per-version schema builders, representative and negative fixtures, CLI-path migration runs, integrity/foreign-key/chat-scope assertions, and idempotency checks. It is a prerequisite for persistent feature breadth and stronger release/restore guarantees. | High |
| EO-009 | Детерминированная fault-injection матрица | Testing & evals | resilience, failure semantics | M | Programmable failpoints and expected state/user/telemetry contracts must cover OpenAI, Telegram, SQLite, ffmpeg, cancellation, and recipe fetching. The breadth is multi-module, though it remains offline test infrastructure; stable boundary contracts and reason codes reduce maintenance. | High |
| EO-010 | Парное и статистически осмысленное сравнение live eval | Testing & evals | model comparison, statistics | M | Changes live-run scheduling, stores attempt-level ordering, pairs baseline/candidate results, and adds bootstrap or exact analysis for quality, consistency, latency, and cost. It depends on repeated live runs and versioned corpora/reports but adds no production state. | High |
| EO-011 | Реестр eval-результатов и тренды качества | Testing & evals | release evidence, artifacts | M | Needs a versioned report schema, sanitizer, immutable artifact retention, summary index/trend rendering, and reviewed baseline-pointer updates. Storage can remain CI/artifact based, but retention and provider/model provenance must be explicit. | Medium |
| EO-013 | Journald retention и защита диска от заполнения | Deployment & operations | logging, disk capacity | M | Production readiness includes safe host/per-unit journal limits, rate limiting, disk metrics, alert thresholds, and a runbook that preserves SQLite and recovery copies. The unknown current scrape/alert path makes this more than a config-only edit; it depends on the monitoring contract in EO-026. | Medium |
| EO-014 | Scheduled dependency compatibility и security drift check | Deployment & operations | supply chain, CI | M | Adds scheduled dependency proposals with constrained permissions, lockfile grouping, the full offline suite, SDK-shape smoke, review metadata, and optionally SBOM output. It integrates with GitHub automation but does not change the application topology. | High |
| EO-016 | Read-only deployment preflight/doctor | Deployment & operations | release checks, SSH | M | A shared requirement/probe schema, local release checks, restricted read-only remote SSH checks, stable verdicts, and remediation output span CLI, deployment assets, and operator permissions. It depends on EO-017 release manifests and benefits from RA-018 command-specific settings. | High |
| EO-023 | Health-check CLI с уровнями readiness | Reliability & observability | probes, readiness, database | M | Typed cheap/deep/external probe modes touch settings, SQLite, ffmpeg, process startup, output contracts, and systemd/deploy integration. It should share a probe registry with EO-016 and depends on RA-018 so offline checks do not require Telegram/OpenAI secrets. | High |
| EO-024 | Event-loop watchdog и отдельный polling health signal | Reliability & observability | asyncio, systemd notify | M | Requires application lifecycle changes, a cancellation-safe heartbeat, `sd_notify`, unit changes, startup grace, and bounded poller state metrics. Complexity remains M if it stays single-process; thresholds should follow measured event-loop/SQLite behavior from EO-030, and aiogram may limit polling-state visibility. | Medium |
| EO-025 | Структурированные JSON-логи с корреляцией | Reliability & observability | logging, redaction | M | A versioned logging envelope, context propagation, adapters across several boundaries, release/request correlation, safe exception normalization, and redaction/cardinality tests are cross-cutting but do not require durable application state. It should project EO-001 traces rather than create a second trace format, and EO-013 sets its storage budget. | High |
| EO-027 | Явная OpenAI resilience policy | Reliability & observability | AI gateway, retries | M | Consolidates client ownership and shutdown, per-operation deadlines/retries, concurrency gates, circuit state, normalized outcomes, fallbacks, and fault tests at an existing provider boundary. EO-009 should qualify retry/cancellation behavior; EO-028 consumes its usage reports. | High |
| EO-028 | Учёт token/cost и мягкие бюджеты | Reliability & observability | AI cost, budgets | M | Gateway usage events, a versioned price table, durable daily aggregates, missing-usage semantics, warning evaluation, and optional per-capability cutoffs require coordinated AI/metrics/storage work and an additive schema. It depends on EO-027; alert delivery can reuse EO-026. | Medium |
| RA-018 | Offline command configuration profiles | Deployment & operations | configuration, safety | M | Command-first parsing, typed per-command settings, dependency construction, path/network/write guards, and negative initialization tests reshape app/config/CLI composition without adding persistence. It is a foundation for EO-016, EO-020, EO-023, and EO-029. | High |
| RA-022 | Runtime and release secret lifecycle | Privacy & security | credentials, incident response | M | A production-ready inventory spans Telegram, OpenAI, deploy SSH, backup, monitoring, webhook, and CI credentials, with redacted permission/age audits plus tested rotation, revocation, recovery, and rollback runbooks. It stores metadata only, but its cross-system operator work is broader than a documentation-only change and supports EO-017/EO-019/EO-022. | High |

## L

| ID | Idea | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| EO-012 | Runtime egress policy для recipe URL | Privacy & security | SSRF, DNS, HTTP | L | This creates a security boundary around untrusted destinations and redirects, including IPv4/IPv6, DNS rebinding, Host/SNI correctness, actual-peer enforcement, and possibly a restricted proxy. It depends on an explicit decision about private recipe hosts and the EO-006 hermetic contract suite. | High |
| EO-017 | Неизменяемые atomic releases с manifest | Deployment & operations | artifacts, rollback | L | Introduces signed immutable bundles, manifests, per-release directories/environments, a `current` symlink, systemd layout changes, verification, pruning, and a material migration from copy-deploy. It depends on EO-021 for schema-safe rollback and RA-022 for signing/deploy credential lifecycle. | High |
| EO-018 | Идемпотентный release state machine | Deployment & operations | orchestration, recovery | L | A durable/recoverable deployment workflow needs checkpoints, receipts, concurrency locking, resumable remote actions, privilege boundaries, and explicit manual smoke acceptance. It composes EO-016/EO-017 with verified backup, migration, health, restore, and rollback contracts. | High |
| EO-019 | Автоматические согласованные SQLite backups | Deployment & operations | data durability, scheduler | L | Adds a scheduled backup worker/timer, SQLite-consistent capture, manifests/checks, atomic publication, encrypted off-host storage, retention, and failure monitoring. It introduces both a scheduler and external storage/key boundary and must be paired with EO-020 restore proof. | High |
| EO-020 | Версионированная restore-команда и регулярный restore drill | Deployment & operations | disaster recovery, SQLite | L | Recovery coordinates artifact selection/decryption, integrity/schema/migration checks, isolated drills, service quiescence, explicit production promotion, and preservation of the failed database. It is a high-risk data operation depending on EO-019, RA-018, defined RPO/RTO, and recoverable keys. | High |
| EO-021 | Rollback pack, связывающий код и состояние базы | Deployment & operations | rollback, migrations | L | Couples release identity, schema compatibility, a verified pre-migration backup, rollback classification, service stop/start, and an explicit potentially lossy restore decision. It depends on EO-017, EO-019, EO-020, and tested forward migrations from EO-008. | High |
| EO-022 | Ручное promotion из GitHub Actions с защищённой средой | Deployment & operations | CI/CD, credentials | L | Adds a protected external promotion path, immutable artifacts, approval gates, a restricted deploy principal/runner, secret handling, deployment receipts, and failure recovery. It should follow EO-016–EO-021 and RA-022; production Telegram smoke remains manual unless a separate test identity is designed. | High |
| EO-026 | Небольшой SLO-набор и actionable alerts | Reliability & observability | monitoring, alerting | L | The repository has only an in-process exporter; full delivery requires trusted scraping, durable metric storage, dashboards, Alertmanager or equivalent, alert-channel delivery, missing-scrape detection, and low-volume SLO semantics. That new monitoring integration depends on EO-013 and the probes/signals from EO-023–EO-025. | High |
| EO-029 | Политика retention и безопасная maintenance-команда | Privacy & security | deletion, SQLite maintenance | L | Production-ready cleanup combines per-data-class policy, additive repository/migration work, dry-run previews, bounded irreversible deletion, scheduling/serialization, backup-aware `VACUUM`, and recovery tests. It depends on a data/deletion registry, EO-019 backups, and the measured storage execution policy from EO-030. | High |
| EO-030 | Подготовка к росту без преждевременного multi-instance | Data & application architecture | SQLite, concurrency | L | Measurement is easy, but completing the idea requires choosing and migrating to one execution/connection policy, possibly WAL/busy deadlines or bounded thread/worker offload, then validating cancellation, shutdown, transactions, backups, and load. It deliberately avoids the XL multi-replica/topology shift; EO-019/EO-020 must qualify any WAL choice. | Medium |
| RA-016 | Feature rollout and interaction compatibility registry | Data & application architecture | feature lifecycle, callbacks | L | A chat-scoped enrollment schema, feature/data prerequisites, interaction versions, stale-callback handling, migrations, exit paths, and preservation/export/deletion semantics become shared release infrastructure with combinatorial tests. It depends on EO-008 plus typed interaction and data-lifecycle foundations. | High |
| RA-017 | Capability-oriented module decomposition | Data & application architecture | maintainability, refactoring | L | The end state redistributes two large composition/storage modules into capability routers, services, repositories, and a stable composition root while preserving authorization, tenancy, transactions, and behavior. Incremental delivery lowers risk, but full completion is a material multi-module migration needing characterization/replay tests such as EO-005 and schema safeguards such as EO-008. | Medium |

## XL

No assigned idea is XL. EO-030 explicitly stops before a multi-instance,
PostgreSQL, webhook, or queue topology shift; the release, recovery, monitoring,
and security-boundary ideas are substantial but remain compatible with one bot
process and one SQLite tenant model.
