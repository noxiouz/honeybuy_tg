# Независимый review калибровки сложности

> **Статус:** исходный blocking review сохранён ниже как audit trail. Его
> требования прошли арбитраж; актуальный результат находится в разделе
> «Final re-review» и заканчивается отдельным `FINAL REVIEW` verdict.

Проверен `ideas/COMPLEXITY.md` против пяти первичных классификаций,
`ideas/CATALOG.md`, исходных карточек и cross-review материалов. Фокус этого
прохода — только согласованность шкалы `XS/S/M/L/XL`; value, приоритет и
календарные оценки не рассматривались.

## Исходный итог до арбитража

Покрытие выглядит полным, а большинство оценок внутри семейств согласовано.
Однако итог пока нельзя считать откалиброванным: одинаковые capabilities
получили разные классы, а несколько карточек оценены по явно опциональному
будущему расширению вместо минимального production-ready результата,
обещанного самой карточкой.

## Обязательные замечания

### CAL-000 — Не зафиксировано, входят ли отдельные prerequisites в размер идеи

- **Текущее состояние:** mixed policy.
- **Рекомендация:** `mixed → intrinsic non-recursive full-core scope`.
- **Доказательство:** `RO-002` оценён как M поверх готового trace store, тогда
  как `PT-018` включает тот же store и получает L; `RA-006` остаётся L поверх
  XL-зависимостей `RA-015`/`RA-020`, но `RO-024` фактически получает XL за
  повторный учёт staging из `RO-023`. Одновременно engineering-классификация
  прямо говорит, что optional extension не складывается в оценку, а
  collaboration/product-классификации иногда делают обратное.
- **Требуемое правило:** оценивать работу, принадлежащую карточке, для её
  минимального честного production-ready core; явно перечисленные отдельные
  foundations не складывать рекурсивно. Обязательную неотделимую миграцию или
  boundary включать. Вариант, обозначенный как `later`, `optional`, `if desired`
  или альтернатива через `or`, оформлять отдельным кандидатом, если его размер
  хотят сохранить.
- **Уверенность:** высокая.

### CAL-001 — Один trace/explainer capability разъехался между M и L

| ID | Сейчас → рекомендуется | Доказательство / аналог | Уверенность |
| --- | :---: | --- | :---: |
| `AI-026` | `L → M` | По исходной карточке это typed instrumentation, bounded additive trace storage и owner view. Те же outer middleware, redaction, optional short-lived persistence и explainer уже оценены как M в `EO-001`, `RO-001` и `RO-002`; sampled window `RO-004` тоже M. Нет нового scheduler, material migration или external boundary. | Высокая |
| `PT-018` | `L → M` | Карточка повторяет correlation ID, compact trace, short retention и authorized formatter. Это объединённый вариант `RO-001` + `RO-002`, но оба компонента M, а источник не добавляет L-триггер. | Высокая |

Privacy/redaction обязательны, но сами по себе не превращают каждую
диагностическую additive schema в L. Иначе пришлось бы синхронно поднять
`EO-001`, `RO-001`, `RO-002` и `RO-004`, что хуже согласуется с рубрикой.

### CAL-002 — Дубликаты OpenAI resilience получили M и L

| ID | Сейчас → рекомендуется | Доказательство / аналог | Уверенность |
| --- | :---: | --- | :---: |
| `RO-011` | `L → M` | `RO-011` и `EO-027` описывают один gateway: operation-specific deadlines, retry policy, semaphore, process-local breaker, bounded outcome и caller fallback. `EO-027` обоснованно M, потому что укрепляет существующую provider boundary и не создаёт durable workflow. | Высокая |

Telegram delivery (`RO-012`, L) не является контрпримером: там retry после уже
зафиксированной mutation создаёт ambiguous/duplicate delivery semantics, которых
нет в чистом pre-result OpenAI gateway.

### CAL-003 — Одинаковая clarification card оценена как M и L

| ID | Сейчас → рекомендуется | Доказательство / аналог | Уверенность |
| --- | :---: | --- | :---: |
| `PT-017` | `L → M` | Источник требует bounded candidates, одну requester-bound pending clarification и обычный confirm/apply. Это тот же scope, что `AI-025` (M), и не сложнее expiring bulk selection `PT-010` (M). В отличие от `AI-024`, `PT-019` или `RA-026`, здесь нет многошагового transition/recovery framework. | Высокая |

Additive pending row, stale check, TTL и настройка `ask/ignore` входят в M; L
потребовался бы только после расширения до общего многошагового conversational
state machine.

### CAL-004 — Offline/opt-in eval tooling систематически завышен до L

| ID | Сейчас → рекомендуется | Доказательство / аналог | Уверенность |
| --- | :---: | --- | :---: |
| `AI-030` | `L → M` | Versioned recipe/audio corpora, deterministic graders, manifests, reporting и opt-in live runner объединяют работы уровня `EO-003`/`EO-004`, обе M. Synthetic/non-identifying fixtures дают production-ready core без нового runtime state. | Высокая |
| `RA-010` | `L → M` | Baseline/candidate execution над approved fixtures, validators, diff report и release link сопоставимы с live comparison `EO-010` и result registry `EO-011`, обе M. Карточка прямо исключает production bulk rewrite. | Высокая |
| `RA-012` | `L → M` | Corpus и format adapters проверяют существующие hostile-input boundaries, но не создают новую runtime security boundary. Это широкий offline harness, как `EO-005`/`EO-009`, обе M. | Высокая |

Количество форматов и важность security tests увеличивают объём внутри M, но не
дают заявленного рубрикой L-триггера: durable production state machine,
scheduler, новой integration/security boundary или material migration.

### CAL-005 — Опциональные будущие интеграции посчитаны как обязательный core

| ID | Сейчас → рекомендуется | Доказательство / аналог | Уверенность |
| --- | :---: | --- | :---: |
| `PT-023` | `L → M` | Исходник обещает локально распознать code и сохранить его с введённым пользователем label; catalog enrichment помечен `if desired`. Такой core — bounded media/decoder + additive fields, M. | Высокая |
| `CI-027` | `XL → M` | One-time `.ics` назван low-dependency version, а subscribed feed/CalDAV — `later ... could`. Одноразовый versioned export аналогичен другим M-export flows; authenticated HTTP/CalDAV следует вынести в отдельный XL-кандидат. | Высокая |
| `CI-029` | `L → M` | Short description допускает search links **or** draft cart; safest first version явно состоит из per-item links. Official-API cart, OAuth и remote partial success — отдельное более глубокое расширение L. | Средняя |
| `CI-030` | `XL → M` | Исходник прямо говорит, что first version — static snapshot document; live HTTP view только `more useful` и существенно меняет deployment/auth. Snapshot сопоставим с M-export; live surface нужно сохранить как отдельный XL-кандидат. | Средняя |

Это не попытка оценить демо вместо production: каждый рекомендованный core
должен по-прежнему иметь authorization, bounded artifacts, privacy warning,
cleanup и offline tests. Это применение того же правила, по которому `CI-014`
остался M без optional background geofencing.

### CAL-006 — Sub-scope и sensitive profile ошибочно названы platform shift

| ID | Сейчас → рекомендуется | Доказательство / аналог | Уверенность |
| --- | :---: | --- | :---: |
| `PT-028` | `XL → L` | `message_thread_id` может и должен быть list sub-scope внутри неизменного tenant `chat_id`; authorization в источнике остаётся chat-level. Это material cross-module migration уровня named lists `PT-003`/`CI-009` (L), а не замена tenant, как `CI-008` (XL). | Высокая |
| `AI-022` | `XL → L` | Subject-owned sensitive preferences, consent, disclosure policy и safety eval создают серьёзную security/data boundary, но используют существующие Telegram actors и chat tenant. Это L, как per-user delivery/privacy flows `CI-013`/`RA-006`; generalized principal platform `RA-015` остаётся настоящим XL-аналогом. | Средняя |

Для `PT-028` формулировку про «composite tenant key» следует заменить на
`chat_id` tenant + topic/list scope, иначе идея конфликтует с базовым
архитектурным инвариантом проекта. Для `AI-022` высокая чувствительность данных
повышает риск и rigor, но не равна identity-platform shift.

### CAL-007 — Smoke sentinel повторно учитывает staging topology

| ID | Сейчас → рекомендуется | Доказательство / аналог | Уверенность |
| --- | :---: | --- | :---: |
| `RO-024` | `XL → L` | Сам sentinel — новый operational/integration workflow с synthetic principal, cleanup, credentials и live Telegram boundary: это L, сопоставимый с protected promotion `EO-022` и monitoring-path proof `RA-024`. Staging topology уже отдельно принадлежит `RO-023` (XL) и не должна рекурсивно прибавляться к каждой зависимой проверке. | Высокая |

`RO-024` остаётся L, а не M, потому что выполняет live mutation/cleanup через
несколько operational boundaries и требует безопасной атрибуции partial
failure.

## Исходный список предложенных изменений

После применения CAL-000 итоговая карта должна изменить ровно следующие оценки:

- `L → M`: `AI-026`, `AI-030`, `CI-029`, `PT-017`, `PT-018`, `PT-023`,
  `RA-010`, `RA-012`, `RO-011`;
- `XL → M`: `CI-027`, `CI-030`;
- `XL → L`: `AI-022`, `PT-028`, `RO-024`.

Если вместо downgrade выбирается широкий live-вариант `CI-027`, `CI-029` или
`CI-030`, обязательная альтернатива — разделить карточку на core и extension с
разными ID; оставлять один ID с оценкой по необязательному будущему scope нельзя.

Первоначально ожидавшееся распределение до междисциплинарного арбитража:

| Сложность | Было | Должно стать |
| --- | ---: | ---: |
| XS | 1 | 1 |
| S | 6 | 6 |
| M | 60 | 71 |
| L | 98 | 92 |
| XL | 11 | 6 |
| **Всего** | **176** | **176** |

Нужно пересчитать также category summary и поправить boundary notes; coverage и
exact titles при этом не меняются.

## Advisory без изменения оценки

- Преобладание L само по себе не является дефектом: большая часть карточек
  действительно включает scheduler, durable lifecycle, security boundary или
  material migration.
- `PT-024` разумно оставить L с низкой уверенностью до отдельного Telegram
  feasibility decision. Если обещание сузится до уже наблюдавшегося bot/reply
  content, это станет новой M-формулировкой, а не скрытым downgrade текущей.
- После перекалибровки полезно сохранять smaller-slice notes, но не смешивать их
  с value ranking или roadmap.

INITIAL REVIEW: BLOCK — SUPERSEDED

## Final re-review

Повторно проверен текущий `ideas/COMPLEXITY.md` после арбитража. Verdict ниже
относится именно к текущей карте, а не к первоначальным рекомендациям выше.

### Disposition исходных findings

| Finding | Финальный disposition | Проверка текущего состояния |
| --- | --- | --- |
| `CAL-000` | Исправлено | Рубрика явно задаёт intrinsic non-recursive production-ready core, не суммирует отдельные prerequisites, отделяет optional/later extensions и перечисляет собственные L/XL-триггеры. |
| `CAL-001` | Принято | `AI-026` и `PT-018` теперь M вместе с `EO-001`, `RO-001`, `RO-002` и `RO-004`; rationale и merge note используют один trace/explainer scope. |
| `CAL-002` | Принято | `RO-011` теперь M и согласован с `EO-027`; `RO-012` обоснованно остаётся L из-за post-commit delivery ambiguity. |
| `CAL-003` | Принято | `PT-017` теперь M рядом с `AI-025`; общий multi-update session framework явно вынесен в отдельный L-scope. |
| `CAL-004` | Принято | `AI-030`, `RA-010` и `RA-012` теперь M и согласованы с M-семейством `EO-003`–`EO-005`, `EO-009`–`EO-011`. |
| `CAL-005` | Принято | `PT-023`, `CI-027`, `CI-029`, `CI-030` оценены по production-ready core как M; optional catalog/cart/live service extensions явно отделены как L/XL candidates. |
| `CAL-006 / AI-022` | Принято | `AI-022` теперь L: sensitive data/security boundary без tenant/identity platform shift. |
| `CAL-006 / PT-028` | Рекомендация отклонена, обоснование принято | Текущая карточка сознательно оценивает собственный широкий composite `(chat_id, message_thread_id)` scope и полную cross-record migration как XL. Отдельно зафиксирован L topic-as-list slice с неизменным tenant. Это допустимый scope choice по XL-рубрике, а не межсемейное расхождение. |
| `CAL-007 / RO-024` | Рекомендация отклонена, обоснование принято | Standalone core явно включает внешний runner, isolated principal/tenant, credentials, live Telegram/OpenAI, mutation cleanup и monitoring — несколько собственных high-risk boundaries. Incremental вариант после готовых foundations отдельно отмечен как L, поэтому стоимость `RO-023` не скрыто суммируется. |

Три дополнительные арбитражные перекалибровки также соответствуют CAL-000:

- `AI-012: L → M` ограничен parser/DTO/provenance core; secure fetch остаётся
  отдельной L-boundary;
- `RA-004: L → M` обещает best-effort disposable projection с manual recovery,
  а не durable retry-until-converged workflow;
- `RO-029: L → M` содержит линейные startup stages и bounded atomic capsule без
  leases, scheduler, resume/compensation или material migration.

### Механическая и арифметическая проверка

| Проверка | Результат |
| --- | --- |
| Покрытие | 176 строк, 176 уникальных ID; множество ID точно совпадает с `CATALOG.md`. |
| Exact titles | Все 176 названий совпадают с `CATALOG.md`. |
| Глобальные totals | `XS 1 / S 6 / M 74 / L 87 / XL 8`, сумма 176; таблица и фактические строки совпадают. |
| Category totals | Все 11 category rows и каждый band-count совпадают с фактическими строками; после переноса `PT-023` получены `Shopping & list UX = 23` и `Language, accessibility & multimodal interaction = 11`, сумма категорий 176. |
| Сортировка | В каждой категории строго `XS → S → M → L → XL`, затем ID; нарушений нет. |
| Scope notes | Принятые и отклонённые dispositions отражены в rubric, merge/dependency notes, boundary notes и row rationales без value ranking. |
| Whitespace | `git diff --no-index --check` для review-файла не выдаёт diagnostics. |

Остаточный риск не блокирует карту: `PT-028` и `RO-024` имеют широкий
standalone scope и более узкие L-slices, поэтому при будущем value ranking
нельзя сравнивать оба варианта как один candidate. Документ уже требует выдать
extension/slice отдельный ID и acceptance criteria перед таким сравнением.

### Release-pass snapshot

После финального category move и caveat edits проверка выполнена ещё раз:

- `PT-023` перемещён из `Shopping & list UX` в
  `Language, accessibility & multimodal interaction`, оставаясь M; его
  multimodal core и optional L catalog enrichment согласованы с CAL-000;
- уточнения Telegram caveats для `PT-024`/`PT-025` не изменили complexity
  mapping и не внесли скрытый extension в core;
- общий mapping остался `XS 1 / S 6 / M 74 / L 87 / XL 8`, все 176 ID
  уникальны, global/category summaries совпадают с фактическими строками,
  complexity-then-ID sorting не нарушена;
- CAL-000, широкий XL scope `PT-028` с отдельным L-slice и standalone XL scope
  `RO-024` с incremental L-slice сохранены без смыслового дрейфа.

### Final calibration closure

После rationale-only исправления `PT-025` snapshot проверен ещё раз. Mutation
теперь корректно привязана к requester-bound opaque confirmation callback или
эквивалентному explicit handoff; callback повторно проверяет capability,
разрешает target/session и сообщает успех только после service apply.
`ChosenInlineResult`/inline feedback оставлены optional telemetry и больше не
несут correctness.

Это не меняет L: карточке по-прежнему принадлежит новая personalized inline
integration/security boundary, cross-context target authorization и
short-lived mutation token. Рубрика CAL-000, исключения `PT-028`/`RO-024`, все
176 mappings, totals `XS 1 / S 6 / M 74 / L 87 / XL 8`, category summaries и
complexity-then-ID sort после исправления не изменились.

FINAL REVIEW: APPROVE
