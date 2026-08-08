"use client";

import { MediaFile, clock, isPlayable } from "@/lib/api";
import { isComplete, useProgress } from "@/lib/store";
import { Play } from "@/components/icons";
import { ButtonLink } from "@/components/ui";

/** The primary action, labelled by what it will actually do. "Watch Now" on a
 *  file you're 40 minutes into is a lie the user pays for. */
export function ResumeHero({ file, size = "lg" }: { file: MediaFile; size?: "md" | "lg" }) {
  const progress = useProgress(file.id);
  if (!isPlayable(file)) {
    return (
      <ButtonLink href={`/file/${file.id}`} variant="secondary" size={size}>
        Unavailable — see why
      </ButtonLink>
    );
  }

  const resumable = progress && !isComplete(progress) ? progress : null;

  return (
    <ButtonLink href={`/watch/${file.id}`} size={size}>
      <Play />
      {resumable ? `Resume from ${clock(resumable.positionS)}` : "Watch Now"}
    </ButtonLink>
  );
}
