# Final release taxonomy closure

## Проверенный scope

Повторно проверены read-only:

- актуальный `ideas/COMPLEXITY.md`;
- `ideas/CATALOG.md` и все шесть исходных каталогов карточек;
- пять первичных классификаций в `ideas/council/`.

Closure-pass выполнен после rationale-only исправления `PT-025`; placement,
complexity и сводные числа этой идеи не менялись.

Review проверяет taxonomy и пригодность карты для последующего
value-vs-complexity comparison. Он не ранжирует value, не выбирает roadmap и не
переоценивает идеи только из-за полной стоимости их prerequisites.

## Результат обязательных исправлений

| ID проверки | Идея / scope | Требуемое изменение | Результат | Основание |
| --- | --- | --- | :---: | --- |
| TAX-001 | `PT-024` | `Shopping & list UX` → `Integrations & portability` | PASS | Reaction-based capture теперь находится рядом с остальными Telegram ingress-вариантами `CI-025`/`PT-025`; его privacy и feasibility constraints сохранены. |
| TAX-002 | `PT-011` | `Reliability & observability` → `Shopping & list UX` | PASS | User-facing list history/undo классифицированы по продуктовой ценности, а durable journal `RO-014` остаётся отдельной foundation. |
| TAX-003 | `AI-022` | `Privacy & security` → `Recipes & meal planning` | PASS | Dietary/allergen capability сравнивается с recipe, substitution и meal-plan alternatives; consent/privacy остаются в rationale и L complexity. |
| TAX-004 | `CI-008` | `Data & application architecture` → `Collaboration & access` | PASS | Linked household spaces находятся рядом с roles, cross-scope transfer и offboarding; tenant shift по-прежнему отражён как XL. |
| TAX-005 | `PT-028` | `Data & application architecture` → `Shopping & list UX` | PASS | Topic-scoped lists сравнимы с `CI-009`/`PT-003` named lists; composite scope сохраняет XL complexity. |
| TAX-006 | Вся modality-секция | `AI, language & voice` → `Language, accessibility & multimodal interaction` | PASS | Название теперь покрывает natural-language ambiguity, accessibility, voice и image input, не утверждая, что любая идея обязана быть AI-backed. |
| TAX-007 | `PT-023` | `Shopping & list UX` → `Language, accessibility & multimodal interaction` | PASS | Отличительная ценность intrinsic core — camera/barcode или typed-code input modality с bounded local decode; обычный shopping-item write является destination, а optional external product-catalog adapter явно вынесен в отдельный L-extension. |
| TAX-008 | `PT-025` | Сохранить `Integrations & portability`; исправить только rationale | PASS | Inline query остаётся cross-context Telegram ingress. Apply корректно привязан к requester-bound opaque confirmation callback с current-capability/target recheck; `ChosenInlineResult` и inline feedback не являются correctness-механизмом и остаются optional telemetry. |

Все семь placement/rename решений и rationale-only closure применены
последовательно. Финальное размещение
`PT-023` в multimodal-секции заменяет промежуточное размещение в Shopping:
primary category определяется по отличительной пользовательской или
enabling-capability ценности, а обычный destination, implementation boundary и
prerequisites влияют на rationale/complexity, но не подменяют место сравнения.

## Инварианты покрытия и сводок

Независимая механическая сверка итоговых таблиц дала:

- 176 строк идей;
- 176 уникальных ID;
- точное совпадение множества ID с 176 строками `CATALOG.md`;
- ровно одна primary category у каждого ID;
- 11 непересекающихся primary categories;
- ноль нарушений порядка `XS → S → M → L → XL`, затем ID;
- фактическое общее распределение `XS 1 / S 6 / M 74 / L 87 / XL 8`,
  совпадающее с общей сводкой.

Фактические category totals также полностью совпадают с таблицей сводки:

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

## Пригодность для value-vs-complexity comparison

Taxonomy теперь выдерживает четыре необходимых теста:

1. **Ближайшие alternatives сравнимы.** Named/topic lists, recipe safety,
   household linking, list undo, barcode input и Telegram capture находятся в
   категориях своего primary outcome.
2. **Product и foundation не смешиваются по случайной зависимости.** Например,
   `PT-011` остаётся продуктовым consumer, `RO-014` — reusable foundation;
   `CI-008` остаётся collaboration capability, хотя требует tenant migration.
3. **Cross-cutting risk не теряется.** Privacy, authorization, migrations и
   external boundaries сохранены в rationale, complexity и dependency notes,
   даже когда primary category продуктовая.
4. **Общая основа не удваивает портфельную оценку.** Merge/dependency families
   явно перечислены отдельно, а CAL-000 определяет complexity как intrinsic,
   non-recursive размер production-ready core.

`PT-025` удовлетворяет этим границам: primary value — Telegram-native ingress
из другого chat context, поэтому `Integrations & portability` остаётся
правильной категорией. Confirmation callback — безопасный apply contract внутри
этого ingress, а не основание переносить идею в Shopping или Collaboration.

Категории достаточно широки для portfolio-сравнения и при этом имеют
операционные границы. `Data & application architecture` корректно содержит
reusable foundations (`RA-015`, interaction/session, journal, lifecycle и
quantity primitives), а product topology consumers перенесены в свои value
surfaces. `Reliability & observability` теперь не содержит обычную domain
history, а `Privacy & security` зарезервирована для идей, где control/boundary
является самим core.

## Неблокирующие advisory

- `Product onboarding & engagement` можно позднее переименовать в
  `Onboarding, reminders & engagement`: это сделает reminder-heavy состав
  заметнее, но текущая граница уже описана в summary и не мешает сравнению.
- `Data & application architecture` можно стилистически сократить до
  `Domain & application foundations`; текущие состав и definition уже
  однозначны, поэтому rename не требуется.
- При ranking нужно использовать перечисленные merge/dependency families:
  category membership само по себе не означает ни duplicate, ни обязательное
  объединение. Особенно важно не удваивать shared platform cost у
  `AI-005`/`PT-016`, capture-family, multimodal ingestion и
  product-consumer/foundation пар.
- Низкая уверенность `PT-024` относится к Telegram feasibility и scope, а не к
  его месту в taxonomy; до ranking карточку полезно либо сузить, либо отдельно
  подтвердить доступный update contract.

## Итог

Все ранее blocking taxonomy defects исправлены; rationale `PT-025` согласован с
его Integrations placement и больше не полагается на optional inline feedback
для correctness. Актуальных defects в coverage, one-primary-category invariant,
category totals, порядке или границах taxonomy не найдено.

FINAL REVIEW: APPROVE
