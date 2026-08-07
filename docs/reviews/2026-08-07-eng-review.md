# Engineering review — M1

Date: 2026-08-07
Branch: `main` @ `797cbe5`
Reviewers: interactive eng review + independent outside voice (Claude subagent;
Codex was installed but unauthenticated)

**Result: 39 findings, 18 decisions, 0 unresolved, 4 critical gaps. NOT CLEARED —
six P1 tasks open.**

---

## Scope

M1 as landed: incremental scanner, ffprobe playback strategy ladder,
Range-capable streaming endpoint, home grid and player screens. 43 files and
3541 lines, but 780 of that is docs and 1100 is `package-lock.json` — the real
surface is about 900 lines. No scope reduction was needed. Nothing is
speculative except `acquisition/provider.py`, which the spec asked for by name.

---

## Deployment topology (decided during this review)

The original spec assumed one machine. It is two.

```
 phone / laptop (Tailscale)
        │
        ├── HTML/JS ──> ubuntu LAN box  192.168.1.100, tailnet `ban-1`
        │               Next.js frontend ONLY
        │
        └── video + /api ──> Windows 11 + WSL2 box (RTX 5060, NVENC)
                             FastAPI · Postgres (native) · media on /mnt/
                             movies-downloader (later, M6)
```

Consequences that drove several findings below:

- The browser streams **directly** from the WSL2 box. Video bytes never cross
  the frontend server.
- Frontend and API are **different origins**, so cookies cannot be shared
  between them.
- Every device that watches must be on the tailnet. A private LAN IP is not
  reachable from another network no matter how static it is — a DHCP
  reservation fixes address *stability*, never *reachability*. Tailscale is
  what solves the second problem, and it gives stable addresses for free.
- Availability is now the product of two uptimes. This was accepted knowingly.

---

## Decisions

| # | Decision | Chosen |
|---|---|---|
| D2 | Topology | Frontend on the Ubuntu box; API, Postgres, media on the WSL2 box |
| D3 | movies-downloader host | WSL2 box, writing into a library directory |
| D4 | Scan with a missing root | Abort the whole scan; delete nothing |
| D6 | Remote reach | Tailscale on the Windows box; browser streams direct |
| D8 | Password kind | One shared password, long-lived session |
| D9 | Gate placement | Password on the frontend + token-authenticated stream URLs |
| D10 | Postgres | Native in WSL2, not Docker |
| D11 | Concurrent scans | Postgres advisory lock |
| D12 | Config drift | Fix all four (env path, `crossOrigin`, CORS, `.env.example`) |
| D13 | Test scope | Full backend coverage before M2 |
| D14 | Scan syscalls | Rewrite `_walk` on `os.scandir` |
| D15 | Two-box split (tension) | Keep the split |
| D16 | Stream auth (tension) | `stream_tokens` table, not HMAC |
| D17 | Hi10p (tension) | Capture `pix_fmt`/`profile`, resolve strategy per request |
| D18 | TODOS.md | Deferred; captured here instead |

### Why Postgres is native and not Docker

The deciding factor is not Postgres, it is what else has to run on that box.
The WSL2 machine must run ffmpeg with NVENC natively, because GPU passthrough
only works that way, and it must run the API, and WSL2 starts nothing on boot
without a hand-written hook. A native startup path on that machine is therefore
unavoidable. Docker would add a second runtime model and a daemon that itself
needs starting before the database can start. Native Postgres is one more line
in a script that has to exist anyway.

`docker/compose.yml` stays in the repo as the documented alternative, and is
what local development on the Ubuntu box uses.

### Why stream tokens and not HMAC

HMAC's advantage is statelessness between two parties. Here the API both mints
and verifies, so there is no second party and statelessness buys nothing. It
costs revocation and sliding expiry — and sliding expiry is exactly what the
seek problem needs. A Server Component bakes the stream URL into the HTML at
render time, and every seek reuses that same URL, so a signature short enough to
be meaningful expires mid-film with no client-side path to refresh it. A row in
the database that is already running gives sliding expiry, real revocation, no
crypto to get wrong, and one token that can cover a whole HLS segment set at M4.

---

## Critical gaps

Four findings have no test, no error handling, and fail silently.

1. **Hi10p plays as a black screen.** H.264 High 10 Profile probes as plain
   `h264`, so `strategy.py:49` marks it `direct`, and no browser decodes it. A
   large share of fansubbed anime is Hi10p — the primary content type this
   project exists for. `probe_file` never captures `pix_fmt` or `profile`, so
   the data needed to fix it is not even in the database. → T3
2. **A missing mount wipes the library.** `scanner.py:40` treats an absent root
   as an empty one, then `:69` deletes every row it did not see. → T1
3. **A transient ffprobe failure poisons a row permanently.** `scanner.py:55-56`
   writes size and mtime regardless of whether the probe succeeded, so `:48`
   skips the file as unchanged on every future scan. One cold-drive timeout, or
   one scan before ffmpeg is installed, marks files direct-playable with null
   metadata and no retry path. → T4
4. **`sort` is dead end to end.** The select, the search param, the API call,
   and the sidebar link are four dead surfaces with no error. → T9

---

## What already exists and is right

- **Range streaming.** `streaming/router.py` delegates to Starlette's
  `FileResponse` rather than hand-rolling byte ranges. Seven tests pin 206,
  `Content-Range`, open-ended and suffix ranges, 416, 410, and 404. The one
  thing M1 could not afford to get wrong, it did not.
- **`resolve_strategy` is pure.** That single property is what makes D17's fix
  cheap instead of a rewrite.
- **Incremental scan on size + mtime.** The right shape for a slow filesystem.
  The bugs are at the edges, not in the design.
- Reused rather than rebuilt: Starlette's Range support, Vidstack's layout,
  Postgres advisory locks, `os.scandir`.

---

## Test coverage

```
CODE PATHS                                              USER FLOWS
[+] transcode/strategy.py                               [+] First run / empty library
  ├── resolve_strategy()                                  ├── [GAP] [→E2E] Scan → grid populates
  │   └── [★★★ TESTED] 8 rungs + fallback                 └── [GAP]        Empty state renders CTA
  └── probe_file()
      ├── [GAP] ffprobe absent → empty Probe            [+] Playback
      ├── [GAP] CalledProcessError / bad JSON             ├── [GAP] [→E2E] Click card → video plays
      └── [GAP] _container_from() mkv vs mp4 tiebreak     ├── [GAP]        Seek mid-file (Range)
                                                          └── [GAP]        Non-direct file → notice
[+] library/scanner.py                            ← ZERO TESTS
  ├── _walk()
  │   └── [GAP] CRITICAL missing root → wipe            [+] Error states
  ├── scan()                                            ├── [GAP] API down → error card
  │   ├── [GAP] added / updated / unchanged / removed    └── [GAP] File deleted on disk → 410
  │   └── [GAP] CRITICAL concurrent scan
  └── run_scan_job()
      ├── [GAP] success → status done
      └── [GAP] exception → status failed + error text
[+] library/router.py                             ← ZERO TESTS
  ├── [GAP] library(q=…) filter, sort=added
  ├── [GAP] file_detail 404
  └── [GAP] job_status 404
[+] streaming/router.py
  └── [★★★ TESTED] 206 / Content-Range / suffix / 416 / 410 / 404

COVERAGE: 2/16 paths tested (13%)  |  QUALITY: ★★★:2  |  GAPS: 14 (3 E2E)
```

The percentage understates the situation in one direction and overstates it in
another. The two tested pieces are the strategy ladder and Range streaming,
which are where a subtle bug is hardest to notice. The untested piece is the
only code in Miru that deletes anything.

**Blocker for writing these tests:** `models.py:29,42` uses `postgresql.JSONB`,
which hard-binds any scanner or router test to a live Postgres. Switch to
`JSON.with_variant(JSONB, "postgresql")` first.

---

## Implementation tasks

### P1 — blocks M1 close

- [ ] **T1** (human ~20min / CC ~3min) — library — Abort scan when a configured
      root is missing. Verify: `pytest -k missing_root` asserts zero deletions.
- [ ] **T2** (~30min / ~5min) — library — `pg_try_advisory_xact_lock` around
      `scan()`. Transaction-scoped, not session-scoped: a session lock lives on
      the connection and SQLAlchemy returns it to the pool without releasing, so
      a raise between lock and unlock blocks every future scan until restart.
- [ ] **T3** (~5h / ~35min) — transcode — Capture `pix_fmt` and `profile`; treat
      10-bit H.264 as `transcode_full`; resolve strategy per request from stored
      probe facts and drop the `playback_strategy` column.
- [ ] **T4** (~30min / ~5min) — library — Do not persist size/mtime when the
      probe returned empty, so the file is retried on the next scan.
- [ ] **T5** (~8h / ~50min) — core — Password gate on the frontend, bearer token
      held server-side, `stream_tokens` table for video. Delete `core/auth.py`.
      Fixes two verified bugs with it: `/openapi.json` and `/docs` bypass an
      app-level `dependencies=[Depends(...)]` entirely (confirmed: `/x` → 401
      while `/openapi.json` → 200), and `secrets.compare_digest` raises
      `TypeError` on non-ASCII input, giving a 500 instead of a 401.
- [ ] **T6** (~1h / ~10min) — config — Absolute `env_file` path; drop
      `crossOrigin` from the player; delete `CORSMiddleware`; split
      `NEXT_PUBLIC_API_URL` into a server-side and a browser-side variable,
      since one value cannot be both.

### P2 — same branch

- [ ] **T7** (~4h / ~15min) — tests — Full backend coverage. Do the JSONB
      variant change first.
- [ ] **T8** (~1h / ~10min) — library — `os.scandir` walk. Must replicate
      `rglob`'s symlink semantics (skips symlinked directories, follows
      symlinked files) and add a containment check against `settings.libraries`.
- [ ] **T9** (~30min / ~5min) — web — Wire `sort` end to end.
- [ ] **T10** (~20min / ~3min) — web — Drop `load="eager"` (it buffers a
      multi-GB file before play); fix `type: "video/object"`, which is Vidstack's
      marker for object sources, not a MIME type, and currently works by accident.
- [ ] **T11** (~1h / ~10min) — library — Batch commits every N files. A first
      scan is currently hours of ffprobe in one transaction; any kill persists
      nothing.
- [ ] **T12** (~40min / ~8min) — library — Job hygiene: None check in
      `run_scan_job`, drop the dead `attempts` field, reap jobs stuck in
      `running` (which `--reload` causes routinely).
- [ ] **T13** (~40min / ~8min) — docs — `tailscale serve --set-path=/api`
      against an API that already prefixes `/api` yields `/api/api/library`. Add
      a reset/rescan step.
- [ ] **T14** (~30min / ~5min) — ops — Pin API dependencies. `start-api.sh:6`
      runs `pip install -e` on every boot with no lockfile, while
      `package-lock.json` is committed — no internet at boot means no API.

### Parallelization

| Lane | Tasks | Modules | Depends on |
|---|---|---|---|
| A | T3 → T1 → T4 → T2 → T11 → T12 → T8 | `library/`, `transcode/` | — |
| B | T5 → T6 | `core/`, `streaming/`, `web/lib` | — |
| C | T9, T10 | `web/app` | — |
| D | T13, T14 | `docs/`, `scripts/` | — |
| E | T7 | `tests/` | A + B merged |

Launch A–D in parallel, merge, then E. **Conflict:** T6 and T10 both edit
`Player.tsx` — land T10 with lane B, or run lane C first.

---

## Deferred

Recorded here rather than in `TODOS.md`.

- **`media_files.hash` — M3 blocker.** `ARCHITECTURE.md:100` calls it
  load-bearing for surviving file moves; the column does not exist. A torrent
  client moving a file on completion is delete-plus-add, a new id, and lost
  progress. Needs a hash strategy decision first — whole multi-GB file over
  drvfs is its own performance question.
- **Partial-file quarantine — M6 blocker.** A torrent client writes `.mkv` in
  place. A mid-download scan probes a 12%-complete file and, per critical gap 3,
  may record that permanently. Needs an incoming directory, `.part`/`.!qB`
  exclusion, and move-on-completion — which interacts with the hash item above.
- **Responsive layout and pagination — M5.** `grid-cols-6` with no breakpoints
  behind a fixed 264px sidebar, and the whole library server-rendered with no
  pagination and full filesystem paths in the payload. Phone-over-Tailscale is a
  stated use case.
- **movies-downloader hardening — M6.** It ships with no auth. On the tailnet it
  undoes the entire auth design in one line. Bind it to `127.0.0.1` and drive it
  only from the API.
- **Design nits.** `Noto_Sans_JP` is loaded with `subsets: ["latin"]`, so the
  kana that is the whole visual differentiator likely falls back to a system
  font. `--accent` is used for the Vidstack slider and the strategy chip,
  violating the watch-state-only rule stated in the same file. The hero is
  labelled "Recently added" while rendering `files[0]` under A–Z sort.
  `catch { notFound() }` conflates API-down, 500, and 401.
- **Alembic.** Still deferred to M3, but `create_all` never adds columns, so the
  first new field in M2 will 500 on a clean startup. A reset script is the
  minimum.

---

## Outside voice

An independent reviewer with fresh context was asked to find what this review
missed. It returned 34 findings; the ones not already folded into the tasks
above are in [`2026-08-07-outside-voice.md`](./2026-08-07-outside-voice.md).

Three contradicted decisions made earlier in the review. All three were put back
to the user:

| Tension | Review said | Outside voice said | Resolved |
|---|---|---|---|
| Two-box split | Keep it | Zero availability benefit; sole cause of three-layer auth | Split kept |
| Stream auth | HMAC signed URLs | Stateless buys nothing with one party; breaks on seek | Switched to `stream_tokens` |
| Strategy column | (missed entirely) | Hi10p is marked direct; a stored verdict cannot be per-client | Resolve per request |

The most valuable finding in the entire review came from the outside voice, not
from the section-by-section pass: Hi10p is standard in fansubbed anime, and the
ladder promised direct play on exactly those files.
