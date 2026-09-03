# Каталог идей

Это нейтральный индекс brainstorm-кандидатов для последующего сравнения и
ранжирования. Он не является roadmap: порядок строк, принадлежность к исходному
каталогу и формулировки не выражают приоритет, оценку трудоёмкости или решение о
реализации. Подробное описание, потенциальная ценность, архитектурный набросок и
оговорки каждой идеи находятся в указанном исходном файле.

## Сводка

| Префикс | Исходный каталог | Количество |
| --- | --- | ---: |
| AI | [ai-recipes.md](ai-recipes.md) | 30 |
| CI | [collaboration-integrations.md](collaboration-integrations.md) | 30 |
| EO | [engineering-operations.md](engineering-operations.md) | 30 |
| PT | [product-telegram.md](product-telegram.md) | 30 |
| RO | [reliability-observability.md](reliability-observability.md) | 30 |
| RA | [review-additions.md](review-additions.md) | 26 |
| **Всего** |  | **176** |

## AI — Рецепты, AI, голос и персонализация

| ID | Точное название | Категория | Исходный файл |
| --- | --- | --- | --- |
| AI-001 | Карточка сохранённого рецепта | Recipe UX | [ai-recipes.md](ai-recipes.md) |
| AI-002 | Draft-preview и ручное редактирование ингредиентов | Recipe management | [ai-recipes.md](ai-recipes.md) |
| AI-003 | История версий и откат рецепта | Safety / recipe management | [ai-recipes.md](ai-recipes.md) |
| AI-004 | Масштабирование по числу порций | Meal planning | [ai-recipes.md](ai-recipes.md) |
| AI-005 | Выборочное добавление ингредиентов | Recipe-to-list UX | [ai-recipes.md](ai-recipes.md) |
| AI-006 | Домашние базовые продукты | Personalization | [ai-recipes.md](ai-recipes.md) |
| AI-007 | Единая корзина из нескольких рецептов | Meal planning | [ai-recipes.md](ai-recipes.md) |
| AI-008 | Осмысленное сложение единиц и количеств | Data quality | [ai-recipes.md](ai-recipes.md) |
| AI-009 | Недельный план питания | Meal planning | [ai-recipes.md](ai-recipes.md) |
| AI-010 | Повторяемые меню и ротация блюд | Personalization | [ai-recipes.md](ai-recipes.md) |
| AI-011 | Подбор блюд под указанные продукты | Discovery | [ai-recipes.md](ai-recipes.md) |
| AI-012 | JSON-LD-first извлечение рецепта | AI cost / ingestion | [ai-recipes.md](ai-recipes.md) |
| AI-013 | Рецепт из фото или скриншота | Multimodal ingestion | [ai-recipes.md](ai-recipes.md) |
| AI-014 | Сохранение рецепта из пересланного сообщения | Telegram ingestion | [ai-recipes.md](ai-recipes.md) |
| AI-015 | Происхождение, переносимость и безопасное обновление | Trust / provenance | [ai-recipes.md](ai-recipes.md) |
| AI-016 | Безопасный загрузчик внешних ссылок | Security / ingestion | [ai-recipes.md](ai-recipes.md) |
| AI-017 | Фоновая обработка тяжёлого импорта | Reliability | [ai-recipes.md](ai-recipes.md) |
| AI-018 | Атомарное добавление ингредиентов рецепта в список | Reliability | [ai-recipes.md](ai-recipes.md) |
| AI-019 | Поиск и навигация по рецептам | Discovery | [ai-recipes.md](ai-recipes.md) |
| AI-020 | Поиск дублей и управляемое объединение | Data quality | [ai-recipes.md](ai-recipes.md) |
| AI-021 | Замены ингредиентов | Recipe assistance | [ai-recipes.md](ai-recipes.md) |
| AI-022 | Диетические и аллергенные ограничения | Safety / personalization | [ai-recipes.md](ai-recipes.md) |
| AI-023 | Планирование вокруг остатков и сроков | Waste reduction | [ai-recipes.md](ai-recipes.md) |
| AI-024 | Ограниченный контекст для уточняющих реплик | Natural language UX | [ai-recipes.md](ai-recipes.md) |
| AI-025 | Уточнение вместо рискованной догадки | AI safety / cost | [ai-recipes.md](ai-recipes.md) |
| AI-026 | Объяснимый трейс маршрутизации | Observability / support | [ai-recipes.md](ai-recipes.md) |
| AI-027 | Исправления пользователя как eval-кандидаты | AI quality | [ai-recipes.md](ai-recipes.md) |
| AI-028 | Проверка и коррекция голосовой расшифровки | Voice safety | [ai-recipes.md](ai-recipes.md) |
| AI-029 | Язык чата, транслит и смешанные команды | Multilingual UX | [ai-recipes.md](ai-recipes.md) |
| AI-030 | Eval-наборы для рецептов и реального аудио | AI quality | [ai-recipes.md](ai-recipes.md) |

## CI — Совместная работа, планирование, импорт и интеграции

| ID | Точное название | Категория | Исходный файл |
| --- | --- | --- | --- |
| CI-001 | Household roles | Collaboration and access | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-002 | Item requester and shopper attribution | Collaboration and accountability | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-003 | Voluntary item assignment | Collaboration and planning | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-004 | Shopping-trip claim and handoff | Real-time household coordination | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-005 | Item discussion threads | Collaboration and context | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-006 | Suggestion inbox for restricted members | Collaboration and consent | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-007 | Time-limited guest access | Sharing and temporary access | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-008 | Linked household spaces across chats | Cross-chat collaboration | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-009 | Named list spaces | Household organization | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-010 | Recurring staples | Household routines | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-011 | Due dates and shopping windows | Planning | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-012 | Configurable reminder digest | Reminders and engagement | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-013 | Personal notification preferences | Collaboration and notification control | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-014 | Store or area arrival reminder | Contextual reminders | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-015 | Weekly household review | Planning habits | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-016 | Meal plan connected to saved recipes | Planning and recipes | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-017 | Pantry minimums and replenishment | Inventory habits | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-018 | Reusable household routines | Templates and habits | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-019 | Explainable reorder suggestions | Retention and assistance | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-020 | Guided household onboarding | Onboarding | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-021 | Contextual feature discovery | Product education | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-022 | Inactivity-safe reactivation | Retention | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-023 | Self-service data export | Export and trust | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-024 | Preview-first list import | Import and migration | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-025 | Forward-to-list capture | Telegram-native import | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-026 | Portable recipe and routine bundle | Import/export and sharing | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-027 | Calendar handoff | Calendar integration | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-028 | Home Assistant bridge | Smart-home integration | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-029 | Retailer search and cart handoff | Commerce integration | [collaboration-integrations.md](collaboration-integrations.md) |
| CI-030 | Read-only companion view | Optional ecosystem extension | [collaboration-integrations.md](collaboration-integrations.md) |

## EO — Инженерия, тестирование и эксплуатация

| ID | Точное название | Категория | Исходный файл |
| --- | --- | --- | --- |
| EO-001 | Объяснимый trace решения для каждого входящего сообщения | наблюдаемость, AI reliability | [engineering-operations.md](engineering-operations.md) |
| EO-002 | Конвейер «ошибка в эксплуатации → новый eval-кейс» | evals, непрерывное улучшение | [engineering-operations.md](engineering-operations.md) |
| EO-003 | Разделить evals по продуктовым способностям | evals, качество продукта | [engineering-operations.md](engineering-operations.md) |
| EO-004 | Контролируемый voice/audio eval-набор | evals, voice reliability | [engineering-operations.md](engineering-operations.md) |
| EO-005 | Replay-harness для Telegram update-сценариев | интеграционное тестирование | [engineering-operations.md](engineering-operations.md) |
| EO-006 | Герметичный контрактный стенд для recipe URL fetching | интеграционное тестирование, безопасность сети | [engineering-operations.md](engineering-operations.md) |
| EO-007 | Stateful/property-based тесты жизненного цикла списка | тестирование инвариантов | [engineering-operations.md](engineering-operations.md) |
| EO-008 | Матрица миграций из исторических SQLite fixtures | миграции, release safety | [engineering-operations.md](engineering-operations.md) |
| EO-009 | Детерминированная fault-injection матрица | reliability testing | [engineering-operations.md](engineering-operations.md) |
| EO-010 | Парное и статистически осмысленное сравнение live eval | eval methodology | [engineering-operations.md](engineering-operations.md) |
| EO-011 | Реестр eval-результатов и тренды качества | evals, release evidence | [engineering-operations.md](engineering-operations.md) |
| EO-012 | Runtime egress policy для recipe URL | network security, reliability | [engineering-operations.md](engineering-operations.md) |
| EO-013 | Journald retention и защита диска от заполнения | operations, log durability | [engineering-operations.md](engineering-operations.md) |
| EO-014 | Scheduled dependency compatibility и security drift check | maintainability, supply chain | [engineering-operations.md](engineering-operations.md) |
| EO-015 | Systemd sandboxing для runtime процесса | host security, deployment | [engineering-operations.md](engineering-operations.md) |
| EO-016 | Read-only deployment preflight/doctor | deployment safety, developer experience | [engineering-operations.md](engineering-operations.md) |
| EO-017 | Неизменяемые atomic releases с manifest | release engineering, deployment, rollback | [engineering-operations.md](engineering-operations.md) |
| EO-018 | Идемпотентный release state machine | deployment orchestration | [engineering-operations.md](engineering-operations.md) |
| EO-019 | Автоматические согласованные SQLite backups | backup, data durability | [engineering-operations.md](engineering-operations.md) |
| EO-020 | Версионированная restore-команда и регулярный restore drill | disaster recovery | [engineering-operations.md](engineering-operations.md) |
| EO-021 | Rollback pack, связывающий код и состояние базы | release safety, rollback | [engineering-operations.md](engineering-operations.md) |
| EO-022 | Ручное promotion из GitHub Actions с защищённой средой | CI/CD | [engineering-operations.md](engineering-operations.md) |
| EO-023 | Health-check CLI с уровнями readiness | health checks, operations | [engineering-operations.md](engineering-operations.md) |
| EO-024 | Event-loop watchdog и отдельный polling health signal | availability | [engineering-operations.md](engineering-operations.md) |
| EO-025 | Структурированные JSON-логи с корреляцией | observability, diagnostics | [engineering-operations.md](engineering-operations.md) |
| EO-026 | Небольшой SLO-набор и actionable alerts | observability, reliability | [engineering-operations.md](engineering-operations.md) |
| EO-027 | Явная OpenAI resilience policy | runtime reliability | [engineering-operations.md](engineering-operations.md) |
| EO-028 | Учёт token/cost и мягкие бюджеты | operational cost | [engineering-operations.md](engineering-operations.md) |
| EO-029 | Политика retention и безопасная maintenance-команда | storage operations, privacy, cost | [engineering-operations.md](engineering-operations.md) |
| EO-030 | Подготовка к росту без преждевременного multi-instance | scalability boundary, maintainability | [engineering-operations.md](engineering-operations.md) |

## PT — Продукт и Telegram UX

| ID | Точное название | Категория | Исходный файл |
| --- | --- | --- | --- |
| PT-001 | Edit Structured Item Details | List ergonomics | [product-telegram.md](product-telegram.md) |
| PT-002 | Need-By Dates and Time Views | Planning | [product-telegram.md](product-telegram.md) |
| PT-003 | Named Lists Within a Chat | Information organization | [product-telegram.md](product-telegram.md) |
| PT-004 | Recurring Staples | Automation | [product-telegram.md](product-telegram.md) |
| PT-005 | Lightweight Pantry State | Household inventory | [product-telegram.md](product-telegram.md) |
| PT-006 | Store and Aisle Profiles | In-store experience | [product-telegram.md](product-telegram.md) |
| PT-007 | Item Assignment and Claiming | Group collaboration | [product-telegram.md](product-telegram.md) |
| PT-008 | Priority Signals and Household Voting | Group decision-making | [product-telegram.md](product-telegram.md) |
| PT-009 | Synchronized Shopping Sessions | Collaborative shopping | [product-telegram.md](product-telegram.md) |
| PT-010 | Bulk Selection and Partial Actions | List ergonomics | [product-telegram.md](product-telegram.md) |
| PT-011 | Activity Timeline and Precise Undo | Trust and recoverability | [product-telegram.md](product-telegram.md) |
| PT-012 | Bought History and One-Tap Re-Add | Repeat shopping | [product-telegram.md](product-telegram.md) |
| PT-013 | User-Initiated Frequent-Item Suggestions | Assisted planning | [product-telegram.md](product-telegram.md) |
| PT-014 | Reusable Shopping Bundles | Repeat shopping | [product-telegram.md](product-telegram.md) |
| PT-015 | Weekly Meal Plan to Shopping List | Meal planning | [product-telegram.md](product-telegram.md) |
| PT-016 | Recipe Scaling and Ingredient Picker | Recipe UX | [product-telegram.md](product-telegram.md) |
| PT-017 | Clarification Cards for Ambiguous Text | Conversational UX | [product-telegram.md](product-telegram.md) |
| PT-018 | “Why Did the Bot Do That?” Explainer | Trust and explainability | [product-telegram.md](product-telegram.md) |
| PT-019 | Voice Transcript Preview and Correction | Voice UX | [product-telegram.md](product-telegram.md) |
| PT-020 | Multi-Part Voice Shopping Session | Voice UX | [product-telegram.md](product-telegram.md) |
| PT-021 | Photo List Capture | Multimodal input | [product-telegram.md](product-telegram.md) |
| PT-022 | Receipt Reconciliation | Post-shopping workflow | [product-telegram.md](product-telegram.md) |
| PT-023 | Barcode and Product-Code Capture | Multimodal input | [product-telegram.md](product-telegram.md) |
| PT-024 | Reaction-Based Message Capture | Telegram-native interaction | [product-telegram.md](product-telegram.md) |
| PT-025 | Inline Capture From Other Chats | Telegram-native interaction | [product-telegram.md](product-telegram.md) |
| PT-026 | Per-User Language and Accessible Rendering | Accessibility and localization | [product-telegram.md](product-telegram.md) |
| PT-027 | Digests, Reminders, and Quiet Hours | Notifications | [product-telegram.md](product-telegram.md) |
| PT-028 | Telegram Topic-Scoped Lists | Group organization | [product-telegram.md](product-telegram.md) |
| PT-029 | Personal-to-Household List Transfer | Private and group workflow | [product-telegram.md](product-telegram.md) |
| PT-030 | Guided Onboarding and Capability Discovery | Onboarding | [product-telegram.md](product-telegram.md) |

## RO — Надёжность и наблюдаемость

| ID | Точное название | Категория | Исходный файл |
| --- | --- | --- | --- |
| RO-001 | End-to-end request decision trace | Routing diagnostics | [reliability-observability.md](reliability-observability.md) |
| RO-002 | Owner-visible “why?” explainer | Operator diagnostics / Telegram UX | [reliability-observability.md](reliability-observability.md) |
| RO-003 | Stable reason-code catalog | Diagnostic contracts | [reliability-observability.md](reliability-observability.md) |
| RO-004 | Sampled tracing with temporary diagnostic windows | Observability controls | [reliability-observability.md](reliability-observability.md) |
| RO-005 | Structured journald logging with correlation | Logging | [reliability-observability.md](reliability-observability.md) |
| RO-006 | Privacy-preserving diagnostic event schema | Privacy / data model | [reliability-observability.md](reliability-observability.md) |
| RO-007 | Retention, deletion, and backup privacy lifecycle | Data lifecycle | [reliability-observability.md](reliability-observability.md) |
| RO-008 | Low-cardinality routing and fallback metrics | Metrics | [reliability-observability.md](reliability-observability.md) |
| RO-009 | Request latency phase breakdown | Performance observability | [reliability-observability.md](reliability-observability.md) |
| RO-010 | AI outcome, prompt, and cost observability | AI reliability | [reliability-observability.md](reliability-observability.md) |
| RO-011 | Explicit AI resilience envelope | External dependency reliability | [reliability-observability.md](reliability-observability.md) |
| RO-012 | Telegram delivery reliability envelope | External dependency reliability | [reliability-observability.md](reliability-observability.md) |
| RO-013 | Telegram update idempotency ledger | Correctness / recovery | [reliability-observability.md](reliability-observability.md) |
| RO-014 | Durable operation journal for multi-item mutations | Data integrity | [reliability-observability.md](reliability-observability.md) |
| RO-015 | Confirmation expiry and stranded-claim recovery | State lifecycle / recovery | [reliability-observability.md](reliability-observability.md) |
| RO-016 | Read-only database invariant doctor | Data integrity / operator tooling | [reliability-observability.md](reliability-observability.md) |
| RO-017 | SQLite concurrency and event-loop isolation | Performance / availability | [reliability-observability.md](reliability-observability.md) |
| RO-018 | Database health and capacity telemetry | Storage observability | [reliability-observability.md](reliability-observability.md) |
| RO-019 | Migration preflight and postflight contract | Deployment safety | [reliability-observability.md](reliability-observability.md) |
| RO-020 | Automated consistent backup with manifest | Recovery | [reliability-observability.md](reliability-observability.md) |
| RO-021 | Restore drill and rollback rehearsal | Recovery validation | [reliability-observability.md](reliability-observability.md) |
| RO-022 | Release identity and deployment markers | Deployment observability | [reliability-observability.md](reliability-observability.md) |
| RO-023 | Isolated staging and canary release path | Deployment safety | [reliability-observability.md](reliability-observability.md) |
| RO-024 | Post-deploy synthetic smoke sentinel | Availability verification | [reliability-observability.md](reliability-observability.md) |
| RO-025 | Service-level objectives and actionable alerts | Reliability management | [reliability-observability.md](reliability-observability.md) |
| RO-026 | Secure recipe-fetch boundary with explainable failures | Security / external dependency reliability | [reliability-observability.md](reliability-observability.md) |
| RO-027 | Separate security audit trail for authorization changes | Security observability | [reliability-observability.md](reliability-observability.md) |
| RO-028 | Sanitized failure inbox for unresolved requests | Product feedback / diagnostics | [reliability-observability.md](reliability-observability.md) |
| RO-029 | Startup and crash-loop diagnostic capsule | Process reliability | [reliability-observability.md](reliability-observability.md) |
| RO-030 | Unified ephemeral-state maintenance service | Lifecycle / maintenance | [reliability-observability.md](reliability-observability.md) |

## RA — Дополнения по итогам ревью

| ID | Точное название | Категория | Исходный файл |
| --- | --- | --- | --- |
| RA-001 | In-store exception outcomes | Shopping UX / collaboration | [review-additions.md](review-additions.md) |
| RA-002 | Household offboarding and ownership transfer | Collaboration / authorization / privacy | [review-additions.md](review-additions.md) |
| RA-003 | Shopping-trip budget and actual spend | Product / household planning | [review-additions.md](review-additions.md) |
| RA-004 | Canonical pinned list message | Telegram UX | [review-additions.md](review-additions.md) |
| RA-005 | Shopping-item search and archive | Product / retrieval | [review-additions.md](review-additions.md) |
| RA-006 | Privacy and integration control center | Product trust / privacy controls | [review-additions.md](review-additions.md) |
| RA-007 | Versioned derived-state registry | AI/data platform | [review-additions.md](review-additions.md) |
| RA-008 | Data-flow, recipient, and deletion dependency registry | Privacy architecture / governance | [review-additions.md](review-additions.md) |
| RA-009 | Manual-correction precedence contract | Data quality / user agency | [review-additions.md](review-additions.md) |
| RA-010 | Persisted-semantics rollout sandbox | AI release safety / data migration | [review-additions.md](review-additions.md) |
| RA-011 | Capability-specific safe-mode experience | Reliability / product UX | [review-additions.md](review-additions.md) |
| RA-012 | Cross-modal injection and isolation corpus | Security testing / AI evals | [review-additions.md](review-additions.md) |
| RA-013 | Model-independent acceptance receipts | Explainability / mutation integrity | [review-additions.md](review-additions.md) |
| RA-014 | Household disagreement resolution | Collaboration / decision UX | [review-additions.md](review-additions.md) |
| RA-015 | Unified actor and integration-principal model | Authorization architecture | [review-additions.md](review-additions.md) |
| RA-016 | Feature rollout and interaction compatibility registry | Release architecture / product compatibility | [review-additions.md](review-additions.md) |
| RA-017 | Capability-oriented module decomposition | Maintainability / architecture | [review-additions.md](review-additions.md) |
| RA-018 | Offline command configuration profiles | Operations / safety | [review-additions.md](review-additions.md) |
| RA-019 | Immutable application operation context | Application architecture / correctness | [review-additions.md](review-additions.md) |
| RA-020 | Per-user integration credential lifecycle | Integration security / privacy | [review-additions.md](review-additions.md) |
| RA-021 | Architecture decision ledger for irreversible forks | Engineering governance | [review-additions.md](review-additions.md) |
| RA-022 | Runtime and release secret lifecycle | Security operations | [review-additions.md](review-additions.md) |
| RA-023 | Background-work operator budget and failure isolation | Reliability architecture / operations | [review-additions.md](review-additions.md) |
| RA-024 | Monitoring-path health and alert delivery proof | Observability reliability | [review-additions.md](review-additions.md) |
| RA-025 | Structured shutdown and cancellation contract | Reliability / lifecycle correctness | [review-additions.md](review-additions.md) |
| RA-026 | Typed interaction-session framework | Application infrastructure / Telegram safety | [review-additions.md](review-additions.md) |
