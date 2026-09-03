# Final mechanical completeness review

## Reviewed scope

This is a fresh read-only audit of the post-council version of
`ideas/COMPLEXITY.md`. It was checked against:

- the generated ID domain and `ideas/CATALOG.md`;
- all six source idea files;
- all five primary `ideas/council/*-classification.md` files;
- the approved blocking findings and adjudicated caveats in the four other
  council reviews.

The reviewed `ideas/COMPLEXITY.md` SHA-256 is
`ba6a09f9de298bdc805e11a6340ea4a4254f5295d50b4878ad1b1f830fbb3d37`.
Only this report was writable. No source, catalog, classification, or result
file was edited, staged, committed, pushed, or used for a live call.

## Findings

No completeness or accidental-drift defects found.

## Approved-change audit

Relative to the five primary classifications, the result contains exactly 15
complexity changes:

| Finding basis | Exact change set |
| --- | --- |
| Trace and resilience calibration (`CAL-001..003`, `ADR-MF-001..002`) | `AI-026 L->M`, `PT-017 L->M`, `PT-018 L->M`, `RO-011 L->M` |
| Offline/eval scope (`CAL-004`) | `AI-030 L->M`, `RA-010 L->M`, `RA-012 L->M` |
| Optional extension versus intrinsic core (`CAL-005`, `ADR-MF-003..004`, `PTCR-004`) | `AI-012 L->M`, `CI-027 XL->M`, `CI-029 L->M`, `CI-030 XL->M`, `PT-023 L->M` |
| Sensitive-data calibration (`CAL-006`, `ADR-MF-005`) | `AI-022 XL->L` |
| Product/runtime calibration (`PTCR-003`, `ADR-MF-006`) | `RA-004 L->M`, `RO-029 L->M` |

There are exactly 16 primary-category deltas:

- `TAX-001..005`: `PT-024` to Integrations & portability, `PT-011` and
  `PT-028` to Shopping & list UX, `AI-022` to Recipes & meal planning, and
  `CI-008` to Collaboration & access;
- `TAX-006`: the ten former AI, language & voice rows move together to the
  renamed Language, accessibility & multimodal interaction category;
- release adjudication of `PTCR-F01`: `PT-023` moves from its primary
  Integrations & portability classification to Language, accessibility &
  multimodal interaction. Its approved local-decoder core remains M.

No other category or complexity changed. In particular, the adjudicated map
keeps `PT-028` and `RO-024` at XL; it does not accidentally apply the conflicting
CAL downgrade suggestions. `CI-030` is M under the accepted static-snapshot
core despite the earlier product advisory about a separate live-view scope.
The release placement of `PT-023` supersedes only its intermediate Shopping
placement and accounts exactly for the one-row category-total change.

Rationale text differs from the primary classification only for these 21
reviewed IDs:

`AI-012`, `AI-022`, `AI-026`, `AI-030`, `CI-008`, `CI-027`, `CI-029`,
`CI-030`, `PT-011`, `PT-017`, `PT-018`, `PT-023`, `PT-024`, `PT-025`,
`PT-028`, `RA-004`, `RA-010`, `RA-012`, `RO-011`, `RO-024`, `RO-029`.

The two rationale-only edits, `PT-025` and `RO-024`, are the explicit Telegram
authorization and standalone-sentinel caveats from the reviews. The final
`PT-025` rationale now binds correctness/apply to the requester-bound explicit
confirmation callback, treats `ChosenInlineResult` only as optional telemetry,
and still leaves the idea in Integrations & portability at L. There is no title,
source, category, complexity, or confidence drift from that correction, nor any
other unapproved drift in the 176 rows.

## Completeness and arithmetic proof

| Invariant | Result |
| --- | --- |
| Expected ID domain | `AI/CI/EO/PT/RO-001..030` plus `RA-001..026`: 176 |
| Primary classification rows | AI 35, collaboration 36, engineering 35, product 34, reliability 36; total 176 |
| Result coverage | 176 rows, 176 unique IDs, exact set parity with the catalog and source cards |
| Titles | 176/176 exact matches with both catalog and source card |
| Sources | 176/176 row links match the catalog; all 179 relative links in the document resolve |
| Primary category | Exactly one per ID; exactly 11 section/category values |
| Ordering | Every section is sorted XS -> S -> M -> L -> XL, then fixed-width ID |
| Required row data | Every row has rationale, supported confidence, and source |

Category arithmetic was derived from the 176 detailed rows and exactly matches
the summary:

| Category | XS | S | M | L | XL | Total |
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
| **Total** | **1** | **6** | **74** | **87** | **8** | **176** |

The global table has the same `XS 1 / S 6 / M 74 / L 87 / XL 8` counts.
Half-up, one-decimal percentages are `0.6% / 3.4% / 42.0% / 49.4% / 4.5%`.
They sum to `99.9%` solely because the five rows are rounded independently; the
document states this and reports the exact total as 176 and 100%.

## Neutrality review

The document remains a complexity-only pre-ranking map. Its mentions of value,
portfolio ranking, and candidate splitting describe a future separate stage or
warn against double-counting. They do not assign value scores, select or reject
ideas, establish a roadmap, release, calendar, or implementation commitment.
Technical phrases such as depends on, should precede, and should follow explain
prerequisites used to calibrate complexity, not product priority. Section and
row ordering carry only the declared category and XS-to-XL semantics.

## Commands and results

The read-only inline structural validator used `python3 - <<'PY'` and asserted
the explicit approved override dictionaries in addition to parsing every
Markdown table. Its result, with the verified ID dictionaries condensed to
their cardinalities below, was:

```text
PASS classification rows: {'ai-classification.md': 35, 'collaboration-classification.md': 36, 'engineering-classification.md': 35, 'product-classification.md': 34, 'reliability-classification.md': 36} total 176
PASS 176 unique IDs; exact parity with generated domain, CATALOG, and six source files
PASS exact titles and source links: 176 rows; 179 relative links resolve
PASS approved-only level overrides: 15/15, no extras
PASS approved-only category overrides: 16/16, no extras
PASS approved-only rationale edits: 21/21, no extras
PASS confidence drift: none
PASS PT-025: Integrations & portability / L; explicit callback is correctness/apply, ChosenInlineResult is optional telemetry
PASS one primary category per ID; 11 sections; every section sorted XS<S<M<L<XL then ID
PASS category/global arithmetic: {'XS': 1, 'S': 6, 'M': 74, 'L': 87, 'XL': 8} total 176
PASS percentages ROUND_HALF_UP: {'XS': Decimal('0.6'), 'S': Decimal('3.4'), 'M': Decimal('42.0'), 'L': Decimal('49.4'), 'XL': Decimal('4.5')} sum 99.9
```

Independent row-count commands:

```console
$ rg '^\| (XS|S|M|L|XL) \| (AI|CI|EO|PT|RO|RA)-[0-9]{3} \|' ideas/COMPLEXITY.md | wc -l
176
$ rg '^\| (XS|S|M|L|XL) \| (AI|CI|EO|PT|RO|RA)-[0-9]{3} \|' ideas/COMPLEXITY.md | awk -F'|' '{gsub(/ /, "", $3); print $3}' | sort -u | wc -l
176
```

Whitespace and staging checks:

```console
$ git diff --no-index --check /dev/null ideas/COMPLEXITY.md
# no diagnostics; exit 1 only because the untracked file differs from /dev/null
$ git diff --cached --name-only | wc -l
0
```

No runtime tests or Ruff were run because this is a read-only Markdown
consistency review and no application or test source changed.

FINAL REVIEW: APPROVE
