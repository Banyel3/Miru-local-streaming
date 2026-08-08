"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ActiveDownload,
  CatalogWork,
  KIND_LABEL,
  MediaFile,
  Wall as WallData,
  freshness,
} from "@/lib/api";
import { pollDownloads, refreshLibrary } from "@/app/actions";
import { MediaCard } from "@/components/MediaCard";
import { Rail } from "@/components/Rail";
import { ReleaseSheet } from "@/components/ReleaseSheet";
import { Button, ButtonLink, EmptyState, SectionHeading, artTint } from "@/components/ui";
import { Play } from "@/components/icons";
import { isComplete, useContinueWatching } from "@/lib/store";

const KINDS = ["all", "anime", "movie", "series"] as const;

/**
 * The browse wall.
 *
 * Continue watching sits above the discovery rails on purpose. The most
 * frequent thing anyone does with this server is resume the episode they
 * started last night, and a home screen that leads with things you do not own
 * moves that a click away. Netflix orders it the same way.
 */
export function Wall({ data, files }: { data: WallData; files: MediaFile[] }) {
  const [kind, setKind] = useState(data.kind);
  const [wall, setWall] = useState(data);
  const [sheet, setSheet] = useState<CatalogWork | null>(null);
  const [downloads, setDownloads] = useState<Map<number, ActiveDownload>>(new Map());
  const [ready, setReady] = useState<ActiveDownload | null>(null);
  const [pending, setPending] = useState(false);
  const router = useRouter();

  useEffect(() => {
    setWall(data);
    setKind(data.kind);
    setPending(false);
  }, [data]);

  // Kind lives in the URL so the filter survives a reload and can be shared,
  // and the router refetches the server component rather than this holding a
  // second client-side fetcher for the same data.
  const switchKind = useCallback(
    (next: string) => {
      if (next === kind) return;
      setKind(next);
      setPending(true);
      router.replace(next === "all" ? "/" : `/?kind=${next}`, { scroll: false });
    },
    [kind, router],
  );

  // One poll for every in-flight grab, not one per card: eight downloading
  // cards polling individually is eight requests every two seconds to a machine
  // already busy saturating its disk. Fast while something is moving, slow
  // otherwise.
  const seenRef = useRef<Map<number, ActiveDownload>>(new Map());
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      const res = await pollDownloads();
      if (!live) return;
      if (!("error" in res)) {
        const next = new Map<number, ActiveDownload>();
        for (const d of res.downloads) next.set(d.work_id, d);

        // The only moment Watch Now can honestly be honoured: the file has
        // finished *and* the mover has promoted it into the library. Compared
        // against a ref rather than state, so the check cannot read a stale
        // snapshot from an earlier render.
        for (const d of res.downloads) {
          const was = seenRef.current.get(d.work_id);
          if (d.in_library && was && !was.in_library) {
            setReady(d);
            refreshLibrary();
          }
        }
        seenRef.current = next;
        setDownloads(next);

        const active = res.downloads.some(
          (d) => d.state === "downloading" || d.state === "queued",
        );
        timer = setTimeout(tick, active ? 2000 : 15000);
      } else {
        timer = setTimeout(tick, 15000);
      }
    };

    tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, []);

  const inProgress = useContinueWatching(12);
  const byId = useMemo(() => new Map(files.map((f) => [f.id, f])), [files]);
  const resuming = (inProgress ?? [])
    .filter(([, p]) => !isComplete(p))
    .map(([id]) => byId.get(id))
    .filter((f): f is MediaFile => Boolean(f));

  const hero = wall.rails.flatMap((r) => r.items)[0] ?? null;

  return (
    <div className="flex flex-col gap-8 lg:gap-10">
      {/* Filters and freshness. The freshness line is not decoration: a refresh
          that quietly died looks exactly like one that is up to date. */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by kind">
          {KINDS.map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => switchKind(k)}
              aria-pressed={kind === k}
              className={`min-h-11 rounded-full border px-4 text-[12.5px] font-bold transition-colors ${
                kind === k
                  ? "border-primary bg-primary text-white"
                  : "border-border text-text-dim hover:border-border-hover hover:text-text"
              }`}
            >
              {KIND_LABEL[k]}
            </button>
          ))}
        </div>

        <span
          className={`ml-auto text-[12px] ${wall.refresh_error ? "text-accent" : "text-text-muted"}`}
        >
          {pending ? "Loading…" : freshness(wall.refreshed_at, wall.refresh_error)}
        </span>
      </div>

      {!wall.pc_reachable && <PcAsleep />}

      {wall.empty ? (
        <FirstRun error={wall.refresh_error} />
      ) : (
        <>
          {hero && (
            <Hero
              work={hero}
              pcReachable={wall.pc_reachable}
              onGet={() => setSheet(hero)}
            />
          )}

          {resuming.length > 0 && (
            <section className="flex flex-col gap-3.5">
              <SectionHeading title="Continue watching" jp="つづき" />
              <ul className="rail bleed bleed-pad flex gap-4 overflow-x-auto pb-2">
                {resuming.map((f) => (
                  <li key={f.id} className="w-[150px] shrink-0 sm:w-[170px] xl:w-[190px]">
                    <MediaCard file={f} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          {wall.rails.map((rail) => (
            <Rail
              key={rail.key}
              rail={rail}
              kind={kind}
              downloads={downloads}
              pcReachable={wall.pc_reachable}
              onOpen={setSheet}
              note={rail.key === "trending" ? wall.note : null}
            />
          ))}

          {files.length > 0 && (
            <section className="flex flex-col gap-3.5">
              <SectionHeading title="In your library" jp="ライブラリ">
                <ButtonLink href="/library" variant="ghost" size="sm">
                  See all
                </ButtonLink>
              </SectionHeading>
              <ul className="rail bleed bleed-pad flex gap-4 overflow-x-auto pb-2">
                {files.slice(0, 20).map((f) => (
                  <li key={f.id} className="w-[150px] shrink-0 sm:w-[170px] xl:w-[190px]">
                    <MediaCard file={f} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      {sheet && (
        <ReleaseSheet
          work={sheet}
          onClose={() => setSheet(null)}
          onStarted={(workId, jobId) =>
            setDownloads((prev) => {
              const next = new Map(prev);
              next.set(workId, {
                work_id: workId,
                title: sheet.title,
                job_id: jobId,
                state: "queued",
                progress: 0,
              });
              return next;
            })
          }
        />
      )}

      {ready && <ReadyToast download={ready} onDismiss={() => setReady(null)} />}
    </div>
  );
}

function PcAsleep() {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-surface px-4 py-3">
      <span className="text-[15px] text-accent" aria-hidden>
        ☾
      </span>
      <p className="min-w-[200px] flex-1 text-[12.5px] leading-relaxed text-text-dim">
        <b className="text-text">The PC is asleep.</b> Browsing works. Downloading and search need
        it awake.
      </p>
      <ButtonLink href="/settings" variant="secondary" size="sm">
        How to wake it
      </ButtonLink>
    </div>
  );
}

function FirstRun({ error }: { error: string | null }) {
  return (
    <div className="flex flex-col gap-6">
      <EmptyState
        title={error ? "Couldn't reach your indexers" : "Filling the shelves"}
        action={
          <ButtonLink href="/settings" size="md">
            {error ? "Check settings" : "Settings"}
          </ButtonLink>
        }
      >
        <p>
          {error
            ? `Miru asked Prowlarr what exists and got: ${error}. The wall fills itself as soon as the PC is awake and reachable.`
            : "Miru is asking your indexers what's out there. It takes a few seconds the first time, and the wall gets deeper every day it runs, because it keeps what the indexers drop off their front page."}
        </p>
      </EmptyState>

      {!error && (
        <div className="flex flex-col gap-3.5" aria-hidden>
          <div className="skeleton h-5 w-40 rounded" />
          <ul className="rail bleed bleed-pad flex gap-4 overflow-x-auto pb-2">
            {Array.from({ length: 7 }).map((_, i) => (
              <li key={i} className="w-[150px] shrink-0 sm:w-[170px] xl:w-[190px]">
                <div className="flex flex-col gap-2.5 rounded-2xl border border-border bg-surface p-2.5">
                  <div className="skeleton aspect-2/3 rounded-[11px]" />
                  <div className="skeleton h-3 w-4/5 rounded" />
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Hero({
  work,
  pcReachable,
  onGet,
}: {
  work: CatalogWork;
  pcReachable: boolean;
  onGet: () => void;
}) {
  return (
    <section
      className="relative isolate overflow-hidden rounded-3xl border border-border"
      style={{ background: artTint(work.title) }}
    >
      {work.poster_url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={work.poster_url} alt="" className="absolute inset-0 size-full object-cover opacity-45" />
      )}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.06]"
        style={{ backgroundImage: "repeating-linear-gradient(115deg, #fff 0 1px, transparent 1px 18px)" }}
        aria-hidden
      />
      <div className="absolute inset-0 bg-gradient-to-t from-bg via-bg/75 to-bg/20 sm:bg-gradient-to-r sm:from-bg sm:via-bg/80 sm:to-transparent" />

      <div className="relative flex flex-col justify-end gap-3.5 p-6 sm:max-w-[640px] sm:p-9">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-border-hover bg-bg/70 px-3 py-1.5 text-[11.5px] font-bold backdrop-blur-sm">
            Trending now
          </span>
          <span className="rounded-full border border-border-hover px-3 py-1.5 text-[11.5px] text-highlight capitalize">
            {work.kind}
          </span>
        </div>

        <div>
          <h1 className="text-[clamp(1.75rem,5vw,2.625rem)] leading-[1.05] font-extrabold tracking-[-0.02em] text-balance">
            {work.title}
          </h1>
          <p className="mt-2 text-[13px] text-text-muted">
            {[work.year, `${work.release_count} release${work.release_count === 1 ? "" : "s"}`]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>

        <div className="mt-1 flex flex-wrap gap-3">
          {work.library_file_id ? (
            <ButtonLink href={`/watch/${work.library_file_id}`} size="lg">
              <Play className="size-4" /> Watch
            </ButtonLink>
          ) : (
            <>
              <Button size="lg" onClick={onGet} disabled={!pcReachable}>
                <Play className="size-4" /> Watch Now
              </Button>
              <Button variant="secondary" size="lg" onClick={onGet} disabled={!pcReachable}>
                Download
              </Button>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * The moment Watch Now pays off. It cannot stream a partial torrent, so the
 * promise it makes is "you will not have to watch a progress bar" — this is the
 * only point at which the system can keep it.
 */
function ReadyToast({
  download,
  onDismiss,
}: {
  download: ActiveDownload;
  onDismiss: () => void;
}) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 20000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return (
    <div
      role="status"
      className="fixed inset-x-4 bottom-4 z-(--z-toast) mx-auto flex max-w-[420px] items-center gap-3 rounded-2xl border border-border bg-surface/95 p-3.5 backdrop-blur-xl motion-safe:animate-[miru-rise_.3s_var(--ease-out-quart)]"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-bold">{download.title} is ready</p>
        <p className="text-[11.5px] text-text-muted">Finished downloading and added to your library.</p>
      </div>
      <Link
        href="/library"
        onClick={onDismiss}
        className="shrink-0 rounded-xl bg-primary px-4 py-2.5 text-[12.5px] font-bold text-white hover:bg-primary-hover"
      >
        Play
      </Link>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss"
        className="shrink-0 rounded-lg px-2 py-2 text-text-muted hover:text-text"
      >
        ×
      </button>
    </div>
  );
}
