# Handoff — 2026-08-08

Written at the end of a long session, so the next one does not re-derive any of
it. Everything here is measured, not remembered. Commits are on `main`.

---

## 1. Open right now, in the order I would take them

> **2026-08-08, later session:** every item in this section except the
> qBittorrent password (script ready, PC asleep) and the 323 episodeless
> releases has shipped. §1.3's question was answered: Watch Now is now
> ephemeral — the Stremio model — with Keep as the opt-in. See
> docs/plans/STATUS.md for the settled record. The section below is kept as
> written for the reasoning.

### 1.1 The library player shows a bare spinner for minutes

**Reported as "remux is failing". It is not failing — it is silent.**

A 3.8 GB MKV opened at `/watch/40` took about seven minutes to remux, running at
roughly 10 MB/s because source and output share the disk that had just been
writing the download:

```
17:44  .part  1.35 GB
17:46  .part  1.73 GB
17:50  .part  3.42 GB
17:52  done   /api/stream/40 -> 206      # it played
```

`/api/stream/{id}` answers `425 Too Early` with
`{"detail":"Preparing this file for playback — try again shortly."}` the whole
time, and `/watch/[id]` renders nothing but a spinner. Seven minutes of that is
indistinguishable from a hang, which is exactly how it was read.

**Fix:** the live player already says *"Getting it ready to play in your
browser — this takes a moment."* (`components/LiveWatch.tsx`). The library
player needs the same, and it can do better than a message: the `.part` file's
size against the source's size is a real percentage, sitting on disk unused.

### 1.2 The same film is remuxed twice

The live path keys its remux on `info_hash_key(info_hash)`; the library path
keys on the `MediaFile.id`. So a film watched while downloading is remuxed
again, from scratch, the first time it is opened from the library. Two identical
1.34 GB copies of *Spider-Man: Brand New Day* are in `/mnt/storage/miru-remux`
as I write this.

**Fix:** when the mover promotes a download and the scan links it, hand the live
remux over — rename it to the library key rather than discarding it.
`_link_by_filename` in `catalog/router.py` is where the two identities meet.

### 1.3 Watch Now downloads the whole file, and that is not a bug

Reported as *"it finished downloading even when I just clicked watch now"*.
BitTorrent fetches the entire file either way. Sequential mode changes the
**order** pieces arrive — front to back rather than rarest-first — so playback
can start after ~24 MB instead of at 100%. It does not download less.

The UI never says this, so the expectation is reasonable and unmet. Either the
copy explains it, or Miru wants a genuine streaming mode, which is a different
architecture and a decision rather than a fix.

### 1.4 Still open from earlier

- `_restate_works` is an N+1 with row locks.
- Content-Length is promised before the read, so a short read truncates the body
  under a length already sent to the client.
- **qBittorrent's password is still the literal string `YOURPASSWORD`.**
- Per-category refresh passes (`player-and-coverage` §6).
- The episode list inside the download sheet (`file-page` §5).
- 975 of ~2000 releases carry no episode number — a separate parsing job.

---

## 2. Decisions taken this session, with the measurement behind each

| decision | why, measured |
|---|---|
| Provider decides `kind`, not the indexer | TPB carries no anime tag, so it called Frieren a series → TVmaze; Nyaa called it anime → AniList. Two provider ids, never mergeable. **Frieren was 12 cards holding 141 releases.** |
| AniList asked first for every kind | It holds anime and nothing else, so an answer is a claim about the show rather than about who carried the release |
| Anime films in the Anime rail, split by `format` | AniList already says MOVIE vs TV; the wall has no room for a fifth pill at 375px |
| Adult hidden by the provider's flag only | Caught 14. Cannot reach erotic drama: **TVmaze has no adult field at all**, and TMDB answers `adult=False` for *Sex Trip*. A keyword rule would also hide *HK Hentai Kamen*, a mainstream comedy |
| Pack sweep on card open, not a background sweep of all 269 | Your call. 269 searches × 4 indexers on a schedule, for shows you may never open |
| Default pick = smallest complete unit | 630 GB against 860 GB free is 73% of the disk in one click |
| Search filtered by `classify()` | 1650 results over 8 queries carried **326 that are not video**: 280 XXX, 50 PC/Games, 60 Books, 26 Audio. Every row has a download button |

---

## 3. What the sources actually do

Re-measured today. `CLAUDE.md` says measure before designing against it; this is
the measurement.

```
front page depth        about one day. `limit` ignored, `offset` returns nothing
batches on it           54 of 2092 releases (2%)
ONE PIECE               206 releases -> 82 distinct episodes of 1172
BLACK TORCH              26 releases ->  5 distinct episodes

but asking works:
  'one piece batch'     126 results, all packs, up to 1-1071 (630 GB)
  'frieren batch'        57 results, incl. (01-28) Batch, 42 GB
  'spy x family complete' the ONLY query that surfaces the Trix season pack
```

**Completeness is a query problem, not a filter problem.** Nothing can rank its
way to episodes that were never fetched.

Other source facts that have each broken something: Prowlarr re-encrypts its
guids every response; Nyaa dual-tags every anime as Movies/Other **and** files
adult anime under TV/Anime; seeder counts are not comparable between indexers.

---

## 4. Traps that cost real time today

- **nginx `proxy_set_header` does not inherit** into a `location` that sets any
  header of its own. Every location in `miru.conf` set one, so the server-level
  `Host $host` applied to nothing and became `$proxy_host`. Next then decided
  every Server Action was cross-origin and aborted it — every card read
  "Couldn't load the releases for this" while the API answered 200. Headers now
  live in a snippet each location includes.
- **The web is `next start`, a production build.** Web source changes are
  invisible until `systemctl --user stop miru-web && npm run build &&
  systemctl --user start miru-web`. The player fixes were committed, tested and
  simply not running; the build was from 13:01 against source from 13:53.
- **Grep the log for the wrong words and you miss the cause.** The nginx
  diagnosis sat one line above the line I was grepping for, and contains none of
  `error|fail|ECONN|fetch`.
- **A cache key that changes on every request is worse than a stale one.**
  Keying the live remux on the completed-prefix length made every request a miss
  on a growing file: one gigabyte-scale ffmpeg per poll, a finished remux never
  served, 4.8 GB accumulated. The test that would have caught it is one line of
  intent — *two requests at different prefixes must start one ffmpeg.*

---

## 5. Verification

```bash
cd apps/api    && ../../.venv/bin/python -m pytest -q     # 464
cd apps/worker && ../../.venv/bin/python -m pytest -q     # 17
cd apps/web    && npx vitest run && npx tsc --noEmit      # 23, clean
```

Live: `http://100.71.150.101/` (MagicDNS does not resolve from the laptop — use
the raw tailnet IP). Services are `miru-api` and `miru-web`, user units.

One-off scripts used today live in the job's tmp directory and are gone; each is
reconstructable from the sections above. The re-parse/re-group one is worth
keeping if the parser changes again: iterate `catalog_releases`, re-`parse()`,
reassign via `_work_for`, `_restate_works`, delete empty works.

---

## 6. Not answered

**Exposing Miru off the tailnet.** `tailscale funnel 80` does it, no
port-forwarding, TLS included. **Miru has no authentication**, so this would put
the library, the download button and the indexer search on the open internet —
against the tailnet-only rule already set for Prowlarr, aria2 and the worker.
Options, in the order I would trust them: share the tailnet node (Tailscale's
free tier does this), add auth and then Funnel, or Funnel as-is with eyes open.
Awaiting a decision; nothing has been enabled.
