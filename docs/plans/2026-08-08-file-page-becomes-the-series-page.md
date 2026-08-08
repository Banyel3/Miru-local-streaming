# Plan — the file page should be the series page

Status: draft
Date: 2026-08-08

Three reports, one underlying cause: **the library and the catalogue know
nothing about each other.** The wall knows about shows, posters and episodes;
`/file/{id}` knows about a path on disk. Clicking a card crosses that line and
everything falls off.

---

## 1. Art disappears the moment you click

**Reported:** *"the images display fine but when enlarging or clicking something
it just disappears."*

**Cause.** Poster art lives on `CatalogWork.poster_url` and is served from
`/api/posters/{work_id}`. `MediaFile` — the library row `/file/{id}` renders —
has no artwork field at all. So the wall shows a poster and the detail page
falls back to `ArtTile`'s generated tint, which is why the screenshot has a
blank purple rectangle where the cover should be.

The link already exists in one direction: `CatalogWork.library_file_id` points
at the file, and it is now being written on promotion. Nothing reads it
backwards.

**Fix.** `/api/files/{id}` resolves its catalog work and carries the same
`poster_url`, `overview`, `score` and `display_title` the wall uses. A library
file with no catalogue entry keeps `ArtTile`, unchanged — that is the honest
state for something dropped into the folder by hand.

---

## 2. "In this folder" is listing unrelated files

**Reported:** *"the In this folder section just seems unnecessary and
confusing."* Correct, and the screenshot shows why:

```
In this folder — 7 files
  [Diogo4D] Boku no Kokoro no Yabai Yatsu - 13
  [Erai-raws] Honzuki no Gekokujou S4 - 17
  (CBC TV 1080p HEVC AAC)  S01E05
  [SubsPlease] Honzuki no Gekokujou S4 - 17
  Monay 2026 1080p Tagalog WEB-DL
  One.Piece.EP1172
  sintel_trailer-720p
```

Four different shows, a Filipino film and a test clip, presented as if they
belonged together. The section is `siblingsOf()`, which groups by **directory**
— and every download lands flat in `/mnt/storage/media`, so "the folder" is the
entire library. It was a reasonable stand-in when the library was a folder of
season directories. It is noise now.

**Fix: delete it, and put the series' episodes there instead.** Same position,
same shape, actually related.

---

## 3. This page is where the episodes belong

**Reported:** *"this is best where we could put the episodes of the anime listed
instead of just routing to the record in the torrent and the episode, which is
such unintuitive UX."*

Agreed, and it is a better answer than the one already planned. The episode view
was going into the download sheet — reached by clicking a card you do **not**
own. But the natural place to ask *"what else is there of this show?"* is the
page for an episode you **do** own, and both need the same list.

**One component, two placements:**

```
  /file/{id}                          the download sheet
  ┌──────────────────────────┐        ┌──────────────────────────┐
  │ poster   Boku no Kokoro… │        │ Boku no Kokoro no Yabai… │
  │          Episode 13      │        │ 8 releases               │
  │          [Watch]         │        ├──────────────────────────┤
  ├──────────────────────────┤        │ Episodes                 │
  │ Episodes                 │        │  13  ✓ owned         ›   │
  │  13 ✓ owned  ▸ playing   │        │  12     2 releases   ›   │
  │  12 ✓ owned              │        │  11     1 release    ›   │
  │  11    get               │        └──────────────────────────┘
  │  10    get               │
  └──────────────────────────┘
```

The list is the union of two sources, which is the point:

- **Owned** — `media_files` rows whose catalog work is this series. Click plays.
- **Available** — `catalog_releases` for the same work that are not owned yet.
  Click opens the picker.

So one screen answers "what do I have" and "what can I get" together, and the
distinction is a marker on a row rather than two different places to look.

Ordering is descending by episode, since the newest is what a weekly watcher
wants and the highest-owned episode is where a backlog watcher resumes. Batches
stay whole — you cannot download episode 743 alone out of a `741-746` torrent,
so a batch is one row.

---

## 4. What makes this possible now

None of it was buildable a day ago. Three things landed that make it a joining
exercise rather than new machinery:

- `library_file_id` is written on promotion, so a file knows its work.
- Title resolution groups every naming variant of a show onto one work, so
  "episodes of this series" is a single query rather than a fuzzy title match.
- Posters are cached and served by work id.

The remaining gap is the reverse lookup: **file → work**. One indexed column
read backwards, and `/api/files/{id}` gains `series` and `episodes`.

---

## 5. Order

| # | step | why |
|---|---|---|
| 1 | `/api/files/{id}` resolves its catalog work | everything else needs it |
| 2 | poster and title from the work | fixes §1 on its own |
| 3 | `EpisodeList`, owned + available in one list | the component both placements share |
| 4 | replace "In this folder" with it | fixes §2 and §3 together |
| 5 | same component in the download sheet | the sheet's episode view, already planned |

## 6. Not doing

- **Keeping "In this folder" as a fallback for unmatched files.** A file with no
  catalogue entry gets no episode list at all. A list of unrelated files is
  worse than no list, which is the whole finding.
- **Guessing the series from the filename** when resolution failed. That is what
  produced four cards for one show; the honest answer is to show the file alone.
