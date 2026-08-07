# Outside voice — independent challenge of M1

Date: 2026-08-07
Branch: `main` @ `797cbe5`
Source: Claude subagent with fresh context (Codex CLI was installed but not
authenticated)

An independent reviewer was given the code, the deployment topology, and the
list of decisions already made, and asked to find what the section-by-section
review missed rather than repeat it. It returned 34 findings.

Items already folded into tasks T1–T14 are marked. The rest stand as open
observations.

Two claims were verified independently before being accepted:

```
/x               -> 401     (with MIRU_TOKEN set)
/openapi.json    -> 200     <- bypasses the auth dependency
/docs            -> 200     <- bypasses the auth dependency
compare_digest   -> TypeError: comparing strings with non-ASCII characters
                              is not supported
```

---

## Tier 1 — makes the plan wrong, not just the code

**1. The ladder walks into Hi10p.** `strategy.py:49` checks `video_codec` only.
H.264 High 10 Profile probes as plain `h264` → marked `direct` → no browser
decodes it → black player. A large fraction of fansubbed anime is Hi10p.
`probe_file` never captures `profile` or `pix_fmt` (`strategy.py:95-112`), so
the data to fix it is not in the database and a rescan is required later. Same
class: `audio_channels or 2` (`strategy.py:54`) treats an unknown channel count
as fine. → **T3**

**2. A stored `playback_strategy` can never be per-client.** `AUDIO_OK` includes
flac and opus, `VIDEO_OK` includes vp9 and av1 — iOS Safari fails on several of
those combinations, and iPhone-over-Tailscale is an explicit use case. Fixing it
later means a second column per client class, or making resolution request-time,
which undoes the central architectural claim in `ARCHITECTURE.md:66-67`. The
function is already pure (`strategy.py:41`), so moving it costs nothing now. → **T3**

**3. The two-box split has zero availability benefit.** Video comes from the WSL2
box either way, so if that box is down Miru is down. The LAN box adds a second
machine to patch, a tailnet hop on every server-side fetch, and split logs — and
it is the sole reason three auth mechanisms exist on a network where Tailscale
already authenticates every device cryptographically. `SETUP.md:191-198` already
documents the simpler answer. *Raised as a tension; the split was kept knowingly.*

**4. Short-lived signed URLs break on seek.** `watch/[id]/page.tsx:25` is a
Server Component, so the URL is baked into HTML at render time, and every seek
reuses it. A two-hour film with a five-minute signature dies on the first seek
past minute five, with no client-side refresh path. At M4, hls.js fetches a
manifest plus hundreds of segments, each needing a signature, and the scheme has
no notion of a signed prefix. → **T5** (switched to `stream_tokens`)

**5. HMAC is the more complex answer to a problem Postgres already solves.** Only
the API mints and only the API verifies, so statelessness buys nothing, while
costing revocation and sliding expiry — which is exactly what fixes finding 4.
Also: WSL2's clock drifts across Windows sleep and resume, and `SETUP.md:144-148`
only disables `standby-timeout-ac`, so any battery or lid path still hibernates.
→ **T5**

**6. Deleting `CORSMiddleware` will be reversed at M3/M4.** Correct today,
because a bare `<video src>` needs no CORS. But `<track>` elements require
`crossorigin` plus CORS for cross-origin cues, JASSUB fetches ASS over XHR, and
hls.js fetches manifests and segments over fetch. All three are cross-origin
under the two-box design. *Open — accepted for now, re-add deliberately at M3.*

## Tier 2 — real bugs and permanent corruption

**7. A transient ffprobe failure poisons a row forever.** `scanner.py:52` probes,
`:55-56` writes fresh size and mtime regardless of probe success, `:66` stamps
`probed_at`. `probe_file` returns an empty `Probe()` on any `SubprocessError`,
timeout, or `OSError` (`strategy.py:86-87`), or if ffprobe is not installed at
all (`strategy.py:76-77`). An empty Probe resolves to `DIRECT`
(`strategy.py:44-47`), and then `scanner.py:48` skips the file as unchanged on
every future scan. The 60s timeout at `strategy.py:83` is optimistic for a
spun-down drive. → **T4**

**8. There is no way to re-resolve strategies after a rule change** — the thing
`strategy.py:5` and `ARCHITECTURE.md:77` both promise. No endpoint, job, or
script exists; the only fix today is `UPDATE media_files SET mtime = 0`.
*Resolved by T3 — request-time resolution removes the need.*

**9. `media_files.hash` does not exist, and M3 depends on it.**
`ARCHITECTURE.md:100-101` states as load-bearing that the hash survives moves so
progress is preserved. `models.py:13-33` has no such column. `scanner.py:74-76`
deletes any row whose path was not seen and `:53` creates a new row with a new
id — so a move is delete-plus-add and progress is gone. Nobody has specified what
"hash" means here. *Deferred — M3 blocker.*

**10. `pg_advisory_lock` on a pooled connection leaks.** Session-scoped advisory
locks live on the connection, and SQLAlchemy returns connections to the pool
without releasing them. A raise between lock and unlock blocks every future scan
until process restart, with `scanner.py:95`'s bare `except Exception` swallowing
the reason. Use `pg_try_advisory_xact_lock`. → **T2**

**11. The `os.scandir` rewrite introduces a symlink bug `rglob` does not have.**
Verified: `Path.rglob` skips symlinked directories but follows symlinked files. A
hand-rolled walk must replicate both halves. Separately, a symlinked file inside
the library is scanned and then served by `streaming/router.py:36-45` with no
containment check against `settings.libraries` — which matters once an
unauthenticated download service writes into that directory. → **T8**

**12. `/openapi.json` and `/docs` bypass the auth dependency.** App-level
`dependencies=[Depends(require_token)]` (`main.py:22-23`) does not cover them,
because FastAPI registers those through Starlette's `add_route` rather than
`add_api_route`. *Verified.* → **T5**

**13. `secrets.compare_digest` raises `TypeError` on non-ASCII.** `auth.py:17` —
an `Authorization` header with any non-ASCII byte produces an unauthenticated
500 instead of a 401. *Verified.* → **T5**

**14. `run_scan_job` crashes if the job row is gone.** `scanner.py:88-89`, no
None check, inside a BackgroundTask — it dies silently with nothing recorded.
→ **T12**

**15. `jobs.attempts` is dead.** Set at `scanner.py:90`, never read. There is no
retry and no reaper for jobs stuck in `running`, which is exactly what `--reload`
(`start-api.sh:9`) causes when uvicorn restarts mid-scan. → **T12**

## Tier 3 — the downloader integration is naive

**16. `movies-downloader` has no auth and would sit on the tailnet.** It undoes
the three-layer auth design in one line. At minimum it must bind `127.0.0.1` and
be driven exclusively by the API. *Deferred — M6.*

**17. Nothing handles partial files.** A torrent client writes `.mkv` in place,
incrementally. `scanner.py:23` accepts it, so a mid-download scan probes a
12%-complete file, gets garbage or a timeout, and per finding 7 may record that
permanently. There is no `.part` / `.!qB` / `.crdownload` exclusion, no
completion signal, and no incoming-directory quarantine. Move-on-completion is
precisely what finding 9 breaks. *Deferred — M6 blocker.*

**18. Deleting or moving a file 410s a live player and drops the row.**
`streaming/router.py:37-38` plus `scanner.py:74-76`. Stops being hypothetical
once an autonomous process mutates the library directory. *Open.*

## Tier 4 — shipped and broken, frontend

**19. `sort` is dead end to end.** `page.tsx:85-91` renders the select,
`page.tsx:55` destructures only `q`, `api.ts:29-30` never sends it, and the
backend supports it (`router.py:45,49`). The select has no `defaultValue`, so it
resets to A–Z even with `?sort=added` in the URL, and `Sidebar.tsx:46` links to
`/?sort=added` — three dead surfaces. → **T9**

**20. Desktop-only while the deployment story is every device.** `page.tsx:126`
uses `grid-cols-6` with no breakpoints anywhere in the codebase, `page.tsx:66-67`
has fixed `p-14`/`p-7`, and `Sidebar.tsx:33` is `w-[264px] flex-none`. On a phone
that is six ~50px columns behind a fixed sidebar. *Deferred — M5.*

**21. No pagination.** `router.py:44-50` returns the entire library,
`MediaFileOut` includes the full filesystem path (`router.py:20`), and
`page.tsx:127-129` renders every card server-side with `cache: "no-store"`.
*Deferred — M5.*

**22. `NEXT_PUBLIC_API_URL` cannot be both URLs.** `api.ts:21` is used by
`getLibrary`/`getFile` (server-side, should be the private path) and by
`streamUrl` (`api.ts:34`, must be browser-reachable). `NEXT_PUBLIC_*` is inlined
at build time, so the WSL2 hostname gets baked into the LAN box's bundle. → **T6**

**23. `type: "video/object"` is not a MIME type.** `Player.tsx:16` — it is
Vidstack's marker for `MediaStream` sources, not URL strings. Works by accident.
→ **T10**

**24. `load="eager"`** (`Player.tsx:19`) starts buffering a potentially 20GB file
on page load, before play, over a WireGuard tunnel. → **T10**

**25. `Noto_Sans_JP` is loaded with `subsets: ["latin"]`** (`layout.tsx:12`),
which restricts the downloaded unicode ranges to latin — the kana that is the
entire visual differentiator will likely fall back to a system font. *Deferred.*

**26. The `--accent` rule is violated in the commit that states it.**
`globals.css:137` says watch-state only; `globals.css:172,174` use it for the
Vidstack slider fill and thumb, and `watch/[id]/page.tsx:13,18` for the strategy
chip. *Deferred.*

**27. `catch { notFound() }`** at `watch/[id]/page.tsx:31-33` conflates API-down,
500, and (once the token lands) 401 into a not-found page. *Deferred.*

**28. The hero is mislabelled.** `page.tsx:100` renders `files[0]` under a
"Recently added" badge, but the default sort is title A–Z, so it is permanently
whatever is alphabetically first — and during a search it is the first search
result, still labelled "Recently added". *Deferred.*

## Tier 5 — operational

**29. `start-api.sh:6` runs `pip install -e` on every boot with no lockfile,**
while `apps/web/package-lock.json` is committed — asymmetric. With Task Scheduler
auto-start (`SETUP.md:114-135`), no internet at boot means no API, and a bad
`fastapi>=0.115` release means no API. → **T14**

**30. Full backend test coverage is blocked by `JSONB`.** `models.py:29,42` uses
`postgresql.JSONB`, hard-binding any scanner or router test to a live Postgres.
There is no `conftest.py`, no fixture, and no sqlite variant. Use
`JSON.with_variant(JSONB, "postgresql")`, or budget for a real test database.
→ **T7**

**31. "Drop and rescan" is the documented migration story with no script and no
doc.** `db.py:21-26` — `create_all` never adds columns, so the moment M2 adds a
field the app 500s on a missing column with a clean startup. *Deferred.*

**32. `SETUP.md:196-197` is wrong as written.** `tailscale serve
--set-path=/api localhost:8000` mounts an API that already prefixes `/api`
(`library/router.py:10`, `streaming/router.py:10`), so paths become
`/api/api/library`. → **T13**

**33. `web_origin` becomes dead config** once CORS is removed — `config.py:18`,
`.env.example:13`. → **T6**

**34. `scan()` is one transaction with no batching.** `scanner.py:38` loads every
row into a dict and `:78` commits once at the end. A first scan of a large
library over drvfs is hours of ffprobe in a single transaction holding the
advisory lock, and any kill mid-scan persists zero progress. → **T11**
