# Which plans have actually been executed

Audited 2026-08-08 against the code, not against memory. Every plan in this
directory said `draft` regardless of whether it had shipped, so there was no way
to tell what was still owed. This file is the index; each plan's own `Status:`
line now agrees with it.

The rule for reading this: a step counts as executed when the code does the
thing, not when a commit mentions it. Two entries below were found to be
half-done that way — `playback-and-grouping` §1 had a `remux` module that
nothing on the live path called, and `series-identity` §2 resolved titles but
still let the indexer decide identity.

| plan | state |
|---|---|
| [acquisition-home](2026-08-08-acquisition-home.md) | **done** — all 13 steps |
| [debug-cards-and-promotion](2026-08-08-debug-cards-and-promotion.md) | **done** — all three fixes; they are the first three rows of the CLAUDE.md table |
| [player-and-coverage](2026-08-08-player-and-coverage.md) | **mostly done** — §1–5 shipped. §6 per-category refresh passes is **open** |
| [file-page-becomes-the-series-page](2026-08-08-file-page-becomes-the-series-page.md) | **mostly done** — §1–4 shipped. §5, the same episode list inside the download sheet, is **open** |
| [series-identity](2026-08-08-series-identity.md) | **done**, one step superseded — see below |
| [playback-and-grouping](2026-08-08-playback-and-grouping.md) | **done** — §1 and §2a were the last two and shipped 2026-08-08 |

## Superseded, deliberately

**`series-identity` §1, the `catalog_series` table.** The plan proposed a second
table with works hanging off it. What was built instead merges works by provider
id, and the live data says it works: `BLACK TORCH` holds 17 releases of one
episode on one card, `Frieren` holds its whole run. A table would be a second
way to express something already expressed. Recorded here rather than silently
skipped, per CLAUDE.md.

## Still open

Neither is breakage; both are improvements with a measurement behind them.

- **Per-category refresh passes** (`player-and-coverage` §6). One empty query
  returns ~366 rows across four indexers. Running the pass once per category
  multiplies coverage for the same wall-clock, because indexers return a front
  page *per category*. The measurement that motivates it — a ten-hour window
  that cannot be paged — is in that plan's §2.
- **The episode list inside the download sheet** (`file-page` §5). The component
  exists and is used on `/file/{id}`; the sheet still shows releases only.

## Known open bugs, unrelated to any plan

- `_restate_works` is an N+1 with row locks.
- Content-Length is promised before the read, so a short read truncates the body
  under a length the client was already told.
- The picker does not prefer a batch or a complete season.
- qBittorrent's password is still the literal string `YOURPASSWORD`.
