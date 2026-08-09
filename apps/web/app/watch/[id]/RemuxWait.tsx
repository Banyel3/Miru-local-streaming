"use client";

import { useEffect, useState, type ComponentProps } from "react";
import { API_PUBLIC, guardSession } from "@/lib/api";
import { RemuxStatus, remuxWait } from "@/lib/live";
import { Player } from "./Player";
import { ButtonLink } from "@/components/ui";

/**
 * The watch page, while a remux stands between the file and the browser.
 *
 * The page used to mount `<Player src=…>` unconditionally. For a `remux` file
 * the 425 the stream answers lands inside the <video> element's own request,
 * where no page code can read it — so a 3.8 GB MKV spent ~7 minutes behind a
 * bare spinner and was reported as "remux is failing". The remux was fine; the
 * page had nothing to say.
 *
 * This polls `/api/stream/{id}/status` (the same fact somewhere readable) and
 * holds `src=null` — Player mounts no media element, keeps its frame at final
 * size — with the percent in the overlay slot. When the status answers ready,
 * the real src mounts and nothing else moves.
 */
export function RemuxWait({
  fileId,
  ...player
}: { fileId: number } & Omit<ComponentProps<typeof Player>, "children">) {
  const [status, setStatus] = useState<RemuxStatus | null>(null);

  const wait = status ? remuxWait(status) : { done: false, failed: null, label: "Preparing for your browser…" };

  useEffect(() => {
    if (wait.done || wait.failed) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const res = guardSession(
          await fetch(`${API_PUBLIC}/api/stream/${fileId}/status`, { cache: "no-store" }),
        );
        if (!live) return;
        if (res.ok) setStatus(await res.json());
      } catch {
        // The next tick asks again; a blip is not a verdict.
      }
      timer = setTimeout(tick, 1500);
    };

    tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [fileId, wait.done, wait.failed]);

  return (
    <Player {...player} src={wait.done ? player.src : null}>
      {!wait.done && (
        <div className="absolute inset-0 z-(--z-player-chrome) flex flex-col justify-end gap-2.5 bg-gradient-to-t from-bg-deep/95 via-bg-deep/45 to-transparent p-5 sm:p-7">
          {wait.failed ? (
            <>
              <p className="text-[13px] font-extrabold text-accent">Can&apos;t play this</p>
              <p className="max-w-[60ch] text-[12.5px] leading-relaxed text-text-dim">{wait.failed}</p>
              <ButtonLink href="/" variant="secondary" size="sm" className="mt-1 w-fit">
                Back to browse
              </ButtonLink>
            </>
          ) : (
            <>
              <p className="text-[13px] font-extrabold tabular-nums" aria-live="polite">
                {wait.label}
              </p>
              <p className="max-w-[60ch] text-[12px] text-text-muted">
                One-time conversion so your browser can play this container. It starts on its own.
              </p>
            </>
          )}
        </div>
      )}
    </Player>
  );
}
