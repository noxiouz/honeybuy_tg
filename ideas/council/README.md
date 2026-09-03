# Протокол консилиума по сложности идей

## Цель и границы

Консилиум подготовил [pre-ranking карту](../COMPLEXITY.md): все 176 идей получили
ровно одну основную категорию из 11 и относительную оценку сложности. Внутри
каждой категории строки отсортированы `XS → S → M → L → XL`, затем
по стабильному ID.

Это не roadmap, shortlist, value ranking, календарная оценка или решение об
имплементации. Complexity показывает инженерный размер честного
production-ready core и должна позднее рассматриваться отдельно от value,
риска и продуктового приоритета.

## Рубрика CAL-000

`CAL-000` определяет complexity как **intrinsic, non-recursive** размер
минимального честного production-ready core идеи. В него входят необходимые
для самого core миграции, authorization и chat isolation, тесты/eval, privacy,
recovery и operator burden. Полная стоимость отдельных prerequisites не
прибавляется повторно каждому consumer; явно `optional`, `later` или
альтернативные extensions оцениваются как отдельные candidates.

| Уровень | Граница сложности |
| --- | --- |
| XS | Документация, presentation или configuration без нового runtime-state и persistence. |
| S | Локальное production-ready изменение внутри существующей границы без нового durable workflow или integration boundary. |
| M | Несколько модулей, широкий offline/eval suite либо аддитивная schema/migration в текущей topology. |
| L | Durable state machine/scheduler, новая integration/security boundary, необратимая операция или материальная миграция. |
| XL | Смена topology, tenant/identity/platform либо несколько высокорисковых external/data boundaries. |

## Итог

| Сложность | XS | S | M | L | XL | Всего |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Количество | 1 | 6 | 74 | 87 | 8 | 176 |

| Основная категория | XS | S | M | L | XL | Всего |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Shopping & list UX | 0 | 0 | 15 | 7 | 1 | 23 |
| Recipes & meal planning | 0 | 1 | 2 | 13 | 0 | 16 |
| Language, accessibility & multimodal interaction | 0 | 0 | 4 | 7 | 0 | 11 |
| Collaboration & access | 0 | 0 | 3 | 10 | 1 | 14 |
| Product onboarding & engagement | 0 | 0 | 6 | 5 | 0 | 11 |
| Integrations & portability | 0 | 0 | 7 | 4 | 2 | 13 |
| Reliability & observability | 0 | 1 | 17 | 7 | 0 | 25 |
| Testing & evals | 0 | 2 | 11 | 1 | 0 | 14 |
| Deployment & operations | 0 | 1 | 6 | 10 | 2 | 19 |
| Privacy & security | 0 | 1 | 1 | 9 | 1 | 12 |
| Data & application architecture | 1 | 0 | 2 | 14 | 1 | 18 |
| **Всего** | **1** | **6** | **74** | **87** | **8** | **176** |

Итоговые числа вычислены по подробным строкам карты; этот протокол их только
кратко воспроизводит.

## Первичные классификации

Пять отчётов были независимыми входами для синтеза. Они сохраняют исходные
обоснования и граничные случаи, но каноническим результатом после арбитража
остаётся [COMPLEXITY.md](../COMPLEXITY.md).

| Отчёт | Строк идей | Область |
| --- | ---: | --- |
| [AI, recipes и review-дополнения](ai-classification.md) | 35 | AI, recipes, language, voice и связанные foundations |
| [Collaboration и integrations](collaboration-classification.md) | 36 | Совместная работа, consent, access и внешние интеграции |
| [Engineering и operations](engineering-classification.md) | 35 | Testing, eval, release engineering и operations |
| [Product и Telegram](product-classification.md) | 34 | Shopping/list UX и Telegram-native product flows |
| [Reliability и observability](reliability-classification.md) | 36 | Диагностика, resilience, privacy, recovery и monitoring |
| **Всего** | **176** | Каждая идея покрыта ровно один раз |

## Финальные review

После синтеза пять независимых проверок сверили калибровку, taxonomy,
Telegram/product feasibility, архитектурные зависимости и механическую
полноту. Все findings были разрешены до финального snapshot.

| Review | Проверяемая граница | Финальный вердикт |
| --- | --- | --- |
| [Калибровка сложности](reviews/calibration-review.md) | Единообразие `M/L/XL`, CAL-000 и меньшие срезы | `FINAL REVIEW: APPROVE` |
| [Taxonomy](reviews/taxonomy-review.md) | Одна primary category, полезность групп для будущего сравнения | `FINAL REVIEW: APPROVE` |
| [Product и Telegram](reviews/product-complexity-review.md) | Telegram constraints, core-versus-extension и effort calibration | `FINAL REVIEW: APPROVE` |
| [Architecture и dependencies](reviews/architecture-dependency-review.md) | Schema, topology, security, operations и dependency accounting | `FINAL REVIEW: APPROVE` |
| [Completeness](reviews/completeness-review.md) | 176/176 ID, title/source parity, порядок, ссылки и arithmetic | `FINAL REVIEW: APPROVE` |

## Ключевые арбитражные решения

- **Optional extensions не рекурсивны.** Отдельная future boundary не повышает
  оценку core автоматически; её нужно оформить отдельным candidate со своими
  acceptance criteria.
- **`PT-028` остаётся XL в полном заявленном scope.** Идея меняет центральный
  scope key на composite и требует сквозной миграции list/callback/session
  state. Более узкий topic-as-list вариант с неизменным tenant можно оценивать
  как отдельный L-срез.
- **`RO-024` остаётся XL как standalone sentinel.** В полном core одновременно
  присутствуют внешний runner, isolated principal/tenant, credentials, live
  boundaries и destructive cleanup. Incremental scenario после готовых
  foundations можно выделить как отдельный L-срез.
- **`PT-025` применяет изменение только через explicit callback/handoff.**
  Requester-bound callback повторно проверяет текущие права и target/session,
  вызывает обычную service mutation и только затем сообщает успех.
  `ChosenInlineResult` и inline feedback допустимы лишь как optional telemetry,
  но не как correctness/apply trigger.

Остальные merge/dependency families, граничные случаи и rationale находятся
непосредственно в [карте сложности](../COMPLEXITY.md).

## Артефакты

- [Карта категорий и сложности](../COMPLEXITY.md) — канонический результат
  консилиума и все 176 отсортированных строк.
- [Нейтральный каталог](../CATALOG.md) — исходный полный индекс без complexity
  или value ranking.
- [Главная страница brainstorm](../README.md) — навигация по исходным каталогам
  и перекрёстным product/engineering review.
- Первичные классификации и финальные review перечислены в таблицах выше; они
  сохраняют audit trail принятых решений.
