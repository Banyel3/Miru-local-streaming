# Miru — how to work on this

## Test-driven, always. This is not optional.

Write the failing test first, watch it fail, then make it pass. Every fix, every
feature, no exceptions.

This rule exists because of a specific, repeated failure in this repo. Three
fixes in a row shipped with an unexamined second case:

| the fix | the case it broke |
|---|---|
| removed the duplicated card title | cards **with a poster** lost their name entirely |
| sorted the wall by upload recency | the chip still showed the **film's** year, so a correct order looked random |
| linked a promoted file to its card | **nothing ever called `scan()`**, so the link never ran |
| matched a download to its library row | matched on the *torrent title*, which is not the *filename* |

Each was verified against the case that motivated it and shipped. The tests had
the same shape — they asserted the motivating case and stopped.

**So: before writing a fix, write down its state space, and write a test per
state.** The states are usually obvious once asked for:

```
poster / no poster          PC awake / asleep
complete / partial          matched / unmatched
first run / steady state    configured / not configured
one result / many results   empty / full
```

A test names the failure it prevents, in a comment, in plain language. If you
cannot say what breaks without the test, it is not earning its place.

## What the tests must not require

The suite runs with **no Postgres, no ffmpeg, no PC, no network**. Anything that
would reach out is faked at the seam. A failure means Miru is broken, never that
something was not running.

```bash
cd apps/api && ../../.venv/bin/python -m pytest -q     # 246 tests
cd apps/web && npx tsc --noEmit                        # must be clean
```

## Traps that have cost real time here

- **Never `pkill -f`** — its pattern has matched our own shell and killed an
  unrelated app. Kill by exact PID.
- **Never `npm run build`** — a dev server holds `.next` and it 500s the running
  site. Typecheck instead.
- **Port 3000 is an unrelated portfolio app.** Miru's web is 3001.
- **Measure the source before designing against it.** Prowlarr re-encrypts its
  guids every response, ignores `limit`, has no pagination, dual-tags anime as
  movies, and reports seeder counts that are not comparable between indexers.
  Every one of those broke something that had been designed on an assumption.

## Where things live and why

- **Laptop**: API, web, Postgres, **Prowlarr**, and `remux` (stream-copy, 0.37s
  CPU per ten-minute film). Everything needed to *browse and play* works with
  the PC asleep.
- **PC**: qBittorrent (downloads, and the sequential piece order that makes
  watch-while-downloading possible) and the NVENC worker (`transcode_full`).

Decisions recorded in `docs/DEPLOYMENT.md` §8 and `docs/STATE.md` are settled.
Do not reverse one silently — if it needs reversing, say so and write down why.
