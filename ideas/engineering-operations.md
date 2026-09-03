# Engineering, Reliability, Testing, and Operations Ideas

Ниже — неранжированный пул идей для Honeybuy. Он опирается на текущую
архитектуру: один процесс с Telegram long polling, локальная SQLite, опциональные
OpenAI-адаптеры, Prometheus, ручной copy-deploy через `rsync` и systemd. Идеи не
предполагают немедленной реализации и не меняют приватный однопроцессный scope,
кроме тех пунктов, где будущая граница масштабирования названа явно.

## EO-001 — Объяснимый trace решения для каждого входящего сообщения

- **Категория:** наблюдаемость, AI reliability
- **Краткое описание:** собирать компактный структурированный след прохождения
  сообщения через авторизацию, детерминированные парсеры, AI-адаптеры, fallback и
  финальный маршрут.
- **Потенциальная ценность:** быстро отвечает на вопрос «почему солянка была
  распознана как обычная покупка» без догадок по разрозненным логам.

**Подробнее.** Trace должен описывать решения, а не сохранять внутренние chain of
thought модели. Полезные поля: случайный request ID, тип Telegram update, выбранный
режим парсинга, сработавшие guards, имя и ревизия prompt contract, схема/статус
ответа, применение fallback, итоговые route/action и причина пользовательского
ответа. Исходный текст и модельный payload следует либо не хранить, либо хранить
только в явно включённом диагностическом режиме с редактированием чувствительных
данных и коротким TTL.

**Легковесная архитектура.** Внешний Telegram middleware до авторизации создаёт
`ContextVar` с request ID и `DecisionTrace`; каждый routing stage добавляет
типизированное событие. Middleware гарантированно emit-ит trace и сбрасывает context
в `finally`, включая early return, cancellation и exception. Результат уходит в
структурированный лог и, опционально, в ограниченную диагностическую таблицу. Команда
только для владельца может вывести безопасное объяснение последнего решения по ID.

**Предпосылки и компромиссы.** Нужны стабильная схема событий, политика
редактирования/retention и тесты на отсутствие токенов, полного model payload и
лишних пользовательских данных. Подробный trace повышает объём логов и сам может
стать источником приватных данных.

## EO-002 — Конвейер «ошибка в эксплуатации → новый eval-кейс»

- **Категория:** evals, непрерывное улучшение
- **Краткое описание:** превращать выбранные владельцем неудачные trace-записи в
  санитизированные кандидаты для корпуса.
- **Потенциальная ценность:** реальная ошибка перестаёт быть одноразовым инцидентом
  и становится постоянной регрессией, проверяемой до релиза.

**Подробнее.** Сейчас 78-кейсный корпус поддерживается вручную. Для каждого
помеченного сбоя полезно сформировать черновик с исходной формулировкой или её
безопасной минимизацией, ожидаемым route/action, допустимыми вариантами items,
языковыми тегами и объяснением provenance. Попадание в основной JSONL должно
оставаться ручным: оператор подтверждает label и проверяет, что пример не содержит
семейных имён, адресов или иных деталей.

**Легковесная архитектура.** Диагностический trace + отдельно введённый оператором
минимизированный текст → локальная команда экспорта одного ID → карантинный JSONL в
игнорируемом каталоге → ручная редактура и review → обычный versioned corpus.
Альтернатива для сложного инцидента — явно включённый краткоживущий capture исходного
текста. Валидатор корпуса проверяет уникальность ID и обязательный provenance tag.

**Предпосылки и компромиссы.** Зависит от EO-001. Нельзя автоматически коммитить
сырые Telegram-сообщения. Ручной label требует времени, зато не закрепляет неверное
ожидание как «истину».

## EO-003 — Разделить evals по продуктовым способностям

- **Категория:** evals, качество продукта
- **Краткое описание:** дополнить text-routing отдельными наборами для категорий,
  item identity, recipe extraction и пользовательских формулировок ошибок.
- **Потенциальная ценность:** изменение общей parse-модели не сможет незаметно
  улучшить один сценарий и ухудшить другой.

**Подробнее.** Одна модель обслуживает несколько разных операций, но текущий live
runner квалифицирует только pre-confirmation text routing. Независимые корпуса дают
отдельные метрики и release gates: корректность продуктовой категории, канонический
ключ для русско-английских эквивалентов, полнота/точность ингредиентов, корректная
обработка неполных или опасных входов. Для recipe extraction разумны synthetic или
разрешённые к хранению тексты, а не произвольные страницы из production.

**Легковесная архитектура.** Общий eval runtime и формат provenance + отдельные
versioned JSONL-корпуса, graders и operation-specific prompt adapters. Итоговый
report содержит секции по capability и не сводит всё к одной средней цифре.

**Предпосылки и компромиссы.** Нужны вручную проверенные gold labels. Recipe и
normalization допускают несколько корректных ответов, поэтому потребуется аккуратная
система accepted variants, иначе gate будет измерять стиль разметчика.

## EO-004 — Контролируемый voice/audio eval-набор

- **Категория:** evals, voice reliability
- **Краткое описание:** тестировать не только готовый transcript, но всю цепочку
  OGG/OPUS → ffmpeg → transcription → routing на небольшом согласованном аудионаборе.
- **Потенциальная ценность:** ловит русские ошибки распознавания, шум, темп речи и
  проблемы конвертации, которые текстовый корпус принципиально не видит.

**Подробнее.** Набор может включать синтетические и специально записанные фразы:
списки товаров, команды bought/remove, рецепты, смешанный язык, числа и единицы.
Нужны отдельные оценки transcription quality и конечного действия: дословный WER не
всегда важен, если команда выполнена правильно. Live-вызовы остаются opt-in и не
попадают в обычный CI.

**Легковесная архитектура.** Малые versioned audio fixtures с manifest → локальная
ffmpeg-конвертация → выделенный transcription runner с отдельным eval key → текущий
routing grader → JSON-report с model/prompt/ffmpeg fingerprints.

**Предпосылки и компромиссы.** Только записи с явным согласием или синтетика;
аудио увеличивает размер репозитория, стоимость и длительность. Для CI полезен
offline smoke конвертации, но не live transcription.

## EO-005 — Replay-harness для Telegram update-сценариев

- **Категория:** интеграционное тестирование
- **Краткое описание:** описывать многошаговые диалоги как декларативные сценарии
  и проигрывать их через fake aiogram session.
- **Потенциальная ценность:** дешевле покрывать reply context, callbacks, voice
  confirmations, stale actions и групповые режимы без реального Telegram.

**Подробнее.** Текущие dispatcher tests уже имеют fake seam, но сценарные fixtures
могут сделать сложные последовательности читаемыми: входящий update, ожидаемые Bot
API вызовы, состояние SQLite после шага и следующий update. Особенно полезны
проверки requester-only callbacks, одинаковых message IDs в разных chat_id и
повторной доставки update.

**Легковесная архитектура.** Маленькие typed builders в `tests/support` формируют
Telegram updates, callbacks, schema-valid/malformed AI responses и ожидаемые Bot API
calls. YAML/JSON или тонкий Python DSL сценария → эти factories → настоящий
dispatcher → recording fake Bot API → assertions по ответам и временной SQLite.
Stable scenario ID связывает fixture со `SCENARIOS.md`.

**Предпосылки и компромиссы.** DSL не должен стать второй реализацией роутера, а
builders не должны скрывать `chat_id`, requester и ownership в security tests.
Слишком буквальные проверки полного текста сделают suite хрупким; лучше проверять
семантические маркеры, кнопки и state transitions. Миграция существующих тестов —
постепенная, без переписывания ради единообразия.

## EO-006 — Герметичный контрактный стенд для recipe URL fetching

- **Категория:** интеграционное тестирование, безопасность сети
- **Краткое описание:** тестировать redirects, timeouts, content types, bounded
  reads и сетевые отказы на локальном HTTP-сервере.
- **Потенциальная ценность:** закрывает прямо задокументированный пробел и снижает
  риск зависаний, SSRF-подобных обходов и неконтролируемого чтения страниц.

**Подробнее.** Стенд должен выдавать заранее известные ответы: медленный stream,
redirect chain, неверный MIME, слишком большой body, invalid encoding, закрытое
соединение и адреса, которые политика должна отвергать. Тест проверяет как boundary
fetcher, так и нейтральный user-facing fallback, не обращаясь в интернет.

**Легковесная архитектура.** Ephemeral loopback HTTP server в pytest fixture →
матрица endpoint behaviours → `fetch_recipe_page_text` → assertions на лимиты,
таймауты и ошибки. Отдельный pure-policy слой проверяет запрет адресных диапазонов
без реального доступа к ним.

**Предпосылки и компромиссы.** Стенд не создаёт runtime SSRF-защиту: сейчас
private-network URL допустимы в рамках trusted-user assumption, а отдельное изменение
политики описано в EO-012. Реальные DNS rebinding и proxy-особенности полностью не
симулируются. Тестовый сервер должен завершаться детерминированно и не привносить
timing-only assertions.

## EO-007 — Stateful/property-based тесты жизненного цикла списка

- **Категория:** тестирование инвариантов
- **Краткое описание:** генерировать последовательности add/bought/remove/clear и
  recipe-add и сверять инварианты domain/storage модели состояния.
- **Потенциальная ценность:** находит неожиданные комбинации операций, дубликаты и
  нарушения chat isolation, которые трудно перечислить вручную.

**Подробнее.** Domain-модель фиксирует разрешённые переходы `active →
bought/removed`, `bought → removed`, отсутствие воздействия между чатами и
атомарность recipe replacement. Сгенерированная последовательность выполняется через
service/storage, а минимизированный контрпример становится обычным regression test.
Requester-only, authorization, повторную доставку updates и идемпотентность callback
проверяет dispatcher replay из EO-005, а не эта упрощённая модель.

**Легковесная архитектура.** Небольшая эталонная in-memory state machine → generator
команд → временная SQLite и настоящие service methods → сравнение наблюдаемого
состояния и инвариантов после каждого шага.

**Предпосылки и компромиссы.** Потребуется новая dev-зависимость или собственный
ограниченный generator. Эталонную модель нужно держать проще production-кода, иначе
в ней будут те же ошибки.

## EO-008 — Матрица миграций из исторических SQLite fixtures

- **Категория:** миграции, release safety
- **Краткое описание:** хранить минимальные обезличенные fixtures для каждой
  поддерживаемой schema version и прогонять их до HEAD.
- **Потенциальная ценность:** доказывает, что реальный пользователь может обновиться
  не только с последней версии и не потеряет данные.

**Подробнее.** Для каждого fixture проверяются `PRAGMA user_version`, integrity,
ключевые строки до/после, foreign keys, chat scoping и повторный идемпотентный запуск.
Также полезны негативные fixtures: более новая неизвестная схема, частично созданные
таблицы и несовместимые ограничения.

**Легковесная архитектура.** Декларативный fixture builder создаёт старую схему и
seed data → копия файла мигрируется настоящей CLI/storage path → snapshot ключевых
инвариантов сравнивается с ожиданием. Бинарные production dumps не коммитятся.

**Предпосылки и компромиссы.** Fixture builders нужно замораживать, а не импортировать
текущую схему. Матрица растёт с версиями, но объём Honeybuy пока мал.

## EO-009 — Детерминированная fault-injection матрица

- **Категория:** reliability testing
- **Краткое описание:** системно моделировать timeout, cancellation и частичный сбой
  OpenAI, Telegram, SQLite, ffmpeg и recipe fetcher.
- **Потенциальная ценность:** подтверждает graceful fallback и отсутствие ложного
  успеха или полуприменённых критичных операций.

**Подробнее.** Для каждого внешнего boundary описывается таблица failure point →
ожидаемый user response → метрика/log → допустимое состояние БД. Особое внимание:
отмена asyncio-задачи, ошибка после первого элемента batch, Telegram edit failure,
malformed AI JSON и SQLite lock.

**Легковесная архитектура.** Программируемые fakes с именованными failpoints →
существующие service/dispatcher tests → assertions на state, observability signal и
retryability. Никаких случайных network faults или `sleep`.

**Предпосылки и компромиссы.** Матрица может разрастись; сначала стоит покрывать
границы с риском потери данных и неверного подтверждения. Fake должен воспроизводить
контракт библиотеки, а не удобное предположение теста.

## EO-010 — Парное и статистически осмысленное сравнение live eval

- **Категория:** eval methodology
- **Краткое описание:** дополнять абсолютные gates парными различиями по каждому
  case и доверительными интервалами для latency/cost/consistency.
- **Потенциальная ценность:** позволяет отличать реальное улучшение модели или prompt
  от шума нескольких повторов и скрытого обмена quality на стоимость.

**Подробнее.** Baseline и candidate проходят одинаковый упорядоченный корпус в
нескольких чередующихся повторениях. Report выделяет fixed regressions, new
regressions и нестабильные cases, а не только aggregate percentage. Для малой выборки
лучше exact/paired bootstrap оценки и явная пометка «недостаточно данных», чем
псевдоточная p-value.

**Легковесная архитектура.** Scheduler runner чередует baseline/candidate внутри
каждого блока case/repetition и сохраняет фактический порядок с timestamps →
attempt-level JSON → post-processor пар → quality delta, bootstrap intervals,
latency/token distributions → decision summary вместе с текущими жёсткими release
gates. Это требует изменения порядка вызовов, а не только анализа нынешнего отчёта.

**Предпосылки и компромиссы.** Больше повторов увеличивает стоимость. Статистика не
исправляет смещённый corpus и не заменяет ручной разбор каждого нового regression.

## EO-011 — Реестр eval-результатов и тренды качества

- **Категория:** evals, release evidence
- **Краткое описание:** хранить компактные summary каждого квалифицированного запуска
  с привязкой к commit, corpus hash, prompts и model.
- **Потенциальная ценность:** показывает деградацию во времени и делает решение о
  смене модели воспроизводимым.

**Подробнее.** Сырые attempt payload могут оставаться локальными/в CI artifact с
коротким retention, а в репозитории или отдельном artifact store хранится только
безопасный summary: gates, case IDs failures, latency/token quantiles и fingerprints.
Нужно различать production baseline, candidate и ad-hoc smoke.

**Легковесная архитектура.** Live runner → schema-versioned report → sanitizer →
immutable artifact + индекс summaries → простая статическая таблица/график по commit.
Promotion обновляет указатель текущего baseline только после review.

**Предпосылки и компромиссы.** Нельзя коммитить API payload или пользовательский
текст без проверки. Модель-провайдер может менять поведение под тем же именем, поэтому
дата и доступный provider metadata важны.

## EO-012 — Runtime egress policy для recipe URL

- **Категория:** network security, reliability
- **Краткое описание:** явно определить, к каким адресам и через какие redirects
  recipe fetcher имеет право обращаться.
- **Потенциальная ценность:** ограничивает доступ процесса к loopback, metadata и
  приватным сетям, если круг пользователей или deployment topology расширится.

**Подробнее.** Текущий приватный trusted-user scope допускает такие URL и не имеет
SSRF policy; EO-006 лишь тестирует boundary и сам это не исправляет. Отдельное
продуктово-архитектурное решение может запрещать non-public адреса после DNS resolve,
повторять проверку для каждого redirect, ограничивать schemes/ports и не пересылать
credentials. Политика должна учитывать IPv4/IPv6, DNS rebinding и локальные имена.

**Легковесная архитектура.** URL normalizer → DNS resolution policy → pinning
разрешённого IP с корректными Host/SNI и проверкой фактического peer address на
transport boundary (либо отдельный restricted egress proxy) → bounded HTTP client →
повторная policy-check и новое pinning на каждом redirect → visible-text extraction.
Герметичный EO-006 стенд проверяет правила; deny events попадают в безопасную метрику
и trace.

**Предпосылки и компромиссы.** Это изменение текущего trusted-user поведения: могут
сломаться домашние recipe-серверы и нестандартные порты. До реализации нужен явный
security requirement, а не предположение, что любой private URL вредоносен.

## EO-013 — Journald retention и защита диска от заполнения

- **Категория:** operations, log durability
- **Краткое описание:** задать бюджет журналов, retention и алерты по свободному месту
  для VPS.
- **Потенциальная ценность:** verbose AI/trace logging или restart loop не вытеснит
  SQLite backups и не остановит сервис из-за полного диска.

**Подробнее.** Нужно измерять общий размер journal и файловых систем с app/data/cache,
ограничить persistent journal по размеру/возрасту и документировать безопасную
диагностику. При restart storm полезны rate-limited повторяющиеся логи и отдельный
счётчик рестартов, а не бесконечные одинаковые stack traces.

**Легковесная архитектура.** journald drop-in с явными `SystemMaxUse`/retention →
node-level disk metrics → warning/critical thresholds → runbook очистки, который не
трогает SQLite или единственный backup. EO-025 structured events соблюдают log budget.

**Предпосылки и компромиссы.** Слишком короткий retention уничтожит evidence редкого
сбоя. Настройки затрагивают host-wide journald, поэтому сначала проверить, какие ещё
сервисы живут на VPS, либо ограничить per-unit rate/output и оставить глобальную
политику оператору.

## EO-014 — Scheduled dependency compatibility и security drift check

- **Категория:** maintainability, supply chain
- **Краткое описание:** регулярно предлагать маленькие lockfile updates и проверять
  их полным offline suite плюс отдельным SDK-shape smoke.
- **Потенциальная ценность:** обновления aiogram/OpenAI/uv не накапливаются в большой
  рискованный скачок.

**Подробнее.** Изменения группируются консервативно: runtime отдельно от dev tools,
major versions отдельно. Автоматизация только создаёт reviewable proposal; merge и
release остаются ручными. Для runtime dependencies полезен список changelog risks:
Telegram object shape, Responses API payload и Pydantic validation.

**Легковесная архитектура.** Scheduled workflow/bot → обновлённый `uv.lock` в branch →
full CI + compatibility tests → human review. Опционально формируется SBOM текущего
release artifact.

**Предпосылки и компромиссы.** Требует доверия к dependency bot и аккуратных
permissions. Зелёный offline suite не доказывает совместимость с live API, поэтому
существенные SDK changes требуют opt-in smoke.

## EO-015 — Systemd sandboxing для runtime процесса

- **Категория:** host security, deployment
- **Краткое описание:** ограничить процесс бота только необходимыми файловыми,
  сетевыми и системными возможностями.
- **Потенциальная ценность:** уменьшает последствия уязвимости в recipe parser,
  ffmpeg, HTTP/OpenAI SDK или другом dependency.

**Подробнее.** Unit-кандидаты: `NoNewPrivileges`, private temp, read-only system paths,
явные writable paths для SQLite/cache, запрет лишних capabilities и осторожный syscall
filter. Ограничения сети сложнее: боту нужны Telegram/OpenAI/DNS и произвольные
разрешённые recipe URLs, поэтому агрессивный address-family/filter policy надо
проверять отдельно.

**Легковесная архитектура.** `systemd-analyze security` baseline → по одному hardening
directive → staging start/health/voice/recipe smoke → зафиксированный unit + regression
check разрешённых writable paths. ffmpeg наследует тот же sandbox.

**Предпосылки и компромиссы.** Непроверенный hardening легко ломает DNS, temp files,
uv cache или subprocess. Score `systemd-analyze` — ориентир, не цель; изменения должны
следовать фактическому runtime contract.

## EO-016 — Read-only deployment preflight/doctor

- **Категория:** deployment safety, developer experience
- **Краткое описание:** одной командой проверять готовность локального release и VPS
  до остановки сервиса.
- **Потенциальная ценность:** ловит отсутствие backup space, неверные права, env,
  ffmpeg, uv или несовместимую схему до начала downtime.

**Подробнее.** Локальная часть проверяет clean commit, CI/eval evidence и manifest.
Удалённая — systemd unit, пользователя/директории, свободное место, читаемость env без
печати значений, версию Python/uv, integrity/schema SQLite и доступность backup
destination. Никаких изменений в preflight.

**Легковесная архитектура.** Operator CLI → локальные checks + ограниченный SSH
read-only script → машинный JSON и человекочитаемый verdict. Каждый check имеет ID и
remediation hint.

**Предпосылки и компромиссы.** SSH account требует минимальных sudo-read permissions.
Preflight может устареть относительно deploy flow, поэтому оба должны использовать
общие декларативные requirements.

## EO-017 — Неизменяемые atomic releases с manifest

- **Категория:** release engineering, deployment, rollback
- **Краткое описание:** собирать bundle из конкретного clean commit, раскладывать
  версии отдельно и переключать `current` symlink после полной подготовки.
- **Потенциальная ценность:** исключает запуск произвольного или наполовину
  синхронизированного дерева, точно отвечает «что на VPS» и ускоряет возврат к
  предыдущему коду.

**Подробнее.** Bundle содержит исходники, lockfile, deploy assets и manifest с commit
SHA, hashes, Python/uv requirements, schema version и prompt/corpus fingerprints, но
не `.env`, `.git`, БД, caches или eval secrets. Каждая версия распаковывается в
`/opt/honeybuy-tg/releases/<id>`, где проходит checksum/signature verification,
`uv sync --frozen` и preflight. `/opt/honeybuy-tg/current` указывает на выбранный
release, а systemd unit явно меняет `WorkingDirectory` на этот symlink. Переключение
происходит только при остановленном single polling process.

**Легковесная архитектура.** Clean Git tree → tarball + manifest + checksum/signature
→ новый release dir → verification/dependency sync → stop → DB backup/migrate →
atomic `current` symlink swap → start/health/smoke → prune старых release dirs по
retention.

**Предпосылки и компромиссы.** SQLite schema всё равно может сделать code-only
rollback невозможным; нужен EO-021. Появляются artifact storage и signing-key
management. Следует убедиться, что `.venv` и working directory не разделяют
изменяемое состояние между релизами.

## EO-018 — Идемпотентный release state machine

- **Категория:** deployment orchestration
- **Краткое описание:** оформить текущую инструкцию как шаги с явными checkpoints,
  resume и безопасными stop conditions.
- **Потенциальная ценность:** ручной деплой становится повторяемым и не требует
  вспоминать, был ли уже сделан backup или migration.

**Подробнее.** Состояния: artifact verified, preflight passed, dependencies ready,
service stopped, backup verified, migration passed, service healthy, smoke accepted.
Каждый mutating step печатает план, фиксирует локальный release receipt и проверяет
предусловия. Автоматизация не должна сама считать Telegram smoke успешным.

**Легковесная архитектура.** Локальный operator command управляет узкими remote
commands через SSH; VPS хранит non-secret deployment receipt рядом с releases;
повторный запуск читает receipt и повторно валидирует факты.

**Предпосылки и компромиссы.** Требуется строгая модель privileges и защита от двух
одновременных deploy. Более сложный orchestration оправдан только после стабилизации
ручной последовательности.

## EO-019 — Автоматические согласованные SQLite backups

- **Категория:** backup, data durability
- **Краткое описание:** делать проверяемые периодические backups независимо от
  deployment backups.
- **Потенциальная ценность:** защищает от повреждения, ошибочного удаления и проблемы,
  обнаруженной не сразу после релиза.

**Подробнее.** Вместо копирования живого файла использовать SQLite backup API или
коротко quiesce writer. Каждый backup получает timestamp, schema version, checksum и
integrity result; retention сочетает дневные/недельные копии. Копия вне VPS защищает
от потери самого диска.

**Легковесная архитектура.** systemd timer → backup helper от ограниченного
пользователя → temporary file → integrity/checksum → atomic rename → encrypted
off-host sync → retention/prune → success/failure metric.

**Предпосылки и компромиссы.** Нужны защищённое хранилище, ключи, контроль стоимости и
проверка restore. Backup без проверенного восстановления даёт ложную уверенность.

## EO-020 — Версионированная restore-команда и регулярный restore drill

- **Категория:** disaster recovery
- **Краткое описание:** документированно восстанавливать backup в новый путь,
  проверять его и только затем переключать production.
- **Потенциальная ценность:** сокращает RTO и обнаруживает бесполезные backups до
  аварии.

**Подробнее.** Команда по умолчанию read-only проверяет manifest, checksum, SQLite
integrity и поддерживаемую schema. Реальное переключение требует остановленного
сервиса, сохраняет повреждённый файл отдельно и никогда не перезаписывает единственную
копию. Drill можно проводить на временной директории без доступа к Telegram/OpenAI.

**Легковесная архитектура.** Backup catalog → select artifact → decrypt/copy to temp →
integrity + migration dry run + ключевые data counts → explicit promote → start and
smoke. Scheduled CI не получает production database; drill выполняется на VPS или
изолированном recovery host.

**Предпосылки и компромиссы.** Нужно определить RPO/RTO и кто подтверждает promote.
Off-host encrypted restore требует отдельно восстановимых ключей.

## EO-021 — Rollback pack, связывающий код и состояние базы

- **Категория:** release safety, rollback
- **Краткое описание:** перед схемным релизом сохранять совместимую пару previous
  release + database backup + metadata.
- **Потенциальная ценность:** предотвращает опасный «откатили код, но оставили новую
  несовместимую схему».

**Подробнее.** Pack фиксирует old/new schema versions, release IDs, backup checksum,
миграции и допустимый rollback path. Для additive backward-compatible migration можно
разрешить code swap; иначе workflow требует восстановления целого старого SQLite
файла. Никаких down migrations над production data.

**Легковесная архитектура.** Pre-deploy manifest + verified pre-migration backup →
rollback descriptor → health failure decision → stop → restore compatible pair →
start/smoke → preserve failed state for analysis.

**Предпосылки и компромиссы.** Восстановление старой БД теряет записи после backup;
это должно быть явным решением оператора. Требуется классификация migration
compatibility.

## EO-022 — Ручное promotion из GitHub Actions с защищённой средой

- **Категория:** CI/CD
- **Краткое описание:** после offline gates и live-eval evidence вручную продвигать
  конкретный artifact на VPS через protected environment.
- **Потенциальная ценность:** CI и deployment используют один SHA, а production-доступ
  не доступен каждому обычному workflow.

**Подробнее.** Push только тестирует и собирает artifact. Отдельный
`workflow_dispatch` принимает release ID, требует approval и выполняет preflight,
deploy/health; Telegram smoke остаётся ручным подтверждением. Лучше короткоживущая
аутентификация или узкий deploy credential, а не универсальный SSH key.

**Легковесная архитектура.** Required CI → immutable artifact → protected GitHub
environment approval → restricted deploy runner/SSH principal → EO-018 state machine →
deployment receipt linked in workflow summary.

**Предпосылки и компромиссы.** Это внешняя attack surface и secret-management burden.
Для одного VPS ручной локальный deploy может оставаться проще; автоматизация оправдана
только с минимальными permissions и понятным emergency path.

## EO-023 — Health-check CLI с уровнями readiness

- **Категория:** health checks, operations
- **Краткое описание:** дать systemd и оператору безопасную команду проверки процесса,
  конфигурации, базы и опциональных внешних зависимостей.
- **Потенциальная ценность:** отличает «процесс жив» от «бот способен обслужить
  запрос» и ускоряет post-deploy verdict.

**Подробнее.** Offline health проверяет загрузку settings, writable database,
supported schema, quick/integrity check и наличие ffmpeg. Online probe, запускаемый
только явно, может проверить Telegram `getMe` и минимальный OpenAI request отдельным
режимом. Результат не печатает секреты и имеет стабильные exit codes/check IDs.

**Легковесная архитектура.** `honeybuy-tg health --offline|--external` → набор probes →
JSON/text result. systemd `ExecStartPre` использует только дешёвые offline checks;
deploy workflow вызывает более полный post-start probe.

**Предпосылки и компромиссы.** `integrity_check` на большой БД может быть дорогим;
нужны quick и deep уровни. Внешний probe сам тратит quota и может флапать из-за сети.

## EO-024 — Event-loop watchdog и отдельный polling health signal

- **Категория:** availability
- **Краткое описание:** обнаруживать зависание event loop и отдельно наблюдать
  состояние polling/backoff, не считая тишину пользователей аварией.
- **Потенциальная ценность:** systemd может перезапустить процесс, который остаётся
  живым, но перестал планировать runtime-задачи; оператор видит сетевую деградацию
  poller отдельно.

**Подробнее.** Внутренний heartbeat доказывает только то, что asyncio loop продолжает
планировать задачи. Возврат `start_polling` и так завершит процесс под нынешним
`Restart=always`; сетевое зависание внутри живого poller требует отдельного сигнала
состояния/backoff, если библиотека даёт надёжный seam. Возраст последнего update
полезен для диагностики, но не для restart: семья может просто не писать боту.

**Легковесная архитектура.** После завершения startup приложение посылает через
совместимый `sd_notify` сигнал `READY=1`; отдельная async heartbeat task периодически
посылает `WATCHDOG=1`, а shutdown прекращает heartbeat и сообщает stopping state.
Unit использует `Type=notify`, `NotifyAccess=main` и `WatchdogSec`, чтобы restart
срабатывал при event-loop stall. Отдельные низкокардинальные metrics/log events
отражают poller start/backoff/exit. Startup grace period учитывает migration и
Telegram backoff.

**Предпосылки и компромиссы.** Неверный timeout создаёт restart loop во время
легитимной медленной операции. Сначала полезно измерить event-loop stalls и вынести
синхронные SQLite участки из критического пути.

## EO-025 — Структурированные JSON-логи с корреляцией

- **Категория:** observability, diagnostics
- **Краткое описание:** заменить трудно связываемые текстовые строки на события со
  стабильными полями и request/release IDs.
- **Потенциальная ценность:** journald-запросом можно собрать весь путь одного сбоя и
  сравнить ошибки до/после релиза.

**Подробнее.** Поля: event name, severity, request ID, operation, status, duration,
release SHA и безопасные технические identifiers. `chat_id`, `user_id`, message text,
recipe URL и item names по умолчанию не логируются. Если краткоживущая корреляция по
Telegram ID действительно нужна, применяется keyed HMAC с ротацией, а не обычный
перебираемый hash. Exceptions получают класс и boundary, но не секретные payload.

**Легковесная архитектура.** Logging adapter читает context из EO-001 → JSON stdout →
journald → локальные queries/export в мониторинг. Схема событий versioned, а tests
проверяют redaction и cardinality.

**Предпосылки и компромиссы.** JSON менее удобен при простом `journalctl -f`, поэтому
нужен human renderer/query examples. Корреляция не должна превращаться в постоянный
идентификатор пользователя, а HMAC-значения не должны становиться Prometheus labels.

## EO-026 — Небольшой SLO-набор и actionable alerts

- **Категория:** observability, reliability
- **Краткое описание:** определить несколько сигналов пользовательского здоровья, а
  не алертить на каждую техническую ошибку.
- **Потенциальная ценность:** владелец узнаёт о реальной деградации раньше жалобы и не
  тонет в шуме.

**Подробнее.** Кандидаты: доля успешно обработанных Telegram actions, AI fallback и
schema-error rate по operation, p95 latency, restart frequency, backup age/result,
database size/integrity status и eval baseline age. Для маленького приватного бота
абсолютные counts и длинные окна часто полезнее процентных SLO на малой выборке.

**Легковесная архитектура.** In-process низкокардинальные Prometheus metrics + один
release SHA в `info` series → Grafana panels → несколько alert rules с устойчивым
окном → приватный канал/notification. Результаты timer/eval jobs публикуются через
node-exporter textfile collector либо записывают ограниченный status-файл/таблицу,
которую читает exporter; они не полагаются на исчезающий process-local counter.
Trace ID остаётся только в логах и помогает drill-down.

**Предпосылки и компромиссы.** Нужен работающий scrape/alertmanager за доверенной
границей. Низкий трафик делает многие проценты статистически бессмысленными.

## EO-027 — Явная OpenAI resilience policy

- **Категория:** runtime reliability
- **Краткое описание:** централизовать timeout, ограниченные retries, concurrency и
  circuit breaker для AI-операций.
- **Потенциальная ценность:** один медленный провайдер не держит неограниченно много
  outstanding requests и не вызывает неконтролируемую задержку или стоимость
  повторов.

**Подробнее.** Policy различает безопасные retryable network/429/5xx ошибки и
non-retryable schema/prompt failures. У операций разные бюджеты: категория может
быстро fallback, recipe extraction допускает больший timeout, voice должен дать
понятную ошибку. Общий клиент уменьшает лишние connections.

**Легковесная архитектура.** Composition root владеет одним shared `AsyncOpenAI`
client и закрывает его при shutdown. Provider gateway задаёт единственный
per-operation timeout/retry budget, отключая или явно учитывая SDK defaults →
semaphore + breaker → существующие strict adapters → metrics и decision trace.
Fallback остаётся в caller boundary.

**Предпосылки и компромиссы.** Retry может удвоить стоимость и latency; breaker может
отклонить запрос после восстановления. Нужны fault-injection tests EO-009 и значения,
основанные на production metrics.

## EO-028 — Учёт token/cost и мягкие бюджеты

- **Категория:** operational cost
- **Краткое описание:** учитывать usage по AI operation/model и предупреждать о
  дневном/месячном отклонении от ожидаемого расхода.
- **Потенциальная ценность:** делает цену функций видимой и обнаруживает retry loop,
  cache miss storm или неожиданно дорогую модель.

**Подробнее.** Использовать provider usage, когда оно доступно, отдельно считать
requests без usage и оценивать стоимость по versioned price table лишь как estimate.
Не нужны user/chat labels. Мягкий budget сначала только уведомляет; hard cutoff может
быть индивидуальным для необязательных категорий/нормализации, но не должен неожиданно
ломать voice или recipe flow.

**Легковесная архитектура.** AI gateway → usage event `{operation, model, tokens}` →
durable дневной aggregate в SQLite (или внешний monitoring store с retention) →
budget evaluator → warning. Prometheus counters остаются наблюдаемостью, а не
единственным источником budget decision. Dashboard сопоставляет стоимость, cache hit
rate и успешные actions.

**Предпосылки и компромиссы.** Цены меняются, cached/reasoning tokens считаются по-
разному. Локальная оценка не заменяет provider billing dashboard и не должна включать
сырой prompt.

## EO-029 — Политика retention и безопасная maintenance-команда

- **Категория:** storage operations, privacy, cost
- **Краткое описание:** определить и автоматизировать очистку устаревших diagnostics,
  confirmations, message context, sessions, cache и старых item states.
- **Потенциальная ценность:** ограничивает рост SQLite, объём backup и срок хранения
  чувствительного контекста.

**Подробнее.** У каждой таблицы свой lifecycle: pending confirmation после expiry,
bot message context после разумного окна, expired cache rows, diagnostic events и
опционально bought/removed history. Сначала dry-run показывает counts/age/space;
удаление идёт малыми транзакциями и не затрагивает active items/recipes.

**Легковесная архитектура.** Versioned retention policy → `maintenance --dry-run` →
table-specific repository operations малыми transactions → metrics/summary. Лёгкую
очистку запускает сам процесс через сериализованный storage path либо внешний timer
после введения `busy_timeout`; длинная maintenance и отдельный редкий `VACUUM`
требуют backup и явного quiesce сервиса.

**Предпосылки и компромиссы.** Product decision о bought history пока не принято.
Для Telegram нет гарантированного «неактивного окна», поэтому внешний timer не должен
гоняться с текущим writer. Очистка необратима: нужны conservative defaults, backup и
тесты chat/state invariants.

## EO-030 — Подготовка к росту без преждевременного multi-instance

- **Категория:** scalability boundary, maintainability
- **Краткое описание:** снять известные bottlenecks одного процесса, сохранив одну
  polling replica и SQLite как source of truth.
- **Потенциальная ценность:** уменьшает event-loop stalls и lock errors, не вводя
  Redis/Postgres/queue раньше реальной необходимости.

**Подробнее.** Измерить длительность SQLite операций, затем рассмотреть `busy_timeout`,
WAL с корректными backup semantics, offload синхронных calls и ограниченную
сериализацию writes. Multi-item действия могут получить явные transaction boundaries.
Перед любой второй replica нужно отдельно решить Telegram polling ownership,
callbacks, local state и database coordination.

**Легковесная архитектура.** Storage instrumentation → thresholds → один
connection/worker strategy или bounded thread offload → SQLite WAL/busy policy →
load test с несколькими чатами. Документированный trigger (latency/lock volume/data
size) определяет, когда проектировать Postgres/webhook/queue.

**Предпосылки и компромиссы.** WAL усложняет backup/restore, а thread offload —
transactions/cancellation. Без измеренного bottleneck текущая простота ценнее. Эта
идея — граница будущего решения, не предложение немедленно масштабировать topology.
