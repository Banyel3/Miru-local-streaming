"use client";

import { MediaFile, STRATEGY, clock, isPlayable } from "@/lib/api";
import { clearProgress, isComplete, percentOf, toggleFavourite, useFavourites, useProgress } from "@/lib/store";
import { Heart, Play } from "@/components/icons";
import { Button, ButtonLink, ProgressBar } from "@/components/ui";

export function DetailActions({ file }: { file: MediaFile }) {
  const progress = useProgress(file.id);
  const favourites = useFavourites();
  const isFav = favourites?.includes(file.id) ?? false;
  const strategy = STRATEGY[file.playback_strategy];
  const playable = isPlayable(file);
  const resumable = progress && !isComplete(progress) ? progress : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        {playable ? (
          <>
            <ButtonLink href={`/watch/${file.id}`} size="lg">
              <Play />
              {resumable ? `Resume from ${clock(resumable.positionS)}` : "Watch"}
            </ButtonLink>
            {resumable && (
              <ButtonLink
                href={`/watch/${file.id}?restart=1`}
                variant="secondary"
                size="lg"
                onClick={() => clearProgress(file.id)}
              >
                Watch from start
              </ButtonLink>
            )}
          </>
        ) : (
          <div className="flex flex-col gap-2 rounded-xl border border-border-hover bg-surface px-5 py-4">
            <span className="flex items-center gap-2 text-sm font-bold text-accent">
              <span className="size-[7px] rounded-full bg-accent" aria-hidden />
              {/* "right now" invites the user to wake the PC and try again.
                  Nothing will ever decode a DRM-encrypted track, so that
                  wording would send them off to fix an outage that isn't one. */}
              {file.availability === "unplayable"
                ? "This file can't be played"
                : "Not playable right now"}
            </span>
            <p className="max-w-[52ch] text-[13px] leading-relaxed text-text-dim">
              {file.availability_note ?? strategy.note}
            </p>
          </div>
        )}

        <Button
          variant="secondary"
          size="lg"
          onClick={() => toggleFavourite(file.id)}
          aria-pressed={isFav}
          className={isFav ? "text-accent" : ""}
        >
          <Heart className="size-4" filled={isFav} />
          {isFav ? "Favourited" : "Favourite"}
        </Button>
      </div>

      {resumable && (
        <div className="flex max-w-[380px] flex-col gap-2">
          <div className="flex justify-between text-[11.5px] text-text-muted">
            <span>{clock(resumable.durationS - resumable.positionS)} left</span>
            <span className="font-bold text-accent">{Math.round(percentOf(resumable))}%</span>
          </div>
          <ProgressBar percent={percentOf(resumable)} className="h-1" />
        </div>
      )}
    </div>
  );
}
