# Release-pass architecture/dependency review

## Проверенный scope

Повторно проверены текущий `ideas/COMPLEXITY.md`, все 176 исходных карточек,
пять первичных классификаций и замечания первого review-раунда. Для спорных
решений карта заново сверена с реальным baseline из `docs/architecture.md`,
`docs/persistence.md`, `docs/operations-and-testing.md`, `migrations.py`,
Telegram/OpenAI adapters и recipe-fetch path.

Проверка была именно повторной: прежние findings не считались автоматически
верными. Ни код, ни исходные карточки, ни итоговая классификация этим reviewer
не изменялись.

## Итог

Architecture/rubric consistency и арифметика после последнего category move
сохранились. `CAL-000` устранил главный источник расхождений: теперь размер
intrinsic и non-recursive, относится к минимальному честному production-ready
core, а явно optional/later extensions и полная цена отдельных prerequisites
не прибавляются consumer повторно. При этом safe core по-прежнему включает свои
migrations, authorization/isolation, privacy, recovery, tests и operator burden.

Release-pass ранее обнаружил один blocking Telegram feasibility defect в
`PT-025`: chosen-result feedback был ошибочно указан как functional mutation
trigger. Closure-pass подтверждает, что defect исправлен явным requester-bound
confirmation path; размеры и totals при этом не изменились.

Актуальная механическая выборка подробных строк подтверждает:

```text
rows=176 unique=176 duplicate_ids=0 XS=1 S=6 M=74 L=87 XL=8
```

`git diff --no-index --check /dev/null ideas/COMPLEXITY.md` не выдал
whitespace diagnostics; exit code `1` означает только сравнение существующего
untracked-файла с `/dev/null`.

## Disposition прежних architecture findings

| Finding / ID | Текущее решение | Финальная оценка |
| --- | --- | --- |
| `CAL-000` | Зафиксирован intrinsic non-recursive core; breadth отдельно от L/XL trigger. | Принято; правило теперь достаточно точное и применяется последовательно. |
| `RO-011` | `Reliability & observability / M`. | Принято корректно: это тот же existing-boundary gateway, что `EO-027`; process-local circuit не образует durable workflow. |
| `AI-026`, `PT-018` | Оба `Reliability & observability / M`. | Принято корректно: additive bounded trace/explainer cluster согласован с `EO-001`, `RO-001`, `RO-002`; backup-aware diagnostic lifecycle `RO-006` не считается повторно. |
| `AI-012` | `Integrations & portability / M`. | Принято корректно: bounded JSON-LD parser/DTO/provenance расширяет существующий fetch path; L secure-fetch boundary остаётся отдельным prerequisite. |
| `CI-027` | `Integrations & portability / M`. | Принято корректно для on-demand `.ics`; feed/CalDAV явно вынесены в отдельный будущий XL candidate. |
| `CI-030` | `Integrations & portability / M`. | Принято корректно для authorized static snapshot; live HTTP view явно вынесен в отдельный XL candidate. |
| `PT-023` | `Language, accessibility & multimodal interaction / M`. | Complexity и placement теперь согласованы: local barcode/photo decode либо typed code + confirmed label являются multimodal input core; optional catalog adapter остаётся отдельной L integration. |
| `AI-022` | `Recipes & meal planning / L`. | Принято корректно: sensitive profiles/consent/safety образуют L boundary, но не меняют `chat_id` tenant или universal principal platform; product category отражает recipe/meal outcome. |
| `RO-029` | `Deployment & operations / M`. | Принято корректно: bounded atomic crash capsule и линейные startup stages не имеют leases, resume/compensation или scheduler. |

Все шесть блокирующих групп предыдущего architecture review тем самым
закрыты. Новые rationales не просто меняют букву размера, а называют точную
границу core/extension и dependency ownership.

## Проверка остальных принятых перекалибровок

| ID | Текущее решение | Почему оно согласовано с CAL-000 |
| --- | --- | --- |
| `AI-030` | `Testing & evals / M` | Широкие corpora/graders и opt-in live runner не создают production workflow; synthetic fixtures дают complete core. |
| `RA-010` | `Testing & evals / M` | Approved-fixture baseline/candidate sandbox прямо исключает production rewrite и остаётся offline release evidence. |
| `RA-012` | `Testing & evals / M` | Corpus проверяет существующие hostile-input boundaries; format breadth сама по себе не является новой runtime security boundary. |
| `PT-017` | `Language, accessibility & multimodal interaction / M` | Один requester-bound pending clarification/confirm соответствует `AI-025`; multi-update conversational recovery остаётся отдельным L scope. |
| `CI-029` | `Integrations & portability / M` | Provider-neutral search links не вызывают retailer API; cart/OAuth/remote partial success вынесены в отдельный L extension. |
| `RA-004` | `Shopping & list UX / M` | Best-effort disposable projection допускает reason-coded terminal failure и manual refresh/replacement; retry-until-converged outbox не обещан. |

Эти решения не занижают production rigor. Например, M snapshot/export всё ещё
требует authorization, bounded temporary artifact, cleanup и recipient warning;
M clarification — requester binding, stale/TTL checks и deterministic apply;
M pinned view — permission/rate-limit/pagination failure paths и authoritative
SQLite fallback.

## Проверка отклонённых downgrade

### `PT-028`: оставить `Shopping & list UX / XL`

Решение root не принимать предложенный `XL → L` архитектурно допустимо и
согласовано с рубрикой. Карточка не просто добавляет ещё одну nullable list
relation: она прямо делает `(chat_id, optional message_thread_id)` центральным
scope key для items, callbacks, tracked messages, sessions, queries,
export/delete и isolation tests. Это собственный platform/scope-invariant shift,
а не рекурсивно посчитанная цена другой идеи. Product category при этом верна:
пользовательский outcome — topic-scoped list spaces.

Более безопасный `topic → ordinary list_id` mapping с неизменным scope contract
действительно был бы L, но это уже отдельно сформулированный narrower candidate.
При будущей реализации `chat_id` всё равно должен оставаться authorization и
tenant-isolation root; Telegram thread ID не получает самостоятельной authority.

### `RO-024`: оставить `Deployment & operations / XL`

Решение root не принимать предложенный `XL → L` также допустимо. Standalone core
сам вводит внешний runner, isolated synthetic principal/tenant, отдельные
credentials, live Telegram mutation/callback/cleanup и release-scoped result
delivery. Это собственные topology, identity и destructive-data boundaries;
для XL не требуется повторно включать `RO-023`. Optional OpenAI probe не является
основанием размера и может быть полностью исключён из core.

После готовых staging/principal foundations incremental scenario pack был бы L,
что уже честно указано в boundary note. Карта последовательно различает
standalone intrinsic candidate и последующую incremental delivery estimate.

## Проверка category moves

Все blocking taxonomy moves отражены и не скрывают архитектурные зависимости:

- `PT-011` перенесён в `Shopping & list UX`: domain history/undo — list
  capability, а `RO-014` остаётся отдельным recovery foundation;
- `PT-024` находится в `Integrations & portability`: реакция — новый Telegram
  ingress, и L rationale явно учитывает отсутствие source text/fetch API;
- `PT-023` находится в `Language, accessibility & multimodal interaction`:
  primary value — barcode/photo либо typed-code capture, а product catalog не
  включён в intrinsic core;
- `AI-022` находится в `Recipes & meal planning`: consent/privacy определяют
  risk, но primary outcome — проверка recipes/substitutions/plans;
- `CI-008` находится в `Collaboration & access`: household linking — product
  outcome, тогда как replacement business tenant объясняет XL;
- `PT-028` находится в `Shopping & list UX`: topics — list-space alternative,
  а центральный scope shift остаётся причиной XL;
- категория `Language, accessibility & multimodal interaction` теперь точно
  покрывает natural-language ambiguity, localization/accessibility, voice и
  image input, не утверждая, что каждая идея model-backed.

Оставшиеся `Privacy & security` и `Data & application architecture` снова
содержат capabilities, для которых boundary/foundation является самим core, а
не просто дорогой внутренней зависимостью пользовательской функции.

## Release-pass closure

### AR-RP-001 — RESOLVED: chosen-result feedback исключён из apply path

- **Former severity:** high correctness.
- **Blocking:** no; исправлено в текущем snapshot.
- **Evidence:** boundary note `ideas/COMPLEXITY.md:144` и строка
  `ideas/COMPLEXITY.md:280` теперь требуют personal inline result с
  requester-bound opaque `Confirm add` callback/explicit handoff. Только явный
  callback повторно авторизует requester, разрешает target/session, делает
  current-capability recheck, выполняет обычную service mutation и лишь затем
  сообщает success.
- `ChosenInlineResult` и inline feedback теперь названы optional telemetry и
  явно запрещены как correctness/apply path. Это соответствует ограничению
  Telegram: даже при probability `100%` часть feedback может не прийти из-за
  cache, поэтому он пригоден лишь для статистики:
  [Inline feedback](https://core.telegram.org/api/bots/inline#4-inline-feedback).
- Complexity остаётся L, category остаётся `Integrations & portability`; новых
  topology, tenancy, persistence или migration предпосылок правка не вводит.

`PT-024` после release-pass корректен: официальный Bot API действительно требует
bot administrator и explicit `allowed_updates=["message_reaction"]`; actor-bearing
reaction не содержит source text, а anonymous count не даёт requester identity.
Заявленные observed-content/consent/deletion prerequisites не противоречат
`chat_id` tenant или authorization scope.

## Non-blocking advisory

### AR-FINAL-ADV-002 — Snapshot/file revocation должна называться честно

У `CI-027` и `CI-030` M-core local artifact можно удалить после Telegram send,
но уже доставленный `.ics` или snapshot нельзя отозвать из Telegram cloud или у
получателя. `expiry`, `cleanup` и `revocation` применимы к локальному artifact
либо будущему live grant, не к копии файла. При превращении карточки в acceptance
criteria это различие следует оставить явным; текущий размер M от этого не
меняется.

### AR-FINAL-ADV-003 — Зафиксировать scope двух сохранённых XL до ranking

- Для `PT-028` XL означает именно заявленный central composite scope; если будет
  выбран topic-as-list mapping, его надо ранжировать отдельным L candidate.
- Для `RO-024` XL означает standalone isolated sentinel. После `RO-023` нельзя
  повторно использовать XL как incremental estimate одного scenario pack.
- В rationale `RO-024` полезно при следующем prose-pass убрать optional OpenAI
  из перечисления оснований XL, хотя текущий текст уже называет его optional и
  решение не зависит от него.

Эти advisory не меняют полноту, сортировку или итоговое распределение.

## Финальный verdict

Category move `PT-023`, caveats `PT-024`, CAL-000, architecture boundaries и
распределение `XS=1 / S=6 / M=74 / L=87 / XL=8` согласованы. `PT-025` теперь
использует explicit requester-bound confirmation как единственный apply path,
повторно проверяет requester/current capability и target/session, выполняет
mutation до success и оставляет lossy inline feedback только telemetry.
Актуальных blocking architecture/dependency defects не осталось.

FINAL REVIEW: APPROVE
