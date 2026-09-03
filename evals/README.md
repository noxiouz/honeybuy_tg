# Honeybuy Text Routing Evals

The normal test suite is fully offline. It validates the corpus shape, prompt
request contracts, routing regressions, and Telegram integration with fake
clients.

The live comparison runner is a separate operator tool for model and prompt
changes. It never runs in CI and refuses to use the production `OPENAI_API_KEY`.
It requires all of the following before any OpenAI call is made:

- `--allow-live-openai`
- at least one explicit `--model`
- `HONEYBUY_EVAL_OPENAI_API_KEY`

Example:

```sh
HONEYBUY_EVAL_OPENAI_API_KEY=... \
  python -m evals.live_compare \
  --allow-live-openai \
  --model gpt-5.4-mini \
  --output evals/results/text-routing.json
```

The runner strips blank model names and prompt override files before client
construction. It also ignores ambient OpenAI environment such as
`OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, and
`OPENAI_WEBHOOK_SECRET` while constructing the evaluation client, so the
dedicated eval key uses the default OpenAI endpoint.

The runner uses production prompt specs and strict response schemas, mirrors the
text routing order, and writes detailed JSON only when `--output` is provided.
Reports include `HEAD`, a dirty-worktree flag, status lines, and a diff hash so
results are tied to the exact local change under evaluation. Prompt override
files can be compared without mutating production prompts:

```sh
HONEYBUY_EVAL_OPENAI_API_KEY=... \
  python -m evals.live_compare \
  --allow-live-openai \
  --model gpt-5.4-mini \
  --model gpt-5.4-mini \
  --shopping-prompt /tmp/shopping-prompt.txt \
  --recipe-prompt /tmp/recipe-prompt.txt
```

Prompt overrides are candidate-only. The first model entry always uses
production prompts as the baseline; the second and later model entries use any
override files. To compare a prompt change on the same model, pass the same
`--model` twice. A run with overrides and fewer than two model entries is
rejected before client construction.

## Corpus Labels

Cases live in `evals/cases/text_routing.v1.jsonl`. Each record has one expected
route: `shopping`, `recipe`, `unhandled`, or `ambiguous`.

Use `accepted_items` only for shopping actions where several normalized outputs
are acceptable. Item grading is an unordered normalized multiset, so order does
not matter but duplicate items do. Use `accepted_recipe_names` only for explicit
auditable alternatives such as Russian inflected recipe names; the canonical
`recipe_name` remains the primary label. Ambiguous cases must not guess items or
recipe names; they are graded operationally as `unknown`/`unhandled`.

When adding a case, include stable tags for the behavior under test, mark only
release-blocking regressions as `critical`, and run:

```sh
uv run pytest -q tests/test_eval_corpus.py
```

## Release Gates

For each live model run:

- schema-valid completed model responses: 100%
- critical cases: 100%
- shopping-to-recipe false positives: 0
- route/action: at least 98%
- accepted item variants: at least 98%
- recipe names: at least 98%
- overall graded cases: 100%
- consistency across repetitions: at least 99%
- infrastructure errors: at most 2%
- release qualification: at least 72 cases and at least 3 repetitions

For multiple models, the first model is the baseline. Candidate reports flag
p95 latency above 1.5x baseline and output tokens above 1.25x baseline. These
are deterministic gates over a maintained corpus, not statistical proof of
quality. Smaller custom corpus runs and fewer than 3 repetitions are useful
smoke checks, but they are reported as not release-qualified, cannot pass the
release gate, and make the CLI exit nonzero.

## Current Limits

The corpus covers pre-confirmation text intent classification only. It does not
call Telegram, SQLite, recipe URLs, voice transcription, or production data.
Voice cases use transcript text, so they evaluate downstream command
understanding, not audio recognition or Telegram confirmation behavior.
Dispatcher tests own the Telegram confirmation contract.
