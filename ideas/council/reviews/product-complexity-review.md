# Final product / Telegram complexity review

## Scope inspected

Release-pass выполнен по snapshot `ideas/COMPLEXITY.md` с SHA-256
`ba6a09f9de298bdc805e11a6340ea4a4254f5295d50b4878ad1b1f830fbb3d37`.
Проверены все прежние product/Telegram findings, затронутые category summaries,
порядок строк и сохранность согласованных core-versus-extension границ. Value
ranking не выполнялся.

## Закрытие последних blockers

| Finding | Результат | Evidence |
| --- | --- | --- |
| `PTCR-F01` | Исправлен | `PT-023` сохранён как `M` и перенесён в `Language, accessibility & multimodal interaction`; rationale описывает local barcode/photo decode или typed code как core, внешний catalog — как отдельную `L` integration (`ideas/COMPLEXITY.md:221`). Summary пересчитан: Shopping `M 15 / total 23`, Multimodal `M 4 / total 11`. Внутри M-band строка стоит по ID между `PT-017` и `PT-026`. |
| `PTCR-F02` | Исправлен | `PT-024` остаётся `Integrations & portability / L`. И строка, и boundary note фиксируют bot-administrator prerequisite, явный `allowed_updates=["message_reaction"]`, отсутствие source text и fetch-by-message-ID, необходимость bounded observed content/consent/deletion и запрет использовать anonymous count как requester-authorized mutation (`ideas/COMPLEXITY.md:141,279`). |
| `PTCR-F03` | Исправлен с архитектурной коррекцией review | `PT-025` остаётся `Integrations & portability / L`. `answerInlineQuery(..., is_personal=true)` возвращает requester-bound opaque `Confirm add` callback/explicit handoff; только этот callback повторно авторизует requester/target, выполняет service mutation и затем сообщает success (`ideas/COMPLEXITY.md:144,280`). `ChosenInlineResult` и inline feedback не являются functional trigger: Telegram предупреждает, что feedback может не прийти из-за caching, поэтому он допустим только как optional telemetry. |

## Сохранность прежних решений

- `PT-017` остаётся `M`: одна pending clarification переиспользует текущий
  confirmation pattern; multi-update conversational state отделён.
- `RA-004` остаётся `M`: best-effort disposable pinned projection не обещает
  retry-until-converged workflow.
- `CI-027` остаётся `M`: on-demand `.ics` — core, subscribed feed/CalDAV —
  отдельное later `XL` extension.
- `PT-011`, `AI-022`, `CI-008` и `PT-028` сохранили согласованные product-first
  primary categories, не теряя `L/XL` architectural costs в rationale.
- `PT-019`, `PT-026`, `PT-028`, `CI-030` и Mini App caveats остаются
  непротиворечивыми: narrower slices не используются для занижения полного
  заявленного core, optional future boundaries не прибавляются рекурсивно.

Предыдущее требование этого review включить chosen-result feedback как apply
trigger было ошибочным и теперь явно отозвано. Официальный
[Inline Bots contract](https://core.telegram.org/bots/inline) предупреждает, что
не все выбранные results репортятся из-за caching. Надёжный functional trigger —
[`CallbackQuery`](https://core.telegram.org/bots/api#callbackquery) от кнопки в
inline message, проверенный по requester-bound session; `is_personal=true`
по-прежнему обязателен для user-specific results.

## Validation

- Idea rows: `176`.
- Duplicate IDs: `0`.
- Distribution: `XS 1 / S 6 / M 74 / L 87 / XL 8`; совпадает со сводкой.
- Complexity-first, then ID ordering внутри всех 11 категорий: `PASS`.
- Единственный `## Идеи по основной категории`: `PASS`.
- `git diff --no-index --check /dev/null ideas/COMPLEXITY.md`: `PASS` для
  whitespace (ожидаемый exit `1` только потому, что файл новый).

Новых обязательных или advisory findings нет.

`FINAL REVIEW: APPROVE`
