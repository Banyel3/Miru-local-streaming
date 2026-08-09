"use client";

import Link from "next/link";
import { ReactNode } from "react";
import {
  MediaFile,
  displayTitle,
  fileSize,
  folderLabel,
  resolution,
  runtime,
} from "@/lib/api";
import { SeriesGroup, groupLibrary } from "@/lib/library";
import { isComplete, percentOf, useContinueWatching, useProgress } from "@/lib/store";
import { ArtTile, FactChip, SectionHeading } from "@/components/ui";

/** State reads as text, not colour alone. Null while progress is unknown
 *  (first client render) so server and client markup match. */
function stateLabel(progress: ReturnType<typeof useProgress>) {
  if (progress === null) return null;
  if (isComplete(progress)) return { text: "WATCHED", cls: "text-text-muted" };
  return { text: "RESUME", cls: "text-highlight" };
}

function Art({ poster, seed }: { poster: string | null; seed: string }) {
  return poster ? (
    // eslint-disable-next-line @next/next/no-img-element -- same-origin proxy path
    <img
      src={poster}
      alt=""
      className="h-[74px] w-[52px] shrink-0 rounded-lg border border-border object-cover"
    />
  ) : (
    <ArtTile seed={seed} className="h-[74px] w-[52px] shrink-0 rounded-lg border border-border" />
  );
}

function Row({
  href,
  poster,
  seed,
  title,
  titleExtra,
  sub,
  progressId,
  chips,
}: {
  href: string;
  poster: string | null;
  seed: string;
  title: string;
  titleExtra?: ReactNode;
  sub: string;
  /** Which file's watch progress this row shows. */
  progressId: number;
  chips: ReactNode;
}) {
  const progress = useProgress(progressId);
  const state = stateLabel(progress);
  const pct = progress && !isComplete(progress) ? Math.round(percentOf(progress)) : null;

  return (
    <Link
      href={href}
      className="flex items-center gap-4 rounded-2xl border border-border bg-surface p-3 pr-4 transition-colors duration-150 hover:border-border-hover sm:gap-5"
    >
      <Art poster={poster} seed={seed} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <h3 className="truncate text-[14.5px] font-bold">
          {title}
          {titleExtra}
        </h3>
        <p className="truncate font-mono text-[11px] text-text-muted">{sub}</p>
        {pct !== null && (
          <div
            className="mt-1.5 h-[3px] w-full max-w-[340px] rounded-full bg-border"
            role="progressbar"
            aria-label={`${pct}% watched`}
            aria-valuenow={pct}
          >
            <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
          </div>
        )}
      </div>
      <div className="hidden flex-wrap items-center justify-end gap-2 sm:flex">{chips}</div>
      {state && (
        <span className={`w-16 shrink-0 text-right text-[10.5px] font-extrabold tracking-[0.08em] ${state.cls}`}>
          {state.text}
        </span>
      )}
    </Link>
  );
}

function FilmRow({ file }: { file: MediaFile }) {
  const { label } = displayTitle(file);
  const title = file.series?.title ?? label;
  return (
    <Row
      href={`/file/${file.id}`}
      poster={file.series?.poster_url ?? null}
      seed={file.title}
      title={title}
      titleExtra={
        file.series?.year ? (
          <span className="ml-1.5 font-medium text-text-muted">({file.series.year})</span>
        ) : null
      }
      sub={folderLabel(file.path) || "/"}
      progressId={file.id}
      chips={
        <>
          {runtime(file.duration_ms) && <FactChip>{runtime(file.duration_ms)}</FactChip>}
          {resolution(file) && <FactChip tone="bright">{resolution(file)}</FactChip>}
          <FactChip>{fileSize(file.size_bytes)}</FactChip>
        </>
      }
    />
  );
}

function SeriesRow({ group }: { group: SeriesGroup }) {
  const newest = group.files[0];
  const n = group.files.length;
  return (
    <Row
      href={`/file/${newest.id}`}
      poster={group.poster_url}
      seed={group.title}
      title={group.title}
      sub={`${n} episode${n === 1 ? "" : "s"} · ${folderLabel(newest.path) || "/"}`}
      progressId={newest.id}
      chips={
        <>
          {resolution(newest) && <FactChip tone="bright">{resolution(newest)}</FactChip>}
          <FactChip>{n} ep{n === 1 ? "" : "s"}</FactChip>
        </>
      }
    />
  );
}

/** In-progress files, most recent first — the reason most library visits
 *  happen. Renders nothing (not an empty section) when nothing is mid-watch. */
function ContinueStrip({ files }: { files: Map<number, MediaFile> }) {
  const watching = useContinueWatching(3);
  const rows = (watching ?? [])
    .map(([id]) => files.get(id))
    .filter((f): f is MediaFile => Boolean(f));
  if (rows.length === 0) return null;

  return (
    <section className="flex flex-col gap-3">
      <SectionHeading title="Continue watching" jp="続きから" />
      <ul className="flex flex-col gap-2">
        {rows.map((f) => (
          <li key={`cw-${f.id}`}>
            <Row
              href={`/watch/${f.id}`}
              poster={f.series?.poster_url ?? null}
              seed={f.title}
              title={f.series?.title ?? displayTitle(f).label}
              sub={folderLabel(f.path) || "/"}
              progressId={f.id}
              chips={runtime(f.duration_ms) ? <FactChip>{runtime(f.duration_ms)}</FactChip> : null}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

export function LibraryList({ files }: { files: MediaFile[] }) {
  const { films, series } = groupLibrary(files);
  const byId = new Map(files.map((f) => [f.id, f]));

  return (
    <div className="flex flex-col gap-8">
      <ContinueStrip files={byId} />

      {films.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionHeading title="Films" jp="映画">
            <span className="text-[12.5px] text-text-muted">{films.length}</span>
          </SectionHeading>
          <ul className="flex flex-col gap-2">
            {films.map((f) => (
              <li key={f.id}>
                <FilmRow file={f} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {series.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionHeading title="Series" jp="シリーズ">
            <span className="text-[12.5px] text-text-muted">
              {series.length} show{series.length === 1 ? "" : "s"} ·{" "}
              {series.reduce((a, g) => a + g.files.length, 0)} episodes
            </span>
          </SectionHeading>
          <ul className="flex flex-col gap-2">
            {series.map((g) => (
              <li key={g.key}>
                <SeriesRow group={g} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
