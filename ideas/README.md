# Каталог идей Honeybuy

Здесь собран **неранжированный brainstorm**, а не roadmap и не список
обязательств. В шести каталогах находится 176 идей: 150 исходных кандидатов и
26 дополнений, сформулированных по итогам перекрёстных review. Краткая сводка по
всему набору находится в [CATALOG.md](CATALOG.md).

## Карта категорий и сложности

Главный вход для подготовки к будущему ранжированию —
[карта категорий и сложности](COMPLEXITY.md). В ней все 176 идей разнесены по
11 основным категориям и внутри каждой категории отсортированы по сложности
`XS → S → M → L → XL`, затем по ID.

Рубрика, первичные оценки, независимые проверки и ключевые арбитражные решения
зафиксированы в [протоколе консилиума](council/README.md). Карта сложности — это
pre-ranking инструмент, а не roadmap, shortlist или оценка продуктовой ценности.

## Каталоги

| Каталог | ID | Количество | Фокус |
| --- | --- | ---: | --- |
| [Рецепты, AI, голос и персонализация](ai-recipes.md) | `AI-001`–`AI-030` | 30 | Recipe UX, ingestion, AI quality, voice и персонализация |
| [Совместная работа и интеграции](collaboration-integrations.md) | `CI-001`–`CI-030` | 30 | Household collaboration, planning, import/export и внешние интеграции |
| [Engineering и operations](engineering-operations.md) | `EO-001`–`EO-030` | 30 | Тестирование, eval, deploy, эксплуатация и инженерная надёжность |
| [Продукт и Telegram UX](product-telegram.md) | `PT-001`–`PT-030` | 30 | Shopping UX, Telegram-native flows, accessibility и onboarding |
| [Надёжность и наблюдаемость](reliability-observability.md) | `RO-001`–`RO-030` | 30 | Диагностика, privacy, recovery, release safety и monitoring |
| [Дополнения из review](review-additions.md) | `RA-001`–`RA-026` | 26 | Пробелы, найденные при независимом перекрёстном review |

У каждой идеи есть стабильный ID и название, категория, краткое описание,
потенциальная ценность, более подробное объяснение, легковесный архитектурный
набросок и оговорки: риски, неизвестные, зависимости или способы проверки.
Архитектура здесь описывает возможное направление, а не утверждённый дизайн.

## Перекрёстные review

| Review | Что проверялось | Вердикт |
| --- | --- | --- |
| [Product и Telegram](reviews/product-telegram-review.md) | Дубли, соответствие текущему поведению, реалистичность Telegram UX и Bot API, ясность value и пропущенные пользовательские пути | `REVIEW: APPROVE` |
| [AI и data](reviews/ai-data-review.md) | Граница deterministic/model-backed, provenance и validation данных, eval, стоимость, privacy, multilingual UX и feedback loops | `REVIEW: APPROVE` |
| [Architecture и operations](reviews/architecture-operations-review.md) | Общие платформенные примитивы, границы модулей и tenant, зависимости, миграции, runtime и безопасная эксплуатация | `ARCHITECTURE REVIEW: APPROVE` |
| [Collaboration, consent и tenancy](reviews/collaboration-consent-review.md) | Роли, согласие, permissions, abuse resistance, переносимость, внешние зависимости и graceful degradation | `REVIEW: APPROVE` |
| [Reliability, privacy и recovery](reviews/reliability-privacy-review.md) | Наблюдаемость, retention, idempotency, отказоустойчивость, backup/restore, release safety и monitoring | `REVIEW: APPROVE` |

`APPROVE` означает, что набор пригоден для консолидации и последующей
приоритизации. Это **не одобрение реализации** всех идей или их архитектурных
набросков. Перед ранжированием нужно сверяться с картами дублей, зависимостей,
общих платформенных примитивов и gating-оговорками во всех пяти review: похожие
записи могут описывать один capability, а внешне близкие идеи — требовать разных
границ безопасности и продукта.

## Следующий этап

Следующее действие — совместно кластеризовать пересечения, выделить shortlist и
только затем оценить кандидатов по согласованным критериям ценности, риска,
стоимости и зависимостей. В этом каталоге кластеризация, shortlist и scoring ещё
не выполнялись.

Каталог `ideas/` состоит только из brainstorm-документации. Он не включает
изменений production-кода или конфигурации, деплоя и обращений к live-сервисам.
