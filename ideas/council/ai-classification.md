# Консилиум: классификация AI, recipes и связанных review-идей

Это оценка **полной production-ready реализации**, а не прототипа и не
приоритизация по ценности. Идеи сохранены как отдельные записи даже там, где
review рекомендуют объединить их в один capability; такие пересечения указаны
как зависимости.

Шкала сложности:

- **XS** — presentation/configuration без нового persistent state;
- **S** — локальное изменение внутри существующей архитектурной границы;
- **M** — несколько модулей либо аддитивная таблица/миграция;
- **L** — durable state machine, scheduler, integration/security boundary или
  материальная миграция;
- **XL** — смена topology/tenant/identity/platform либо несколько
  высокорисковых внешних и data boundaries.

`ContextVar` ниже считается только request-local переносчиком correlation
state, никогда durable chat context. Model output во всех mutation flows — лишь
candidate: deterministic policy, domain validation, requester-bound preview и
повторная проверка scope остаются обязательными.

## XS

Нет идей: даже самые маленькие кандидаты требуют как минимум изменений в
нескольких слоях либо полноценного Telegram flow.

## S

| ID | Точное название | Основная категория | Сложность | Обоснование и зависимости | Deterministic/model, eval, cost и privacy | Уверенность |
| --- | --- | --- | :---: | --- | --- | :---: |
| AI-001 | Карточка сохранённого рецепта | Recipes & meal planning | S | Использует существующий chat-scoped recipe lookup; нужны handler/callback, formatter, pagination и escaping, но не новая таблица. | Полностью deterministic, без новых model-вызовов и cost; показывает только уже доступные в чате данные. | Высокая |

## M

| ID | Точное название | Основная категория | Сложность | Обоснование и зависимости | Deterministic/model, eval, cost и privacy | Уверенность |
| --- | --- | --- | :---: | --- | --- | :---: |
| AI-005 | Выборочное добавление ингредиентов | Shopping & list UX | M | Нужны paginated requester-only callbacks, stale/revision checks и pending selection; зависит от общего typed interaction envelope и желательно AI-018 для atomic apply. | Selection и применение deterministic; новых model-cost нет. Payload хранит только bounded candidate и требует TTL. | Высокая |
| AI-006 | Домашние базовые продукты | Shopping & list UX | M | Добавляет chat-scoped preferences, CRUD и override в recipe-add plan; опирается на canonical identity и контракты RA-007/RA-009. | Правило suppression deterministic; model normalization допустима только на cache miss. Нужны cross-language eval, versioned cache key и запрет inference из истории покупок. | Высокая |
| AI-011 | Подбор блюд под указанные продукты | Recipes & meal planning | M | Stateless query поверх текущих recipes/identities: service ranking, Telegram cards и optional ranker; новый pantry state не нужен. | Deterministic overlap — source of truth; модель видит только shortlist и возвращает известные IDs. Eval измеряет relevance/hallucination, cost bounded; raw query не хранится бессрочно. | Высокая |
| AI-014 | Сохранение рецепта из пересланного сообщения | Integrations & portability | M | Переиспользует Telegram reply/caption и существующий extractor/save flow; добавляет bounded ingestion DTO, provenance и dispatcher coverage. | Recognizer deterministic, extraction model-backed и платная. Нужны injection/evidence eval, минимизация чужого текста и явный запрет автоматического открытия ссылок. | Средняя |
| AI-018 | Атомарное добавление ингредиентов рецепта в список | Data & application architecture | M | Короткая bulk transaction, operation ID/result и plan digest затрагивают service/storage/Telegram и аддитивную persistence, но не требуют long-running workflow. | Candidate строится до транзакции; apply полностью deterministic, без AI внутри lock. Нужны barrier-based concurrency/idempotency/chat-isolation tests. | Высокая |
| AI-019 | Поиск и навигация по рецептам | Recipes & meal planning | M | Exact/alias/ingredient search, tags, pagination и при необходимости FTS требуют queries/index/schema, но остаются внутри SQLite topology. | Core deterministic, AI не обязателен и cost отсутствует. Fuzzy result не получает mutation authority; индекс должен удаляться/rebuild вместе с source data. | Высокая |
| AI-025 | Уточнение вместо рискованной догадки | AI, language & voice | M | Добавляет decision policy, candidate card и expiring confirmation поверх текущего router; общий typed interaction primitive удерживает работу в M. | Rules/risk/agreement принимают решение, model candidates не дают authority и не используют self-reported confidence как gate. Eval считает false-execute, abstention, recovery и extra-call cost; raw text не нужен в trace. | Средняя |

## L

| ID | Точное название | Основная категория | Сложность | Обоснование и зависимости | Deterministic/model, eval, cost и privacy | Уверенность |
| --- | --- | --- | :---: | --- | --- | :---: |
| AI-002 | Draft-preview и ручное редактирование ингредиентов | Recipes & meal planning | L | Durable requester-bound draft lifecycle, optimistic digest, atomic edit и recipe revision semantics; зависит от AI-003 и typed interaction sessions. | Extraction остаётся model candidate, edit/save deterministic; ручная правка не требует второго вызова. Нужны ambiguity eval для optional NL edits и TTL/minimization draft payload. | Высокая |
| AI-003 | История версий и откат рецепта | Recipes & meal planning | L | Перевод существующих recipes к immutable revisions/current pointer — материальная migration и фундамент для edit, merge, scaling и portability. | Полностью deterministic. Нужны fresh/legacy migration, concurrent restore и retention tests; versions — product data, не diagnostic log. | Высокая |
| AI-004 | Масштабирование по числу порций | Recipes & meal planning | L | Требует base servings, structured quantities, recipe revisions, preview и materialization; основные зависимости AI-003, AI-008, AI-018. | Модель может распознать исходные поля, но arithmetic/rounding deterministic и unresolved значения не угадываются. Нужны quantity extraction eval/property tests; новые recipe calls слегка дороже. | Высокая |
| AI-007 | Единая корзина из нескольких рецептов | Recipes & meal planning | L | Комбинирует recipe revisions, quantity engine, multi-source provenance, selection session и atomic batch; зависит от AI-003/AI-005/AI-008/AI-018. | Merge совместимых units deterministic; model assistance только для parse candidates и не конвертирует dimensions. Eval/cost ограничены unresolved quantities, privacy — только выбранные chat recipes. | Высокая |
| AI-008 | Осмысленное сложение единиц и количеств | Data & application architecture | L | Новый quantity value model и structured/original representation затрагивают extraction, recipes, service, formatting и старые данные; нужен rollout contract RA-010. | Парсинг может быть model-backed, но compatibility и arithmetic только deterministic. Нужны fuzz/property/locale eval, prompt-versioned cache и controlled cost на cache miss. | Высокая |
| AI-009 | Недельный план питания | Recipes & meal planning | L | Новый durable meal-plan aggregate, recipe metadata/revisions, quantity/materialization foundations и Telegram editor; объединяется с PT-015/CI-016 и зависит от AI-004/AI-007/AI-008. | Deterministic picker — baseline; AI planner optional, получает минимальный catalog и только возвращает разрешённые IDs. Нужны constraint/hallucination/cost eval и минимизация sensitive preferences. | Высокая |
| AI-010 | Повторяемые меню и ротация блюд | Recipes & meal planning | L | Template/occurrence tables, timezone, opt-in scheduler, idempotent delivery/outbox и restart recovery образуют durable scheduling system. | MVP deterministic и без AI cost. Privacy касается истории ротации/предпочтений; tests фиксируют DST, quiet hours, catch-up и duplicate-send behavior. | Высокая |
| AI-012 | JSON-LD-first извлечение рецепта | Integrations & portability | L | Требует bounded hostile-data parser, common DTO, field provenance и secure fetch prerequisite AI-016; меняет ingestion/extraction pipeline. | Deterministic extraction first снижает latency/tokens, model дополняет только validated gaps. Нужны schema-variant и injection/evidence eval; snapshots offline, page body не сохраняется. | Высокая |
| AI-013 | Рецепт из фото или скриншота | AI, language & voice | L | Новый bounded media boundary, temp artifact lifecycle, multi-page session, vision adapter и confirmation flow; зависит от common ingestion/session infrastructure. | Vision output — платный candidate со strict schema и deterministic domain validation. Synthetic fixtures идут в Git, live eval отдельно; реальные фото требуют consent, short retention и metadata stripping. | Высокая |
| AI-016 | Безопасный загрузчик внешних ссылок | Privacy & security | L | Redirect/DNS/actual-peer/IPv4-v6/decompression/deadline policy — отдельная security boundary; надо решить public-only против explicit private allowlist. | Полностью deterministic, AI cost отсутствует. Нужны hermetic policy/transport tests на rebinding и redirects; URL secrets/body не попадают в logs. | Высокая |
| AI-017 | Фоновая обработка тяжёлого импорта | Reliability & observability | L | SQLite jobs, leases, bounded worker, backpressure, retries, restart recovery и status delivery — durable state machine. | Worker deterministic, fetch/model stages изолированы и завершаются до confirmed save. Per-operation retry/cost caps и TTL для media/page text обязательны; fault tests покрывают crash/cancel. | Высокая |
| AI-020 | Поиск дублей и управляемое объединение | Recipes & meal planning | L | Нужны high-precision candidate scan, version-aware transactional merge aliases/ingredients, conflict handling и rollback через AI-003. | Candidate generation deterministic; model может лишь rank/explain, никогда auto-merge. Eval оптимизирует precision/false merge, cost — только shortlist; chat data не уходит шире пары кандидатов. | Высокая |
| AI-021 | Замены ингредиентов | Recipes & meal planning | L | Новый substitution adapter, safety/domain validation, plan confirmation и optional persistent recipe variant; зависит от AI-003/AI-008/typed sessions. | Предложение model-backed и платное, apply deterministic после выбора. Нужны allergen/unsafe-claim eval и bounded context; это не medical guarantee, user-approved substitutes имеют precedence RA-009. | Средняя |
| AI-023 | Планирование вокруг остатков и сроков | Recipes & meal planning | L | Новый inventory/lot/expiry domain, time semantics, explicit bought-to-pantry flow и planner/materialization; не смешивается со stateless AI-011. | Expiry/overlap scoring deterministic, модель только предлагает из shortlist и не выдумывает food-safety сроки. Нужны constraint/safety eval; pantry history чувствительна, opt-in и deletable. | Высокая |
| AI-024 | Ограниченный контекст для уточняющих реплик | AI, language & voice | L | Typed `(chat_id, user_id)` multi-update session с allowed transitions, TTL, revision/digest и restart recovery — durable interaction state machine. | Deterministic forms first; модель классифицирует только разрешённые actions и не выбирает scope. Eval покрывает contamination/stale/recovery/cost; `ContextVar` хранит лишь текущий correlation ID. | Высокая |
| AI-026 | Объяснимый трейс маршрутизации | Reliability & observability | L | Cross-cutting reason taxonomy, outer finalizer, context propagation, bounded trace store, sampling и owner view; это core общего trace epic с RO/EO/PT идеями. | Сохраняются только deterministic observed stages, не chain-of-thought. Нужны tests на exception/cancel/background propagation, secret/content absence и fixed-cardinality fields; model name/usage — metadata, raw prompts исключены. | Высокая |
| AI-027 | Исправления пользователя как eval-кандидаты | Testing & evals | L | Consented correction, quarantine/review states, trace linkage, sanitized export и corpus governance образуют durable human-in-the-loop workflow; объединяется с EO-002/RO-028. | Никакого online learning: model/rules меняются только через reviewed corpus/release. Eval governance предотвращает leakage; raw/label/Git record разделены, cost возникает лишь при последующем opt-in eval. | Высокая |
| AI-028 | Проверка и коррекция голосовой расшифровки | AI, language & voice | L | Risk policy, transcript/candidate session, correction/retry flow, bounded speech hints и private/group preview semantics расширяют существующую media boundary. | ASR model-backed и повтор платный; parse/apply после transcript проходят deterministic validation. Нужны consented/synthetic audio eval, false-destructive gate и короткая retention raw transcript. | Высокая |
| AI-029 | Язык чата, транслит и смешанные команды | AI, language & voice | L | Storage preference, message catalog, parser preprocessing, language-neutral categories/identities и prompt contracts затрагивают почти весь user-visible stack и derived caches. | Lexicon/transliteration first, model fallback получает locale hint; original item text не переписывается. Eval — RU/EN/translit/code-switch strata и reply language; cache/provenance versioning ограничивает semantic drift и cost. | Средняя |
| AI-030 | Eval-наборы для рецептов и реального аудио | Testing & evals | L | Общий runner/reporting, capability-specific corpora/graders, repeatability, artifact manifest и release gates выходят далеко за текущий text-routing suite. | Offline CI только валидирует fixtures/fakes; live model/ASR runs opt-in с отдельным credential, token/audio budget и фиксированным workload. Real consented media хранится вне Git в deletable restricted storage. | Средняя |
| RA-007 | Versioned derived-state registry | Data & application architecture | L | Сквозной provenance/lifecycle contract и миграции для caches, identities, metadata и scores; затрагивает несколько owner tables, но избегает generic feature store. | Model-derived state всегда rebuildable/versioned, deterministic facts и user decisions отделены. Recompute требует cost dry-run/caps; source deletion и manual override распространяются по declared policy. | Высокая |
| RA-009 | Manual-correction precedence contract | Data & application architecture | L | Общая provenance/value envelope и deterministic precedence lattice должны согласовать items, recipes, transcripts, categories и identities; нужна миграция существующих derivations. | AI возвращает только candidate и не перезаписывает manual scope. Нужны conflict/reset/upgrade eval и capability rules; corrections могут быть personal data и имеют отдельные visibility/retention. | Высокая |
| RA-010 | Persisted-semantics rollout sandbox | Testing & evals | L | Baseline/candidate adapter execution, versioned fixtures, semantic validators, diff reports и release linkage создают отдельный AI release-safety workflow поверх RA-007. | Сравнение model-backed и потому stochastic/платное; deterministic validators решают допустимость, а не judge-only score. Только synthetic/approved fixtures, отдельный eval key, repetitions и budget cap; production bulk rewrite не выполняется. | Средняя |
| RA-012 | Cross-modal injection and isolation corpus | Testing & evals | L | Format-neutral manifest плюс OCR/document/media/integration adapters и resource/isolation graders охватывают несколько hostile-input boundaries. | Routine CI использует fakes и deterministic reason/outcome graders; live models opt-in и budgeted. Fixtures synthetic/scrubbed, production Telegram content и sensitive media автоматически не сохраняются. | Средняя |
| RA-013 | Model-independent acceptance receipts | Data & application architecture | L | Chat-scoped receipt schema, canonical digests и связь typed session → confirmation → operation result проходят через все model-backed mutations и retention policy. | Receipt deterministic и фиксирует видимый candidate/result, не model reasoning. Raw prompt/input/media исключены; нужны canonicalization, idempotency, deletion/linkability и display-revision tests. | Высокая |

## XL

| ID | Точное название | Основная категория | Сложность | Обоснование и зависимости | Deterministic/model, eval, cost и privacy | Уверенность |
| --- | --- | --- | :---: | --- | --- | :---: |
| AI-015 | Происхождение, переносимость и безопасное обновление | Integrations & portability | XL | Одна идея объединяет material recipe revision migration, field provenance, versioned export/import schema, conflict resolution и безопасный external refresh; это несколько high-risk data/external boundaries. Зависит от AI-003/AI-012/AI-016/RA-007/RA-009. | Fetch/extract могут быть model-backed, но serialization, digest, diff и apply deterministic. Нужны compatibility/injection/provenance eval и costed refresh policy; URL secrets, source bodies и forwarded exports имеют отдельные retention/recipient риски. | Средняя |
| AI-022 | Диетические и аллергенные ограничения | Privacy & security | XL | Вводит subject-owned private profiles, household rules, participant consent/disclosure, capability checks, deletion и safety-sensitive planner/substitution integration — фактически новый identity/privacy layer. | Exact exclusions deterministic; model classifier только выдаёт warning/evidence и никогда safe-certification. False-safe gate строже false-warning, live cost bounded; sensitive health-like data opt-in, purpose-limited и не раскрывается владельцу чата автоматически. | Высокая |

## Контроль покрытия

| Сложность | Количество |
| --- | ---: |
| XS | 0 |
| S | 1 |
| M | 7 |
| L | 25 |
| XL | 2 |
| **Всего** | **35** |

Охвачены ровно `AI-001`–`AI-030` и `RA-007`, `RA-009`, `RA-010`,
`RA-012`, `RA-013`. Внутри каждого уровня строки отсортированы по ID; value
ranking не выполнялся.
