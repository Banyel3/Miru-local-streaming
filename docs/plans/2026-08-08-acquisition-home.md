# Plan — the acquisition home screen

Status: draft for review
Date: 2026-08-08

The ask: make the home screen a browse wall instead of the library list. Rows of
things to watch, pulled from the source sites, split by Anime / Movie / Series,
paging onward as you scroll, and nothing downloaded until a card is acted on.

Everything in §1 was measured against the live Prowlarr on the PC
(`100.67.44.13:9696`) on 2026-08-08, not assumed. Several of the measurements
contradict the obvious design, so they come first.

---

## 1. What the source actually gives us

**Browsing without a query works.** An empty `query=` with `type=search` returns
results, which is what makes a browse wall possible at all:

| request | results | time |
|---|---|---|
| no category filter | 301 | 2.7 s |
| `categories=5070` (Anime) | 131 | 3.8 s |
| `categories=2000` (Movies) | 236 | 4.1 s |
| `categories=5000` (TV) | 146 | 3.9 s |

Three indexers are configured: Nyaa.si (anime), The Pirate Bay (general), YTS
(movies, 720p/1080p/2160p x264 only).

**There is no pagination.** `offset=100` returns zero rows and `limit` is
ignored. What comes back is the indexers' current front page and nothing
behind it:

```
offset=0   -> 131 results
offset=100 -> 0 results
offset=200 -> 0 results
```

This is the fact that reshapes the whole feature. A single refresh can only ever
see roughly 300 items. Infinite scroll over a live indexer query is not
something these sources can support.

**The category filter leaks badly.** Nyaa tags every one of its anime releases
with *both* `5070 TV/Anime` and `2020 Movies/Other`, so all 130 of its anime
results come back inside a request for Movies:

```
cats=2000 -> Nyaa.si:  130 results, {'5070/TV/Anime': 130, '2020/Movies/Other': 130}
             YTS:      101 results, {'2040/Movies/HD': 101}
             TPB:        4 results, {'2040/Movies/HD': 4}
```

Passing the user's chosen filter straight through to Prowlarr would put One Piece
batches in the Movies row. Classification has to happen on our side.

**Metadata identifiers are mostly absent.** Of 235 results, 102 carry an
`imdbId` — effectively YTS alone — and `tmdbId`, `tvdbId` and `tvMazeId` are
empty on every single row. Anime arrives with no identifier of any kind.

**Releases are not works.** The top of the anime list by seeders:

```
303s  [RLSP] One Piece 744-746 [BD 720p]
293s  [RLSP] One Piece 721-724 [BD 720p]
279s  [RLSP] One Piece 729-732 [BD 720p]
258s  [RLSP] One Piece 733-736 [BD 720p]
```

One show, four cards, and that continues for pages. A wall built directly from
release rows is a wall of One Piece.

---

## 2. What follows from that

### 2.1 The catalog accumulates; it is not a cache

Because a refresh sees only the indexers' front page, the depth needed for
scrolling cannot come from asking harder. It has to come from asking *regularly
and keeping what came back*. A background refresh every 30 minutes writes into a
`catalog_release` table, upserting on identity and leaving older rows in place.
Day one has a few hundred items; a fortnight in it has thousands, and the
scroll is genuinely deep because Miru remembers what the indexers have already
forgotten.

This is the argument against Redis, and it is not a close call — see §6.

Rows are never deleted on refresh. A release whose torrent has died is still a
true statement about what exists, and it will simply sort last once seeders
decay. A weekly prune of rows unseen for 90 days with zero seeders is enough.

### 2.2 A card is a work, not a release

Release titles get parsed into `(title, year, season, episode, quality, group)`
and grouped. The card shows One Piece once; the four batches become the choices
behind its Download button.

Parsing is the risky part and it is well-trodden: `guessit` handles scene and
p2p naming, and `anitopy` is the Python port of Anitomy, which is what every
anime client uses for exactly these `[Group] Title - 01 [1080p][HASH]` shapes.
Route by indexer — anime sources through anitopy, everything else through
guessit — because each is good at the shape it was written for and poor at the
other.

Grouping key is the normalised title plus year when present. Imperfect grouping
is acceptable and self-correcting; a wrongly split card shows the same show
twice, which is a cosmetic failure, whereas a wrongly merged card offers the
wrong download, which is not. When unsure, split.

### 2.3 Posters come from elsewhere

Torrent indexers have no artwork, and a Netflix-shaped wall without artwork is
just a list with more whitespace. Two lookups cover the catalog:

- **Anime** — AniList GraphQL. Free, no key, no registration, and its search
  tolerates the romaji/English/abbreviated titles that come out of release
  names. Returns cover art, banner, description, genres, score, year.
- **Film and TV** — TMDB. `find/{imdb_id}?external_source=imdb_id` is exact for
  the YTS half of the catalog; the rest falls back to title+year search. Needs a
  free API key.

Both are cached in a `catalog_work` table keyed by provider and id, fetched once
and then never again. Poster *images* are proxied through the API and cached on
disk, so the browser makes no third-party requests and the wall keeps working
if TMDB is slow.

A work with no artwork still shows, with a generated title card. Missing a
poster must not mean missing from the catalog.

### 2.4 Streaming an unfinished download is not possible

> **Superseded in part by D4.** This section's *constraint* stands; the
> conclusion it originally drew — delete the Watch Now button — was reversed by
> the design review. Read D4 for what the button actually does.

`DEPLOYMENT.md` §3 already records that Watch Now was lost when movies-downloader
was dropped: aria2 writes to disk and cannot stream a torrent that is still
arriving. Nothing in this plan changes that, and nothing here fakes it —
`aria2 --bt-prioritize-piece=head,tail` biases piece order without guaranteeing a
playable prefix, and a play button that works for some files and stalls on others
is worse than one that is honest about waiting.

What follows from the constraint is only that **playback cannot begin
immediately**. It does not follow that the user must think about downloads, which
is what D4 fixes.

The card's action is state-dependent, reusing the availability vocabulary the
library already speaks:

| state | action |
|---|---|
| already in the library | **Play** — straight to the player |
| downloading | progress with percentage and ETA, cancel available |
| not acquired | **Watch Now** (download, then play on its own) or **Download** — D4 |
| acquired but needs the PC, PC asleep | **Play** shown disabled with the existing offline note |

---

## 3. Data model

```
catalog_release            one row per release seen at an indexer
  id, indexer, guid                       -- (indexer, guid) unique
  title, parsed_title, year, season, episode, quality, release_group
  size_bytes, seeders, leechers, grabs, published_at
  categories jsonb, magnet, download_url, imdb_id
  grabbable bool                          -- D13; no magnet and no URL means no action
  seeder_pct                              -- D8; percentile within its own indexer
  kind                                    -- anime | movie | series, ours not theirs
  work_id -> catalog_work.id  nullable
  first_seen_at, last_seen_at

catalog_work               one row per thing you would actually watch
  id, kind, normalised_title, year
  provider, provider_id                   -- anilist | tmdb, unique together
  display_title, overview, poster_path, backdrop_path, genres jsonb
  score, popularity                       -- the source's own metric
  best_seeder_pct, release_count          -- denormalised for sorting, D8
  library_file_id -> media_files.id  nullable   -- written at download time, D10
  download_job_id                         -- survives a reload, D11
  first_seen_at, last_seen_at
```

`work_id` being nullable matters: a release is stored the moment it is seen, and
enrichment catches up on its own schedule. A slow or broken TMDB never blocks
ingestion.

**Classification is ours.** Precedence, applied to the `categories` array:

1. any category in the anime range (5070, or Nyaa's own 127720–134634 block) → `anime`
2. otherwise any 5000–5999 → `series`
3. otherwise any 2000–2999 → `movie`
4. otherwise dropped from the wall

Anime is checked first specifically because of the Nyaa dual-tagging measured in
§1. This ordering is the fix for it.

---

## 4. The queue and paging

The screen asks for `GET /api/catalog?kind=anime&sort=trending&cursor=...` and
gets back `{items, next_cursor}`.

Keyset pagination on `(sort_value, id)`, not offset — offset paging over a table
that is concurrently being inserted into shows duplicates and skips rows, and
this table is written to by a refresh job every 30 minutes.

Rows, and the metric behind each:

| row | ordering |
|---|---|
| Trending now | `best_seeders` desc |
| Just added | `first_seen_at` desc |
| Highly rated | provider `score` desc, requires enrichment |
| Anime / Movies / Series | the `kind` filter, each with the above sorts |

The client keeps one page in hand: when the user is within one viewport of the
end of a row, the next page is already requested. That is the "queue" — a small
client-side buffer over a cursor, not a server-side session. Nothing about it
requires state on the server, which is what keeps it survivable across a restart.

**No indexer is contacted by any of this.** The wall reads Postgres only. The
only path to an indexer is the scheduled refresh and an explicit search box.

---

## 5. Work breakdown

| # | change | area | from |
|---|---|---|---|
| 1 | `catalog_release` / `catalog_work` tables, Alembic (first table the filesystem cannot regenerate — see ARCHITECTURE.md §3) | api | §3 |
| 2 | classification with anime-first precedence, plus tests over the real category shapes measured in §1 | api | §3 |
| 3 | release-name parsing, anitopy for anime and guessit for the rest, grouping into works | api | §2.2 |
| 4 | refresh job every 30 min, upsert-and-keep, computing `seeder_pct` per indexer | api | D8 |
| 5 | AniList and TMDB enrichment, backfill-style, never blocking ingest | api | §2.3 |
| 6 | poster proxy, on-disk cache, **404 on miss** | api | D18 |
| 7 | `GET /api/catalog` — keyset paging, rail dedupe, `pc_reachable`, inline download status | api | D3, D9, D11 |
| 8 | `POST /api/catalog/{work}/download` — default-pick algorithm, writes `library_file_id` and `download_job_id` | api | D10, D11, D19 |
| 9 | `GET /api/downloads` — one call for every active job | api | D11 |
| 10 | the wall: hero, rails in fixed order, kind pills, dedupe, sparse-rail rule, freshness line | web | D1, D3, D5, D21 |
| 11 | the card: one state slot, five caption renderings, hover glyph carries the action | web | D3, D12 |
| 12 | release sheet: three named choices, series scope selector, full-table disclosure | web | D6, D14 |
| 13 | `Downloading` section in the existing sidebar + ready toast | web | D16 |
| 14 | `/search?q=` as a dense list with its own three states | web | D15 |
| 15 | PC-asleep strip and card treatment | web | D9 |
| 16 | first-run skeletons and the TMDB-key note | web | D2, D20 |

The wall becomes `/`. **`/library` does not move** — it is already the dense
file-facts view and stays exactly as it is (D17); the poster grid that used to
live on `/` survives as the *In your library* rail.

---

## 6. Redis — recommendation: no

The question was whether to provision Redis in the laptop's Docker. It should not
be, and the reasons are specific rather than general minimalism:

- **The data has to survive.** Depth comes from accumulating what the indexers
  have dropped from their front page (§1, §2.1). Redis is a cache with eviction;
  the very thing being stored is the thing eviction would destroy.
- **The access pattern is keyset pagination over sorted, filtered rows.** That is
  a `WHERE kind = ? AND (score, id) < (?, ?) ORDER BY ... LIMIT n` query. Doing
  that in Redis means maintaining a sorted set per sort per kind by hand and
  keeping them in step with the source of truth.
- **Postgres is already running on the laptop.** Adding Redis adds a container, a
  port, a healthcheck, a second thing to remember at boot, and a second way for
  the home screen to be broken.
- **It would not fix the actual bottleneck.** The constraint is that indexers
  rate-limit and answer in ~3 s. The scheduled refresh removes indexers from the
  request path entirely; the store behind it is then serving a few thousand rows
  to one user, which Postgres does without noticing.

The honest case *for* Redis is a cross-process job queue with retries. Miru's
scan job is already in-process, and a second scheduled job does not justify
inverting that. If acquisition ever grows to many concurrent long-running jobs
that must survive an API restart, revisit — and note that Postgres has
`SELECT ... FOR UPDATE SKIP LOCKED`, which is a job queue, before reaching for a
new dependency.

---

## 7. Deliberately not in scope

- Recommendations or personalisation. One user, no signal to learn from.
- RSS-style auto-download of new episodes. Real feature, different feature.
- More indexers. Three is enough to prove the shape; the classification work in
  §3 is what makes adding a fourth cheap.
- Trakt or MAL sync.

---

## 8. Design review — passes, ratings and decisions

Run 2026-08-08. The gstack designer and the Codex outside voice were both
unavailable (no OpenAI credentials — `401 Unauthorized`), so mockups are HTML
wireframes built in the real palette rather than generated images, and the
outside voice is single-model.

### What already exists, and must be reused

There is no `DESIGN.md`, but there *is* a design system: it lives in
`apps/web/app/globals.css` as `@theme` tokens with the reasoning written beside
them. Nothing here invents a colour, a radius or an easing curve.

| Existing | Use for |
|---|---|
| `.rail` in `globals.css` | already scroll-snap + quiet native scrollbar; the rails were anticipated |
| `ArtTile` + `artTint()` | the no-poster fallback, deterministic per title |
| `MicroChip` | quality / group chips on cards |
| `ProgressBar` | download progress, in the same gold that means watch state |
| `SectionHeading` | rail headings, with the Japanese sub-label |
| `EmptyState` | first-run and sparse-rail copy |
| `Button` / `ButtonLink` | one button vocabulary; no new variants |
| `--color-accent` `#d9a441` | **reserved.** Watch state and playback position only. Download progress qualifies; a "new" badge does not. |

### Pass 1 — Information architecture · 4/10 → 9/10

The plan said "the wall becomes `/`" and stopped. It never said what is seen
first, and the version it implied was wrong: it replaces a home screen whose top
item is *resume what you were watching* with a wall of things the user does not
own. The most frequent action on this server is continuing an episode, and that
would have moved a click away.

**D1. Home keeps continuing-to-watch above the discovery rails.** This is what
Netflix actually does — billboard, then Continue Watching, then discovery — so
it also follows the brief more literally than the original plan did.

```
┌── sidebar ──┬─────────────────────────────────────────────────┐
│ Browse    ● │  [ All ][ Anime ][ Movies ][ Series ]   [search] │
│ Library     ├─────────────────────────────────────────────────┤
│ Favourites  │  HERO — one featured work, key art, 2 actions    │
│ Downloads   │                                                  │
│ Settings    ├─────────────────────────────────────────────────┤
│             │  Continue watching   ◀ ▪▪▪▪▪▪ ▶   (library)      │
│             │  Trending now        ◀ ▪▪▪▪▪▪ ▶   (catalog)      │
│             │  Fresh this week     ◀ ▪▪▪▪▪▪ ▶   (catalog)      │
│             │  In your library     ◀ ▪▪▪▪▪▪ ▶   (library)      │
└─────────────┴─────────────────────────────────────────────────┘
```

Constraint worship, three things only: **what you were watching**, **one
featured thing**, **what is worth getting**. Everything else is a filter away.

Selecting Anime / Movies / Series switches to a browse mode where the library
rails drop out and only catalog rails remain, because in that mode the user has
stated an intent to look outward.

### Pass 2 — Interaction state coverage · 3/10 → 9/10

The plan named the first-run problem as a risk and specified none of the states.

| Surface | Loading | Empty | Error | Success | Partial |
|---|---|---|---|---|---|
| Wall (first ever load) | rails render as 6 skeleton cards, one line above: "Finding what's out there — a few seconds." Refresh is kicked off by the request itself | never empty on first run: the refresh is triggered, not awaited | "Couldn't reach Prowlarr on the PC." + Retry + link to Settings. Library rails still render | rails fill in place, no layout shift | some rails filled, others still skeleton — each rail resolves independently |
| Rail | skeleton cards | see sparse-rail rule, D5 | rail hidden, one quiet line in its place | — | fewer cards than a full rail, no stretching |
| Card art | `artTint` tile immediately, poster fades in when cached | `ArtTile` with the title set as real type (already built) | same as empty — a failed poster is a missing poster | — | — |
| Release picker | three skeleton options | "No usable releases left" + why (all dead) | "Couldn't reach the indexers." + Retry | sheet closes, card flips to Downloading | — |
| Download | button shows a spinner for the submit round-trip only | — | inline red line under the card + Retry, download stays listed | card shows percentage | percentage + ETA + speed |
| Search | inline spinner in the field | "Nothing matched *x*." + "Browse instead" | "Indexers unreachable" — **502, never an empty list** | results grid | partial indexer failure noted below results |

**D2. First run never shows a blank wall.** The request that finds an empty
catalog triggers the refresh and returns skeletons. Measured refresh is 2.7–4.1 s
(§1), so the copy promises seconds and can keep the promise.

### Pass 3 — User journey · 2/10 → 8/10

| Step | User does | Should feel | Now specified by |
|---|---|---|---|
| 1 | Opens Miru | "there's my show" | D1 — continue-watching stays above the fold |
| 2 | Scrolls past their own stuff | curious, not sold to | rails named by fact, not mood |
| 3 | Sees something | "can I actually watch this?" | card state, D3 |
| 4 | Clicks Watch | committed | D4 — Watch always means watch |
| 5 | Faces the release list | **this is where it breaks** | D6 — three named choices, not a table |
| 6 | Waits | patient if informed | waiting screen with real percentage and ETA |
| 7 | Comes back later | "it's just there" | promotion into the library already works |

Five seconds: the wall must look like a media library, not a torrent client.
Five minutes: the release picker must not have made them feel stupid.
Five years: the catalog accumulating (§2.1) means the wall gets *better* with
age, which is the opposite of how a scraper usually decays.

### Pass 4 — AI slop risk · 5/10 → 9/10

Classified **APP UI**. The existing system already passes the universal rules —
CSS variables, a real typeface, no decorative gradients, cards that are the
interaction.

One hard-rejection risk was real and is the most valuable finding in this pass:
**"sections repeating the same mood statement."** With only ~300 catalog items
(§1), *Trending now*, *Just added* and *Highly rated* would draw from the same
small pool and show largely the same titles in a different order. Three rails of
One Piece is worse than one.

**D3. Rails are mutually exclusive and each has a distinct job.** A work appears
in at most one rail; the first rail that claims it wins. Rails are ordered
Trending → Fresh this week → the kind rails, and any rail left with nothing
after deduplication is not rendered. *Highly rated* is dropped entirely until
enrichment exists, since without a score it is just Trending sorted differently.

Also rejected: badge-covered cards. **At most one state marker per card**, always
in the same corner, by priority: downloading percentage > in-library tick >
nothing. "Needs the PC" is deliberately *not* on a browse card — it is only true
once you own the file, so it belongs on the library card and the detail page.

### Pass 5 — Design system alignment · 6/10 → 9/10

The plan never referenced the existing tokens. Now it does, in the table above.
Two new components only, both justified: the **rail** (a horizontal `SectionHeading`
plus `.rail`, reusing the existing card) and the **release sheet** (D6).

The absence of `DESIGN.md` is a real gap — the system exists but is discoverable
only by reading CSS comments. Logged as a TODO rather than done here, since
extracting it is documentation work, not this feature.

### Pass 6 — Responsive and accessibility · 1/10 → 8/10

Unspecified entirely, and horizontal rails are the single most common
accessibility failure in this shape of UI.

- Rails are `<ul>` with an `aria-label` naming the rail; cards are `<li>`.
- Keyboard: no roving tabindex. Every card is a normal tab stop and
  `scroll-snap-align: start` plus focus scrolling brings it into view — native
  behaviour beats a custom key handler. Arrow buttons are `aria-hidden` since
  they duplicate what Tab already does.
- Arrow buttons appear only on `(hover: hover)` pointers; touch scrolls.
- Touch targets 44px minimum. The current favourite button is 36px (`size-9`) —
  fixed to `size-11` on touch.
- Contrast: `--color-text-muted` `#918a9e` on `--color-surface` `#1e1b26` is
  4.9:1, which passes. It must not be used below 11px on the new surfaces.
- Viewports: 375px shows one-and-a-bit cards per rail and the sidebar collapses
  to the existing drawer; 768px shows three; 1280px+ shows six. The hero's text
  bed switches from a bottom scrim to a left scrim at `sm`, matching the
  existing `Hero`.
- `prefers-reduced-motion` already kills the sweep and the card lift globally.

### Pass 7 — Decisions that would otherwise haunt the implementer

| Decision | If left open |
|---|---|
| Watch vs Download on a card (D4) | engineer ships two buttons and the user picks wrong |
| What the release picker shows (D6) | engineer ships a 40-row table sorted by seeders |
| Sparse rail (D5) | a four-item rail ships and reads as broken |
| Which rail owns a duplicated work (D3) | the same show fills the screen three times |
| Poster failure (D7) | a grid of grey rectangles |

**D4. "Watch Now" is restored, and it is the primary action everywhere.** The
original plan deleted it because aria2 cannot stream a partial torrent. That was
solving the wrong problem: the button does not have to mean "bytes are already
here", it has to mean *"I want to watch this — you deal with it"*. So:

| Card state | Watch does |
|---|---|
| in the library | plays immediately |
| downloading | opens the waiting screen |
| not acquired | opens the release picker, starts the download, then the waiting screen |

The waiting screen shows real percentage, speed and ETA, says plainly **"Playback
starts on its own when it's ready"**, and does exactly that by polling until the
mover promotes the file into the library. **Download** stays as the secondary
action: the same thing without the waiting screen, for when you are queueing
things for later.

This honours the request without promising streaming that does not exist. What
is *not* being done is `--bt-prioritize-piece=head,tail` to fake early playback:
it biases piece order without guaranteeing a playable prefix, and a Watch button
that works for some files and stalls on others is worse than one that is honest
about waiting.

**D5. A rail with fewer than 8 items renders as a grid, not a rail.** Measured,
the Series row has ~13 items from one indexer and will sometimes have fewer
(§1). A short grid looks deliberate; a short rail looks broken. Below 8 items
the section renders as a small grid with a one-line note: *"Only The Pirate Bay
covers TV here. Add an indexer in Prowlarr for more."* — which tells the user the
truth and gives them the fix.

**D6. The release picker offers three named choices, not a table.** Nobody can
usefully rank forty releases by seeders, size and group. Miru knows something no
generic torrent client knows: **its own playback ladder**. A release name
carrying `x265`/`HEVC` will need the PC's GPU; `x264` will not. So each choice is
labelled with its consequence in the user's terms:

```
  ┌ Best                                    (preselected) ┐
  │ 1080p · x264 · 1.4 GB · 1,470 seeders                 │
  │ Plays directly — no PC needed                         │
  ├ Smallest ─────────────────────────────────────────────┤
  │ 720p · x265 · 610 MB · 340 seeders                    │
  │ Needs the PC to transcode                             │
  ├ Best quality ─────────────────────────────────────────┤
  │ 2160p · x265 · 12.4 GB · 88 seeders                   │
  │ Needs the PC to transcode                             │
  └───────────────────────────────────────────────────────┘
     ▸ All 37 releases
```

"Best" is chosen as the highest-seeded release at the best quality that will
still direct-play or remux — preferring a rung that does not depend on the PC
being awake. The full table stays one disclosure away for when the default is
wrong.

**D7. A missing poster is never an empty rectangle.** `ArtTile` already sets the
title as real type over a deterministic tint. That is the poster-failure state,
unchanged, and it is why the wall is usable before any TMDB key is configured.

### Outside voice — what it caught that the passes above did not

An independent design review found three things that outrank everything in
passes 1–7, because each one makes a state silently never fire rather than
merely look wrong.

**D8. Seeder counts are not comparable across indexers, so Trending cannot sort
on them.** Measured on the same snapshot:

| indexer | results | seeders reported | max | median |
|---|---|---|---|---|
| Nyaa.si | 130 | 124 of 130 | 303 | 10.5 |
| YTS | 101 | **35 of 101** | 100 | **0** |
| The Pirate Bay | 4 | 4 of 4 | **2** | 1 |

YTS reports zero for two thirds of its catalogue and The Pirate Bay's ceiling is
2. Ordering a mixed wall by raw `seeders` therefore returns Nyaa, all of it,
every time — the Movies rail would be anime and the wall would look broken in a
way that is very hard to diagnose. **Trending ranks on a release's percentile
within its own indexer's current snapshot**, not on the raw count. An indexer
reporting no usable seeders at all falls back to recency.

**D9. The PC being asleep disables the entire feature, not just playback.**
aria2, Prowlarr *and* the worker all live on the PC (`MIRU_ARIA2_URL=http://100.67.44.13:6800`).
The wall is served from the laptop's Postgres, so it renders perfectly while
every Download button 502s and live search fails. §2.4 treated this as affecting
Play only, which was wrong.

`GET /api/catalog` carries `pc_reachable`. When false: one strip under the
header — *"The PC is asleep. Browsing works. Downloading and search need it
awake."* — cards dim to `opacity-55` with a moon marker in the single state slot,
and the picker does not open. Not a red error, because nothing is broken.

**D10. Nothing in the original plan ever populated `library_file_id`, so the
in-library state would never have fired.** `media_files` stores only `title` and
`path` — no parsed title, no year — so a join needs both sides normalised. The
cheap ninety percent is to write the link **at download time**, from the job that
grabbed it; the normalised-title match is a backfill for files that arrived some
other way. Without this every show you already own sits on the wall offering to
download itself again.

**D11. In-flight downloads must survive a page reload.** `catalog_work` gains
`download_job_id`, and the catalog response carries live status inline so the
first paint is already correct. Polling is **one** `GET /api/downloads` for all
active jobs every 2 s while any card is downloading — never one request per card.

**D12. Two more card states, both of which occur constantly.** *Adding to
library…* covers the gap between aria2 finishing and the scan promoting the file;
without it the card snaps back to Download and the user grabs it twice. *Failed —
pick another* reopens the picker with the dead release struck through and
excluded from the default. A three-seeder torrent that never starts is the most
likely outcome of a careless pick, so this is a main path, not an edge case.

**D13. `grabbable` is carried into `catalog_release`.** The provider already
computes it. A work whose only releases have neither a magnet nor a torrent URL
renders no Download action at all, rather than one that fails on click.

**D14. For a series, the picker asks scope before quality.** Releases are
batches — `[RLSP] One Piece 744-746` — so the first control is *Season 1
complete* / *Episodes 744–746* / *Episode 744*, and quality is chosen inside the
selected scope. Otherwise the user downloads three arbitrary episodes and cannot
tell why.

**D15. Search is its own route, not a mode of the wall.** Live search returns
*releases*; the wall shows *works*. They cannot be the same card. `/search?q=`
renders the dense list the library view already uses, with three states of its
own: in-flight (*"Asking three indexers — this takes a few seconds"*, honest
because it measures 2.7–4.1 s), 502, and no matches.

**D16. There is no downloads tray.** The sidebar already contains exactly the
right component — `ContinueWatching` renders an `ArtTile` plus label plus a 3px
`ProgressBar`. A `Downloading` section above it, same treatment, costs nothing
and invents nothing. On completion a toast (`--z-toast` is defined and currently
unused) says *"One Piece 744 is ready"* with a Play button — which is the only
moment at which Watch Now can honestly be honoured.

**D17. `/library` already exists** and is the dense file-facts view, whose own
header comment says "Home is the browsing view; Library is the dense one". It
does not move and is not replaced. The poster grid that used to be on `/`
survives as the *In your library* rail on the wall.

**D18. Poster geometry is fixed at `aspect-2/3` with `object-cover`, and the
proxy returns 404 on a miss.** AniList covers are ~0.708 and TMDB is exactly
0.667; without a stated aspect they crop differently and the wall looks subtly
misaligned. The 404 is what lets the client branch to `ArtTile` instead of
rendering a broken image or a spinner that never resolves.

**D19. The default pick is an algorithm, written down.** Exclude anything
matching `\b(CAM|HDCAM|TS|HDTS|TELESYNC)\b`; exclude zero seeders; prefer 1080p,
then 720p, then 2160p; within that prefer a release that will direct-play or
remux over one needing the GPU; then prefer the higher within-indexer seeder
percentile (D8). If nothing clears five seeders the sheet says so up front rather
than defaulting to a corpse.

**D20. A missing TMDB key is explained once, not implied by a broken-looking
rail.** Anime enriches with no configuration; film and TV degrade to `ArtTile`.
One dismissible line above the rails — *"Film and TV posters need a free TMDB
key."* with a link to Settings — and never a per-card badge.

**D21. The wall says how fresh it is.** The first rail's heading carries
*Updated 12 min ago*, clickable to force a refresh. When the last refresh failed:
*"Couldn't reach the indexers — showing what we have."* A 30-minute-old snapshot
with no way to say "look again" is the kind of thing that quietly rots.

### One disagreement, resolved

The outside voice argued for keeping Watch Now deleted and adding a *"Play when
it's ready"* checkbox to the picker. The reasoning about aria2 is identical to
D4's and is not in dispute. The difference is only where the intent lives, and
the request was explicit — *"differing options watch now or download"* — so both
actions stay as named buttons, and Watch Now simply *is* the checkbox, always on.
Everything else from that finding is adopted: Download never navigates, the card
becomes the progress state in place, and the toast fires on promotion.

### NOT in scope, deliberately

- **Personalisation and recommendations** — one user, no signal to learn from.
- **A "because you watched" rail** — same reason.
- **Trailers or preview-on-hover** — no source for them, and hover-gated content
  fails on touch.
- **`DESIGN.md` extraction** — real gap, but documentation work; TODO instead.
- **Multi-select / batch download** — a season pack is already one release.
- **Sorting controls on rails** — the rail *is* the sort. Adding a control makes
  the user do the editorial work the wall exists to do for them.

## 9. CEO review — scope, sequencing and bets

Run 2026-08-08, mode **HOLD SCOPE with a phase split**. The scope is right; the
milestone is not.

### The premise holds, and the differentiator is not the wall

Doing nothing is survivable: you open a torrent site in another tab and drop
files into the library folder, which already works. So convenience alone is a
weak case for sixteen work items.

The strong case is D6 and D19. Miru is the only thing in this space that knows
its **own** playback ladder, so it can tell you *"this release plays directly,
that one wakes the PC"* before you commit twelve gigabytes. No general torrent
client can say that, because none of them own the player. That is the headline
feature, and the wall is the surface that delivers it. Treat it that way — if
scope has to be cut later, the release picker is the last thing to go, not the
first.

### Alternatives considered

| | approach | effort | verdict |
|---|---|---|---|
| A | **Live query per rail**, no tables, in-memory 15-minute cache | S | **Rejected.** §1 measured no pagination and ~300 items, so this can never get deeper than one snapshot, and nothing survives a restart. Every scroll would also spend an indexer request. |
| B | **Accumulating catalog, posters phased** | L, split | **Chosen.** |
| C | B plus RSS auto-grab and Trakt sync | XL | **Rejected.** Both are different features (§7), and neither makes the wall better. |

### The scope answer: right scope, wrong single milestone

Sixteen items is too much to verify at once, and the split falls out cleanly
because **posters are the only part that cannot be tested end-to-end quickly** —
they depend on two third-party APIs, one of which needs a key that does not exist
yet.

**M5a — the wall works.** Items 1–4, 7–17 minus enrichment. Ships with `ArtTile`
placeholders, which is exactly what that component was built for; its own comment
says "Replaced by real posters at M2." Everything is verifiable against the live
Prowlarr on the PC in one sitting: browse, classify, group, pick, download,
promote, play.

**M5b — posters.** Items 5, 6 and D20. Purely additive: an `enrichment` pass that
fills `catalog_work` and a proxy that serves what it cached. Nothing in M5a
blocks on it, and nothing in M5a changes when it lands.

Shipping M5a alone is a real product. Shipping M5b alone is nothing.

### D22. The accumulate-forever bet is right, and its failure mode is stale magnets

Accumulating is the only way to get depth out of sources that expose one page
(§1, §2.1), and it makes the wall improve with age instead of decaying. That bet
is sound.

Its failure mode is specific: a magnet stored in August may be dead in November,
so a wall that never deletes slowly fills with links that fail on click — and
that is exactly the kind of rot that is invisible until someone clicks. So:
a release not seen in the last **20 refreshes** is marked `stale`; stale releases
are excluded from the default pick (D19) and shown struck through in the full
table; a work whose releases are *all* stale drops out of the rails but stays
searchable. Nothing is deleted, and nothing dead is ever recommended.

### D23. The refresh job is one asyncio task, not a new dependency

`library/router.py:85` already records the house position — `BackgroundTasks`,
not APScheduler. Adding a scheduler for one job would reverse a deliberate call.
A single task started in the app lifespan, sleeping 30 minutes between passes,
does the same work. It **skips the pass entirely when the PC is unreachable**,
because every request would fail anyway and a log full of connection errors is
how a real failure gets missed.

### D24. A silent refresh failure is the one thing that would kill this quietly

The whole design assumes the refresh keeps running. If it dies, the wall does
not break — it just stops getting deeper, which nobody notices for weeks. That
is a silent failure and it is not acceptable.

`catalog_refresh` records every pass: started, finished, per-indexer counts, and
the error if there was one. D21's *"Updated 12 minutes ago"* reads that table, so
a dead refresh shows up on the wall itself rather than in a log nobody opens.

### D25. The poster proxy needs an allowlist

It fetches a URL and returns the bytes, which is the same server-side request
forgery shape the transcode worker already has to defend against with
`WORKER_ALLOWED_SOURCE_PREFIXES`. The proxy accepts `image.tmdb.org` and
`s4.anilist.co` and nothing else. This is not hypothetical: the URL comes from a
third-party API response, so the allowlist is what stops that API from choosing
what our server fetches.

### Redis — the answer is no

The question that opened this plan. §6 gives the reasoning and the CEO pass
confirms it: the data must persist and grow (which is what a cache evicts), the
access pattern is keyset pagination over sorted filtered rows (which Redis makes
you hand-maintain), Postgres is already running, and the real constraint is
indexer rate limits, which the scheduled refresh removes from the request path
regardless of the store. Provisioning it would add a container, a port and a
boot-order dependency in exchange for nothing measurable at one user.

Revisit only if acquisition grows many concurrent long-running jobs that must
survive an API restart — and even then, `SELECT … FOR UPDATE SKIP LOCKED` is a
job queue that needs no new infrastructure.

### Deferred to a TODO list, with reasons

| item | why not now |
|---|---|
| `DESIGN.md` extraction | The system exists in `globals.css` comments; writing it down is documentation, not this feature. |
| API auth (eng-review T5) | Still open, still tailnet-only. The wall makes an unauthenticated API more interesting to reach, so this rises in priority — but it is not this plan's job. |
| RSS auto-grab of new episodes | A real feature, and a different one. |
| More indexers | The classification work here is what makes a fourth cheap. Adding one now proves nothing. |

## 10. Open risks

1. **Grouping quality is the whole illusion.** If One Piece still appears eight
   times, the wall has failed regardless of how good the rest is. This deserves a
   test corpus of real titles taken from the live indexers.
2. **TMDB needs a key.** AniList does not. Anime works with no configuration; film
   and TV degrade to title cards until a key is set, and must degrade rather than
   error.
3. **The series row is thin.** YTS is movies-only and Nyaa is anime-only, so TV
   comes from The Pirate Bay alone — 13 results in the measured sample. The row
   will look sparse and that is a source problem, not a code problem.
4. **First run is empty.** Nothing exists until the first refresh completes. The
   screen needs a real first-run state that triggers a refresh and says what it
   is doing.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | mode: HOLD SCOPE + phase split, 4 proposals accepted, 4 deferred |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | CLEAR | score 4/10 → 9/10, 21 decisions |
| Outside Voice | design subagent | Independent completeness | 1 | ISSUES FOUND | 19 findings, 3 critical, all absorbed |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 (stale) | ISSUES OPEN | 39 issues, 4 critical gaps — from 2026-08-07 against a different plan |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | UNAVAILABLE | `401 Unauthorized` — no OpenAI credentials |

**CROSS-MODEL:** Not achievable this run. Both OpenAI-backed voices (the gstack
designer and the Codex reviewer) returned `401 Unauthorized`. The independent
Claude design subagent stood in, and its three critical findings — the PC-asleep
blind spot (D9), `library_file_id` never being populated (D10), and download
state not surviving a reload (D11) — were each things the primary pass missed.
Single-model review found the seeder-comparability bug (D8) that the subagent
lacked the data to see. Both halves mattered; treat this as one voice short.

**VERDICT:** CEO + DESIGN CLEARED — ready to implement M5a. Eng review is stale
(it predates this plan and this commit) and should be re-run against the
catalog schema and the refresh job before M5b.

NO UNRESOLVED DECISIONS
