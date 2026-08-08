# Plan — one card per series, the way Netflix does it

Status: draft
Date: 2026-08-08

The ask: anime episodes should be one series, not a card per episode. The
research says the current approach cannot get there by improving, because it is
grouping on the wrong thing.

---

## 1. What the catalogue actually looks like

Measured on the live catalogue (667 releases, 436 works):

```
anime works                          111
anime works with exactly ONE release  71   (64%)
title-prefix collisions               21 groups covering 47 works
```

Two thirds of anime cards are a single episode. And the splits are not what the
previous plan assumed:

```
'youjo senki'    -> Youjo Senki 幼女戦記        (2 releases)
                    Saga of Tanya the Evil      (1)
                    Youjo Senki 幼女戦記 Movie   (2)
'ore monogatari' -> Ore Monogatari NCOP NCED    (3)
                    My Love Story!!             (3)
'fruits basket'  -> Fruits Basket               (7)
                    Fruits Basket The Final Season (1)
'the ribbon'     -> THE RIBBON HERO             (5)
                    THE RIBBON HERO 2026        (3)
                    THE RIBBON HERO Movie       (1)
```

Romaji versus English. Native script versus romaji. Season names. Year suffixes.
Type suffixes. **No amount of episode-marker stripping merges *Youjo Senki* with
*Saga of Tanya the Evil*** — they share no characters. The previous plan's §2a
was solving the smallest of these.

### 1.1 Uploaders do not solve it for us

The theory was that Nyaa uploaders post whole series. Measured:

```
releases carrying an episode RANGE (a batch)   18 / 193
keyword hits: season 30 · BD 17 · batch 8 · movie 7
```

Batches exist but are the exception; roughly 90% of Nyaa anime is posted one
episode at a time. So the bundling has to be ours.

---

## 2. Identity comes from a metadata provider, not from a title

This is the whole plan. Verified against the live AniList API:

```
Youjo Senki     -> 21613    Saga of Tanya the Evil  -> 21613    SAME
Ore Monogatari  -> 20946    My Love Story!!         -> 20946    SAME
Kimi no Na wa   -> 97962    Your Name               -> 97962    SAME
Jujutsu Kaisen  -> 113415   JUJUTSU KAISEN          -> 113415   SAME
```

AniList's search already knows that romaji, English, native and abbreviated
names are one work. That is exactly the problem we have been trying to solve
with string cleaning, and it is already solved by someone whose job it is.

**So a release's identity is the provider id it resolves to, and the parsed
title is only ever a search term.**

```
  now                              proposed
  release ──parse──> title         release ──parse──> title ──AniList──> 21613
           group by title string             group by provider id
  "Youjo Senki"     -> card A       "Youjo Senki"           ┐
  "Saga of Tanya…"  -> card B       "Saga of Tanya the Evil"├─> series 21613
  "Youjo Senki 幼女" -> card C       "Youjo Senki 幼女戦記"    ┘
```

### 2.1 The model

`catalog_work` stops being "a title we saw" and becomes an **episode-or-film**,
hanging off a new `catalog_series`:

```
catalog_series          one row per show or film, keyed on the provider
  id
  provider, provider_id            -- anilist | tmdb, unique together
  kind                             -- anime | movie | series
  title, native_title, year, format  -- TV | MOVIE | OVA | ONA | SPECIAL
  poster_url, backdrop_url, overview, genres, score
  episode_count                    -- from the provider, for "3 of 24"

catalog_release
  series_id -> catalog_series.id   -- resolved once at ingest
  season, episode, episode_end     -- already stored
```

`format` is what separates a film from a series without a fifth pill: AniList
labels *One Piece Film: Red* `MOVIE` and the weekly show `TV`, so the existing
Anime rail can group by it rather than the wall gaining another filter.

### 2.2 Resolution is cached and cheap

One AniList lookup per **distinct parsed title**, not per release — 193 anime
releases in the catalogue reduce to well under a hundred lookups, once, then
cached in a `title_resolution` table keyed on the normalised title. New releases
of a known show cost nothing. AniList allows 90 requests a minute; a first full
resolve is about two minutes of background work.

Unresolvable titles keep today's behaviour: their own card, grouped by title.
That is the current experience, so nothing regresses.

---

## 3. What the screen becomes

```
  Wall            One Piece            [1,172 episodes · 8 available]
                  Fruits Basket        [63 episodes · 7 available]

  Series sheet    Fruits Basket
                  ├ Season 1   (26 episodes, 5 available)
                  ├ Season 2   (25, 0 available)
                  └ The Final Season (13, 2 available)

  Episode list    Only episodes with a grabbable release
                  ▸ Batches first — "Episodes 1–24 · BD 1080p"
                  ▸ then singles, newest first

  Releases        the existing three-choice picker, unchanged
```

Three rules that the data forces:

- **Only episodes with a release are listed.** AniList says One Piece has 1,172
  episodes; the catalogue holds 8. Rendering 1,172 rows, 1,164 of them dead, is
  worse than the current card.
- **Batches are not expanded.** You cannot download episode 743 alone out of a
  `741-746` torrent, so a batch is one row and it *is* the scope.
- **A single-episode series skips the episode level** and opens straight at
  releases.

Seasons come from AniList `relations` where they exist, and otherwise each
season is its own series card — which is what Netflix does for anime anyway.

---

## 4. Two bugs this exposes, both currently live

**Enrichment renames the card but not its identity.** `enrich_work` sets
`display_title` to the provider's title and leaves `normalised_title` as the
parsed one. So a work displays as *Saga of Tanya the Evil* while still grouping
as `youjo senki`, and the next *Saga of Tanya* release makes a second card that
looks identical to the first. Under §2 this disappears, because identity stops
being the title at all.

**Ghost works.** Several cards show `release_count = 0` — `JUJUTSU KAISEN [0]`
beside `Jujutsu Kaisen [2]`, `Fruits Basket [0]` beside `Fruits Basket [7]`.
`_restate_works` zeroes them and `rails._base()` filters them from the wall, but
they still hold the unique key, so the row they were meant to merge with cannot
take their title. They should be deleted once empty.

---

## 5. Still open from the previous plan, unchanged

**There are still two players.** The screenshot shows `/watching/` playing real
video at 2:13/24:29 through the browser's own controls — no Miru chrome, no
subtitles, no quality menu, because it is a bare `<video>`. The engineering
review's C1 finding said `Player`'s props are library-file shaped and have to be
narrowed before the routes can share it; that is the prerequisite, and it has
not been done.

---

## 6. Order

| # | step | why |
|---|---|---|
| 1 | `catalog_series` + Alembic | overdue already; §2 cannot land without it |
| 2 | title → provider id resolution, cached | the load-bearing piece |
| 3 | backfill the 667 existing releases | otherwise only new arrivals group |
| 4 | delete ghost works | small, and it unblocks merges |
| 5 | series card + episode sheet | the visible change |
| 6 | `format` splits films from series | replaces the fifth pill idea |
| 7 | shared player | independent, and still owed |

## 7. Not doing

- **A fifth filter pill.** `format` from AniList separates films inside the
  anime rail; another pill would not fit at 375px anyway.
- **Expanding batches into per-episode rows.** They are not separable.
- **Trusting uploaders to post complete series.** Measured at 18 of 193.
