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

## Settled: how adult content is kept off the wall

Decided 2026-08-08 after measuring all three providers. **The provider's own
flag, and nothing else.** AniList `isAdult`, TMDB `adult`; stored on the work
and excluded once in `rails._base()`.

What that catches and what it cannot, measured rather than assumed:

| | |
|---|---|
| caught | 14 works, every adult-anime title that was on the home page |
| missed | erotic drama — `Sex Trip`, `High (School) On Sex` |
| correctly left alone | `HK Hentai Kamen`, the 2013 comedy *HK: Forbidden Super Hero* |

The miss is a source limit, not a bug. **TVmaze has no adult field at all** —
`High (School) On Sex` returns `type: Scripted`, genres `Drama, Comedy,
Romance`. And TMDB answers `adult=False` for all three titles above, including
*Sex Trip*: that flag is reserved for pornography, not for an R rating.

A keyword rule was considered and refused. It would catch the two, and it would
also hide *HK Hentai Kamen*, which is a mainstream film — and every future title
with an unlucky name. The provider flag has no false positives, which is the
trade this catalogue makes everywhere else: a wrong split is cosmetic, a wrong
merge or a wrongly hidden film is not.

If this needs revisiting, the option not taken is a per-work blocklist the user
sets from the card — nothing guessed, nothing to maintain.

## 2026-08-08, later: the pack sweep and the Watch Now regression

Both shipped. See [HANDOFF.md](../HANDOFF.md) for what is still open and why.

- **Pack sweep** (`eb1a542`). Opening a series card searches `<title> batch` and
  `<title> complete`, once per show per day, in the background. The catalogue
  could not reach past the indexers' one-day front page, so a card held many
  encodings of a few recent episodes: ONE PIECE, 206 releases covering 82
  distinct episodes of 1172. It now carries packs from episode 1 to 915.
- **Watch Now** (`79bb25e`). The live remux keyed on the completed-prefix
  length, which changes every second on a growing file — so every request
  missed, every miss started a gigabyte-scale ffmpeg, and the remux that
  finished was never served. One ffmpeg per download now, following the file.
- **The picker** prefers the smallest complete unit, a run from episode 1 ahead
  of size. Two things this surfaced: `One Piece 2023` (Netflix's live-action
  show) fuzzy-matched the 1999 anime because anitopy leaves a trailing year in
  the title; and "complete" alone picked `Episodes 838-875`, a chunk out of the
  middle, because uploaders tag any multi-episode release a batch.

## Known open bugs, unrelated to any plan

- `_restate_works` is an N+1 with row locks.
- Content-Length is promised before the read, so a short read truncates the body
  under a length the client was already told.
- The picker does not prefer a batch or a complete season.
- qBittorrent's password is still the literal string `YOURPASSWORD`.
- The library player shows a bare spinner for the minutes a large remux takes —
  reported as "remux is failing", when it was silent rather than broken.
- A film watched while downloading is remuxed again when opened from the
  library, under a different cache key.
