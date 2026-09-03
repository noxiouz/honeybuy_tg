# Ingestion And Recipes

## Scope

Honeybuy does not currently import arbitrary files or structured datasets.
There is no Telegram document/photo handler, CSV or JSON format, batch import
job, import-run table, or export counterpart.

Implemented external-input paths are:

| Input | Entry point | Result |
| --- | --- | --- |
| Slash command | Telegram command handler | One shopping operation or management action |
| Natural text | `F.text` handler, subject to parse mode | Shopping action, reply-context action, or recipe command |
| Voice note | `F.voice` or reply reanalysis | Transcription followed by the same recipe/shopping pipeline |
| Recipe URL | Natural text or transcribed voice | Fetch visible page text, extract ingredients, save recipe |
| Pasted recipe | Multiline natural text | Extract ingredients from the pasted body, save recipe |
| SQLite database copy | Operator action outside the bot | Move the complete state file, then run schema migration |

The rest of this document focuses on recipe ingestion, the closest current
equivalent to an import feature.

## Reaching The Recipe Pipeline

There is no `/import` or `/save_recipe` slash command. A recipe-learning request
is ordinary natural text or a voice transcript handled by `apply_recipe_command`
in [`telegram_bot.py`](../src/honeybuy_tg/telegram_bot.py).

For typed text, the chat's natural-text mode still applies. With the default
`TEXT_PARSE_MODE=mention`, a request must mention the bot, for example:

```text
@HoneyBuyBot выучи солянку https://example.com/solyanka
```

In `all` mode, the mention can be omitted. A request beginning with `/` is
reserved for slash-command routing and does not reach the generic recipe text
pipeline.

Local recipe parsers run before the optional AI command parser. They recognize
common Russian and English learn, reuse, and alias phrases. The AI command
parser is considered only when no local form matched and the message contains a
recipe-related marker.

## URL Learning

A deterministic URL request has a learn verb followed by a recipe name and the
first HTTP(S) URL. Supported leading verbs include:

```text
выучи солянку https://example.com/solyanka
запомни рецепт суп https://example.com/soup
learn recipe pancakes https://example.com/pancakes
save pancakes https://example.com/pancakes
teach recipe pancakes https://example.com/pancakes
```

The parser strips trailing `.`, `,`, or `)` from the captured URL. The fetcher
then applies these rules:

- only `http` and `https` schemes are accepted;
- the request timeout defaults to 15 seconds;
- the response content type must contain `html` or `text`;
- at most 1,500,000 response bytes are read;
- decoding uses the response charset or UTF-8 with replacement;
- text inside `script`, `style`, `noscript`, and `svg` elements is omitted.

The resulting visible text is passed to the recipe extractor. Only its first
20,000 characters are included in the OpenAI input.

### URL Trust Boundary

The code validates the scheme but does not verify that the destination is
public. It does not block loopback, private-network, link-local, or
redirect-resolved internal addresses. The feature is safe only under the
project's current trusted-user access assumption. Before exposing Honeybuy to
untrusted users, add URL resolution and redirect checks or move fetching behind
a restricted egress service.

## Pasted Recipe Learning

A pasted request must satisfy all of these deterministic conditions:

1. The first nonblank line starts with a supported learn verb.
2. The stripped message is at least 50 characters and contains a newline.
3. It contains a recipe marker such as `ingredients`, `состав`, `method`, or
   `приготовление`, or at least two nonblank lines start with `-` or `•`.
4. A recipe name is present on the command line or can be inferred from a valid
   second nonblank title line.

If the command line contains no name, the second nonblank line may supply one
when it is not an ingredient heading and is between 1 and 80 characters.

```text
Save recipe pancakes
Ingredients:
- flour 200 g
- milk 300 ml
Method:
Mix and cook.
```

The complete stripped Telegram text, including the leading learn command, is
used as extractor input. There is no separate hard limit on the pasted message
inside Honeybuy, although Telegram has its own message limits and the extractor
still receives only the first 20,000 characters.

### Paste Takes Precedence Over URL

If a message qualifies as pasted recipe text, any URL inside the body is not
fetched. The body itself is the source and the saved recipe has
`source_url = NULL`. The same rule protects the original body when the AI
command parser identifies a learn action but intentionally does not echo a long
`recipe_text` field.

## AI Extraction Contract

Recipe learning requires `OPENAI_API_KEY`. The extractor asks the configured
parse model for grocery ingredients only, preserving useful quantities and
omitting navigation, equipment, nutrition, ratings, and recommendations.

The response must validate as this logical shape:

```json
{
  "name": "pancakes",
  "ingredients": [
    {"name": "flour", "quantity": "200 g"},
    {"name": "milk", "quantity": "300 ml"}
  ]
}
```

Validation is strict about JSON field types. The name must be nonempty and at
least one ingredient is required. Whitespace is collapsed; blank ingredient
names are removed at the service boundary. Unknown response fields are rejected
by the strict schema and lead to the existing recipe-learning failure response.

The extracted name is authoritative when nonblank; the user's requested name
is the fallback. Thus a model can change the stored display and lookup name.
The extractor request allows up to 2,500 output tokens.

Before persistence, the service obtains a canonical identity for every
ingredient. It uses a nonexpired global identity cache, then the optional AI
normalizer, then a local base-name fallback. Normalizer failure does not fail the
recipe save.

## Save And Conflict Semantics

One recipe save is atomic:

1. clean the recipe name and ingredients;
2. acquire a SQLite `BEGIN IMMEDIATE` transaction;
3. reject a conflicting alias or existing recipe when overwrite is not allowed;
4. insert or update the recipe header;
5. replace the ordered ingredient rows; and
6. commit.

Exact recipe-name uniqueness is case-insensitive through application
normalization of whitespace and casing, scoped to a chat. The same source URL
may be saved under several names because URLs are not unique and do not provide
idempotency.

Recipe lookup first tries an exact normalized name, then an exact alias, then a
small Russian-ending heuristic that removes one final vowel for comparison.
The database uniqueness rule covers exact normalized names only. Avoid creating
several names that differ only under this loose heuristic because recall then
selects the earliest match.

### Existing Recipe Confirmation

Learning an exact existing name does not overwrite immediately. Extraction has
already completed, and the bot persists a pending confirmation containing:

- the proposed name, source URL, and extracted ingredients;
- the current target recipe ID and normalized name; and
- a SHA-256 digest of the target recipe and ordered ingredient state.

Only the requester can confirm or cancel. Confirmation is atomically claimed so
a successful button press is single-use. Under the recipe write lock, the save
checks the ID, normalized name, and digest again. A changed, deleted, or
replaced target is rejected as stale. A new alias conflict is also rejected.

An overwrite keeps the existing recipe row ID, creator, creation time, and
aliases. It updates display name, source URL, and update time, then deletes and
reinserts the ingredients. Alias-only changes do not alter the digest and do
not by themselves stale a pending overwrite.

Pending confirmations do not expire by age. An unexpected process failure
after a confirmation is claimed can leave it in an intermediate non-pending
status; there is no recovery worker.

## Recipe Management And Reuse

Saved recipes are managed with:

```text
/recipes
/recipe_alias pancakes = breakfast
/delete_recipe pancakes
```

Natural alias forms also accept separators such as `as`, `to`, and `как`.
Aliases are unique within a chat and cannot collide with another recipe name or
alias. Re-adding the same alias to the same recipe is idempotent.

Deletion intentionally accepts only the saved recipe's exact normalized name,
not an alias or loose lookup variant. SQLite foreign keys cascade deletion to
ingredients and aliases.

Reuse requests include deterministic forms such as:

```text
добавь все для солянки
купи все для солянки
добавь ингредиенты для солянки
```

Looser forms such as `купи на солянку` can use the AI recipe-command parser.
Recall accepts a saved name, alias, or loose Russian match.

For each ingredient, the quantity is appended to the shopping-item display name
as `name, quantity`. The service skips an ingredient when the active list
already contains its full normalized name, its base name before the comma, or
the same canonical identity. This makes `tomato paste` cover `tomato paste,
60 g` and permits cross-language matches when normalization identifies them as
the same grocery product.

The reuse operation is not one database transaction. Each missing ingredient
is inserted and committed separately, so an exception or process stop can leave
a partially added recipe. Concurrent reuse requests can also race because
`shopping_items` has no active-name uniqueness constraint.

## Errors And Diagnostics

Learning fails with user guidance when the OpenAI key is absent. Fetch,
extraction, schema-validation, alias-conflict, and persistence errors are logged;
most are presented to the user as a generic request to try a readable recipe
link or paste the recipe text.

Selected learning paths write diagnostic rows to `events`: a first-time save
records a compact success summary, while a caught learning failure records its
error. Duplicate-confirmation completion is not logged in the same uniform way.
Where present, `raw_text` contains the complete user input, which means pasted
recipe bodies remain in SQLite event history even though the recipe tables
retain only the extracted name, optional source URL, and ingredients. There is
currently no event retention policy.

The Prometheus integration records the underlying AI operation and generic
Telegram message status but has no recipe/import-specific progress or count
metric.

## Unsupported Import Work

Do not assume any of the following exists:

- Telegram document or photo ingestion;
- a recipe file schema;
- CSV, JSON, archive, or third-party recipe export support;
- bulk conflict resolution or all-or-nothing multi-recipe transactions;
- import checkpoints, resume, dry-run, or provenance hashes;
- database merge between chats or installations.

Copying a complete SQLite database is an operator migration, not a semantic
import. Stop the writer, copy the database as a unit, run
`python -m honeybuy_tg migrate`, and verify it as described in
[Operations And Testing](operations-and-testing.md).

Photo-based shopping-list input is recorded as deferred work in
[`PLAN.md`](../PLAN.md).
