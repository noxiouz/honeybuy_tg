# Категории и сложность идей

Это авторитетная **pre-ranking**-карта всего brainstorm-каталога Honeybuy:
176 идей распределены по одной основной категории и оценены по относительной
сложности. Документ нужен для следующего отдельного этапа — сравнения value и
выбора investment candidates. Здесь нет value ranking, roadmap или решения об
имплементации.

Источники: [нейтральный каталог](CATALOG.md), пять первичных классификаций
консилиума в [`council/`](council/), независимые review в
[`council/reviews/`](council/reviews/) и исходные карточки, указанные в каждой
строке. Review-добавления уже консолидированы как `RA-001`–`RA-026`; новые
review findings повторно не считаются идеями. Пересекающиеся и дублирующие
карточки пока сохраняют отдельные ID, чтобы до ранжирования ничего не потерялось.

Исходные поля `Категория/Category` неоднородны и остаются provenance metadata;
ниже применяется нормализованная taxonomy из 11 основных категорий.

## Рубрика сложности

**CAL-000:** complexity — intrinsic, non-recursive размер минимального честного
production-ready core всей карточки. В него входят принадлежащие этому core
schema/migrations, authorization и chat isolation, tests/evals, privacy,
recovery и operator burden. Отдельные prerequisites указываются, но их полная
стоимость не прибавляется повторно каждому consumer. Расширения, явно названные
`later`, `optional`, `if desired` или альтернативой, не входят в core и требуют
отдельного candidate, если их нужно ранжировать.

Это не demo-MVP: core обязан безопасно выполнять обещанный outcome. Но breadth
сама по себе не является L-триггером — широкий offline suite или multi-module
flow остаётся M без durable production workflow, новой boundary или material
migration.

| Уровень | Критерий |
| --- | --- |
| XS | Документация, presentation или configuration без нового runtime-state и persistence. |
| S | Локальное production-ready изменение внутри существующей границы без нового durable workflow или integration boundary. |
| M | Согласованное изменение нескольких модулей, широкий offline/eval suite либо аддитивная schema/migration в текущей topology. |
| L | Собственный durable state machine/scheduler, новая integration/security boundary, необратимая операция или материальная миграция. |
| XL | Собственная смена topology, tenant/identity/platform либо несколько высокорисковых external/data boundaries. |

`Уверенность` показывает устойчивость size-класса, а не качество идеи и не
вероятность того, что её следует реализовать.

## Общее распределение

| Сложность | Количество | Доля |
| --- | ---: | ---: |
| XS | 1 | 0.6% |
| S | 6 | 3.4% |
| M | 74 | 42.0% |
| L | 87 | 49.4% |
| XL | 8 | 4.5% |
| **Всего** | **176** | **100%** |

Доли округлены независимо до одного знака после запятой.

L остаётся самым большим классом, но после CAL-000 он составляет меньше
половины каталога: отдельные foundations и optional live extensions больше
не оплачиваются рекурсивно. M и L вместе по-прежнему доминируют, потому что
карточки описывают безопасные production outcomes, а не одиночные кнопки.

## Сводка по категориям

| Основная категория | Что объединяет | XS | S | M | L | XL | Всего |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Shopping & list UX | Товары, list history/undo, list spaces/topics, in-store flows, bundles и прямые операции со списком. | 0 | 0 | 15 | 7 | 1 | 23 |
| Recipes & meal planning | Recipe lifecycle, quantities, meal plans, pantry и food-related safety/preferences. | 0 | 1 | 2 | 13 | 0 | 16 |
| Language, accessibility & multimodal interaction | Natural-language ambiguity, locale/accessibility, voice, image и другие input/output modalities. | 0 | 0 | 4 | 7 | 0 | 11 |
| Collaboration & access | Household roles, consent, attribution, shared trips и намеренное взаимодействие нескольких chat/user scopes. | 0 | 0 | 3 | 10 | 1 | 14 |
| Product onboarding & engagement | Onboarding, discovery, reminders, reactivation и opt-in бытовые привычки. | 0 | 0 | 6 | 5 | 0 | 11 |
| Integrations & portability | Import/export и внешние ingress/egress surfaces, включая Telegram-native capture. | 0 | 0 | 7 | 4 | 2 | 13 |
| Reliability & observability | Failure behavior, diagnostics, delivery/AI resilience, metrics, SLO и operational evidence. | 0 | 1 | 17 | 7 | 0 | 25 |
| Testing & evals | Offline harnesses, capability corpora, live-eval methodology, migration/fault evidence и история результатов. | 0 | 2 | 11 | 1 | 0 | 14 |
| Deployment & operations | Releases, backup/restore, health, staging, monitoring и production workflows. | 0 | 1 | 6 | 10 | 2 | 19 |
| Privacy & security | Capabilities, где control/boundary — это core: lifecycle, credentials, audit, egress и sensitive-data safeguards. | 0 | 1 | 1 | 9 | 1 | 12 |
| Data & application architecture | Reusable domain/application foundations, state models, identity primitives, lifecycle и structural refactors. | 1 | 0 | 2 | 14 | 1 | 18 |
| **Всего** |  | **1** | **6** | **74** | **87** | **8** | **176** |

Основная категория определяется по главной пользовательской или enabling-capability
ценности — то есть по ближайшим alternatives для будущего сравнения. Самая
дорогая внутренняя dependency остаётся в rationale и не переносит consumer
автоматически в architecture, reliability или security.

### Сильные merge- и dependency-кандидаты

Эти связи нужны, чтобы при portfolio ranking не посчитать общую ценность или
foundation дважды. ID пока не схлопываются:

- named lists: `CI-009` / `PT-003`; recurring staples: `CI-010` / `PT-004`;
  due dates: `CI-011` / `PT-002`; pantry: `CI-017` / `PT-005`;
- item assignment: `CI-003` / `PT-007`; reusable routines: `CI-018` / `PT-014`;
  onboarding: `CI-020` / `PT-030`; reminders: `CI-012` / `PT-027`;
- reorder suggestions: `CI-019` / `PT-013`; meal plan: `AI-009` / `CI-016` /
  `PT-015`; clarification: `AI-025` / `PT-017`; voice correction: `AI-028` /
  `PT-019`;
- decision trace: `AI-026` / `EO-001` / `RO-001`, а `PT-018` / `RO-002` —
  authorized explainers; весь cluster M и не хранит chain-of-thought;
- production failure → eval: `AI-027` / `EO-002` / `RO-028`; structured logs:
  `EO-025` / `RO-005`; SLOs: `EO-026` / `RO-025`;
- eval platform family: `AI-030`, `EO-003`–`EO-005`, `EO-010`–`EO-011`,
  `RA-010`, `RA-012`; общий runner не отменяет отдельные labels/gates;
- backup production: `EO-019` / `RO-020`; restore proof: `EO-020` / `RO-021`;
  AI resilience: `EO-027` / `RO-011`; secure recipe fetch: `AI-016` /
  `EO-012` / `RO-026`;
- capture family: `AI-014`, `CI-025`, `PT-024`, `PT-025`; общий bounded
  ingestion envelope не схлопывает разные destinations и authorization. Для
  `PT-025` correctness/apply идёт через explicit confirmation callback, а
  chosen-result feedback остаётся только optional telemetry;
- multimodal ingestion family: `AI-013`, `PT-021`, `PT-022`, `PT-023` делит
  bounded download/temp/media foundation, но сохраняет разные outcomes;
- shared-foundation пары: `AI-005` и picker-часть `PT-016`; `PT-026` и
  `AI-029`; `PT-011` и `RO-014`; `AI-024` и `RA-026`; `AI-018` и общий
  atomic-operation primitive.

Нельзя схлопывать named lists, Telegram topics, linked households и cross-scope
copy: они меняют разные scope boundaries. Domain history, recovery journal,
security audit и diagnostics дают разные гарантии. Backup, restore, rollback,
staging, smoke и health также остаются разными capabilities.

## Граничные случаи и возможные срезы

Некоторые идеи можно разбить на меньшие independently shippable slices. Такой
срез не меняет присвоенную ниже оценку intrinsic core; если extension нужно
сравнивать, он получает отдельную формулировку и acceptance criteria.

- Для trace/session идей `ContextVar` — только request-local carrier correlation state, не durable context и не источник tenant authority. Model output во всех mutation flows остаётся candidate с deterministic validation, requester-bound confirmation и scope recheck.
- `RA-021` — XS для Markdown ADR-ledger; автоматическая CI-проверка ссылок и ID была бы отдельным S-срезом.
- `AI-001` — S: существующий recipe lookup, handler/formatter/pagination и без новой таблицы.
- `EO-006` — S как hermetic test harness; runtime SSRF/egress boundary — отдельная L-работа (`AI-016`/`EO-012`/`RO-026`).
- `EO-015` — S для hardening существующего systemd unit; immutable artifacts и secret lifecycle оценены отдельно.
- `AI-012` — M parser/DTO/provenance core; отдельный secure-fetch prerequisite не прибавляется рекурсивно.
- `AI-030`, `RA-010` и `RA-012` — широкие M offline/synthetic eval cores; opt-in live runs не создают production runtime boundary.
- `AI-022` — L sensitive recipe/meal capability: consent и private profiles обязательны, но tenant/identity platform не меняется.
- `PT-007` и `CI-003` были бы M как nullable self-assignee, но полный consent, membership, concurrency и offboarding lifecycle делает их L.
- `PT-012` и `PT-013` имеют S query-only prototypes; retention, pagination, suppression и rebuild/delete semantics поднимают core до M.
- `PT-015` — M как ephemeral recipe picker, но durable weekly revision-safe plan с quantity aggregation — L.
- `PT-016` естественно делится: ingredient picker — M, полный scaling с quantity migration — L.
- `PT-017` — M для одной pending clarification; общий multi-update conversational framework был бы отдельным L.
- `PT-019` и `RA-001` выглядят как callbacks, но follow-up/recovery transitions делают их L; transcript preview без reply correction был бы M.
- Trace/explainer cluster (`AI-026`, `EO-001`, `RO-001`, `RO-002`, `PT-018`) — M; L `RO-006` остаётся отдельной backup-aware diagnostic lifecycle/migration.
- `PT-023` — multimodal M для local barcode/photo decode или typed code + user-confirmed label; optional product-catalog enrichment — отдельная L integration.
- `PT-024` остаётся L и low-confidence: bot должен быть administrator, а polling/webhook — явно подписан на `message_reaction`. Reaction не содержит source text, общего fetch-message API нет; нужен заранее observed bounded content либо consented retention, а anonymous count без requester actor не авторизует mutation.
- `PT-025` остаётся L: `answerInlineQuery(..., is_personal=true)` возвращает result с requester-bound opaque `Confirm add` callback либо эквивалентным explicit handoff. Destination не выводится из update: callback повторно авторизует requester, разрешает target/session, выполняет обычную service mutation и лишь затем сообщает success. `ChosenInlineResult` и inline feedback в BotFather ненадёжны для apply и допустимы только как optional telemetry.
- `PT-026` остаётся M для requester-local rendering; synchronized per-user replica messages были бы L.
- `PT-028` остаётся XL: карточка явно делает scope key composite и мигрирует callbacks/tracked-message/list state через центральный invariant. Scope должен либо ограничить feature forum-supergroups, либо покрыть private-topic semantics; более узкий topic-as-list вариант с неизменным tenant мог бы быть отдельным L.
- `PT-030` имеет stateless S-tour; полный localized, capability-aware per-user progress core — M.
- `RA-003` остаётся M для manual values/reviewed imports; live price lookup — отдельная L integration.
- `RA-004` — M best-effort disposable projection с manual recovery; retry-until-converged durable workflow был бы отдельным L.
- `CI-014` остаётся M для явно отправленной location/«я здесь»; background geofencing требует отдельной topology.
- `CI-019` имеет M user-invoked suggestion slice; scheduled opt-in digest и derived-state lifecycle делают core L.
- `CI-023` — user export, не operator backup и не cross-tenant share.
- `CI-025` остаётся M для Telegram-delivered content по explicit intent; arbitrary group retention или URL fetching были бы отдельной L-boundary.
- `CI-027` — M on-demand `.ics` core; subscribed feed/CalDAV — отдельный XL candidate.
- `CI-029` — M provider-neutral search-link handoff; official cart API/OAuth/remote partial success — отдельный L candidate.
- `CI-030` — M authorized static snapshot core; authenticated live web view — отдельный XL candidate.
- `CI-028` остаётся XL для bidirectional bridge; outbound-only webhook — отдельный L candidate.
- `EO-030` остаётся L, потому что заканчивается до multi-instance/PostgreSQL/queue shift; следующий topology step был бы XL.
- `RO-011` — M process-local policy вокруг существующего OpenAI gateway; post-commit Telegram delivery semantics `RO-012` остаются L.
- `RO-024` остаётся XL standalone: внешний runner, isolated principal/tenant, credentials, live Telegram/OpenAI и destructive cleanup. После готовых staging/principal foundations incremental scenario slice ближе к L.
- `RO-029` — M bounded crash capsule; leased/restart-reconciled workflows остаются L.
- `AI-015` остаётся XL как неделимый заявленный epic; для ranking полезно вынести revision/provenance/portability/refresh в отдельные candidates.

## Идеи по основной категории

### Shopping & list UX

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| M | AI-005 | Выборочное добавление ингредиентов | Нужны paginated requester-only callbacks, stale/revision checks и pending selection; зависит от общего typed interaction envelope и желательно AI-018 для atomic apply. | Высокая | [карточка](ai-recipes.md) |
| M | AI-006 | Домашние базовые продукты | Добавляет chat-scoped preferences, CRUD и override в recipe-add plan; опирается на canonical identity и контракты RA-007/RA-009. | Высокая | [карточка](ai-recipes.md) |
| M | CI-011 | Due dates and shopping windows | Adds item date/window fields, chat timezone, deterministic relative-date parsing, original-phrase preservation, correction, and grouped list/shop views. It shares the clock contract with scheduled features and duplicates `PT-002`, but does not itself send reminders. | Высокая | [карточка](collaboration-integrations.md) |
| M | CI-017 | Pantry minimums and replenishment | Adds explicit chat-scoped coarse pantry state, minimum rules, correction, and optional bought-item reconciliation across storage, service, and recipe/list views. It duplicates `PT-005`, depends on canonical identity/provenance, and must not infer stock from purchases or leak state through global caches. | Высокая | [карточка](collaboration-integrations.md) |
| M | CI-018 | Reusable household routines | Adds routine and ordered-entry tables, selection/edit preview, target-list resolution, deduplication, and an idempotent batch apply. It can reuse typed interaction-session and atomic-batch foundations and does not require a scheduler unless recurrence is added separately. | Высокая | [карточка](collaboration-integrations.md) |
| M | PT-001 | Edit Structured Item Details | A structured patch crosses Telegram routing, parser/AI validation, service, storage, and formatting. It needs item revisions/stale-value checks and a quantity value model before name or quantity edits are generally safe. | Высокая | [карточка](product-telegram.md) |
| M | PT-002 | Need-By Dates and Time Views | Adds item scheduling fields, chat timezone, deterministic relative-date resolution, grouped views, and routing evals. The stated scope explicitly excludes reminder delivery, so it does not yet require the scheduler from `PT-027`. | Высокая | [карточка](product-telegram.md) |
| M | PT-005 | Lightweight Pantry State | Coarse explicit `in stock`/`low` state needs chat-scoped persistence and reconciliation with active items and recipe previews. It depends on canonical identity/provenance and must remain separate from inferred history and expiry inventory. | Высокая | [карточка](product-telegram.md) |
| M | PT-006 | Store and Aisle Profiles | Requires chat-scoped store/placement tables, precedence over global categories, store selection, and shop-session snapshots. No live retailer boundary is required; placement must remain local and resettable. | Высокая | [карточка](product-telegram.md) |
| M | PT-010 | Bulk Selection and Partial Actions | A requester-bound expiring selection plus revision checks and an atomic multi-item service operation spans Telegram, service, and storage. It should reuse the shared typed interaction-session and batch primitives rather than create a new workflow engine. | Высокая | [карточка](product-telegram.md) |
| M | PT-012 | Bought History and One-Tap Re-Add | Existing bought rows can seed an indexed, chat-scoped query, but production scope also needs deduplication, pagination, retention semantics, deletion propagation, and exact re-add actions. | Средняя | [карточка](product-telegram.md) |
| M | PT-014 | Reusable Shopping Bundles | Adds chat-scoped bundle and bundle-item tables, selection UI, and dedupe-aware atomic materialization. It can reuse interaction-session/batch infrastructure and does not need recipe AI or a scheduler. | Высокая | [карточка](product-telegram.md) |
| M | RA-003 | Shopping-trip budget and actual spend | Adds chat/trip-scoped currency and integer minor-unit values, estimate coverage, immutable actual totals, provenance, and retention/export/delete rules. Manual estimates fit existing topology; live pricing is optional and would independently raise the scope to L. | Средняя | [карточка](review-additions.md) |
| M | RA-004 | Canonical pinned list message | Core — best-effort disposable pinned projection с additive binding/desired revision, permission/rate-limit/pagination failure paths и manual refresh/replacement; SQLite authoritative, а retry-until-converged outbox не обещан, поэтому M. | Средняя | [карточка](review-additions.md) |
| M | RA-005 | Shopping-item search and archive | Deterministic normalized-name search, status/list filters, stable result selection, archive semantics, pagination, and indexes span storage/service/Telegram. Start with direct indexed queries; FTS, if later justified, must be a rebuildable projection. | Высокая | [карточка](review-additions.md) |
| L | CI-009 | Named list spaces | Introduces a list aggregate and materially migrates items, recipes, shop sessions, pending operations, message context, commands, and exports to a list-within-`chat_id` model. It duplicates `PT-003` and requires an explicit default/current-list contract without becoming topic or workspace tenancy. | Высокая | [карточка](collaboration-integrations.md) |
| L | CI-010 | Recurring staples | Recurrence rules, durable occurrence keys, restart catch-up, skip/pause, duplicate suppression, target-list resolution, DST semantics, and delivery outcomes require the shared work coordinator/outbox and clock contract. It duplicates `PT-004`. | Высокая | [карточка](collaboration-integrations.md) |
| L | PT-003 | Named Lists Within a Chat | Introduces a list aggregate and materially migrates items, recipes, shop sessions, callbacks, and context to a list-within-`chat_id` model. It needs an explicit default/current-list contract and must not silently become topic or workspace tenancy. | Высокая | [карточка](product-telegram.md) |
| L | PT-004 | Recurring Staples | Recurrence definitions, durable occurrences, pause/skip state, restart-safe materialization, duplicate prevention, DST rules, and delivery outcomes require the shared work coordinator/outbox and clock contract. | Высокая | [карточка](product-telegram.md) |
| L | PT-011 | Activity Timeline and Precise Undo | User-facing list history и precise undo требуют durable domain journal, eligibility/conflict checks и compensating actions, поэтому остаются L; общий recovery primitive `RO-014` — отдельная foundation, не primary category. | Высокая | [карточка](product-telegram.md) |
| L | PT-022 | Receipt Reconciliation | Adds a sensitive receipt ingestion boundary, strict extraction and calibrated matching, three-way reconciliation, private preview, and atomic selected status changes. Retention, payment/loyalty redaction, and false-match evals are essential. | Высокая | [карточка](product-telegram.md) |
| L | RA-001 | In-store exception outcomes | Adds revisioned trip outcomes and transitions among active, deferred, substituted, and awaiting-answer states, with actor retention and best-effort requester delivery. Substitution safety and lifecycle semantics make it more than an additive status field. | Средняя | [карточка](review-additions.md) |
| XL | PT-028 | Telegram Topic-Scoped Lists | Источник явно вводит composite `(chat_id, message_thread_id)` scope и мигрирует items, callbacks, tracked messages, sessions, queries, export/delete и isolation matrix; это XL изменение центрального scope-инварианта, хотя topic-as-list slice мог бы быть L. | Высокая | [карточка](product-telegram.md) |

### Recipes & meal planning

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| S | AI-001 | Карточка сохранённого рецепта | Использует существующий chat-scoped recipe lookup; нужны handler/callback, formatter, pagination и escaping, но не новая таблица. | Высокая | [карточка](ai-recipes.md) |
| M | AI-011 | Подбор блюд под указанные продукты | Stateless query поверх текущих recipes/identities: service ranking, Telegram cards и optional ranker; новый pantry state не нужен. | Высокая | [карточка](ai-recipes.md) |
| M | AI-019 | Поиск и навигация по рецептам | Exact/alias/ingredient search, tags, pagination и при необходимости FTS требуют queries/index/schema, но остаются внутри SQLite topology. | Высокая | [карточка](ai-recipes.md) |
| L | AI-002 | Draft-preview и ручное редактирование ингредиентов | Durable requester-bound draft lifecycle, optimistic digest, atomic edit и recipe revision semantics; зависит от AI-003 и typed interaction sessions. | Высокая | [карточка](ai-recipes.md) |
| L | AI-003 | История версий и откат рецепта | Перевод существующих recipes к immutable revisions/current pointer — материальная migration и фундамент для edit, merge, scaling и portability. | Высокая | [карточка](ai-recipes.md) |
| L | AI-004 | Масштабирование по числу порций | Требует base servings, structured quantities, recipe revisions, preview и materialization; основные зависимости AI-003, AI-008, AI-018. | Высокая | [карточка](ai-recipes.md) |
| L | AI-007 | Единая корзина из нескольких рецептов | Комбинирует recipe revisions, quantity engine, multi-source provenance, selection session и atomic batch; зависит от AI-003/AI-005/AI-008/AI-018. | Высокая | [карточка](ai-recipes.md) |
| L | AI-009 | Недельный план питания | Новый durable meal-plan aggregate, recipe metadata/revisions, quantity/materialization foundations и Telegram editor; объединяется с PT-015/CI-016 и зависит от AI-004/AI-007/AI-008. | Высокая | [карточка](ai-recipes.md) |
| L | AI-010 | Повторяемые меню и ротация блюд | Template/occurrence tables, timezone, opt-in scheduler, idempotent delivery/outbox и restart recovery образуют durable scheduling system. | Высокая | [карточка](ai-recipes.md) |
| L | AI-020 | Поиск дублей и управляемое объединение | Нужны high-precision candidate scan, version-aware transactional merge aliases/ingredients, conflict handling и rollback через AI-003. | Высокая | [карточка](ai-recipes.md) |
| L | AI-021 | Замены ингредиентов | Новый substitution adapter, safety/domain validation, plan confirmation и optional persistent recipe variant; зависит от AI-003/AI-008/typed sessions. | Средняя | [карточка](ai-recipes.md) |
| L | AI-022 | Диетические и аллергенные ограничения | Chat/user-scoped sensitive dietary profiles, consent/disclosure/deletion и conservative checks в recipes, planning и substitutions создают L data/security boundary, но сохраняют текущие Telegram numeric IDs и `chat_id` tenant. | Высокая | [карточка](ai-recipes.md) |
| L | AI-023 | Планирование вокруг остатков и сроков | Новый inventory/lot/expiry domain, time semantics, explicit bought-to-pantry flow и planner/materialization; не смешивается со stateless AI-011. | Высокая | [карточка](ai-recipes.md) |
| L | CI-016 | Meal plan connected to saved recipes | A durable plan spans meal slots, participant proposals/decisions, recipe revisions, dates, serving quantities, ingredient deduplication, and idempotent materialization to a list. It duplicates `PT-015`/`AI-009` and depends on recipe revision, quantity, confirmation, and batch foundations. | Средняя | [карточка](collaboration-integrations.md) |
| L | PT-015 | Weekly Meal Plan to Shopping List | Adds a durable versioned plan and combines revised recipes, dates, quantities, active items, and optional pantry state before atomic confirmation. It depends on recipe revisions, the quantity engine, interaction sessions, and atomic batch materialization. | Средняя | [карточка](product-telegram.md) |
| L | PT-016 | Recipe Scaling and Ingredient Picker | The picker alone is M, but full scaling needs migrated structured quantities/base servings, deterministic unit arithmetic, unresolved-value handling, recipe revision checks, and a versioned selection preview. | Средняя | [карточка](product-telegram.md) |

### Language, accessibility & multimodal interaction

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| M | AI-025 | Уточнение вместо рискованной догадки | Добавляет decision policy, candidate card и expiring confirmation поверх текущего router; общий typed interaction primitive удерживает работу в M. | Средняя | [карточка](ai-recipes.md) |
| M | PT-017 | Clarification Cards for Ambiguous Text | Bounded candidates, одна requester-bound pending clarification, stale/TTL checks и confirm/apply переиспользуют существующий confirmation pattern; нет multi-update recovery state machine или новой boundary, поэтому M. | Средняя | [карточка](product-telegram.md) |
| M | PT-023 | Barcode and Product-Code Capture | Primary value — multimodal capture через bounded local barcode/photo decode или typed code с user-confirmed label и additive chat-scoped fields; optional external catalog enrichment — отдельная L integration. | Средняя | [карточка](product-telegram.md) |
| M | PT-026 | Per-User Language and Accessible Rendering | Requires stable message keys, a broad formatter/UI catalog refactor, persisted per-user presentation settings, and Telegram-client validation. Stored item text must remain unchanged; shared edited messages require a chat-level fallback or separate views. | Высокая | [карточка](product-telegram.md) |
| L | AI-013 | Рецепт из фото или скриншота | Новый bounded media boundary, temp artifact lifecycle, multi-page session, vision adapter и confirmation flow; зависит от common ingestion/session infrastructure. | Высокая | [карточка](ai-recipes.md) |
| L | AI-024 | Ограниченный контекст для уточняющих реплик | Typed `(chat_id, user_id)` multi-update session с allowed transitions, TTL, revision/digest и restart recovery — durable interaction state machine. | Высокая | [карточка](ai-recipes.md) |
| L | AI-028 | Проверка и коррекция голосовой расшифровки | Risk policy, transcript/candidate session, correction/retry flow, bounded speech hints и private/group preview semantics расширяют существующую media boundary. | Высокая | [карточка](ai-recipes.md) |
| L | AI-029 | Язык чата, транслит и смешанные команды | Storage preference, message catalog, parser preprocessing, language-neutral categories/identities и prompt contracts затрагивают почти весь user-visible stack и derived caches. | Средняя | [карточка](ai-recipes.md) |
| L | PT-019 | Voice Transcript Preview and Correction | A reply-based correction is a multi-update requester-bound lifecycle with transcript/candidate retention, expiry, authorization, and risk-based apply rules. It also depends on the bounded media envelope and private-preview policy. | Средняя | [карточка](product-telegram.md) |
| L | PT-020 | Multi-Part Voice Shopping Session | Ordered voice notes, expiry, contradiction folding, cancellation/review, cost/resource limits, and transactional application form a new durable capture state machine. It also stresses the shared media, scheduler-cleanup, and SQLite policies. | Высокая | [карточка](product-telegram.md) |
| L | PT-021 | Photo List Capture | Adds a bounded untrusted-media boundary, temporary artifact lifecycle, vision/OCR adapter and evals, strict candidates, private/minimized preview, and requester-confirmed batch apply. | Высокая | [карточка](product-telegram.md) |

### Collaboration & access

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| M | CI-002 | Item requester and shopper attribution | Adds chat-scoped requester/completer/source relations, actor capture at the Telegram boundary, safe display-label resolution, and retention/offboarding behavior. It depends on observed numeric-user membership and the data lifecycle in `RA-008`; attribution must remain separate from assignment and security audit. | Высокая | [карточка](collaboration-integrations.md) |
| M | CI-005 | Item discussion threads | Requires bounded note persistence, authorship and timestamps, reply-to-item resolution, escaping, and concise rendering across Telegram, service, and storage. It needs item revision/deletion rules and `RA-008` retention, but does not introduce a new tenant or external service. | Высокая | [карточка](collaboration-integrations.md) |
| M | PT-008 | Priority Signals and Household Voting | Priority is an additive item field; voting adds a per-user relation, callback authorization, and ordering/display rules. Actor visibility and retention need a small capability/privacy contract, but tenancy remains unchanged. | Высокая | [карточка](product-telegram.md) |
| L | CI-001 | Household roles | A role/capability model changes authorization across every protected action and adds observed membership, migrations, role-change audit, last-admin/recovery rules, and adversarial tests. It depends on the principal foundation in `RA-015`; Telegram admin status and mutable usernames cannot grant household authority. | Высокая | [карточка](collaboration-integrations.md) |
| L | CI-003 | Voluntary item assignment | The complete scope needs observed membership, self-claim versus assign-other capabilities, acceptance, compare-and-set revisions, stale callbacks, synchronized projections, and leave/handoff behavior. It duplicates `PT-007`; a bare nullable assignee would be M but would omit the stated consent and security lifecycle. | Средняя | [карточка](collaboration-integrations.md) |
| L | CI-004 | Shopping-trip claim and handoff | Introduces a persisted trip state machine with shopper identity, start snapshot, new-item handling, bought/skipped/unfinished outcomes, handoff, expiry/admin close, and idempotent transitions. Coalesced best-effort Telegram refresh and partial-delivery outcomes are required; it composes with but does not duplicate synchronized shop views. | Высокая | [карточка](collaboration-integrations.md) |
| L | CI-006 | Suggestion inbox for restricted members | A durable requester/approver workflow must normalize the proposed effect, bind two capability sets, expire and atomically claim callbacks, revalidate state, rate-limit abuse, and deliver outcomes safely. It depends on `RA-015`, typed interaction sessions, and the role model rather than granting proposal text any authority. | Высокая | [карточка](collaboration-integrations.md) |
| L | CI-007 | Time-limited guest access | Guest grants create a security boundary requiring an observed-ID or two-party redemption handshake, narrow capabilities, expiry checked on every request, immediate revocation, last-admin safeguards, audit, and optional expiry delivery. A forwarded deep link alone cannot establish the intended identity. | Высокая | [карточка](collaboration-integrations.md) |
| L | PT-007 | Item Assignment and Claiming | Production assignment creates a capability boundary around self-claim versus assignment, needs observed numeric-user membership, compare-and-set updates, leave/offboarding behavior, and bounded identity retention. | Средняя | [карточка](product-telegram.md) |
| L | PT-009 | Synchronized Shopping Sessions | Turns retained snapshots into revisioned session lifecycle state and coalesced best-effort Telegram fan-out. It needs optimistic callbacks, terminal/expiry behavior, partial-delivery reporting, and measured SQLite concurrency. | Высокая | [карточка](product-telegram.md) |
| L | PT-029 | Personal-to-Household List Transfer | Crosses two independent chat tenants without replacing them, so preview and commit must reauthorize both scopes, bind actor/revisions, minimize notes, and make copy atomic before any optional source update. | Высокая | [карточка](product-telegram.md) |
| L | RA-002 | Household offboarding and ownership transfer | This is a security-sensitive lifecycle spanning leave/removal, guest expiry, last-admin and owner recovery, two-party transfer, assignment handoff, integration revocation, unlinking, de-identification, and backup-aware deletion. It depends on authoritative membership/principals (`RA-015`) and the data map in `RA-008`. | Высокая | [карточка](review-additions.md) |
| L | RA-014 | Household disagreement resolution | A common decision-policy vocabulary must govern owner policy, requester choice, personal opt-in, votes, unanimity, expiry/ties, and unresolved outcomes across several durable collaboration flows. It depends on `RA-015`, requester-bound interaction sessions, and strict non-disclosure of another member's private constraints. | Средняя | [карточка](review-additions.md) |
| XL | CI-008 | Linked household spaces across chats | Пользовательский core связывает household spaces для совместной работы, но источник заменяет `chat_id` как business tenant и мигрирует data/auth paths; поэтому product placement — collaboration, а intrinsic scope остаётся XL. | Высокая | [карточка](collaboration-integrations.md) |

### Product onboarding & engagement

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| M | CI-014 | Store or area arrival reminder | The stated privacy-preserving version handles an explicit location or “I’m here” action, local distance matching, saved coarse store data, deletion, and a filtered shop view. It needs explicit consent and `RA-008`, but no background tracking, companion client, or live provider. | Высокая | [карточка](collaboration-integrations.md) |
| M | CI-020 | Guided household onboarding | A resumable, capability-aware private/group journey needs persisted milestones/version/dismissal, owner-versus-member paths, and hooks from successful operations. It duplicates `PT-030`; actual Telegram/OpenAI/voice availability must drive the flow without blocking normal commands. | Средняя | [карточка](collaboration-integrations.md) |
| M | CI-021 | Contextual feature discovery | Deterministic post-success eligibility rules, impressions, dismissal, cooldowns, and one-tip rendering span several product paths and require additive state. It depends on trustworthy domain events and capability detection, and must not inspect raw messages or become a scheduler-driven notification system. | Высокая | [карточка](collaboration-integrations.md) |
| M | CI-022 | Inactivity-safe reactivation | The reactive path derives inactivity from successful chat-scoped actions, stores the last shown version, renders aggregate counts, and uses confirmed archive/reset/review actions. It depends on `RA-008`; a proactive reactivation campaign would separately add the L scheduler/outbox boundary. | Средняя | [карточка](collaboration-integrations.md) |
| M | PT-013 | User-Initiated Frequent-Item Suggestions | Deterministic frequency/recency aggregation is local, but production scope includes exclusion, requester-bound selection, suppression, provenance, and deletion/rebuild behavior. It needs sufficient retained history and canonical identity. | Средняя | [карточка](product-telegram.md) |
| M | PT-030 | Guided Onboarding and Capability Discovery | A production-ready state-aware tour needs callback flows, capability-aware content, per-user progress/reset, owner/member separation, localization, and bounded dismissible hints. It can be reduced to a stateless S tour, but that is not the stated complete idea. | Средняя | [карточка](product-telegram.md) |
| L | CI-012 | Configurable reminder digest | Production delivery needs persisted opt-in schedules, timezone/DST/quiet hours, durable occurrence deduplication, snapshot construction, retry limits, snooze/mute controls, and partial-failure handling. It duplicates `PT-027` and should share one scheduler/outbox with recurrence. | Высокая | [карточка](collaboration-integrations.md) |
| L | CI-013 | Personal notification preferences | Per-user preferences require observed household membership, private-chat enrollment, personal revocation, event-policy resolution, a transactional outbox, retry/idempotency, and protection against sending household data outside authorized surfaces. It depends on `RA-015` and the shared scheduler/delivery foundation. | Высокая | [карточка](collaboration-integrations.md) |
| L | CI-015 | Weekly household review | A scheduled review combines retained history and configuration into a revisioned snapshot, then applies multiple requester-bound keep/remove/snooze/recurrence actions safely. It depends on explicit retention, typed interaction sessions, the scheduler/outbox, and rebuildable derived views. | Средняя | [карточка](collaboration-integrations.md) |
| L | CI-019 | Explainable reorder suggestions | Full scope requires retained purchase history, chat-scoped deterministic cadence projections, evidence, suppression/reset/delete, opt-in delivery, cooldowns, and scheduled presentation. It duplicates `PT-013` at the suggestion layer but adds proactive scheduling; derived state must follow source deletion and never auto-add items. | Средняя | [карточка](collaboration-integrations.md) |
| L | PT-027 | Digests, Reminders, and Quiet Hours | Requires persisted policies and occurrences, timezone/DST/catch-up semantics, quiet hours, opt-in scope, idempotent delivery/outbox, partial-failure handling, and restart-safe scheduling. | Высокая | [карточка](product-telegram.md) |

### Integrations & portability

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| M | AI-012 | JSON-LD-first извлечение рецепта | Core — bounded deterministic JSON-LD parser, общий DTO, field provenance и fallback orchestration поверх существующего fetch path; отдельный L secure-fetch policy остаётся prerequisite, а не частью размера parser. | Высокая | [карточка](ai-recipes.md) |
| M | AI-014 | Сохранение рецепта из пересланного сообщения | Переиспользует Telegram reply/caption и существующий extractor/save flow; добавляет bounded ingestion DTO, provenance и dispatcher coverage. | Средняя | [карточка](ai-recipes.md) |
| M | CI-023 | Self-service data export | A production export needs owner capability checks, explicit dataset selection, a consistent chat-scoped SQLite snapshot, a versioned neutral schema, bounded temporary files, and Telegram-retention warnings. It depends on `RA-008` and shared serialization with `CI-026`, while remaining distinct from operator backups. | Высокая | [карточка](collaboration-integrations.md) |
| M | CI-025 | Forward-to-list capture | Uses existing Telegram and list boundaries but adds reply/forward/external-reply normalization, bounded data-only extraction, source minimization, and a requester-bound preview before ordinary list mutation. It depends on the shared ingestion/session envelope; protected or incomplete Telegram payloads need an explicit fallback. | Средняя | [карточка](collaboration-integrations.md) |
| M | CI-027 | Calendar handoff | Production-ready core — on-demand `.ics` с stable UID, deterministic serialization, authorized dataset selection, bounded temporary document и cleanup; subscribed feed/CalDAV — отдельное later XL-расширение. | Средняя | [карточка](collaboration-integrations.md) |
| M | CI-029 | Retailer search and cart handoff | Core генерирует provider-neutral per-item retailer search links с locale mapping, explicit handoff и graceful fallback внутри текущей topology; official cart API, OAuth и remote partial success — отдельный L-slice. | Средняя | [карточка](collaboration-integrations.md) |
| M | CI-030 | Read-only companion view | First production-ready core — authorized static snapshot document из service read model с bounded artifact, expiry/privacy warning и cleanup; authenticated live web view остаётся отдельным XL topology candidate. | Высокая | [карточка](collaboration-integrations.md) |
| L | CI-024 | Preview-first list import | A production importer needs bounded file download/temp handling, format-specific text/CSV/JSON parsers, encoding/row/formula/archive defenses, a durable requester-bound preview, duplicate policy, stable import IDs, and explicit atomic/partial batch semantics. It depends on the shared ingestion and typed-session foundations. | Высокая | [карточка](collaboration-integrations.md) |
| L | CI-026 | Portable recipe and routine bundle | Combines selective export and untrusted import under a versioned neutral schema, manifest/integrity semantics, conflict-by-conflict preview, requester authorization, and idempotent multi-object apply. It depends on recipe/routine revisions, typed sessions, atomic-batch rules, and `RA-008`; checksum is not trust or authorship. | Высокая | [карточка](collaboration-integrations.md) |
| L | PT-024 | Reaction-Based Message Capture | Bot должен быть administrator, а polling/webhook — явно подписан через `allowed_updates=["message_reaction"]`; update не содержит source text, fetch-by-message-ID отсутствует, поэтому нужны actor-bearing event, bounded-retained observed content/consent/deletion, а anonymous count не авторизует mutation. | Низкая | [карточка](product-telegram.md) |
| L | PT-025 | Inline Capture From Other Chats | `answerInlineQuery(..., is_personal=true)` возвращает result с requester-bound opaque `Confirm add` callback/explicit handoff; только callback делает current-capability recheck, resolves target/session, performs normal service mutation и затем сообщает success. Destination не выводится из update; `ChosenInlineResult`/inline feedback — optional telemetry, never correctness/apply. | Средняя | [карточка](product-telegram.md) |
| XL | AI-015 | Происхождение, переносимость и безопасное обновление | Одна идея объединяет material recipe revision migration, field provenance, versioned export/import schema, conflict resolution и безопасный external refresh; это несколько high-risk data/external boundaries. Зависит от AI-003/AI-012/AI-016/RA-007/RA-009. | Средняя | [карточка](ai-recipes.md) |
| XL | CI-028 | Home Assistant bridge | Bidirectional Home Assistant support adds HTTP or MQTT ingress/egress topology, a non-human least-privilege principal, credential storage, replay protection, durable outbox/idempotency, explicit chat mapping, and network threat modeling. Core Telegram shopping must remain available when the bridge or home network fails. | Высокая | [карточка](collaboration-integrations.md) |

### Reliability & observability

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| S | RO-003 | Stable reason-code catalog | A versioned enum registry and validation can stay within the existing diagnostic boundary. It should precede RO-001/002/005/008/010 and be shared with tests; migrating every existing signal can be incremental. | Высокая | [карточка](reliability-observability.md) |
| M | AI-026 | Объяснимый трейс маршрутизации | Typed reason stages, request-local correlation, redaction/finalizer, bounded additive TTL-store и authorized owner view образуют широкий M; отдельная backup-aware diagnostic data migration `RO-006` не включается рекурсивно. | Высокая | [карточка](ai-recipes.md) |
| M | EO-001 | Объяснимый trace решения для каждого входящего сообщения | Requires outer Telegram middleware/finalization, a bounded typed event schema, instrumentation across routing and AI fallbacks, redaction tests, and log/optional short-lived persistence projections. A stable reason-code catalog, sampling, and retention policy should precede the owner-facing explainer; it must remain separate from audit and domain history. | Средняя | [карточка](engineering-operations.md) |
| M | EO-023 | Health-check CLI с уровнями readiness | Typed cheap/deep/external probe modes touch settings, SQLite, ffmpeg, process startup, output contracts, and systemd/deploy integration. It should share a probe registry with EO-016 and depends on RA-018 so offline checks do not require Telegram/OpenAI secrets. | Высокая | [карточка](engineering-operations.md) |
| M | EO-024 | Event-loop watchdog и отдельный polling health signal | Requires application lifecycle changes, a cancellation-safe heartbeat, `sd_notify`, unit changes, startup grace, and bounded poller state metrics. Complexity remains M if it stays single-process; thresholds should follow measured event-loop/SQLite behavior from EO-030, and aiogram may limit polling-state visibility. | Средняя | [карточка](engineering-operations.md) |
| M | EO-025 | Структурированные JSON-логи с корреляцией | A versioned logging envelope, context propagation, adapters across several boundaries, release/request correlation, safe exception normalization, and redaction/cardinality tests are cross-cutting but do not require durable application state. It should project EO-001 traces rather than create a second trace format, and EO-013 sets its storage budget. | Высокая | [карточка](engineering-operations.md) |
| M | EO-027 | Явная OpenAI resilience policy | Consolidates client ownership and shutdown, per-operation deadlines/retries, concurrency gates, circuit state, normalized outcomes, fallbacks, and fault tests at an existing provider boundary. EO-009 should qualify retry/cancellation behavior; EO-028 consumes its usage reports. | Высокая | [карточка](engineering-operations.md) |
| M | EO-028 | Учёт token/cost и мягкие бюджеты | Gateway usage events, a versioned price table, durable daily aggregates, missing-usage semantics, warning evaluation, and optional per-capability cutoffs require coordinated AI/metrics/storage work and an additive schema. It depends on EO-027; alert delivery can reuse EO-026. | Средняя | [карточка](engineering-operations.md) |
| M | PT-018 | “Why Did the Bot Do That?” Explainer | Correlation ID, compact typed trace, short retention, redaction, access checks и authorized formatter составляют тот же additive M trace/explainer cluster, что `EO-001`/`RO-001`/`RO-002`; chain-of-thought не хранится. | Высокая | [карточка](product-telegram.md) |
| M | RO-001 | End-to-end request decision trace | Middleware/context setup, routing instrumentation, a bounded finalizer, and tests cross several modules; an additive short-lived store may follow. RO-003 should come first, while RO-004/006 define capture and persistence policy; ContextVars remain request-local and reset on every exit. | Высокая | [карточка](reliability-observability.md) |
| M | RO-002 | Owner-visible “why?” explainer | The view spans trace lookup, sanitization, owner-only Telegram delivery, expiry, and rate limits, but can build on existing authorization and an established trace store. It depends on RO-001/003/004/006 and stays a separate product projection rather than exposing model reasoning. | Высокая | [карточка](reliability-observability.md) |
| M | RO-004 | Sampled tracing with temporary diagnostic windows | Sampling, per-chat caps, a durable expiring debug setting, and cleanup require additive state plus middleware integration. It depends on RO-001/003/006, an explicit clock/TTL contract, and RO-007 or RO-030 for lifecycle enforcement. | Высокая | [карточка](reliability-observability.md) |
| M | RO-005 | Structured journald logging with correlation | A shared logging adapter plus conversion of call sites is multi-module but introduces no business state. It should be a redacted projection of RO-001/003 rather than a second trace, and needs bounded fields plus a separate journald disk-retention policy. | Высокая | [карточка](reliability-observability.md) |
| M | RO-008 | Low-cardinality routing and fallback metrics | Instrumenting terminal routing/fallback outcomes crosses middleware, routing, and the metrics module, with strict enum validation and tests. It depends on RO-003 and preferably RO-001; dashboard ratios must distinguish ineligible text, unknown parsing, and AI/schema failures. | Высокая | [карточка](reliability-observability.md) |
| M | RO-009 | Request latency phase breakdown | Fixed timers must be added around Telegram, AI, fetch, ffmpeg, and SQLite boundaries and closed on errors/cancellation. This is broad instrumentation without new durable state; it depends on bounded stage names and should provide evidence before RO-017 or scaling work. | Высокая | [карточка](reliability-observability.md) |
| M | RO-010 | AI outcome, prompt, and cost observability | A common AI request report, normalized model aliases, usage handling, and trace/metric projections span AI, eval metadata, and observability modules. It depends on RO-003 and prompt fingerprints; cost budgets remain separate policy, while RO-011 owns resilience. | Высокая | [карточка](reliability-observability.md) |
| M | RO-011 | Explicit AI resilience envelope | Shared OpenAI client, per-operation deadlines/retry eligibility, concurrency gate, process-local circuit, normalized outcome и caller fallback укрепляют существующую provider boundary без persistence или durable workflow, поэтому M. | Высокая | [карточка](reliability-observability.md) |
| M | RO-018 | Database health and capacity telemetry | A bounded low-priority sampler, cached expensive checks, freshness semantics, and gauges require storage, lifecycle, and metric integration but no data mutation. Share probes with RO-016 and let RO-025/RA-024 consume freshness rather than assuming scrape success. | Высокая | [карточка](reliability-observability.md) |
| L | AI-017 | Фоновая обработка тяжёлого импорта | SQLite jobs, leases, bounded worker, backpressure, retries, restart recovery и status delivery — durable state machine. | Высокая | [карточка](ai-recipes.md) |
| L | EO-026 | Небольшой SLO-набор и actionable alerts | The repository has only an in-process exporter; full delivery requires trusted scraping, durable metric storage, dashboards, Alertmanager or equivalent, alert-channel delivery, missing-scrape detection, and low-volume SLO semantics. That new monitoring integration depends on EO-013 and the probes/signals from EO-023–EO-025. | Высокая | [карточка](engineering-operations.md) |
| L | RA-011 | Capability-specific safe-mode experience | A truthful capability registry must coordinate fallbacks across handlers, AI, media, delivery, schedulers, health, and onboarding, including combinations of outages. It depends on explicit envelopes such as RO-011/012/025 and requires broad failure-path tests so degradation cannot bypass authorization or claim false success. | Высокая | [карточка](review-additions.md) |
| L | RA-024 | Monitoring-path health and alert delivery proof | Dead-man monitoring and synthetic alert delivery create a separate provider/credential/network boundary with independent placement, retention, rate limits, and operator ownership. It depends on RO-022/025 and must distinguish zero traffic from absent telemetry without paging household chats. | Высокая | [карточка](review-additions.md) |
| L | RO-012 | Telegram delivery reliability envelope | Classifying and selectively retrying sends, edits, reactions, callbacks, and downloads is an external-integration correctness boundary: unsafe retry can duplicate output after a committed mutation. It becomes shared delivery infrastructure and depends on stable reasons, explicit partial outcomes, and coalescing/idempotency policy. | Высокая | [карточка](reliability-observability.md) |
| L | RO-025 | Service-level objectives and actionable alerts | Production-ready SLOs need capability-aware indicators, low-volume statistics, recording/alert rules, deployment annotations, runbooks, and sustained actionable thresholds. It depends on RO-008/009/018/022 and on RA-024 to prove the external scrape/alert path. | Высокая | [карточка](reliability-observability.md) |
| L | RO-028 | Sanitized failure inbox for unresolved requests | A quota-bound durable inbox, expiry/deduplication, owner review, explicit resubmission/redaction, and corpus proposal workflow form a privacy-sensitive queue. It depends on RO-001/003/006 and should merge with the human-reviewed production-failure-to-eval workflow without auto-copying messages. | Высокая | [карточка](reliability-observability.md) |

### Testing & evals

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| S | EO-006 | Герметичный контрактный стенд для recipe URL fetching | Contained test infrastructure around the existing fetch boundary: an ephemeral loopback server, deterministic failure endpoints, and pure address-policy cases. It does not itself implement runtime SSRF protection; that is EO-012. | Высокая | [карточка](engineering-operations.md) |
| S | EO-007 | Stateful/property-based тесты жизненного цикла списка | A test-only reference model, generated command sequences, and temporary SQLite exercise existing service/storage APIs. The main cost is keeping the model deliberately smaller than production; no product schema or runtime flow changes. | Высокая | [карточка](engineering-operations.md) |
| M | AI-030 | Eval-наборы для рецептов и реального аудио | Versioned recipe/audio corpora, deterministic graders, manifests, reporting и opt-in live runner — широкий offline/eval M без нового production workflow; sensitive real media остаётся optional governed input. | Средняя | [карточка](ai-recipes.md) |
| M | EO-002 | Конвейер «ошибка в эксплуатации → новый eval-кейс» | Adds a trace exporter, quarantine format, sanitization/minimization workflow, corpus validator, and review provenance across diagnostics and eval tooling. It depends on EO-001 and deliberately keeps labeling and promotion into the versioned corpus manual. | Высокая | [карточка](engineering-operations.md) |
| M | EO-003 | Разделить evals по продуктовым способностям | Several versioned corpora, capability-specific graders, adapters, accepted-variant rules, reports, and gates must be added around the shared runner. It depends on reviewed gold labels and must keep category, identity, recipe extraction, and user-facing-error results separate. | Высокая | [карточка](engineering-operations.md) |
| M | EO-004 | Контролируемый voice/audio eval-набор | Introduces consented or synthetic binary fixtures, a manifest, offline conversion smoke, opt-in transcription runner, route grading, and tool/model fingerprints. It depends on a separate eval credential and the shared capability-eval/reporting infrastructure from EO-003/EO-011. | Высокая | [карточка](engineering-operations.md) |
| M | EO-005 | Replay-harness для Telegram update-сценариев | Typed update/callback/AI builders, a restrained scenario DSL, recording Bot API fake, database assertions, and resource cleanup span dispatcher integration tests and support code. It reuses the existing fake seam and must not become a second router. | Высокая | [карточка](engineering-operations.md) |
| M | EO-008 | Матрица миграций из исторических SQLite fixtures | Requires frozen per-version schema builders, representative and negative fixtures, CLI-path migration runs, integrity/foreign-key/chat-scope assertions, and idempotency checks. It is a prerequisite for persistent feature breadth and stronger release/restore guarantees. | Высокая | [карточка](engineering-operations.md) |
| M | EO-009 | Детерминированная fault-injection матрица | Programmable failpoints and expected state/user/telemetry contracts must cover OpenAI, Telegram, SQLite, ffmpeg, cancellation, and recipe fetching. The breadth is multi-module, though it remains offline test infrastructure; stable boundary contracts and reason codes reduce maintenance. | Высокая | [карточка](engineering-operations.md) |
| M | EO-010 | Парное и статистически осмысленное сравнение live eval | Changes live-run scheduling, stores attempt-level ordering, pairs baseline/candidate results, and adds bootstrap or exact analysis for quality, consistency, latency, and cost. It depends on repeated live runs and versioned corpora/reports but adds no production state. | Высокая | [карточка](engineering-operations.md) |
| M | EO-011 | Реестр eval-результатов и тренды качества | Needs a versioned report schema, sanitizer, immutable artifact retention, summary index/trend rendering, and reviewed baseline-pointer updates. Storage can remain CI/artifact based, but retention and provider/model provenance must be explicit. | Средняя | [карточка](engineering-operations.md) |
| M | RA-010 | Persisted-semantics rollout sandbox | Approved fixtures, baseline/candidate adapters, deterministic semantic validators, diff report и release linkage образуют offline M; карточка исключает production bulk rewrite и не создаёт runtime state machine. | Средняя | [карточка](review-additions.md) |
| M | RA-012 | Cross-modal injection and isolation corpus | Synthetic/scrubbed cross-modal corpus, format adapters и deterministic isolation/resource graders проверяют существующие boundaries offline; breadth требует M, но не создаёт новую runtime security boundary. | Средняя | [карточка](review-additions.md) |
| L | AI-027 | Исправления пользователя как eval-кандидаты | Consented correction, quarantine/review states, trace linkage, sanitized export и corpus governance образуют durable human-in-the-loop workflow; объединяется с EO-002/RO-028. | Высокая | [карточка](ai-recipes.md) |

### Deployment & operations

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| S | RO-016 | Read-only database invariant doctor | A read-only CLI over existing SQLite schema needs bounded checks, deadlines, redacted output, and tests, but no durable state or live integration. Reuse its probes in RO-019/021/029 while keeping cheap, deep, and deployment modes explicit. | Высокая | [карточка](reliability-observability.md) |
| M | EO-013 | Journald retention и защита диска от заполнения | Production readiness includes safe host/per-unit journal limits, rate limiting, disk metrics, alert thresholds, and a runbook that preserves SQLite and recovery copies. The unknown current scrape/alert path makes this more than a config-only edit; it depends on the monitoring contract in EO-026. | Средняя | [карточка](engineering-operations.md) |
| M | EO-014 | Scheduled dependency compatibility и security drift check | Adds scheduled dependency proposals with constrained permissions, lockfile grouping, the full offline suite, SDK-shape smoke, review metadata, and optionally SBOM output. It integrates with GitHub automation but does not change the application topology. | Высокая | [карточка](engineering-operations.md) |
| M | EO-016 | Read-only deployment preflight/doctor | A shared requirement/probe schema, local release checks, restricted read-only remote SSH checks, stable verdicts, and remediation output span CLI, deployment assets, and operator permissions. It depends on EO-017 release manifests and benefits from RA-018 command-specific settings. | Высокая | [карточка](engineering-operations.md) |
| M | RA-018 | Offline command configuration profiles | Command-first parsing, typed per-command settings, dependency construction, path/network/write guards, and negative initialization tests reshape app/config/CLI composition without adding persistence. It is a foundation for EO-016, EO-020, EO-023, and EO-029. | Высокая | [карточка](review-additions.md) |
| M | RO-022 | Release identity and deployment markers | Generating an immutable manifest, validating it at startup, and exposing safe release/deployment markers spans packaging, deployment, runtime, and metrics. It should merge with the atomic-release identity work and feed RO-019/023/024/025; raw hashes belong in logs/info, not unbounded labels. | Высокая | [карточка](reliability-observability.md) |
| M | RO-029 | Startup and crash-loop diagnostic capsule | Линейные startup stages, bounded atomically replaced last-failure capsule и systemd-facing readiness затрагивают несколько модулей, но не имеют leases, resume/compensation, scheduler или material migration, поэтому M. | Высокая | [карточка](reliability-observability.md) |
| L | EO-017 | Неизменяемые atomic releases с manifest | Introduces signed immutable bundles, manifests, per-release directories/environments, a `current` symlink, systemd layout changes, verification, pruning, and a material migration from copy-deploy. It depends on EO-021 for schema-safe rollback and RA-022 for signing/deploy credential lifecycle. | Высокая | [карточка](engineering-operations.md) |
| L | EO-018 | Идемпотентный release state machine | A durable/recoverable deployment workflow needs checkpoints, receipts, concurrency locking, resumable remote actions, privilege boundaries, and explicit manual smoke acceptance. It composes EO-016/EO-017 with verified backup, migration, health, restore, and rollback contracts. | Высокая | [карточка](engineering-operations.md) |
| L | EO-019 | Автоматические согласованные SQLite backups | Adds a scheduled backup worker/timer, SQLite-consistent capture, manifests/checks, atomic publication, encrypted off-host storage, retention, and failure monitoring. It introduces both a scheduler and external storage/key boundary and must be paired with EO-020 restore proof. | Высокая | [карточка](engineering-operations.md) |
| L | EO-020 | Версионированная restore-команда и регулярный restore drill | Recovery coordinates artifact selection/decryption, integrity/schema/migration checks, isolated drills, service quiescence, explicit production promotion, and preservation of the failed database. It is a high-risk data operation depending on EO-019, RA-018, defined RPO/RTO, and recoverable keys. | Высокая | [карточка](engineering-operations.md) |
| L | EO-021 | Rollback pack, связывающий код и состояние базы | Couples release identity, schema compatibility, a verified pre-migration backup, rollback classification, service stop/start, and an explicit potentially lossy restore decision. It depends on EO-017, EO-019, EO-020, and tested forward migrations from EO-008. | Высокая | [карточка](engineering-operations.md) |
| L | EO-022 | Ручное promotion из GitHub Actions с защищённой средой | Adds a protected external promotion path, immutable artifacts, approval gates, a restricted deploy principal/runner, secret handling, deployment receipts, and failure recovery. It should follow EO-016–EO-021 and RA-022; production Telegram smoke remains manual unless a separate test identity is designed. | Высокая | [карточка](engineering-operations.md) |
| L | RA-023 | Background-work operator budget and failure isolation | Production scope goes beyond a document: each worker needs bounded resources, retries, poison handling, shutdown/restart semantics, health, and an accountable operator path, with some jobs isolated in systemd timers. It should constrain RO-020/021/024/030 and depends on measurements from RO-009/017/018. | Средняя | [карточка](review-additions.md) |
| L | RO-019 | Migration preflight and postflight contract | Orchestrating service-stop proof, disk/permissions/version checks, verified backup, forward migration, integrity validation, and a durable release receipt is a high-consequence deployment workflow. It depends on RO-016/020/022 and must leave service stopped on failed verification rather than reverse-migrating. | Высокая | [карточка](reliability-observability.md) |
| L | RO-020 | Automated consistent backup with manifest | Snapshot consistency, logical verification, checksums/manifests, permissions, retention, and optional encrypted off-host replication cross sensitive data and operational boundaries. Merge duplicate backup-production ideas, depend on RO-016/022, and keep restore proof in RO-021 distinct. | Высокая | [карточка](reliability-observability.md) |
| L | RO-021 | Restore drill and rollback rehearsal | A scheduled or manual isolated restore must select a compatible artifact, protect copied production data, validate schema/application reads, publish freshness, and securely clean up. It depends on RO-016/020/022 and is a separate guarantee from producing backups. | Высокая | [карточка](reliability-observability.md) |
| XL | RO-023 | Isolated staging and canary release path | A genuinely isolated environment adds a second bot identity, credentials, chat, database, metrics endpoint, systemd unit, network policy, artifact promotion, and operating burden. It depends on RO-019/020/022 and must remain staging—not two production pollers sharing a token or SQLite file. | Высокая | [карточка](reliability-observability.md) |
| XL | RO-024 | Post-deploy synthetic smoke sentinel | Standalone full scope включает внешний runner, isolated synthetic principal/tenant, отдельные credentials, live Telegram и optional OpenAI, destructive cleanup и monitoring; несколько high-risk boundaries дают XL, хотя incremental slice после готовых foundations ближе к L. | Высокая | [карточка](reliability-observability.md) |

### Privacy & security

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| S | EO-015 | Systemd sandboxing для runtime процесса | The core change is iterative hardening of the existing unit plus smoke validation of known file, DNS, temp, and ffmpeg needs. It depends on an explicit runtime write/network contract; artifact immutability from EO-017 and secret lifecycle from RA-022 are complementary rather than part of this contained unit change. | Средняя | [карточка](engineering-operations.md) |
| M | RA-022 | Runtime and release secret lifecycle | A production-ready inventory spans Telegram, OpenAI, deploy SSH, backup, monitoring, webhook, and CI credentials, with redacted permission/age audits plus tested rotation, revocation, recovery, and rollback runbooks. It stores metadata only, but its cross-system operator work is broader than a documentation-only change and supports EO-017/EO-019/EO-022. | Высокая | [карточка](review-additions.md) |
| L | AI-016 | Безопасный загрузчик внешних ссылок | Redirect/DNS/actual-peer/IPv4-v6/decompression/deadline policy — отдельная security boundary; надо решить public-only против explicit private allowlist. | Высокая | [карточка](ai-recipes.md) |
| L | EO-012 | Runtime egress policy для recipe URL | This creates a security boundary around untrusted destinations and redirects, including IPv4/IPv6, DNS rebinding, Host/SNI correctness, actual-peer enforcement, and possibly a restricted proxy. It depends on an explicit decision about private recipe hosts and the EO-006 hermetic contract suite. | Высокая | [карточка](engineering-operations.md) |
| L | EO-029 | Политика retention и безопасная maintenance-команда | Production-ready cleanup combines per-data-class policy, additive repository/migration work, dry-run previews, bounded irreversible deletion, scheduling/serialization, backup-aware `VACUUM`, and recovery tests. It depends on a data/deletion registry, EO-019 backups, and the measured storage execution policy from EO-030. | Высокая | [карточка](engineering-operations.md) |
| L | RA-006 | Privacy and integration control center | The control surface must safely join personal and household read models across roles, diagnostics, schedules, exports, location, retention, and external grants while preserving separate sources of truth. Mutations need fresh authorization, preview, asynchronous revocation truth, and dependencies on `RA-008`, `RA-015`, and `RA-020`. | Средняя | [карточка](review-additions.md) |
| L | RA-008 | Data-flow, recipient, and deletion dependency registry | A production registry spans SQLite rows/free pages/WAL, temporary media, logs, metrics, exports, backups, derived views, and provider-side copies; it must feed maintenance dry-runs, export metadata, control-center summaries, and testable deletion completion. Domain-specific handlers remain necessary, and uncontrollable retention must be reported honestly. | Высокая | [карточка](review-additions.md) |
| L | RO-006 | Privacy-preserving diagnostic event schema | Separating metadata from content requires additive schema, sanitizer enforcement, migration/expiry decisions for sensitive existing rows, and backup-aware retention; the optional protected content path adds another data boundary. It depends on RO-003 and must stay distinct from domain history, operation journals, audit, and delivery state. | Высокая | [карточка](reliability-observability.md) |
| L | RO-007 | Retention, deletion, and backup privacy lifecycle | Policies and retry-safe deletion span multiple data classes, incremental cleanup scheduling, owner deletion, and backup expiry where erasure is necessarily delayed. It depends on an explicit clock/classification matrix, RO-020 backup metadata, and a bounded executor such as RO-030. | Высокая | [карточка](reliability-observability.md) |
| L | RO-026 | Secure recipe-fetch boundary with explainable failures | Actual-peer DNS/IP enforcement on every redirect, deadline/size/content budgets, safe telemetry, and hermetic tests form a security-critical network boundary. Merge runtime policy with the equivalent AI/operations ideas, retain the separate test harness, and explicitly decide trusted private-host behavior. | Высокая | [карточка](reliability-observability.md) |
| L | RO-027 | Separate security audit trail for authorization changes | An append-only, restricted, retention-controlled trail for grants and destructive actions is a new sensitive security-data boundary whose write failure cannot grant access. It depends on an authoritative membership/grant model; it is evidence, not the source of authorization, and must remain separate from diagnostic/domain history. | Высокая | [карточка](reliability-observability.md) |
| XL | RA-020 | Per-user integration credential lifecycle | A reusable credential platform needs encrypted-at-rest or external secret storage, key recovery/rotation, per-user/provider scopes, refresh/revocation state, redacted audit, backup/restore policy, and failure isolation across several live providers. It depends on `RA-015` and an accepted integration topology/threat model; callbacks may never carry tokens. | Высокая | [карточка](review-additions.md) |

### Data & application architecture

| Сложность | ID | Точное название | Обоснование / ключевая зависимость | Уверенность | Источник |
| :---: | --- | --- | --- | :---: | --- |
| XS | RA-021 | Architecture decision ledger for irreversible forks | The core deliverable is a numbered Markdown template, status convention, and linkage policy; it adds no runtime path or persistence. Optional CI link/ID linting would be a later S-sized addition. | Высокая | [карточка](review-additions.md) |
| M | AI-018 | Атомарное добавление ингредиентов рецепта в список | Короткая bulk transaction, operation ID/result и plan digest затрагивают service/storage/Telegram и аддитивную persistence, но не требуют long-running workflow. | Высокая | [карточка](ai-recipes.md) |
| M | RA-019 | Immutable application operation context | Resolving one immutable value at transport boundaries and passing it through selected service paths is a multi-module refactor without new durable authority. It depends on preserving explicit `chat_id` predicates and must not let ContextVars or background jobs supply implicit tenancy. | Высокая | [карточка](review-additions.md) |
| L | AI-008 | Осмысленное сложение единиц и количеств | Новый quantity value model и structured/original representation затрагивают extraction, recipes, service, formatting и старые данные; нужен rollout contract RA-010. | Высокая | [карточка](ai-recipes.md) |
| L | EO-030 | Подготовка к росту без преждевременного multi-instance | Measurement is easy, but completing the idea requires choosing and migrating to one execution/connection policy, possibly WAL/busy deadlines or bounded thread/worker offload, then validating cancellation, shutdown, transactions, backups, and load. It deliberately avoids the XL multi-replica/topology shift; EO-019/EO-020 must qualify any WAL choice. | Средняя | [карточка](engineering-operations.md) |
| L | RA-007 | Versioned derived-state registry | Сквозной provenance/lifecycle contract и миграции для caches, identities, metadata и scores; затрагивает несколько owner tables, но избегает generic feature store. | Высокая | [карточка](review-additions.md) |
| L | RA-009 | Manual-correction precedence contract | Общая provenance/value envelope и deterministic precedence lattice должны согласовать items, recipes, transcripts, categories и identities; нужна миграция существующих derivations. | Высокая | [карточка](review-additions.md) |
| L | RA-013 | Model-independent acceptance receipts | Chat-scoped receipt schema, canonical digests и связь typed session → confirmation → operation result проходят через все model-backed mutations и retention policy. | Высокая | [карточка](review-additions.md) |
| L | RA-016 | Feature rollout and interaction compatibility registry | A chat-scoped enrollment schema, feature/data prerequisites, interaction versions, stale-callback handling, migrations, exit paths, and preservation/export/deletion semantics become shared release infrastructure with combinatorial tests. It depends on EO-008 plus typed interaction and data-lifecycle foundations. | Высокая | [карточка](review-additions.md) |
| L | RA-017 | Capability-oriented module decomposition | The end state redistributes two large composition/storage modules into capability routers, services, repositories, and a stable composition root while preserving authorization, tenancy, transactions, and behavior. Incremental delivery lowers risk, but full completion is a material multi-module migration needing characterization/replay tests such as EO-005 and schema safeguards such as EO-008. | Средняя | [карточка](review-additions.md) |
| L | RA-025 | Structured shutdown and cancellation contract | One lifecycle coordinator must span SQLite, subprocesses, HTTP, OpenAI, Telegram, scheduled work, and durable leases, defining commit points and bounded drain/restart behavior. Actual library cancellation limits and phase-by-phase fault tests make this a cross-boundary lifecycle change. | Высокая | [карточка](review-additions.md) |
| L | RA-026 | Typed interaction-session framework | A durable typed session lifecycle, child schemas, reauthorization, versioned callbacks, expiry, recovery, and migration of existing confirmations form a security-sensitive state machine. It should precede expansion of RO-015-style flows and depends on bounded retention plus maintenance. | Высокая | [карточка](review-additions.md) |
| L | RO-013 | Telegram update idempotency ledger | A transactional claim/finalize ledger with TTL and crash recovery is a durable state machine around every update. It requires a migration, bot-scoped identity, uncertain-in-progress rules, and maintenance; keep update deduplication distinct from RO-014 operation recovery and domain undo history. | Высокая | [карточка](reliability-observability.md) |
| L | RO-014 | Durable operation journal for multi-item mutations | Journal and business-row linkage, progress/terminal states, reconciliation, retention, and idempotent resume or compensation create durable workflow state. Each operation must first choose atomic transaction versus resumable execution; never blindly replay an uncertain batch. | Высокая | [карточка](reliability-observability.md) |
| L | RO-015 | Confirmation expiry and stranded-claim recovery | Per-kind TTLs, claim transitions, digest-based overwrite reconciliation, scheduled cleanup, and explicit uncertain outcomes extend an existing security-sensitive state machine. RA-026 is the likely shared foundation; RO-030 can execute cleanup, but feature-specific recovery remains separate. | Высокая | [карточка](reliability-observability.md) |
| L | RO-017 | SQLite concurrency and event-loop isolation | A bounded executor/queue, connection policy, busy deadlines, and possibly WAL materially change storage concurrency and shutdown behavior. RO-009/018 should prove need and measure results; WAL also changes RO-020 backup/restore contracts and requires careful preservation of transaction semantics. | Высокая | [карточка](reliability-observability.md) |
| L | RO-030 | Unified ephemeral-state maintenance service | A bounded, restart-safe scheduler deleting across several tables needs per-class policy, ordering, backoff, fairness, dry-run, and health while protecting business data. Merge policy with RO-007 but keep the executor distinct; depend on a clock contract, RA-023 budgets, RA-025 shutdown, and relevant recovery rules. | Высокая | [карточка](reliability-observability.md) |
| XL | RA-015 | Unified actor and integration-principal model | Recasts humans, roles, automations, sentinels, jobs, share links, and providers as typed principals with scoped capabilities, issuer/expiry/revocation, and audit across all transport and domain boundaries. This identity-platform shift must preserve `chat_id` predicates and cannot pre-authorize integrations that do not yet exist. | Высокая | [карточка](review-additions.md) |

## Контроль полноты

- Строк идей: **176**.
- Уникальных ID: **176**.
- Покрытие исходного каталога: **176 из 176**, без пропусков и дополнительных ID.
- Названия: дословно совпадают с `CATALOG.md` и заголовками исходных карточек.
- Категории: только 11 значений из нормализованной taxonomy.
- Сложность: только `XS`, `S`, `M`, `L`, `XL`; внутри каждой категории порядок
  строго XS → S → M → L → XL, затем ID.
- Каждая идея имеет confidence, краткое size-обоснование и ссылку на источник.
