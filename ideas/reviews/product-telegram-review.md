# Перекрёстное ревью продуктовых и Telegram-идей

## Scope и метод

Проверены все пять исходных каталогов:

- `ideas/product-telegram.md` — `PT-001`–`PT-030`;
- `ideas/ai-recipes.md` — `AI-001`–`AI-030`;
- `ideas/collaboration-integrations.md` — `CI-001`–`CI-030`;
- `ideas/reliability-observability.md` — `RO-001`–`RO-030`;
- `ideas/engineering-operations.md` — `EO-001`–`EO-030`.

В сумме это 150 уникальных заголовочных ID. Каталог сопоставлен с текущими
`SCENARIOS.md`, `docs/README.md`, `docs/architecture.md`,
`docs/request-lifecycle.md`, `docs/ingestion-and-recipes.md`, `PLAN.md` и
ключевыми Telegram-обработчиками. Проверялись:

1. совпадающие и частично пересекающиеся идеи;
2. соответствие описанию уже реализованного поведения;
3. реалистичность Telegram UX и Bot API;
4. ясность пользовательской ценности и допущений;
5. отсутствующие пользовательские пути.

Это ревью не ранжирует и не оценивает идеи баллами. Его задача — подготовить
каталог к отдельному этапу консолидации и приоритизации.

## Общий вывод

Каталог широк, в основном корректно отделяет существующие возможности от
будущих и хорошо учитывает `chat_id`, подтверждения, приватность, TTL,
идемпотентность и ограничения однопроцессной SQLite-архитектуры. Материально
опасной идеи, представленной как готовая или заведомо безопасная возможность,
не найдено.

Перед ранжированием следует нормализовать дубли: иначе одна продуктовая тема,
например tracing или meal planning, получит несколько голосов только потому,
что описана разными агентами. Платформенные гипотезы про реакции, inline mode и
синтетический Telegram smoke нужно явно пометить как требующие feasibility
spike.

## Findings

### PTR-001 — Трейсинг и explainer представлены как несколько вариантов одной системы

- **Идеи:** `PT-018`, `AI-026`, `RO-001`, `RO-002`, `RO-003`, `RO-004`,
  `RO-006`, `RO-008`, `RO-028`, `EO-001`, `EO-002`, `EO-025`.
- **Рекомендация:** merge/clarify.

`PT-018`, `AI-026` и `EO-001` почти одинаково описывают структурированный путь
решения и пользовательское объяснение. `RO-001` точнее отделяет сбор фактов от
`RO-002`, который показывает их владельцу. Их следует представить как один
продуктовый epic с независимыми слоями:

1. стабильные reason codes (`RO-003`);
2. request-local trace (`RO-001`/`EO-001`/`AI-026`);
3. безопасная persistence и sampling (`RO-004`, `RO-006`);
4. owner-facing explanation (`PT-018`/`RO-002`);
5. агрегаты и failure inbox (`RO-008`, `RO-028`).

Эти слои не стоит сливать в одну implementation story: базовый trace может быть
полезен без Telegram-команды, а explainer не должен существовать без
санитизированного источника данных. `ContextVar` корректно описан лишь как
переносчик correlation state внутри одного update, а не как durable storage.

### PTR-002 — Контур «production failure → eval» продублирован

- **Идеи:** `AI-027`, `RO-028`, `EO-002`, частично `EO-003`, `AI-030`.
- **Рекомендация:** merge для capture/curation, keep separate для eval suites.

`AI-027` и `EO-002` — один пользовательско-операционный путь: явное исправление,
согласие, редактирование и ручное включение обезличенного кейса. `RO-028`
предлагает правильный предшествующий inbox без raw content. Их лучше объединить
в один lifecycle, исключив трактовку failure metadata как готового gold label.

`EO-003` и `AI-030` остаются отдельной задачей: они определяют новые capability
suites, а не сбор production-кандидатов. Автоматическое добавление домашних
сообщений в корпус нигде не должно появиться как shortcut.

### PTR-003 — Named lists, recurrence и due dates имеют точные межфайловые дубли

- **Идеи:** `PT-003` + `CI-009`; `PT-004` + `CI-010`; `PT-002` + `CI-011`.
- **Рекомендация:** merge каждой пары.

В каждой паре совпадают пользовательская задача, state model и основные риски.
Версии из `CI` чуть точнее формулируют idempotency recurrence и запрет AI
изобретать list IDs; версии из `PT` лучше описывают Telegram-переключатели и
конфликт per-user/per-chat active list. Консолидированная формулировка должна
сохранить обе части.

### PTR-004 — Pantry-кластер требует трёх чётких уровней, а не четырёх похожих идей

- **Идеи:** `PT-005`, `CI-017`, `AI-006`, `AI-023`.
- **Рекомендация:** merge `PT-005` с `CI-017`; clarify and keep separate
  `AI-006` и `AI-023`.

`PT-005` и `CI-017` одинаково предлагают coarse state `in stock/low/out`.
`AI-006` уже и дешевле: это явно заданный список staples, которые по умолчанию
исключаются из recipe-add preview. `AI-023` заметно шире: persistent quantities,
expiry и планирование блюд. Последние две идеи следует сохранить отдельно, но
не называть все три одним словом `pantry` без уровня сложности — иначе при
ранжировании будут смешаны разные ценность и стоимость ведения данных.

### PTR-005 — Meal planning и multi-recipe cart частично смешаны

- **Идеи:** `PT-015`, `AI-007`, `AI-009`, `AI-010`, `CI-016`.
- **Рекомендация:** merge `PT-015`, `AI-009` и `CI-016`; keep separate
  `AI-007` и `AI-010`.

Три идеи описывают один недельный plan, связанный с сохранёнными рецептами.
`AI-007` — более узкий reusable primitive: объединить несколько выбранных
рецептов без календаря. `AI-010` — отдельная привычка ротации уже известных
меню. Они могут стать зависимостями или продолжениями meal plan, но решают
другие пользовательские задачи и не должны исчезать при merge.

### PTR-006 — Recipe scaling и ingredient selection лучше оставить разными capability

- **Идеи:** `PT-016`, `AI-004`, `AI-005`, а также `AI-008`.
- **Рекомендация:** clarify/keep separate.

`PT-016` объединяет масштабирование и выбор строк в одном UX, тогда как
`AI-004` и `AI-005` правильно разделяют их. Ingredient selection — полностью
детерминированный confirmation flow, который полезен и без структурированных
количеств. Scaling зависит от servings, quantity parsing и правил округления;
`AI-008` добавляет ещё одну самостоятельную capability — совместимые единицы.
`PT-016` разумно оставить umbrella journey, но не считать четвёртой независимой
фичей при ранжировании.

### PTR-007 — Assignment дублируется, а trip coordination и session sync — нет

- **Идеи:** `PT-007`, `CI-003`, `CI-002`, `PT-009`, `CI-004`.
- **Рекомендация:** merge `PT-007` с `CI-003`; keep separate остальные.

Два assignment-пункта совпадают; вариант `CI-003` точнее фиксирует atomic
compare-and-set, а `PT-007` — проблемы изменяемых Telegram display names.
`CI-002` остаётся отдельной атрибуцией requester/completer и не означает
ответственность за покупку. `PT-009` решает техническую свежесть нескольких
shop messages, а `CI-004` — социальный lifecycle поездки и handoff. Эти две
идеи могут использовать общую session model, но не являются дублями.

### PTR-008 — Clarification cards и bounded follow-up context нельзя считать одной фичей

- **Идеи:** `PT-017`, `AI-025`, `AI-024`.
- **Рекомендация:** merge `PT-017` с `AI-025`; keep separate `AI-024`.

`PT-017` и `AI-025` описывают одно безопасное разрешение неоднозначного
shopping/recipe intent через candidate buttons. `AI-024` позволяет продолжить
уже открытый preview фразой «без лука» или «на четверых» и требует typed
conversation state. Первая возможность уменьшает ошибочные действия в одном
update, вторая добавляет multi-update dialogue; их риски и тестовые матрицы
различаются.

### PTR-009 — Voice correction дублируется, multi-note capture остаётся самостоятельным

- **Идеи:** `PT-019`, `AI-028`, `PT-020`, `AI-030`, `EO-004`.
- **Рекомендация:** merge `PT-019` с `AI-028`; merge eval-части `AI-030` и
  `EO-004`; keep separate `PT-020`.

Первые две идеи совпадают в transcript preview, исправлении и риск-зависимом
подтверждении; `AI-028` дополнительно предлагает bounded speech hints. Multi-note
capture — отдельная batch-сессия с иным cost/privacy profile. `EO-004` является
подробным вариантом audio-части широкого `AI-030`; один eval epic может иметь
отдельные recipe и audio workstreams.

### PTR-010 — Bundles, reorder suggestions и onboarding имеют прямые дубли

- **Идеи:** `PT-014` + `CI-018`; `PT-013` + `CI-019`; `PT-030` + `CI-020` +
  `CI-021`.
- **Рекомендация:** merge первых двух пар; clarify onboarding cluster.

Reusable bundle/routine и frequency-based suggestion представлены дважды почти
без смысловой разницы. В onboarding-кластере `CI-020` — resumable initial setup,
а `CI-021` — поздние contextual tips; их полезно оставить отдельными. `PT-030`
может быть общим journey, но не дополнительной самостоятельной идеей. `CI-022`
про реактивацию после долгого отсутствия остаётся отдельным lifecycle.

### PTR-011 — Notification digest дублируется, personal delivery остаётся отдельным

- **Идеи:** `PT-027`, `CI-012`, `CI-013`.
- **Рекомендация:** merge `PT-027` с `CI-012`; keep separate `CI-013`.

Первые два пункта — один opt-in scheduled digest с timezone, quiet hours и
deduplication. `CI-013` решает другой вопрос: разные участники хотят разные
сигналы. В Telegram общую group message нельзя скрыть от части участников, а
личный delivery возможен только пользователю, который ранее начал private chat
с ботом. `CI-013` это корректно отмечает; это ограничение нужно перенести в
любой объединённый notification journey.

### PTR-012 — Engineering и reliability каталоги содержат семь почти точных пар

- **Идеи и рекомендации:**

| Merge-кандидаты | Общая тема |
| --- | --- |
| `RO-005`, `EO-025` | structured journald/JSON logging и correlation |
| `RO-011`, `EO-027` | OpenAI timeout/retry/concurrency/circuit policy |
| `RO-017`, `EO-030` | SQLite/event-loop isolation без преждевременного multi-instance |
| `RO-020`, `EO-019` | consistent periodic backup с manifest |
| `RO-021`, `EO-020` | restore command/drill |
| `RO-025`, `EO-026` | небольшой пользовательский SLO и actionable alerts |
| `RO-030`, `EO-029` | retention-driven bounded maintenance |

Эти пары следует объединить до ранжирования. При этом соседние идеи не надо
схлопывать только из-за общей области: `RO-007` задаёт policy, а `RO-030`/`EO-029`
её исполняет; `RO-022` описывает release identity, а `EO-017` — immutable release
mechanism; `RO-020` создаёт backup, а `RO-021` доказывает восстановимость.

### PTR-013 — Doctor, preflight и health-check используют один engine, но имеют разные gates

- **Идеи:** `RO-016`, `RO-019`, `EO-016`, `EO-023`, частично `RO-029`.
- **Рекомендация:** clarify shared core; keep separate entry points.

Database invariant doctor, deployment preflight, process readiness и startup
failure capsule отвечают на разные вопросы. Им нужен общий registry проверок и
reason codes, но нельзя выдавать один общий `healthy`: успешный SQLite doctor не
доказывает polling readiness, а `getMe` не доказывает приём и обработку updates.
Каталог должен показывать эту иерархию, чтобы одна зелёная команда не создавала
ложное release evidence.

### PTR-014 — Recipe fetch security описана трижды и с разной threat framing

- **Идеи:** `AI-016`, `RO-026`, `EO-012`; тестовая зависимость `EO-006`.
- **Рекомендация:** merge и унифицировать requirement.

Три идеи предлагают scheme/address/redirect/deadline policy. `AI-016` и
`RO-026` называют отсутствие private-address filtering SSRF-риском, тогда как
`EO-012` точнее отмечает, что нынешний trusted-user scope фактически допускает
такие адреса. При этом пользовательский контракт говорит о public recipe URL.
До реализации нужно явно решить: private URLs запрещены как invariant или
поддерживаются как trusted self-hosted feature. `EO-006` следует сохранить
отдельно как герметичный verification harness: тестирование boundary само по
себе policy не меняет.

### PTR-015 — Reaction capture не получает текст исходного сообщения из reaction update

- **Идеи:** `PT-024`.
- **Рекомендация:** clarify и провести Telegram feasibility spike.

Telegram reaction update сообщает chat/message identity, actor и изменение
reaction, но не является API для получения произвольного текста по message ID.
Чтобы распарсить исходный текст, бот должен был получить и кратко сохранить
сообщение ранее; в group privacy mode это не гарантировано. Для reaction updates
также нужны корректный `allowed_updates` и, в чатах, соответствующие права бота.

Безопасный fallback — реакция только на сообщение самого бота или ответ/forward,
в котором Telegram реально передал content. Идея уже признаёт необходимость
проверить delivery/content availability, поэтому это clarification, а не
блокирующая ошибка каталога.

### PTR-016 — Inline capture не знает чат, куда пользователь вставил результат

- **Идеи:** `PT-025`.
- **Рекомендация:** clarify и провести Telegram feasibility spike.

`chosen_inline_result` не предоставляет destination chat ID. Предложенная идея
может работать только потому, что пользователь заранее выбирает Honeybuy-
destination, а opaque token связывает именно его; нельзя выводить target из
чата, где inline result был опубликован. Mutation не должна происходить на
`inline_query`, потому что пользователь может не выбрать результат. Получение
chosen-result feedback также зависит от настройки inline feedback.

Нужно отдельно проверить, не раскрывает ли вставленный inline result название
домашней группы или список людям в стороннем чате. Более простой competitor
journey — explicit forward/reply из `CI-025`.

### PTR-017 — Button-heavy flows нуждаются в общем Telegram interaction contract

- **Идеи:** `PT-009`, `PT-010`, `PT-017`, `PT-019`, `AI-002`, `AI-005`,
  `AI-009`, `AI-017`, `CI-006`, `CI-012`, `CI-024`, `CI-026`, `CI-029`.
- **Рекомендация:** clarify cross-cutting constraint.

Каталог в основном правильно предлагает opaque IDs и persisted sessions, но
общие ограничения стоит сформулировать один раз:

- callback query нужно быстро acknowledge, а долгий AI/network job продолжать
  отдельно;
- `callback_data` не предназначен для ingredient arrays, raw text, URLs или
  полномочий — только для короткого opaque reference/action;
- Telegram message/card имеет ограничение размера, поэтому длинные recipes,
  history, import preview и bulk selection требуют pagination;
- каждое действие повторно проверяет chat, requester/role, current revision,
  expiry и terminal status;
- database mutation остаётся authoritative, даже если edit/send после неё не
  удался.

Это не новая продуктовая идея, а общий acceptance contract для десятков идей.

### PTR-018 — Synthetic Telegram smoke требует не только Bot API credential

- **Идеи:** `RO-023`, `RO-024`, `EO-022`, `EO-023`.
- **Рекомендация:** clarify test actor and boundary.

Bot token позволяет боту делать исходящие Bot API calls, но не позволяет
изобразить обычного Telegram-пользователя и доставить себе настоящий inbound
update. Поэтому end-to-end sentinel, который проверяет receive/routing/callback,
нуждается либо в отдельном тестовом user client с существенно более сложным
credential/rate-limit boundary, либо в ручном smoke. `getMe` и отправка
сообщения ботом проверяют только часть транспорта.

`RO-023` (isolated staging), `RO-024` (end-to-end smoke) и `EO-023` (internal
health probes) следует сохранить отдельно и не подменять один зелёным сигналом
другого.

### PTR-019 — Telegram membership и roles нельзя строить на полном roster lookup

- **Идеи:** `CI-001`, `CI-006`, `CI-007`, `PT-007`, `CI-003`.
- **Рекомендация:** clarify identity acquisition.

Числовой Telegram user ID — правильный ключ, но Bot API не даёт универсального
надёжного списка всех участников группы. Role/guest/assignment UX должен
привязывать человека через уже полученное взаимодействие, reply, explicit
forward-safe handshake или заранее известный ID, а не через произвольный
member picker. Anonymous administrator/channel-sender updates также могут не
иметь обычного `from_user`; для них нужно определить reject/fallback поведение.

`CI-007` уже требует взаимодействия пользователя и поэтому ближе всего к
реалистичному flow. Ни одна роль не должна автоматически следовать из Telegram
admin status.

### PTR-020 — Forward, external reply, photo и document — разные Telegram payload paths

- **Идеи:** `PT-021`, `PT-022`, `PT-023`, `AI-013`, `AI-014`, `CI-024`,
  `CI-025`.
- **Рекомендация:** keep separate user intents; clarify shared ingestion matrix.

Photo list, receipt, barcode and recipe image могут разделять download/limit/
temporary-file pipeline, но требуют разных schemas и confirmation policies.
Forwarded/replied content может прибыть как обычный reply, `external_reply`,
caption, compressed Telegram photo или document; protected content и неполные
reply stubs ограничивают доступность оригинала. Нужна единая capability matrix,
но нельзя направлять все изображения в один общий model prompt или автоматически
открывать найденные внутри ссылки.

### PTR-021 — Topic-scoped lists и linked households меняют разные isolation boundaries

- **Идеи:** `PT-028`, `CI-008`, `PT-029`, `CI-026`.
- **Рекомендация:** keep separate and explicitly model authorization.

`PT-028` делит один авторизованный chat по `message_thread_id`; `CI-008`
объединяет несколько chats под новым workspace; `PT-029` делает разовую копию
items; `CI-026` переносит выбранный versioned bundle. Это четыре разных
семантики, хотя все выглядят как «несколько мест».

Первые две идеи меняют tenant model и требуют полного пересмотра ключей в
items, recipes, confirmations, bot-message context, shop sessions и callbacks.
Разовая copy/export не должна незаметно превращаться в shared live state. Для
topic mode ответы и edits всегда должны сохранять исходный thread ID.

### PTR-022 — Accessibility value нужно валидировать на реальном Telegram client

- **Идеи:** `PT-026`, частично `AI-029`.
- **Рекомендация:** clarify value hypothesis and test method.

Отделить response locale от сохранённого item text — сильная и реалистичная
гипотеза. Однако бот контролирует только text, HTML и button labels, а не
поведение screen reader, размер шрифта или contrast Telegram client. Термины
`screen-reader-friendly` и `large-label` следует считать целями пользовательской
проверки, не гарантией API. Общая редактируемая shop message также не может
одновременно иметь разные языки для разных участников; персонализированные
views потребуют отдельных сообщений.

### PTR-023 — Allergen idea должна обещать сигнал, а не предотвращение ошибки

- **Идеи:** `AI-021`, `AI-022`, `AI-023`.
- **Рекомендация:** clarify value statement.

`AI-022` уже правильно указывает, что ingredient risk не доказывает безопасность
конкретного бренда. Но формулировка ценности «предотвращает очевидные ошибки»
может восприниматься как safety guarantee. Реалистичный outcome — подсветить
возможный конфликт по явно введённым данным и потребовать ручной проверки.
Substitutions и expiry advice также не должны давать medical или food-safety
гарантии. Этот кластер безопасен только при fail-uncertain wording и отдельном
safety eval.

### PTR-024 — Несколько ecosystem-идей пока не имеют проверенной частоты проблемы

- **Идеи:** `PT-023`, `PT-025`, `CI-028`, `CI-029`, `CI-030`.
- **Рекомендация:** clarify value hypotheses before architectural comparison.

Barcode capture предполагает частые труднонабираемые packaged goods и полезный
региональный catalog. Inline mode предполагает, что context switching является
реальной болью. Home Assistant, retailer cart и companion web view вводят новые
credentials, network boundaries или HTTP surface. У каждой идеи понятная
возможная ценность, но каталог не содержит evidence о частоте сценария.

До оценки архитектурной реализации для каждой нужен короткий discovery question:
кто пользуется, как часто, какое текущее обходное действие и какой минимальный
Telegram-only prototype проверит гипотезу. Это не основание удалить идеи; это
защита от сравнения speculative ecosystem value с уже наблюдаемой проблемой
неверного русского routing.

### PTR-025 — Текущая функциональность в каталогах в целом описана корректно

- **Идеи:** весь каталог; особенно `PT-009`, `PT-011`, `RO-014`, `RO-017`,
  `RO-022`, `AI-018`.
- **Рекомендация:** keep, with one terminology clarification.

Проверенные baseline claims совпадают с проектом:

- `/shop` хранит snapshot и сейчас не синхронизирует старые sessions;
- undo шире обычного только для tracked latest/replied `Added` context;
- recipe ingredient reuse выполняет отдельные inserts и может примениться
  частично, тогда как recipe replacement уже атомарен;
- synchronous SQLite вызывается из async storage interface;
- production copy-deployed, а metric version сейчас статически `0.1.0`;
- `events` — выборочный diagnostics store с потенциальным raw content, не audit
  log.

Единственное междокументное напряжение — термин `public recipe URL`: behavior
contract ожидает public URL, но текущая network boundary не блокирует private
destinations. Это уже отражено в `PTR-014` и должно быть решено как requirement,
а не молча исправлено при реализации соседней идеи.

## Идеи, которые важно не слить несмотря на сходство

| Review ID | Идеи | Почему оставить раздельными |
| --- | --- | --- |
| PTR-K01 | `PT-021`, `AI-013`, `PT-022`, `PT-023` | Один media transport, но list OCR, recipe extraction, receipt reconciliation и barcode lookup имеют разные schemas, privacy surface и mutation semantics. |
| PTR-K02 | `PT-009`, `CI-004` | Session consistency и social trip ownership дают разную пользовательскую ценность. |
| PTR-K03 | `PT-011`, `AI-003`, `RO-014` | User undo, recipe version rollback и crash recovery journal — разные гарантии. |
| PTR-K04 | `AI-011`, `AI-023` | Explicit stateless ingredient query не должен превращаться в inferred persistent pantry. |
| PTR-K05 | `AI-004`, `AI-005`, `AI-008` | Servings scaling, item selection и unit arithmetic могут поставляться и проверяться независимо. |
| PTR-K06 | `PT-029`, `CI-008` | Explicit copy между scopes безопаснее и семантически отличается от live shared workspace. |
| PTR-K07 | `RO-020`, `RO-021` | Наличие backup и доказанная restoreability — разные outcomes. |
| PTR-K08 | `RO-022`, `EO-017` | Release evidence не требует немедленно переходить на immutable symlink deployments. |
| PTR-K09 | `RO-023`, `RO-024`, `EO-023` | Staging environment, end-to-end Telegram smoke и internal health check покрывают разные failure modes. |
| PTR-K10 | `RO-001`, `RO-003`, `RO-004`, `RO-006`, `RO-002` | Trace collection, vocabulary, sampling, storage policy и presentation должны образовать систему, но не один неделимый change. |

## Missing-angle suggestions

### PTR-M01 — Явный результат `not found / substituted / skipped` в магазине

В каталоге хорошо проработаны `bought`, assignment и trip handoff, но почти нет
основного in-store failure journey: товара нет, выбран заменитель, покупка
отложена или нужно спросить requester. Это не то же самое, что удалить item.
Полезна отдельная идея с item-level outcome, быстрым возвратом в active list,
опциональным вопросом requester и сохранением причины без принудительной
истории навсегда.

### PTR-M02 — Offboarding, deauthorization и полное удаление chat data

Есть roles, temporary guests, export и diagnostic retention, но нет цельного
пути: отозвать авторизацию чата, передать household admin role, удалить данные
ушедшего household или выполнить подтверждённый `forget this chat`. Он особенно
важен до linked workspaces и integrations. Нужны owner recovery semantics,
backup warning, двухэтапное подтверждение и ясная граница между live deletion и
старыми backups.

### PTR-M03 — Бюджет поездки и фактическая стоимость

Receipt extraction упоминает optional prices, а retailer integration — внешние
цены, но нет самостоятельного user journey: задать мягкий trip budget, увидеть
примерную сумму, ввести фактический total или отметить дорогую замену. Это может
оказаться ненужным для маленького household; идея нужна как отдельная гипотеза,
чтобы не протащить price tracking незаметно внутри receipt feature.

### PTR-M04 — Один закреплённый актуальный list message

`/shop` sessions и synchronized sessions покрыты, но нет более простого
Telegram-native варианта: владелец закрепляет одну bot message как canonical
read view, а бот best-effort обновляет её после list mutations. Нужно учитывать
право бота закреплять сообщения, удаление/unpin, edit failures, длинные списки и
то, что database остаётся source of truth. Такая идея может проверить спрос на
live view до отдельного web companion.

### PTR-M05 — Поиск и архивирование shopping items

Recipe search проработан в `AI-019`, но после появления named lists, notes,
history и due dates может понадобиться deterministic item search: найти active
или недавний item, показать его list/status и повторно добавить. Это следует
отделить от predictive suggestions и от полного activity timeline.

### PTR-M06 — Единый центр privacy и notification controls

Удаление traces, export, pantry, dietary preferences, location, reminders и
integrations описаны по отдельности. Пользователю может понадобиться одна
read-only сводка: какие данные и scheduled jobs включены для этого chat/user,
куда уходят уведомления и внешние данные, как отключить и удалить каждую
категорию. Это продуктовый trust journey, а не только operator retention policy.

## Verdict

Каталог пригоден для следующего этапа после учёта merge-кластеров и Telegram
feasibility оговорок. Ни одна найденная проблема не требует блокировать сам
brainstorm: рискованные направления помечены caveats, а review сохраняет точные
вопросы для discovery и architecture work.

REVIEW: APPROVE
