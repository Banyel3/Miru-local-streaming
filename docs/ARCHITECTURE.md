# Miru — Architecture

Miru is a personal, self-hosted media server for one person's own anime, film, and TV
library. It runs on a Windows desktop under WSL2 and is reachable over Tailscale.

Everything below follows from two facts: **one user**, **one machine**.

---

## 1. Why a modular monolith

One FastAPI process, strict internal module boundaries.

At one user on one machine, splitting `library/`, `streaming/`, and `transcode/` into
separate services buys nothing measurable. It costs four processes to supervise, four
sets of logs, a network hop on every call that is currently a function call, and a
deployment story that no longer fits in a single systemd unit. The failure mode of the
monolith — one process dies, everything is down — is already the failure mode of the
deployment, because when the PC sleeps the whole thing is down anyway.

The boundaries still matter. They are drawn where a real seam exists, so a module
*could* be lifted out later if that ever stops being hypothetical.

### Modules

| Module | Owns | Exposes |
|---|---|---|
| `library/` | Filesystem scanning, `media_files` records, series/episode CRUD | `scan(paths)`, query functions, `/api/library`, `/api/series` |
| `metadata/` | AniList + TMDB clients, filename → title matching, artwork cache | `match(file) -> SeriesMatch` |
| `streaming/` | Range requests, HLS manifests, segment serving | `/api/stream/*`, `/api/subtitles/*` |
| `transcode/` | ffprobe, strategy resolution, ffmpeg orchestration | `probe(path) -> Probe`, `resolve_strategy(probe)` |
| `progress/` | Watch state, continue-watching | `/api/progress`, `/api/continue-watching` |
| `acquisition/` | Provider protocol only | `AcquisitionProvider` |
| `core/` | Config, DB session, jobs, auth dependency | imported by everyone |

### The rule

**No module imports another module's internals.** `library.scanner` may call
`transcode.probe_file()`; it may not import `transcode.ffmpeg` or touch a model that
`transcode` owns. `core/` is the only module everyone imports, and it holds no domain
logic.

The one dependency that crosses a seam today is `library → transcode`, and it is
deliberate: probing happens during a scan (see decision 3), so the scanner needs the
probe result to write `media_files.playback_strategy`. It goes through one function
with a plain dataclass return.

### No Redis, no Celery

Background work is: library scans, thumbnail generation, metadata fetches. All of it is
one machine's work, none of it needs distributing. A `jobs` table in Postgres plus
APScheduler covers it, survives restarts, and is inspectable with `psql`. A broker
would add a daemon whose only job is moving work between two processes that are
already the same process.

At M1 there is no APScheduler yet — a scan is a `jobs` row plus a FastAPI
`BackgroundTask`. APScheduler arrives when periodic rescans do (M2).

---

## 2. Playback strategy ladder

The most important decision in the system: **most requests must never transcode.**

Each file is probed once with `ffprobe` at scan time and the result is stored in
`media_files.playback_strategy`. At playback time the server does a column lookup, not
a decision.

| Rung | Condition | Action | Cost |
|---|---|---|---|
| 1. `direct` | Container and codecs browser-compatible (MP4/WebM + H.264/VP9/AV1 + AAC/Opus) | Serve the file with HTTP Range support | Zero |
| 2. `remux` | Video codec is fine, container is not (H.264 in MKV) | `ffmpeg -c copy` to fMP4 | Near-instant, no GPU |
| 3. `transcode_audio` | Video fine, audio is 5.1 AC3/DTS/TrueHD | Copy video, encode audio to stereo AAC | Low |
| 4. `transcode_full` | HEVC, VC-1, or otherwise undecodable in-browser | NVENC to H.264, HLS segments | GPU |

Resolution lives in `transcode/strategy.py` as a pure function over a `Probe`
dataclass. It is pure so it is testable without a media file, and so re-resolving the
whole library after a rule change is a `UPDATE` loop rather than a rescan.

**Subtitles never trigger transcoding.** ASS/SSA tracks are extracted and served
separately; JASSUB renders them client-side with full styling. A subtitle format is
never a reason to touch the video stream.

M1 implements rung 1 only. Files that probe as `remux` or above are catalogued and
listed with their real strategy, and the UI marks them as not yet playable. This is
deliberate: the ladder is visible in the data from day one, so M3 is wiring, not
redesign.

---

## 3. Data model

Per the spec (`users`, `series`, `genres`, `series_genres`, `episodes`, `media_files`,
`progress`, `favourites`, `jobs`).

Two properties are load-bearing:

- **Series exist independently of files.** A title can be catalogued before it is
  acquired, and a file disappearing does not delete the series.
- **`media_files.hash` survives moves.** Renaming or relocating a file is detected as
  the same file, not as a delete plus an add, so watch progress is preserved.

M1 creates only what M1 uses: `media_files` and `jobs`. The rest arrive with the
metadata module in M2.

Schema is created with `Base.metadata.create_all()` at M1, not Alembic. There is one
database, on one machine, with no production data to preserve — the migration story is
"drop and rescan", and a rescan is cheap because the files are the source of truth.
Alembic lands when `progress` does (M3), because watch state is the first data in the
system that cannot be rebuilt from the filesystem.

---

## 4. API contract

```
GET    /api/library?type=&genre=&q=&sort=    -> Series[]
GET    /api/series/{id}                      -> Series + Episode[]
GET    /api/episodes/{id}                    -> Episode + MediaFile
GET    /api/continue-watching                -> Progress[] with Series
GET    /api/stream/{fileId}                  -> direct/remux, Range-capable
GET    /api/stream/{fileId}/index.m3u8       -> HLS manifest
GET    /api/stream/{fileId}/seg/{n}.ts       -> HLS segment
GET    /api/subtitles/{fileId}/{track}       -> ASS or VTT
POST   /api/progress                         -> { episodeId, positionMs }
POST   /api/library/scan                     -> enqueue scan job
GET    /api/jobs/{id}                        -> job status
```

Pydantic models are the contract. TypeScript types are generated from the OpenAPI
schema into `packages/types` (M2, once the shapes stop moving). Neither side reaches
across the boundary.

**HTTP Range support is mandatory** on the direct/remux path: `Accept-Ranges: bytes`,
`206 Partial Content`, correct `Content-Range`. Without it, every seek restarts the
download. Starlette's `FileResponse` implements this correctly, so Miru does not
hand-roll it — but `tests/test_range.py` asserts the behaviour, because it is the one
thing that makes the difference between a media server and a download link.

**Auth:** single user. A long-lived token compared against `MIRU_TOKEN`, accepted from
an httpOnly cookie or a bearer header. Tailscale is the real perimeter; this exists so
that a misconfigured port is not an open library. If `MIRU_TOKEN` is unset the API is
open, which is the correct default for a first run on localhost.

No OAuth, no user management, no password reset. There is one person.

---

## 5. Open decisions, resolved

### Postgres native in WSL2, or Docker container

**Native.** WSL2 already runs a full Ubuntu; Postgres 16 installs from apt and starts
with `service postgresql start`. Running it in Docker means either Docker Desktop with
WSL integration (a second VM's worth of overhead on a machine that is also transcoding
video) or dockerd inside WSL2 (another daemon to start on a system that already needs
Task Scheduler to start WSL itself).

The usual argument for containerised Postgres — reproducible environments across a
team — does not apply to a single machine with a single user. `docker/compose.yml` is
included as the alternative for anyone who wants it, but the documented path is
native.

### HLS versus fMP4 for transcoded output

**HLS**, as the spec defaults. It handles the live-generated case cleanly: segments
can be produced ahead of the playhead and the manifest grows, so playback starts after
one segment instead of after the whole file. Seeking past the encoded region is a
manifest problem with a known solution (restart the encode at the target segment)
rather than a byte-offset problem in a file that does not exist yet. Vidstack supports
it natively via hls.js.

fMP4 is used for the `remux` rung, where the whole file is a copy operation and no
segmenting is needed. So both exist, at different rungs, for different reasons.

### Probe during scan, or lazily on first playback

**During scan**, in the background job.

The concern is that `/mnt/` is slow — but it is slow for *metadata* operations:
`stat`, directory walks, per-file syscalls across the 9p/drvfs boundary. `ffprobe`
reads a few hundred KB from the head of the file, which is a sequential read, and
sequential reads across `/mnt/` are fine.

Lazy probing would put a 200ms–2s stall in front of the first play of every file, on
the interaction where latency is most visible, and would still need the eager path as
a fallback for anything the UI wants to display before playback (resolution, audio
layout, subtitle tracks — all of which the detail screen shows). It trades a
background cost for a foreground one.

The scan is already a job with a progress row. Probing inside it costs wall-clock time
nobody is waiting on.

---

## 6. What M1 actually is

FastAPI + Postgres in WSL2. Scan a directory, list what was found, serve it with Range
requests, play it in a browser. Direct play only, no metadata, no transcoding.

This is already a usable media server, which is why it ships before anything else.
