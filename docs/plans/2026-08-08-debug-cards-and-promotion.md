# Debug — nameless cards, "random" sorting, and downloads that never land

Status: diagnosed, fixes specified
Date: 2026-08-08

Three reports, three different causes. One of them is not a bug in the thing
that was reported.

---

## 1. Cards with a poster have no name anywhere

**Reported:** *"some cards dont have their names… some cards still dont show
anything at all."*

**Cause — mine, introduced two commits ago.** The card used to print the title
twice: once as type on the `ArtTile` and again in the caption. Removing the
duplicate, I kept the tile copy and made the caption heading `sr-only`:

```tsx
label={art ? undefined : work.title}   // tile shows the title ONLY when there is no poster
...
<h3 className="sr-only">{work.title}</h3>
```

So the two states diverged:

| | tile | caption | visible name |
|---|---|---|---|
| no poster | title as type | chips | yes |
| **poster** | the poster | chips | **none** |

Which is exactly the screenshot: the *Latest releases* rail (no posters yet)
shows names, and *Trending now* (posters) shows anonymous artwork. The fix I
made for duplication created absence for the half of the wall that has art —
and as poster coverage rises, more of the wall goes nameless.

**Fix.** The caption always carries the name; the tile never does. A poster is
artwork, not a label — it does not reliably contain a legible title, especially
at 150px, and for a film it is often not in English. One line each way:

```tsx
label={undefined}                                  // the tile is artwork only
<h3 className="truncate text-[13.5px] font-bold">  // the caption is the name
```

The no-poster case keeps the generated tint tile behind it, so it reads as
"artwork pending" rather than as an empty box, and the name is in the same
place either way.

---

## 2. The sorting is not random. The number on the card is not the sort key.

**Reported:** *"Sorting still random, on some cards they're 2026 the next tag
becomes 1996… its never latest to old."*

**Measured — the rail is correctly ordered:**

```
uploaded 2026-08-08 10:40  |  year chip: 2026  |  Governor
uploaded 2026-08-08 10:40  |  year chip: 1999  |  Boys Don't Cry
uploaded 2026-08-08 10:30  |  year chip: 1984  |  Paris Texas
uploaded 2026-08-08 10:30  |  year chip: 2022  |  365 Days This Day
uploaded 2026-08-08 10:29  |  year chip: 1991  |  Until the End of the World
```

The upload timestamps descend perfectly. The chip is `work.year` — **the year
the film was made**, which has nothing to do with when it was posted. *Latest
releases* means "recently uploaded", and a 1984 film uploaded ten minutes ago
belongs at the top.

So the ordering is right and the label is lying about what the row is sorted by.
Showing a number that is not the sort key, in a row whose heading promises an
order, is a UI bug even though the query is correct.

**Fix.** A rail labels rows with its own sort key.

| rail | chip |
|---|---|
| Latest releases | **"2h ago"** — relative upload age, the thing it is sorted by |
| Trending now | seeder standing, or nothing |
| everywhere else | the film year, as now |

The film year moves to the release sheet and the detail view, where it is a fact
about the film rather than a claim about the row's position.

---

## 3. Finished downloads never leave "Adding to library…"

**Reported earlier and still true.** Measured — both completed files are still
sitting in `incoming`:

```
/mnt/storage/incoming/
  [Erai-raws] Honzuki no Gekokujou S4 - 17 …mkv     1.47 GB   11:05
  [NanakoRaws] Super no Ura de Yani Suu Futari …mkv  245 MB   11:31
```

**Cause: nothing ever triggers a library scan.** Promotion happens inside
`scan()`, and the only caller is the **Settings → Scan button**:

```
apps/web/components/ScanPanel.tsx:30   const started = await startScan();
```

The catalog refresh task runs every 30 minutes and calls `refresh()` and
`backfill()` — never `scan()`. So a download completes, qBittorrent reports
`done`, `in_library` stays false because the file is still in `incoming`, and
the card sits at *"Adding to your library…"* forever. The `library_file_id`
linking added earlier is correct and never runs, because the scan it hangs off
never happens.

This also explains the earlier report that files *did* eventually appear "only
in the home rail" — that was after a manual scan.

**Fix, two parts:**

- **The scheduler runs a scan**, not just a catalog refresh. It is cheap when
  nothing changed: the scanner already skips unchanged files by size and mtime,
  so a pass over a settled library is a stat per file.
- **A completing download triggers one directly**, rather than waiting up to
  thirty minutes. `/api/catalog/downloads` already knows the moment a job turns
  `done`; that is the point to enqueue a scan, once, guarded so ten finishing
  downloads do not start ten scans.

The 120-second settle delay stays. It is what stops a still-being-written file
from being promoted and probed as garbage.

---

## Why these three shipped together

All three are the same class of mistake: **a change that was correct in itself,
with an unexamined second case.**

- The duplicate title was real; removing it without checking the poster case
  removed the name entirely.
- The recency sort was real; adding it without changing the label left the card
  advertising a different number.
- The promotion link was real; adding it without checking that anything calls
  `scan()` meant it never executed.

The pattern worth naming: each fix was verified against the case that motivated
it and not against its sibling. The tests have the same shape — they assert the
motivating case and stop.
