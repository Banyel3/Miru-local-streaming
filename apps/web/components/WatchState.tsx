"use client";

import { Check } from "@/components/icons";
import { isComplete, percentOf, useProgress } from "@/lib/store";

/** A dot that carries watch state: hollow = unseen, accent ring = partial,
 *  check = done. Colour is doing real work here, not decoration. */
export function WatchState({ id }: { id: number }) {
  const progress = useProgress(id);

  if (!progress) {
    return <span className="size-2 shrink-0 rounded-full border border-border-hover" aria-hidden />;
  }

  if (isComplete(progress)) {
    return (
      <span className="shrink-0 text-text-muted" title="Watched">
        <Check className="size-3.5" />
      </span>
    );
  }

  const pct = Math.round(percentOf(progress));
  return (
    <span
      className="size-2.5 shrink-0 rounded-full bg-accent"
      title={`${pct}% watched`}
      aria-label={`${pct}% watched`}
    />
  );
}
