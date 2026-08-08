# Plan — why nothing plays, and why One Piece is two cards

Status: executed — §1 live MKV and §2a title cleaning landed 2026-08-08. See STATUS.md
Date: 2026-08-08

Six reported problems. One of them turned out to have a cause nobody guessed —
the file was DRM-encrypted — and two of the others share a root cause the
codebase already had the answer to.

---

## 0. Correction: the file that would not play is DRM-encrypted

An earlier reading of this blamed the Matroska container. That was wrong, or at
least incomplete, and the real cause is worse. Probing the completed file:

```
Stream #0:0(jpn): Video: none (encv / 0x76636E65), none, 1920x1080
codec_name      = unknown
codec_tag_string= encv
pix_fmt         = unknown
```

`encv` is the MPEG Common Encryption marker for an **encrypted video track**.
This particular release — a Crunchyroll WEB-DL — shipped with the video never
decrypted. Nothing can play it: not the browser, not ffmpeg, not VLC. ffmpeg
refuses even to remux it:

```
Could not find codec parameters for stream 0 (Video: none (encv), 1920x1080)
Could not find tag for codec none in stream #0
```

So the black player with a running clock was not Miru failing to serve the file.
It was Miru faithfully serving a file that cannot be decoded by anything.

**But Miru does have a real bug here, and it is the one worth fixing.** It
cannot tell "encrypted and undecodable" apart from "not probed yet":

```
probe: container=matroska video=None audio='aac' 1920x1080
resolve_strategy -> direct
```

`resolve_strategy` treats a missing video codec as *unprobed* and optimistically
returns `direct`, on the documented grounds that "a wrong direct costs one
failed play". For an encrypted file that reasoning does not hold: it is not one
failed play, it is a black player forever, with no explanation, on every attempt.

**Fix:**

- `probe_file` reads `codec_tag_string`. A tag of `encv`/`enca`, or a video
  stream whose `codec_name` is `unknown` while a valid `width` and `height` are
  present, means undecodable — which is distinguishable from unprobed, where
  there are no dimensions either.
- A fifth strategy, `unplayable`, joins the ladder. It never reaches the worker,
  because there is nothing the GPU can do about it.
- Both the card and the player say so plainly: *"This release is DRM-encrypted
  and can't be played. Try a different release."* — with a button that reopens
  the picker, since the fix is a different release rather than anything the user
  can do to this one.
- The scan applies it to library files too, so an encrypted file that arrives by
  any route is labelled rather than silently black.

This is also the strongest argument yet for the release picker: the user has no
way to know a release is encrypted before downloading it, so the wall should
learn from it. When a downloaded release probes as `unplayable`, the release row
is marked, and the picker never recommends it again.

---

## 1. Live playback still serves a container the browser should not be given

Independent of §0, and still real for every other MKV.

**Measured.** The file being served:

```
container : matroska,webm
audio     : aac
subtitle  : ass × 16 languages
attachment: ttf × 23
```

`/api/stream/live/{hash}` serves those bytes with `Content-Type:
video/x-matroska`. Browser support for Matroska is inconsistent at best — Chrome
will sometimes read the duration from one and still refuse to decode it, which
is precisely the `0:00 / 23:50` in the second screenshot — and a `<video>` given
a container it cannot demux collapses to the element's default intrinsic size of
300×150, the "1/8 of the screen" strip.

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
| 0 | §0 detect undecodable files | the reported symptom's actual cause; a black player with no explanation is the worst failure in the list |
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
