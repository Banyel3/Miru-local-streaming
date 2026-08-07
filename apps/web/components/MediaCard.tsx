"use client";

import Link from "next/link";
import { MediaFile, STRATEGY, resolution, runtime } from "@/lib/api";
import { percentOf, toggleFavourite, useFavourites, useProgress } from "@/lib/store";
import { Check, Heart, Play } from "@/components/icons";
import { MicroChip, ProgressBar, artTint } from "@/components/ui";
import { isComplete } from "@/lib/store";

export function MediaCard({ file }: { file: MediaFile }) {
  const progress = useProgress(file.id);
  const favourites = useFavourites();
  const isFav = favourites?.includes(file.id) ?? false;
  const watched = progress ? isComplete(progress) : false;
  const strategy = STRATEGY[file.playback_strategy];

  return (
    <article className="group relative">
      <Link
        href={`/file/${file.id}`}
        className="flex flex-col gap-2.5 rounded-2xl border border-border bg-surface p-2.5 transition-[transform,border-color] duration-200 ease-[var(--ease-out-quart)] hover:border-border-hover motion-safe:hover:-translate-y-1"
      >
        <div
          className={`relative aspect-2/3 overflow-hidden rounded-[11px] transition-opacity ${
            watched ? "opacity-55" : ""
          }`}
          style={{ background: artTint(file.title) }}
        >
          <span className="absolute inset-x-0 bottom-0 line-clamp-3 bg-gradient-to-t from-black/45 to-transparent p-3 pt-8 font-jp text-[11px] leading-snug text-text-dim">
            {file.title}
          </span>

          {watched && (
            <span
              className="absolute top-2 left-2 flex size-[22px] items-center justify-center rounded-full bg-bg/85 text-text-muted"
              title="Watched"
            >
              <Check />
            </span>
          )}

          {!strategy.playable && (
            <span className="absolute top-2 right-2 rounded-md border border-border-hover bg-bg/85 px-1.5 py-0.5 font-mono text-[9px] text-text-muted">
              {file.playback_strategy}
            </span>
          )}

          {/* Hover affordance on pointer devices; touch users get the whole
              card as the target, so nothing is hover-gated. */}
          <span className="pointer-events-none absolute inset-0 hidden items-center justify-center bg-bg/45 opacity-0 transition-opacity duration-200 group-hover:opacity-100 [@media(hover:hover)]:flex">
            <span className="flex size-11 items-center justify-center rounded-full bg-primary text-white">
              <Play className="size-4 translate-x-px" />
            </span>
          </span>

          {progress && !watched && (
            <ProgressBar percent={percentOf(progress)} className="absolute inset-x-0 bottom-0 h-1 rounded-none" />
          )}
        </div>

        <div className="flex flex-col gap-1.5 px-1 pb-1">
          <h3 className="truncate text-[13.5px] font-bold" title={file.title}>
            {file.title}
          </h3>
          <div className="flex flex-wrap items-center gap-1.5">
            {runtime(file.duration_ms) && (
              <span className="mr-0.5 text-[11.5px] text-text-muted">{runtime(file.duration_ms)}</span>
            )}
            {resolution(file) && <MicroChip tone="bright">{resolution(file)}</MicroChip>}
            {file.video_codec && <MicroChip>{file.video_codec}</MicroChip>}
          </div>
        </div>
      </Link>

      <button
        type="button"
        onClick={() => toggleFavourite(file.id)}
        aria-pressed={isFav}
        aria-label={isFav ? `Remove ${file.title} from favourites` : `Add ${file.title} to favourites`}
        className={`absolute top-4 right-4 grid size-9 place-items-center rounded-full border border-border bg-bg/80 backdrop-blur-sm transition-[color,border-color,opacity] duration-150 hover:border-border-hover ${
          isFav
            ? "text-accent opacity-100"
            : "text-text-muted opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
        }`}
      >
        <Heart className="size-4" filled={isFav} />
      </button>
    </article>
  );
}

export function MediaGrid({ files }: { files: MediaFile[] }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4 sm:grid-cols-[repeat(auto-fill,minmax(170px,1fr))] xl:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]">
      {files.map((f) => (
        <MediaCard key={f.id} file={f} />
      ))}
    </div>
  );
}
