"use client";

import Link from "next/link";
import { useState } from "react";
import { ActiveDownload, CatalogWork, clock, posterUrl, uploadedAgo } from "@/lib/api";
import { ArtTile, MicroChip, ProgressBar } from "@/components/ui";
import { Check, Play } from "@/components/icons";

/**
 * One work on the wall.
 *
 * The rule that keeps this from becoming a dashboard of badges: **at most one
 * state marker, always in the same corner**, and the states are mutually
 * exclusive rather than stacked. Downloading beats in-library beats nothing.
 * Seeder counts never appear here — they are an input to the picker and noise
 * everywhere else.
 */
export function CatalogCard({
  work,
  download,
  pcReachable,
  onOpen,
  sortedBy,
}: {
  work: CatalogWork;
  download?: ActiveDownload;
  pcReachable: boolean;
  onOpen: (work: CatalogWork) => void;
  /** What the surrounding rail is ordered by, so the card can label rows with
   *  the number they are actually sorted on. Showing the film's year in a row
   *  headed "Latest releases" made a correct ordering look shuffled. */
  sortedBy?: "latest" | "trending";
}) {
  // A poster can 404 after the row said it had one — the cache fetch runs
  // lazily and the provider can be down. Falling back to the tile is the whole
  // reason the proxy 404s instead of serving a placeholder.
  const [artFailed, setArtFailed] = useState(false);
  const art = artFailed ? null : posterUrl(work);

  const owned = work.library_file_id !== null;
  const busy = download && download.state !== "failed" && !download.in_library;
  const failed = download?.state === "failed";
  const pct = Math.round((download?.progress ?? 0) * 100);

  // aria2 finishing is not the same as playable: the mover still has to promote
  // the file and the scan has to index it. Naming that gap is what stops the
  // card snapping back to Download and the file being grabbed twice.
  const settling = download?.state === "done" && !owned;

  // One slot, one occupant. Padding is not in the shared string because the
  // tick is a square and the percentage is a pill, and two conflicting Tailwind
  // padding utilities resolve by stylesheet order rather than by the order they
  // are written in — which is a bug that looks like a rendering glitch.
  const SLOT =
    "absolute top-2 left-2 z-2 rounded-[7px] border border-border-hover bg-bg/85 text-[10px] font-extrabold tabular-nums backdrop-blur-sm";

  const marker = busy ? (
    settling ? (
      <span className={`${SLOT} px-1.5 py-0.5 text-text-dim`}>Adding&hellip;</span>
    ) : (
      <span className={`${SLOT} px-1.5 py-0.5 text-accent`}>{pct}%</span>
    )
  ) : owned ? (
    <span
      className={`${SLOT} grid size-[22px] place-items-center text-text-muted`}
      title="In your library"
    >
      <Check />
    </span>
  ) : null;

  const caption = failed ? (
    <span className="text-[11.5px] font-bold text-accent">Failed — pick another</span>
  ) : settling ? (
    <span className="text-[11.5px] text-text-muted">Adding to your library…</span>
  ) : busy ? (
    <span className="text-[11.5px] text-text-muted tabular-nums">
      {pct}%
      {download?.eta_seconds ? ` · ${clock(download.eta_seconds)} left` : ""}
    </span>
  ) : (
    <div className="flex flex-wrap items-center gap-1.5">
      {sortedBy === "latest" && work.latest_release_at ? (
        <MicroChip tone="bright">{uploadedAgo(work.latest_release_at)}</MicroChip>
      ) : (
        work.year && <MicroChip>{work.year}</MicroChip>
      )}
      {/* The strict wall means an anime card here IS complete — say so.
          Elsewhere (search can surface fragments) the honest count shows. */}
      {work.complete === true ? (
        <MicroChip tone="bright">Complete ✓</MicroChip>
      ) : work.complete === false && work.episodes_expected ? (
        <MicroChip tone="muted">
          {work.episodes_covered ?? 0} of {work.episodes_expected}
        </MicroChip>
      ) : (
        <MicroChip tone={work.release_count > 1 ? "bright" : "muted"}>
          {work.release_count} rel
        </MicroChip>
      )}
    </div>
  );

  const body = (
    <>
      <ArtTile
        seed={work.title}
        label={undefined}
        className={`aspect-2/3 rounded-[11px] transition-opacity ${
          pcReachable || owned ? "" : "opacity-55"
        }`}
      >
        {art && (
          // eslint-disable-next-line @next/next/no-img-element -- proxied and
          // cached by the API; next/image would add a second cache for nothing.
          <img
            src={art}
            alt=""
            onError={() => setArtFailed(true)}
            className="absolute inset-0 size-full object-cover"
            loading="lazy"
          />
        )}
        {marker}
        {busy && !settling && (
          <ProgressBar percent={pct} className="absolute inset-x-0 bottom-0 h-1 rounded-none" />
        )}

        {/* One circle, one glyph, one action. State changes the glyph; state
            never adds a badge. */}
        <span className="pointer-events-none absolute inset-0 hidden items-center justify-center bg-bg/45 opacity-0 transition-opacity duration-200 group-hover:opacity-100 [@media(hover:hover)]:flex">
          <span
            className={`flex size-11 items-center justify-center rounded-full ${
              owned || pcReachable
                ? "bg-primary text-white"
                : "border border-border-hover bg-surface-2 text-text-muted"
            }`}
          >
            {owned ? <Play className="size-4 translate-x-px" /> : pcReachable ? "↓" : "☾"}
          </span>
        </span>
      </ArtTile>

      {/* The name lives here and only here. It used to be set on the tile
          instead, which read fine until posters arrived: a poster is artwork,
          not a label — it does not reliably contain a legible title at 150px,
          and for a film it is often not in English — so every card with art
          became anonymous. One place, both states. */}
      <div className="flex min-h-[34px] flex-col gap-1.5 px-1 pb-1">
        <h3 className="truncate text-[13.5px] font-bold" title={work.title}>
          {work.title}
        </h3>
        {caption}
      </div>
    </>
  );

  const shell =
    "flex flex-col gap-2.5 rounded-2xl border border-border bg-surface p-2.5 transition-[transform,border-color] duration-200 ease-[var(--ease-out-quart)] hover:border-border-hover motion-safe:hover:-translate-y-1";

  return (
    <article className="group relative" title={work.title}>
      {owned ? (
        <Link href={`/file/${work.library_file_id}`} className={shell}>
          {body}
        </Link>
      ) : (
        <button type="button" onClick={() => onOpen(work)} className={`${shell} w-full text-left`}>
          {body}
        </button>
      )}
    </article>
  );
}
