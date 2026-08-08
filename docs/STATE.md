# Miru — current state

Written as a handoff. If you are picking this up with no memory of the sessions
that built it, read this first, then `DEPLOYMENT.md` for the shape and
`SETUP-PC.md` for the second machine.

Last updated: 2026-08-08

---

## 1. What Miru is

A personal, single-user media server for anime, film and TV. Self-hosted,
local-first, reachable from anywhere over Tailscale. Deliberately not
multi-tenant, not a Plex competitor, not distributed.

The original brief is in `ARCHITECTURE.md`. Where this document and that one
disagree, **this one and `DEPLOYMENT.md` are current** — several decisions were
reversed once real hardware met the design, and the reversals are recorded in
`DEPLOYMENT.md` §8.

---

## 2. The two machines

```
LAPTOP — ban-1 — 100.71.150.101 — always on          PC — ban-pc — 100.67.44.13 — on demand
  i5-1155G7, 932 GB HDD (ext4), wifi only              Ryzen 5700G, RTX 5060, Win11 + WSL2
  Next.js          :3001                               NVENC worker      :8010
  FastAPI          :8000                               Prowlarr (search) :9696
  Postgres (docker):5432                               aria2 (download)  :6800
  media  /mnt/storage/media                            mounts the laptop's /mnt/storage/incoming
```

**The laptop is the server.** Library, database, UI, API. Up whenever you are.

**The PC is an accelerator.** It re-encodes video and it downloads. Only
`transcode_audio` and `transcode_full` depend on it; everything else keeps
working when it is asleep, and the UI says so per file.

Tailscale is the transport. MagicDNS names do **not** resolve on the laptop
(`/etc/resolv.conf` holds only `1.1.1.1`), so **use the raw 100.x addresses**.
`tailscale ping` succeeds with names and misleads you — it resolves internally
while curl, ffmpeg and uvicorn go through system DNS and fail.

---

## 3. Storage layout

```
/mnt/storage/            ext4 on /dev/sda1 (932 GB) — reformatted from exFAT
  media/                 the library. MIRU_LIBRARY_PATHS points here. Scanned.
  incoming/              NFS drop-box, exported to the PC only. NOT scanned.
  files/                 FileBrowser's root — deliberately outside the library
  lost+found/
```

`files/` is separate because **FileBrowser is published on the public internet**
via cloudflared at `files.vancornelio.dev`. It is a systemd service
(`/usr/local/bin/filebrowser`, database at `/var/lib/filebrowser/`), re-rooted so
your media is not reachable through that tunnel.

`incoming/` is not scanned on purpose: a growing file probes as garbage, and
because the scan also writes size and mtime it would never be re-probed.

> One drive, no redundancy. This is capacity, not durability.

---

## 4. What is built and verified

| Area | State |
|---|---|
| Scanner, `media_files`, jobs | Working. Incremental on size+mtime |
| Range streaming | Working. 206, Content-Range, suffix ranges, 416/410/404 |
| Strategy ladder | Working: direct / remux / transcode_audio / transcode_full |
| Frontend | Home, Library, Favourites, Settings, file detail, player |
| Progress + favourites | localStorage behind `lib/store.ts`, swaps to API at M3 |
| Subtitles | ASS via JASSUB with full styling; sidecar + embedded extraction |
| Transcoding (M4) | NVENC on the PC, adaptive ladder, quality menu, clean switching |
| Availability | Derived per request from (strategy, worker reachable) |
| Acquisition | Prowlarr search → aria2 → NFS → mover → library. **Verified end to end** |
| Tests | 30 API, 12 worker |

The acquisition chain was proven with a real download: Miru's endpoint → aria2
RPC on the PC → NFS write to the laptop → held back while fresh → promoted after
the settle window → probed → playable.

---

## 5. Open problems, in the order they will hurt

### P1 — from the engineering review (`docs/reviews/2026-08-07-eng-review.md`)

**T3 — Hi10p is marked `direct` and will not play.** 10-bit H.264 probes as
plain `h264`, no browser decodes it, and the ladder promises direct play. It is
standard in anime fansubbing, so the first thing grabbed from nyaa is likely to
hit it. `probe_file` does not capture `pix_fmt` or `profile`, so the data needed
to fix it is not even in the database — this needs a rescan, not an UPDATE.

**T4 — a failed probe poisons a row permanently.** `scanner.py` writes size and
mtime regardless of whether the probe succeeded, so one ffprobe timeout marks a
file direct-playable with null metadata and it is never retried.

**T1 — a scan with the drive unmounted deletes the library.** A missing root is
treated as an empty one, then every unseen row is deleted.

**T2** advisory lock on scans (use `pg_try_advisory_xact_lock`, not the session
variant — SQLAlchemy returns pooled connections without releasing session locks).
**T5** auth: password gate + `stream_tokens`. **T6** remaining config drift.

### P2 — known bugs

**The player shows LIVE with a red timeline while an encode runs.** HLS event
playlists carry no `EXT-X-ENDLIST` until ffmpeg finishes. Seeking backward works,
but a two-hour film labelled LIVE is wrong.

**CJK subtitles render as tofu boxes.** libass resolves the style's font (usually
Arial) to the bundled Latin face and will not reach into the shipped Noto file
for missing glyphs. Adding `availableFonts` + `defaultFont` made it *worse* — the
line vanished silently. Needs the fontselect name mapping worked out properly.
For a Japanese-first library this is not a footnote.

**Watch Now no longer exists.** It depended on `movies-downloader` streaming
torrent bytes over HTTP. aria2 replaced it and writes to disk, so there is no
live source to transcode from. See `DEPLOYMENT.md` §3.

### P3

No acquisition UI — the endpoints work, there is no search screen.
No metadata (M2): titles are filenames, `displayTitle()` splits SxxEyy off as a
stopgap until anitopy.
`media_files.hash` does not exist, though `ARCHITECTURE.md` calls it load-bearing
for surviving file moves — an M3 blocker.

---

## 6. Traps this environment has already sprung

Four separate times, a Windows-side layer intercepted something inside WSL and
presented as a Linux problem. Expect a fifth.

| Symptom | Actually |
|---|---|
| `apt`, `sudo`, `python3` not found; prompt shows `/mnt/host/c` | You are in Docker Desktop's WSL VM, not Ubuntu |
| npm spawns CMD.EXE, logs to `C:\Users\...\npm-cache` | Windows npm via PATH interop; Node not installed inside Ubuntu |
| `EADDRINUSE` with nothing in `ss -ltn`, curl times out | VS Code's port forwarder holding a `CLOSE_WAIT` socket |
| `docker run` puts a container somewhere unexpected | Docker Desktop's VM, not your Ubuntu distro |

Plus two of my own that cost real time: a cross-origin CORS **redirect** taints
`Origin` to `null` so the worker can never match it (hand the browser the worker
URL directly instead), and running `next build` against the same `.next` a dev
server is using 500s the dev server.

---

## 7. Credentials and where they live

| What | Where |
|---|---|
| Prowlarr API key | Prowlarr → Settings → General; mirrored in laptop `.env` |
| aria2 RPC secret | `~/.aria2/rpc-secret` on the PC; mirrored in `aria2.conf` and laptop `.env` |
| Postgres | `miru:miru@localhost/miru`, Docker container `docker_db_1` |
| `MIRU_TOKEN` | Empty. Auth is unbuilt — see T5 |

`.env` on the laptop is gitignored and is the single source of runtime config.
`.env.example` documents every key.

---

## 8. Running it

`scripts/miru` starts everything for whichever machine it is run on. See
`docs/RUNNING.md`.

Manual equivalents, if the script is not there:

```bash
# laptop
docker start docker_db_1
set -a && . ./.env && set +a
.venv/bin/uvicorn miru.main:app --host 0.0.0.0 --port 8000 --app-dir apps/api &
(cd apps/web && npm run dev)          # pinned to :3001, since :3000 is the portfolio

# PC (Ubuntu WSL, not docker-desktop)
cd ~/miru/apps/worker && ../../.venv/bin/uvicorn miru_worker.main:app --host 0.0.0.0 --port 8010 &
/opt/Prowlarr/Prowlarr -nobrowser -data=$HOME/.config/prowlarr &
aria2c --conf-path="$HOME/.aria2/aria2.conf" &
```

Nothing survives a reboot yet. That is the startup-script step at the end of
`SETUP-PC.md`.

---

## 9. Decisions that are settled

Do not re-litigate these without a reason; each cost a conversation.

- **Modular monolith**, one FastAPI app. The worker is the sole exception, and it
  holds no database and no domain models.
- **Miru runs on the laptop**, not the PC — reversing the original plan. The
  laptop is always on; the PC sleeps.
- **Postgres on the laptop in Docker**, reversing "native in WSL2".
- **Nothing that re-encodes video runs on the laptop.** `remux` stays local
  because it is `-c copy` (0.06% of a core) and is the majority rung for anime.
- **The transcode worker takes a URL, never a path.** No filesystem sharing is
  needed to transcode.
- **Adaptive ladder**, master playlist, capped by source, never upscaling.
- **Availability is derived per request**, never stored.
- **Tailnet IPs, not MagicDNS names.**
- **Prowlarr for search, aria2 for downloads.** `movies-downloader` was tried and
  dropped: its scrapers are rotted (TPB no longer emits `<tbody>`, which its
  selector requires) and it fails silently by collapsing errors into empty
  arrays.
- **Errors surface as 502**, never as an empty result list.
