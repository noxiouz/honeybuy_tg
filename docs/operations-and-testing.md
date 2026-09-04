# Operations And Testing

## Runtime Requirements

- Python 3.13 or newer
- `uv` for locked dependency installation and command execution
- a Telegram bot token
- one configured owner identity
- write access to the configured SQLite path
- outbound Telegram network access
- an OpenAI key and outbound OpenAI access for AI-backed features
- `ffmpeg` for voice-note conversion
- outbound HTTP(S) access for recipe URL learning

Dependencies and the `honeybuy-tg` console script are declared in
[`pyproject.toml`](../pyproject.toml). Exact dependency versions are locked in
[`uv.lock`](../uv.lock).

## Configuration

Settings are loaded from process environment variables and a `.env` file in the
working directory. Empty values are ignored and unknown values are allowed.
Pydantic validates the configuration before either normal startup or the
migrate-only command.

### Core And Access

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | none; required | Telegram Bot API token |
| `OWNER_USER_ID` | none | Preferred stable Telegram owner ID |
| `OWNER_USERNAME` | none | Alternative owner match by username, with optional leading `@`; at least one owner field is required |
| `ALLOWED_USER_IDS` | empty | Comma-separated IDs allowed to use private chats; a configured `OWNER_USER_ID` is included automatically |
| `DATABASE_PATH` | `./data/honeybuy.sqlite3` | SQLite database path, relative to the working directory when not absolute |
| `TEXT_PARSE_MODE` | `mention` | Default `off`, `mention`, or `all` for chats without an override |
| `LOG_LEVEL` | `INFO` | Python logging level passed through uppercasing |

Prefer `OWNER_USER_ID` after using `/whoami` to discover it. A username can
change, whereas the numeric ID is stable. If both owner settings are present,
the code accepts a user matching either one; keep them synchronized or remove
the username after bootstrap.

### Telegram Inline Mode

Cross-chat item capture requires one manual Telegram-side setting; there is no
environment variable or deployment-file change for it:

1. Open `@BotFather` and send `/setinline`.
2. Select the Honeybuy bot.
3. Set a short placeholder such as `Item to add`.

The bot must also be an administrator in every authorized group that should be
offered as an inline destination. The requester must still be a current member
of that group. Ordinary group authorization with `/authorize` remains required.

Do not rely on `/setinlinefeedback` for correctness. It is unnecessary for this
feature: selecting or sending a result does not mutate a list, and Honeybuy does
not use `chosen_inline_result` as a functional trigger. If Telegram feedback is
enabled independently, Telegram may deliver those updates as telemetry, but
Honeybuy currently ignores them and they have no application state effect.

### OpenAI And Limits

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENAI_API_KEY` | unset | Enables text parsing, categories, identity normalization, recipe commands/extraction, and voice transcription |
| `OPENAI_PARSE_MODEL` | `gpt-5.4-mini` | Shared model for non-transcription AI operations |
| `OPENAI_TRANSCRIBE_MODEL` | `gpt-4o-mini-transcribe` | Voice transcription model |
| `MAX_VOICE_DURATION_SECONDS` | `120` | Reject before download/transcription when Telegram duration is greater |
| `MAX_VOICE_FILE_SIZE_BYTES` | `10000000` | Reject before download when Telegram reports a larger file |
| `MAX_TRANSCRIPT_CHARACTERS` | `4000` | Reject after transcription when text is longer |
| `CATEGORY_CACHE_TTL_SECONDS` | `2592000` | Category cache lifetime, 30 days by default |
| `ITEM_NORMALIZATION_CACHE_TTL_SECONDS` | `7776000` | Canonical item-identity cache lifetime, 90 days by default |

The three limits and two TTLs must be positive. A missing OpenAI key does not
disable the bot: slash commands, local natural-text parsing, saved recipe reuse,
and local-name matching remain available. It does disable voice transcription
and learning new recipes. Categorization can render without AI, while identity
matching uses cached or local fallbacks; a new uncached cross-language match may
therefore be unavailable.

### Metrics

| Variable | Default | Meaning |
| --- | --- | --- |
| `METRICS_ENABLED` | `false` | Start the Prometheus HTTP exporter |
| `METRICS_HOST` | `127.0.0.1` | Exporter bind address |
| `METRICS_PORT` | `9108` | Exporter port, from 1 through 65535 |

Keep the exporter on loopback unless a trusted network boundary or reverse
proxy protects it.

The local and production templates are
[`.env.example`](../.env.example) and
[`deploy/ubuntu/env.example`](../deploy/ubuntu/env.example).

## Local Development

Install dependencies and run all checks from the repository root:

```sh
uv sync
cp .env.example .env
uv run python -m honeybuy_tg
```

Equivalent installed entrypoint:

```sh
uv run honeybuy-tg
```

Run automated checks:

```sh
uv run pytest
uv run ruff check .
```

Most tests create an isolated database under pytest's temporary directory and
do not need the configured runtime database. Telegram tests use a fake aiogram
session; AI tests fake client responses. No live Telegram or OpenAI call should
be needed for the automated suite.

The single required CI job is named `offline`. It runs locked dependency sync,
the full offline pytest suite, Ruff, and `git diff --check` on every push and
pull request. On pushes and same-repository PRs, that same job must also run the
Linux/root/systemd containment integration test as root. Fork PRs skip only
that privileged step and still run every ordinary offline gate. This split
keeps untrusted fork code out of a root process without weakening the exact-SHA
push gate used for deployment. CI must not run live model evals or use Telegram
or OpenAI credentials.

## Test Map

| Test module | Main contract |
| --- | --- |
| `test_parser.py` | Deterministic shopping phrases, splitting, filler, and unknown input |
| `test_recipes.py` | Recipe URL/paste/alias/reuse parsing and visible HTML text |
| `test_ai.py` | Strict response models, wrapper payloads, and invalid-response metrics |
| `test_service.py` | Domain validation, inline-item normalization/application, recipe operations, matching, identity deduplication |
| `test_storage.py` | Chat isolation, state transitions, inline-intent atomicity/expiry/quota, transaction/conflict rules, caches, sessions, and migrations |
| `test_formatting.py` | Category grouping, HTML escaping, recipes, and shop rendering |
| `test_telegram_bot.py` | Routing helpers and fake-session integration for inline capture, text, voice context, recipes, and shop mode |
| `test_metrics.py` | AI status/error/cancellation instrumentation |
| `test_config.py` | Owner and positive-limit validation |
| `test_deploy.py` | Installer, systemd units, bootstrap gates, and migrate/healthcheck CLI behavior |
| `test_release_controller.py` | GitHub evidence, immutable release preparation, containment, activation journals, backups, rollback, receipts, quarantine, and bootstrap recovery |

[`SCENARIOS.md`](../SCENARIOS.md) is the user-visible acceptance checklist.
When behavior changes, update its stable scenario IDs and add the narrowest
module test plus an integration-style Telegram test when routing is involved.

For a focused offline inline-capture check, run:

```sh
uv run pytest -q tests/test_service.py -k inline_capture
uv run pytest -q tests/test_storage.py \
  -k "inline_capture or migrates_v1_database_to_inline_capture_schema"
uv run pytest -q tests/test_telegram_bot.py -k inline
```

These tests use temporary SQLite files and a fake Telegram session. They do not
contact Telegram, OpenAI, or a deployed database. Follow them with the full
`uv run pytest -q`, `uv run ruff check .`, and `git diff --check` gates before
release.

## Text Routing Evals

Text routing has three layers:

- unit and integration tests under `tests/`, always offline;
- a strict JSONL corpus at `evals/cases/text_routing.v1.jsonl`, validated by
  `tests/test_eval_corpus.py`;
- an opt-in live model comparison runner, `python -m evals.live_compare`, for
  release decisions around model or prompt changes.

The corpus records the expected route, action, accepted item variants, accepted
recipe-name alternatives, critical status, and tags for Russian, English,
mixed-language, mention, voice-transcript, shopping, recipe, unhandled, and
ambiguous inputs. Item grading treats accepted variants as unordered normalized
multisets. Recipe-name alternatives must be explicit labels in the corpus, not
hidden stemming. Ambiguous cases must be labeled as `unknown` without guessed
items or recipe names.

Before editing prompts or changing `OPENAI_PARSE_MODEL`, add or review corpus
cases for the behavior being changed, then run the offline suite. For a live
comparison, use a separate evaluation key:

```sh
HONEYBUY_EVAL_OPENAI_API_KEY=... \
  python -m evals.live_compare \
  --allow-live-openai \
  --model gpt-5.4-mini \
  --output evals/results/text-routing.json
```

The live runner ignores ordinary `OPENAI_API_KEY`, strips ambient OpenAI
endpoint/org/project/webhook environment while constructing the client, never
loads `.env`, and refuses to call OpenAI unless `--allow-live-openai`, at least
one nonblank `--model`, and `HONEYBUY_EVAL_OPENAI_API_KEY` are all present. It
uses production prompt fingerprints and response schemas but does not call
Telegram, SQLite, recipe URLs, voice transcription, or production data. Reports
include `HEAD`, a dirty-worktree flag, status lines, and a diff hash to prevent
misattributing results to a clean commit.

Prompt override files are candidate-only. The first `--model` entry always uses
production prompts as the baseline; the second and later entries use any
override files. To compare a prompt change on the same model, pass that model
twice. A run with overrides and fewer than two model entries is rejected before
client construction.

Release gates for each live model are:

- 100% schema-valid completed model responses;
- 100% critical cases;
- zero shopping-to-recipe false positives;
- 100% overall graded cases;
- at least 98% route/action accuracy;
- at least 98% accepted item accuracy where labeled;
- at least 98% recipe-name accuracy where labeled;
- at least 99% consistency across repetitions;
- no more than 2% failed model requests; and
- release qualification with at least 72 cases and at least 3 repetitions.

When comparing models, the first `--model` is the baseline. Candidate reports
flag p95 latency above 1.5x baseline and output tokens above 1.25x baseline.
These gates are deterministic release checks over the maintained corpus, not a
claim of statistical certainty. Smaller custom runs are smoke checks: they can
be useful locally, but the report must show that they are not release-qualified,
and the CLI exits nonzero.

Known coverage gaps remain around live recipe URL fetching: scheme and
content-type rejection, redirects, timeouts, bounded reads, private-network
destinations, and the generic user-facing failure path do not have dedicated
end-to-end tests. Voice eval cases use transcript text and cover
pre-confirmation intent classification only; dispatcher tests own Telegram
confirmation behavior and they do not measure audio transcription quality.

## Database Initialization And Migration

Normal startup calls `Storage.init` before polling, so a new local database is
created and known migrations are applied automatically.

Run the explicit migration path with:

```sh
uv run python -m honeybuy_tg migrate
```

When a migration is applied, the command prints the old and new
`PRAGMA user_version`, applied versions, and the result of
`PRAGMA integrity_check`. An already-current database reports only its current
version and integrity result. The command then exits without contacting
Telegram. The current CLI still constructs all settings first, so it requires a
valid bot token and owner identity even though it does not use them.

Never run an older binary against a database advertising a newer schema
version. The migration runner refuses that case by design.

The separate health check needs only `DATABASE_PATH`:

```sh
DATABASE_PATH=./data/honeybuy.sqlite3 \
  uv run python -m honeybuy_tg healthcheck
```

It opens the source read-only, copies it into an in-memory database, and checks
the exact supported `PRAGMA user_version`, required tables and columns,
`PRAGMA integrity_check`, and `PRAGMA foreign_key_check`. It does not load bot
tokens or OpenAI settings, migrate the source, start polling, or make network
requests. Production uses this database-only command for candidate checks and
as `ExecStartPre` for the bot service.

## Guarded Ubuntu Deployment

The guarded release path does not use a mutable application checkout under
`/opt`. A converted legacy host may retain unused files from its former
copy-deployment, but neither the bot unit nor the controller treats them as a
release:

| Path | Owner/mode intent | Purpose |
| --- | --- | --- |
| `/opt/honeybuy-tg/releases/<40-char-sha>/` | root, not group/world-writable | Sealed immutable release and `.ready.json` provenance manifest |
| `/opt/honeybuy-tg/current` | root-owned symlink | Release used by `honeybuy-tg.service` |
| `/opt/honeybuy-tg/previous` | root-owned symlink | Last release available to rollback |
| `/etc/honeybuy-tg/env` | root, `0600` | Runtime secrets and settings |
| `/etc/honeybuy-tg/allowed_signers` | root, protected | One namespace-restricted public SSH key used only to verify Git signatures |
| `/var/lib/honeybuy-tg/honeybuy.sqlite3` | `honeybuy`, `0600` | Live SQLite database |
| `/var/cache/honeybuy-tg/uv/` | `honeybuy-build` | Locked dependency-build cache |
| `/var/lib/honeybuy-release-controller/repository/` | root, private | Bare Git object repository used for fixed HTTPS fetches |
| `/var/lib/honeybuy-release-controller/deployed-sha` | root | Durably committed deployed Git SHA |
| `/var/lib/honeybuy-release-controller/deployment-journal.json` | root, `0600` | In-progress activation or rollback state |
| `/var/lib/honeybuy-release-controller/bootstrap-journal.json` | root, `0600` | One-time bootstrap state |
| `/var/lib/honeybuy-release-controller/control-plane-manifest.json` | root, `0600` | Installed-control-plane phase and exact hashes |
| `/var/lib/honeybuy-release-controller/receipts/` | root, private | Successful deployment/bootstrap receipts by SHA |
| `/var/lib/honeybuy-release-controller/quarantine/` | root, private | Rejected post-start candidate records by SHA |
| `/var/lib/honeybuy-release-controller/scratch/` | root-controlled | Dry-run, clone, and restore staging |
| `/var/lib/honeybuy-release-controller/empty/` | root, `0755`, below a `0711` state directory | Empty working directory for isolated migration and health checks |
| `/var/backups/honeybuy-tg/` | root-controlled, private | Validated pre-migration SQLite backups |
| `/run/honeybuy-release-controller/controller.lock` | root, `0600` | Single-controller flock |
| `/usr/local/lib/honeybuy/release_controller.py` | root, executable | Installed controller program |

The deployment control plane consists of:

- `honeybuy-tg.service`, running as `honeybuy` from
  `/opt/honeybuy-tg/current`, with a two-minute start timeout and 30-second stop
  timeout;
- `honeybuy-release-controller.service`, a root `Type=exec` one-shot with a
  30-minute runtime bound and control-group cleanup; and
- `honeybuy-release-controller.timer`, scheduled every five minutes with
  `RandomizedDelaySec=30s`, `Persistent=true`, and one-minute accuracy.

The controller must be the service's real `MainPID` so every unprivileged build,
migration, and health-check subprocess can be placed in a bounded sibling
transient service tied to the controller lifetime. Consequently, even an
operator-requested run goes through systemd:

```sh
sudo systemctl start honeybuy-release-controller.service
sudo systemctl status honeybuy-release-controller.service --no-pager
sudo journalctl -u honeybuy-release-controller.service -n 200 --no-pager
```

Do not execute `/usr/local/lib/honeybuy/release_controller.py` directly, and do
not wrap it in an ad-hoc `sudo`, shell, cron job, or `systemd-run`. Those paths
do not satisfy the controller's MainPID/containment contract.

### GitHub trust and repository settings

The controller uses unauthenticated GitHub REST requests and a fixed public
HTTPS Git remote. The configured repository must therefore be public; private
repository credentials and token rotation are not part of this design. API
unavailability or public rate limiting is treated as a transient failure, not
permission to deploy without evidence.

Configure `main` with all of these repository rules:

1. Require changes to arrive through a pull request.
2. Enable **Rebase and merge** and disable merge commits and squash merging for
   the deployment path.
3. Require the status check named exactly `offline`, and require the branch to
   be up to date before merging (strict required checks).
4. Require linear history.
5. Disallow force pushes and branch deletion.
6. Apply the rules to administrators and configure no bypass actor or admin
   bypass.
7. Do **not** require signed commits on `main`.

The last point is intentional. GitHub's Rebase and merge operation rewrites the
commits, so the final commits on `main` are not the developer's signed commit
objects. Requiring signed commits on `main` conflicts with this workflow.
Instead, the PR head commit must carry an SSH signature accepted by the
root-owned `allowed_signers` file.

For a normal rebased release, the controller requires all of the following:

- the fetched candidate is the exact current `main` SHA and descends from the
  recorded deployed SHA;
- the newest unambiguous workflow run, ordered by validated
  `run_number`/`run_attempt`/`id`, is a completed successful `push` run for that
  exact SHA, branch, repository, head repository, and workflow path;
- GitHub reports exactly one merged associated PR whose base and head are in
  the same configured repository, whose base is `main`, and whose merge SHA is
  the candidate;
- the fetched PR head is the exact head SHA reported by GitHub, contains one
  accepted SSH signature, and verifies against `/etc/honeybuy-tg/allowed_signers`;
  and
- the signed PR head tree is byte-for-byte the same Git tree as GitHub's
  rewritten candidate.

An unrelated association is ignored, but zero or multiple matching
associations fail closed. Fork PRs, a changed PR head, a different base,
ambiguous workflow ordering, a failed/pending workflow, or a tree mismatch all
block deployment. The controller never treats GitHub array order as trust
evidence.

### Automatic release lifecycle

After a qualifying merge, the timer performs the complete application update:

1. Acquire the root-owned lock and validate paths, symlinks, state namespaces,
   signer, installed control-plane manifest, and any recovery journal.
2. Fetch only the configured public `main` ref into the root-owned bare
   repository and validate the GitHub workflow and rebase evidence above.
3. Compare the five deployment-critical files in the candidate with the
   control-plane manifest: the controller program, bot service, controller
   service, controller timer, and allowed signer.
4. Extract the authenticated tree into
   `/opt/honeybuy-tg/releases/.<sha>.tmp`, run `uv sync --frozen` as
   `honeybuy-build`, seal the tree as root with runtime-readable canonical
   modes, then run an isolated `honeybuy_tg.app` import as `honeybuy-build`,
   write its provenance manifest, and atomically rename only the completed
   tree to `releases/<sha>`.
5. Clone the live SQLite database and dry-run the candidate migration and
   database-only health check without stopping the bot.
6. Re-fetch `main` and revalidate the candidate, installed control plane,
   links, and deployed state immediately before activation.
7. Write the activation journal before stopping the bot. Stop the bot, create
   and validate `/var/backups/honeybuy-tg/honeybuy.sqlite3.<sha>.bak`, migrate
   the live database, and run the database-only candidate health check.
8. Point `previous` at the old release, atomically switch `current`, durably
   record the start boundary, start the bot, and require repeated stable service
   observations and health checks.
9. Write the deployed SHA and a successful receipt, then clear the journal.

The controller, not normal bot startup, owns schema-bearing production release
changes. `honeybuy-tg.service` also runs the database-only health check as
`ExecStartPre`, so an incompatible, corrupt, or wrong-version database prevents
the bot process from starting. The health check never migrates or contacts
Telegram/OpenAI.

Normal updates require no server checkout command:

```sh
# After the PR is rebased into main and the exact main-push `offline` run passes:
sudo systemctl start honeybuy-release-controller.service  # optional; timer also runs it
sudo journalctl -u honeybuy-release-controller.service -n 200 --no-pager
sudo readlink -f /opt/honeybuy-tg/current
sudo cat /var/lib/honeybuy-release-controller/deployed-sha
sudo systemctl is-active honeybuy-tg.service
```

### Exact two-pass legacy bootstrap

The installer is an explicit trust-boundary operation. Run
[`deploy/ubuntu/install.sh`](../deploy/ubuntu/install.sh) as root from a
separate, trusted checkout of the reviewed revision. Do not clone that source
checkout into `/opt/honeybuy-tg`; `/opt/honeybuy-tg` is the release namespace.
The installer creates `honeybuy` and `honeybuy-build`, installs the pinned `uv`,
signer, controller program and units, creates the protected directories, and
creates `/etc/honeybuy-tg/env` if absent.

For an existing legacy/copy-deployed production database, use exactly two
installer passes:

1. **First installer pass:** run `sudo deploy/ubuntu/install.sh`. With no
   coherent immutable baseline, it installs the controller-side control plane,
   writes an exact `bootstrap_pending` manifest, verifies the installed files,
   and keeps the automatic timer disabled. It then intentionally exits nonzero:
   that result is the expected bootstrap boundary, not a request to rerun the
   installer immediately. Configure `/etc/honeybuy-tg/env` if the installer
   created it.
2. **Quiesce and bootstrap:** make an operator backup, stop
   `honeybuy-tg.service`, verify it is inactive, then wait for the controller:
   `sudo systemctl --wait start honeybuy-release-controller.service`. The
   controller
   requires a safe `honeybuy`-owned `0600` SQLite file with the exact schema,
   verifies public `main`, builds the immutable baseline, checks it against the
   existing database, creates `current` and `deployed-sha`, and leaves an exact
   `awaiting_service` bootstrap journal. It neither migrates nor starts the bot.
3. **Second installer pass:** while the bot remains inactive, rerun the same
   installer revision. It validates that exact pending state and byte-compares
   all five control-plane artifacts with the prepared immutable release before
   changing the timer or installed control plane. It then installs the bot unit,
   changes the control-plane manifest to `installed`, and enables the bot
   service and the persistent timer. A mismatch is a hard stop: rerun the
   installer from the exact reviewed candidate revision named in the error.
4. **Start and confirm:** start `honeybuy-tg.service`, confirm its
   `ExecStartPre` and process are healthy, then wait for the next timer run or
   start `honeybuy-release-controller.service` once more. Only an `installed`
   manifest plus a stably healthy active service permits the controller to
   write the bootstrap receipt and clear the bootstrap journal.

The installer intentionally accepts an existing bootstrap journal only at the
exact `awaiting_service` boundary. If bootstrap stops at `intent` or
`release_prepared`, do not rerun the installer and do not delete the journal or
prepared release. Correct the underlying environment while keeping the bot
inactive, then rerun the installed controller so its journal recovery owns the
next transition.

One historical controller used `/var/empty/honeybuy-healthcheck` as that empty
working directory. On hosts where the root-owned `/var/empty` lacks search
permission for `honeybuy`, an exact `release_prepared` recovery may temporarily
add only the other-user search bit to `/var/empty`, run the installed controller
once with `systemctl --wait`, and immediately restore the original mode even if
the run fails. A plain `systemctl start` returns as soon as this `Type=exec`
controller has executed, not when the controller has finished, so do not use
that return as the restore boundary. Before doing so, verify the bot is
inactive, the journal is exactly `release_prepared`, and neither `current` nor
`deployed-sha` exists. The controller must then either
reach `awaiting_service` or safely clear a stale uncommitted candidate before a
new installer revision is used. Never leave the shared system directory more
permissive and never replace this recovery with manual journal or release
deletion.

If the legacy database is not already at the schema expected by the chosen
baseline, stop. Do not ask bootstrap to infer or migrate it. Handle that as an
explicit legacy/emergency migration with an independently verified backup,
then restart the two-pass procedure from a coherent state.

### Recovery, rollback, receipts, and quarantine

Every activation transition is journaled. Normal phases are `prepared`,
`stopped`, `backed_up`, `migrated`, `switched`, `start_requested`, and
`healthy`. Rollback adds two important durable boundaries:

- `rollback_pending` is written before restoring a backup or switching
  `current` back; and
- `rollback_start_requested` is written before starting the previous service.
  Once a service may have accepted writes beyond that boundary, recovery must
  not replay an old database backup.

Before the start boundary, a failure restores the verified backup when needed,
switches back to the previous immutable release, and restarts it. After the
candidate has been started, the controller preserves the migrated live
database, verifies that the previous release can use it, durably quarantines
the failed candidate SHA, switches `current` and the deployed-state record back,
then starts the previous service. The activation journal is cleared only after
the required quarantine record exists exactly and the previous service is
stable. That SHA remains blocked on later timer runs.

A process crash leaves the journal as recovery authority. The next systemd
controller run converges from the recorded phase; it also safely handles a
trusted partial release-staging directory and fixed atomic-write temporary
files. Unknown files, unsafe owners/modes/types, corrupt manifests, mismatched
links or SHAs, invalid backups, and ambiguous recovery evidence fail closed.
`intervention_required` means automatic recovery deliberately stopped. Inspect
the journal, service state, symlinks, deployed SHA, backup, receipt, quarantine,
and controller logs together. Do not delete or rewrite recovery files merely to
make the next timer run proceed.

Receipts are durable success evidence, not a cleanup queue. Quarantine records
are safety state, not disposable logs. Backups are validated SQLite snapshots
associated with the candidate journal record. Retain them until the release and
its rollback window have been reviewed; there is no general operator-facing
backup-retention or arbitrary point-in-time restore command.

### Control-plane changes

The root-owned `0600` control-plane manifest has version 1, phase
`bootstrap_pending` or `installed`, and exact SHA-256 hashes for these candidate
paths:

- `deploy/ubuntu/release_controller.py`;
- `deploy/ubuntu/allowed_signers`;
- `deploy/systemd/honeybuy-tg.service`;
- `deploy/systemd/honeybuy-release-controller.service`; and
- `deploy/systemd/honeybuy-release-controller.timer`.

Automatic activation requires `installed`, verifies the installed files, and
requires the candidate copies to match those hashes. A PR changing any of the
five files is therefore rejected before the bot is stopped, the live database
is migrated, links are switched, or a candidate is quarantined. This is the
manual installer gate: an operator must review the new trust boundary and run
the installer from that exact trusted revision before asking the controller to
activate its application release. The controller never updates itself, its
units, its timer, or its signer.

The allowed-signers file contains public verification material, not a server
private key. It is expected to contain exactly the configured principal and one
key restricted to the Git namespace. Signer rotation is a separate explicit
trust operation; the installer intentionally does not silently replace an
existing signer.

### Legacy/emergency in-place recovery only

After immutable bootstrap, do **not** run `git pull`, `rsync --delete`, or
`uv sync` in `/opt/honeybuy-tg`. Do not manually copy files into `current` or a
SHA release. Those actions invalidate provenance and can destroy rollback
evidence.

An old copy-deployed host may still need `git pull` or `rsync` before its first
bootstrap, and an exceptional recovery may need a manual database restore.
Those are explicitly legacy/emergency procedures, not release steps. Disable
the controller timer, stop the bot, take and verify an off-path backup, preserve
the controller journals and quarantine evidence, and obtain an incident-specific
review before using them. Re-enter normal operation only through the two-pass
installer/bootstrap procedure or a verified immutable state.

## Observability

Process logs use timestamp, level, logger name, and message on standard output,
which systemd captures in journald:

```sh
sudo journalctl -u honeybuy-tg -f
```

Useful Prometheus metric families are:

- `honeybuy_info` and `honeybuy_metrics_exporter_up`;
- `honeybuy_bot_starts_total`;
- `honeybuy_telegram_messages_total{kind,status}`;
- `honeybuy_shopping_actions_total{action,source}`;
- `honeybuy_ai_requests_total{operation,status}`;
- `honeybuy_ai_request_seconds{operation}`;
- `honeybuy_voice_rejections_total{reason}`; and
- `honeybuy_voice_transcript_chars`.

Metrics contain no chat or user labels, avoiding unbounded per-user cardinality.
They also do not expose database health, migration state, recipe-import counts,
or queue depth. Combine them with logs and selected `events` rows when
diagnosing a request.

## Post-Deploy Smoke Test

At minimum, verify:

- `/whoami`, private access, group `/authorize`, and an unauthorized case;
- after enabling `@BotFather` `/setinline`, type `@bot_username milk` from an
  unrelated chat, confirm that the private list and only currently eligible
  authorized groups appear, then send one result;
- before tapping `Confirm add`, verify the destination list is unchanged and
  the public card reveals neither its destination nor existing list contents;
- tap `Confirm add`, verify exactly one literal `milk` item appears in the
  selected list, and verify a repeated tap cannot add it again;
- repeat with `milk and eggs` to confirm it is stored as one literal item, then
  spot-check expiry or loss of group capability before confirmation;
- `/add`, `/list`, `/remove`, `/bought`, and `/clear_bought`;
- `/shop` and repeated/stale `Got` callbacks;
- `mention` and `all` natural-text modes plus a mention-only reply;
- a direct voice note, replied voice reanalysis, and bare-item confirmation;
- recipe learning from both URL and paste, reuse, alias, delete, cancel, and
  overwrite;
- `/list`, `/shop`, `/remove`, and `/bought` against legacy rows after identity
  schema or normalization changes; and
- metrics reachability when enabled.

The detailed maintained checklist is at the end of
[`SCENARIOS.md`](../SCENARIOS.md).

## Known Operational Gaps

The release controller creates and validates deployment-scoped database backups
and can roll back within its journaled release transaction. The repository also
has a database-only CLI health check used by deployment and `ExecStartPre`.
There is still no general operator-facing point-in-time restore command,
scheduled backup policy, HTTP health endpoint, log-rotation policy, general
state-retention task, voice/recipe confirmation reaper, inline-intent background
reaper, or multi-instance deployment mechanism. Inline intents do have logical
expiry and opportunistic cleanup; they do not require a worker for correctness.
These gaps are suitable places to start when moving beyond a small private
installation.
