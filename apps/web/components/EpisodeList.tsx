"use client";

import Link from "next/link";
import { API_PUBLIC, EpisodeRow, SeriesRef, episodeLabelFor } from "@/lib/api";
import { MicroChip, SectionHeading } from "@/components/ui";
import { Check, Play } from "@/components/icons";

/**
 * Every episode of a show, owned and available in one list.
 *
 * This replaces "In this folder", which grouped by directory — and since every
 * download lands flat in one media folder, "the folder" was the entire library.
 * The screenshot that prompted this showed four different shows, a Filipino
 * film and a test clip presented as if they belonged together.
 *
 * Owned and available are one list on purpose: the question is "what do I have
 * and what can I get", and answering it in two places makes the user do the
 * joining. The difference is a marker on a row.
 */
export function EpisodeList({
  series,
  episodes,
  currentFileId,
}: {
  series: SeriesRef;
  episodes: EpisodeRow[];
  currentFileId?: number;
}) {
  if (episodes.length === 0) return null;

  const owned = episodes.filter((e) => e.owned).length;

  return (
    <section className="flex flex-col gap-4">
      <SectionHeading title="Episodes" jp="エピソード">
        <span className="text-[12.5px] text-text-muted">
          {owned} of {episodes.length} in your library
        </span>
      </SectionHeading>

      <ul className="flex flex-col gap-2">
        {episodes.map((e) => {
          const here = e.file_id != null && e.file_id === currentFileId;
          const label = episodeLabelFor(e);
          const body = (
            <>
              <span className="flex min-w-0 flex-1 items-center gap-3">
                <span
                  className={`grid size-6 shrink-0 place-items-center rounded-full text-[11px] ${
                    e.owned ? "bg-primary text-white" : "border border-border text-text-muted"
                  }`}
                  aria-hidden
                >
                  {e.owned ? <Check /> : "↓"}
                </span>
                <span className="truncate text-[13.5px] font-bold">{label}</span>
                {here && <MicroChip tone="bright">playing</MicroChip>}
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                {e.quality && <MicroChip>{e.quality}</MicroChip>}
                {!e.owned && (
                  <MicroChip>
                    {e.releases} release{e.releases === 1 ? "" : "s"}
                  </MicroChip>
                )}
                {e.owned && <Play className="size-3.5 text-text-muted" />}
              </span>
            </>
          );

          const shell =
            "flex min-h-11 items-center gap-3 rounded-2xl border border-border bg-surface px-4 py-2.5 transition-colors hover:border-border-hover";

          return (
            <li key={`${e.episode}-${e.episode_end ?? ""}`}>
              {e.owned && e.file_id != null ? (
                <Link href={`/watch/${e.file_id}`} className={shell}>
                  {body}
                </Link>
              ) : (
                // Not owned: the picker lives on the wall, which is where the
                // release choice and its "needs the PC" labelling already are.
                <Link href={`/?kind=${series.kind}`} className={`${shell} opacity-80`}>
                  {body}
                </Link>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
