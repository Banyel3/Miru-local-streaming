# Plan — play immediately, and stop losing what the indexers drop

Status: draft
Date: 2026-08-08

Two asks. The first is a UX defect with a straightforward fix. The second is a
correct instinct about the data, and the measurement changes what to do about it.

---

## 1. Watch Now should open the player, not a waiting room

**Reported:** *"shouldn't it already automatically go to the player then just
buffer and play what's already been downloaded like any other player?"*

Yes. That is how every streaming player behaves, and the current screen is a
different thing wearing the same button.

**What it does now.** `/watching/{hash}` renders a poster card and a paragraph
until `watchable` flips true, then swaps the whole layout for a `<video>`:

```tsx
if (playing && status) { return <video …/> }   // one layout
return <section>…poster + copy…</section>       // a completely different one
```

`watchable` requires `MIN_PLAYABLE_BYTES = 24 MB`. So at 0% the user gets a
paragraph explaining that playback will start later — which is a **waiting
room**, not a player buffering.

**What it should do.** The player mounts at second zero, in its final position
and size, and shows a buffering state inside its own frame. Nothing about the
layout changes when playback begins; the overlay fades and the controls appear.
The design review already specified this and named the requirement:

> mount the shared player from second zero at `aspect-video`, with the `artTint`
> surface as its poster. At `watchable`, nothing moves — the overlay cross-fades
> out. **Zero layout shift is the requirement.**

**The 24 MB gate stays, but moves.** It stops being "should there be a player"
and becomes "should the player start playing". That is what a buffer is. The
distinction matters because a player that appears and immediately stalls is the
failure this design has refused three times — so the overlay says what it is
waiting for, with real numbers:

```
  ▸ Buffering — 8.2 MB of 24 MB   ·   2.1 MB/s   ·   about 8 seconds
```

rather than a static paragraph.

**This is blocked on the shared player.** `/watching` currently uses a bare
`<video>`, which is also why it has no subtitles and no Miru chrome. The
engineering review found the prerequisite: `Player`'s props are library-file
shaped (`file.id`, `file.playback_strategy`, `nextAfter(file, all)`) and must be
narrowed to `{title, backHref, progressKey, …}` before either route can share
it. That narrowing is step one, not a follow-up.

---

## 2. "Pull the latest uploads" — already happening, and that is the problem

**Reported:** *"the issue isn't the sorting but rather how we pull data from
sources, we should pull from the latest uploads."*

The instinct is right and the measurement redirects it. The browse feed **is**
the latest uploads, and it is already newest-first:

```
indexer          newest             oldest            already descending
Nyaa.si          2026-08-08 11:41   2026-08-08 01:05   yes
The Pirate Bay   2026-08-08 10:41   2026-08-08 09:47   yes
YTS              2026-08-08 09:40   2026-08-05 17:22   no
Knaben           2026-08-08 10:19   2026-08-08 08:03   yes
```

The real finding is the **span**, not the order:

```
oldest item on the front page
  Nyaa.si          0 days     ← ten hours of uploads. That is the whole window.
  The Pirate Bay   0 days
  Knaben           0 days
  YTS              2 days
```

And asking for more does not work — `limit` is ignored entirely:

```
limit=100 -> 366 results
limit=300 -> 366 results
```

So each indexer shows roughly **one day**, we cannot ask for more, and there is
no pagination (measured previously: `offset=100` returns nothing).

**The consequence nobody has stated yet: a missed refresh is a permanent hole.**
Nyaa's front page turns over in about ten hours. The refresh runs every thirty
minutes, but it **skips entirely when the PC is unreachable** — and the PC is
asleep by design. Sleep overnight and the uploads from those hours are never
seen by Miru, and never will be, because there is no way to ask for yesterday.

The accumulate design assumed the front page was a window we sampled. It is
actually a conveyor belt we are next to, and we only catch what passes while we
are looking.

**Fixes, in order of value:**

- **Refresh from the laptop, not conditionally on the PC.** Prowlarr runs on the
  PC, which is the actual coupling — so the honest options are to run Prowlarr
  on the laptop (it is a search proxy, not a GPU job), or to accept the gaps and
  say so. Moving Prowlarr to the laptop makes the catalogue independent of when
  the PC happens to be awake, and downloads stay on the PC where they belong.
  This is the single change that fixes the hole.
- **Widen each pass.** One empty query returns ~366 rows across four indexers.
  Running the same pass per category (anime / movies / series) multiplies
  coverage for the same wall-clock, since indexers return a front page *per
  category*.
- **Use RSS where offered.** Nyaa publishes an RSS feed with a deeper window
  than the API front page. Torznab exposes it via `t=search` with no query;
  worth measuring per indexer before committing.

---

## 3. Search should feed the catalogue

**Reported:** *"searching would also query the sources we have to ensure we have
the full library in Miru."*

Search already queries the indexers live — that is `/search`. What it does not do
is **keep** anything. The results render once and are discarded, so searching for
a show you want tells Miru nothing.

**Fix: a live search is also an ingest.** The same rows already flow through
`classify` → `parse` → `_upsert_release`; a search hands them to the same path.
Consequences, all good:

- Searching for *Fruits Basket* permanently adds every Fruits Basket release the
  indexers currently list, so it appears on the wall afterwards.
- It is the only way to reach **behind** the one-day window: the front page
  cannot be paged, but a query can ask for anything.
- The catalogue then reflects what this person actually looks for, rather than
  only what the world happened to upload while the PC was awake.

Two guards. Search results must not push the *Latest* rail around — they are
ingested with their real `published_at`, so an old release lands where it
belongs rather than at the top. And a search that returns two hundred rows for a
one-character query should not write two hundred rows; the existing two-character
minimum plus the `grabbable` and `info_hash` filters already handle it.

---

## Order

| # | step | why |
|---|---|---|
| 1 | narrow `Player`'s props | blocks everything about playback |
| 2 | one shared player, mounted at second zero | the reported bug, and subtitles come with it |
| 3 | buffering overlay with real numbers | the 24 MB gate becomes a buffer, not a gate |
| 4 | ingest live search results | cheap, and the only way past the one-day window |
| 5 | decide Prowlarr's home | the permanent-hole fix; a topology change, so it needs a decision rather than a patch |
| 6 | per-category refresh passes | widens each pass once §5 settles |

## Not doing

- **Paging the front page.** Measured: `offset` returns nothing and `limit` is
  ignored. There is nothing to page.
- **Polling more often to beat the conveyor.** Thirty minutes against a ten-hour
  window is already ample; the hole is the PC being asleep, not the interval.
