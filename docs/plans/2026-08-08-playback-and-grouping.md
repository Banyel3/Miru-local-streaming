# Plan — why nothing plays, and why One Piece is two cards

Status: draft
Date: 2026-08-08

Six reported problems. Two of them share a root cause that the codebase already
had the answer to, which is the interesting part.

---

## 1. The headline bug: live playback serves a container no browser can play

**Reported:** *"the player appears as 1/8 of the screen and never plays, it just
loops."*

**Measured.** The file being served:

```
container : matroska,webm
video     : 1920x1080          (AVC)
audio     : aac
subtitle  : ass × 16 languages
attachment: ttf × 23
```

`/api/stream/live/{hash}` serves those bytes with `Content-Type:
video/x-matroska`. **No browser plays Matroska.** Chrome, Brave and Firefox all
refuse it, and a `<video>` given a container it cannot demux shows exactly what
the screenshot shows: `0:00`, a spinner, and a collapse to the element's default
intrinsic size of 300×150 — the "1/8 of the screen" strip.

This is not a new discovery. Miru's own ladder has said so since M1:

```python
CONTAINER_OK = {"mp4", "m4v", "mov", "webm"}   # transcode/strategy.py:21
...
if not container_ok:
    return REMUX
```

Every MKV in the library already goes through `remux` for exactly this reason.
The live path was built as "serve the growing file over Range" and skipped the
ladder entirely — so it works for the MP4 minority and fails for the MKV
majority, which is most of an anime library.

**The fix is not to remux on the laptop.** That was rejected early and the
reason still holds: the laptop is not the machine for that load.

**Fix: the live path goes through the worker, like every other non-direct file.**
The worker already produces HLS with `-hls_playlist_type event`, which is
precisely the format for a source that is still growing. And the source is
already local to it: the PC writes into the laptop's `incoming` over NFS at
`/mnt/incoming`, so the worker reads the partial file from disk rather than
pulling it over HTTP — no Range fetch, no EOF-at-the-prefix problem to solve.

```
  now (broken)
    browser ──Range──> laptop API ──> raw .mkv bytes        ✗ browser can't demux

  fixed
    browser ──HLS──> PC worker ──reads /mnt/incoming──> fMP4 segments   ✓
                          └── ffmpeg -re -copyts -c:v copy -c:a copy
```

For a growing source the remux is stream-copy only, so it is cheap: no encode,
just rewrapping into fMP4. The worker restarts the segmenter when it reaches the
current end and the playlist grows, which is what `event` playlists are for.

**MP4 releases keep the direct path.** They are already playable, and sending
them through the worker would make the PC a dependency for the one case that
does not need it.

### 1a. There must be one player, not two

*"why is it a separate player... shouldn't we just use one at all"* — correct,
and this is the deeper version of the same bug.

`/watch/[id]` uses Vidstack: HLS via bundled hls.js, ASS subtitles rendered by
JASSUB, a quality menu, resume, next-episode countdown. `/watching/[hash]` uses
a bare `<video controls>`. That is why the second screenshot shows the browser's
own chrome instead of Miru's, and it is why **subtitles cannot render there at
all** — nothing in a bare `<video>` knows what JASSUB is. Problem §5 is partly
just this.

Two players also means every future playback fix has to be made twice, and the
second one will be forgotten.

**Fix: one `Player` component, used by both routes.** They differ in exactly two
ways, both of which are props rather than a fork:

| | `/watch/[id]` | `/watching/[hash]` |
|---|---|---|
| source | library file | file still being written |
| seekable | whole file | up to the completed prefix |

The second row is the only real behavioural difference, and Vidstack already
models it — a live/event HLS playlist with a moving seekable range is exactly
the shape it expects, which is another reason §1's HLS route is the right one:
it makes the live case a normal case rather than a special one.

Once both routes share a player, the `<video>` collapsing to a 300×150 strip
stops being possible, because the shared component already sets its own aspect
ratio.

---

## 2. One Piece is two cards, and anime films sit in with the series

**Reported:** *"I chose one piece and it's actually just ep 1172."*

**Measured:**

```
work  6: anime 'ONE PIECE'          8 releases
work 83: anime 'One Piece S01E1172' 1 release
```

anitopy is built for `[Group] Title - 01 [1080p]` and handles it perfectly — all
eight of the `[RLSP] One Piece 741-743` shapes parsed to `One Piece`. But a
release named in scene style, `One Piece S01E1172`, is not an anime-style name,
and anitopy leaves the `S01E1172` in the title. So the same show becomes two
works.

**Fix, two parts.**

**2a. Strip episode and season markers from the parsed title before grouping.**
A post-parse pass removes trailing `S01E1172`, `- 17`, `1x05`, `Episode 3`,
`741-743` and season words from whatever either parser returned. Grouping keys
off the cleaned title, so scene-named and fansub-named releases of one show land
on one card whichever parser saw them.

**2b. Anime films are their own kind.** Right now `classify()` returns `anime`
for everything Nyaa tags, so *One Piece Film: Red* sits in a rail next to a
weekly episode. anitopy already reports `anime_type` (`Movie`, `OVA`, `ONA`,
`Special`), and a release with no episode number and a film marker is a film.
The wall gains a fourth kind, `anime_movie`, and the filter pills become
**All · Anime · Anime films · Movies · Series**.

**2c. A series card opens to its episodes, not to a release picker.** Today
clicking a series shows a flat list of every release. It should show the show,
then its episodes, then the releases for the episode chosen. The data is already
there — `episode` and `episode_end` are stored per release — it is a grouping in
the sheet rather than new ingest.

---

## 3. Downloads land in the wall but not in `/library`

**Reported:** *"downloaded files appear only in the In your library section on
the home screen and not the Library sidebar item, which is confusing."*

Both read the same API. The difference is timing: the home rail renders whatever
`getLibrary()` returned on that request, while `/library` was rendered earlier
and cached by the router. The file is in the database; the page is stale.

**Fix:** `refreshLibrary()` already exists and is called when a download
completes — it needs to invalidate `/library` too, not just `/`. One line, plus
a scan trigger when the mover promotes something so there is no window where the
file exists and the library does not know.

---

## 4. A download cannot be stopped

**Reported:** *"when something downloads there's just no way to cancel/stop it
pause it."*

The API has `cancel()` and qBittorrent supports pause and resume; none of it is
exposed. `cancel` already passes `deleteFiles=false`, so stopping never destroys
something part-watched.

**Fix:** pause / resume / cancel on the sidebar Downloading row and on the
waiting screen. Cancel asks first, because it is the one that is not reversible
from the UI.

---

## 5. Subtitles do not render

**Measured:** the test file carries **16 ASS subtitle tracks and 23 font
attachments** inside the MKV. Nothing is extracted for a live file, so there is
nothing for JASSUB to render — and once §1 routes live playback through the
worker, the subtitle streams have to be published on the HLS side too.

Two distinct gaps:

- **Live files:** subtitles must be extracted from the partial file once enough
  has arrived, and served as they already are for library files.
- **Fonts:** those 23 attachments are the fonts the typesetting expects. JASSUB
  renders with them or the styling collapses to a default face. They need
  extracting and serving alongside the track.

---

## 6. Around half the wall still has no art

**Measured:** 214 of 412 works (52%).

The misses are mostly releases whose parsed title is still messy — which §2a
directly improves, since a cleaner title is a better search term. Beyond that:

- Retry with the year stripped, then with bracketed junk removed.
- For anime, try AniList with the romaji *and* the English title.
- Cache negative results with a timestamp rather than a permanent `none`, so a
  title that fails today is retried next week rather than never.

---

## Order of work

| # | fix | why this order |
|---|---|---|
| 1 | §1a one shared player | the bare `<video>` is why there are no subtitles and no Miru chrome; every other playback fix depends on there being one place to make it |
| 2 | §1 live playback through the worker | nothing else matters if playback does not work, and MKV cannot play without it |
| 3 | §2a title cleaning | wrong cards are the most visible wrongness after playback |
| 4 | §3 library staleness | data loss is only apparent, but it reads as loss |
| 5 | §4 pause / cancel | small, and removes a trapped feeling |
| 6 | §2b anime films, §2c episode view | structural, worth doing once §2a lands |
| 7 | §5 subtitles and fonts | largely falls out of §1a, since the shared player already renders ASS |
| 8 | §6 art coverage | improves on its own once §2a lands |

---

## What is deliberately not here

- **Transcoding on the laptop.** Rejected repeatedly and still rejected.
- **A second downloader.** Settled; see DEPLOYMENT.md.
- **Alembic.** Still owed, and now overdue: §2 changes the works table, and the
  catalogue is at 646 releases. It should land with §2 rather than after it.
