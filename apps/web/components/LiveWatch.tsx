"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { API_PUBLIC, fileSize } from "@/lib/api";
import { liveStatus, makeWatchable } from "@/app/actions";
import { Button, ButtonLink, ProgressBar, artTint } from "@/components/ui";

type Status = Exclude<Awaited<ReturnType<typeof liveStatus>>, { error: string }>;

/**
 * Waiting for enough of a file to arrive, then playing it.
 *
 * Deliberately not a spinner. A download can take twenty seconds or twenty
 * minutes depending on the swarm, and the difference between those two feeling
 * fine and feeling broken is entirely whether the screen says which one is
 * happening.
 */
export function LiveWatch({ infoHash }: { infoHash: string }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const video = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      const res = await liveStatus(infoHash);
      if (!live) return;
      if ("error" in res) {
        setError(res.error);
      } else {
        setError(null);
        setStatus(res);
        // Start on its own. That is the whole promise of the button.
        if (res.watchable) setPlaying(true);
      }
      // Slow down once it is playing: the poll is then only keeping the
      // scrubbable ceiling honest, not deciding whether to start.
      timer = setTimeout(tick, playing ? 5000 : 1500);
    };

    tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [infoHash, playing]);

  const pct = Math.round((status?.progress ?? 0) * 100);
  const readyPct = status?.size_bytes
    ? Math.min(100, Math.round((status.playable_bytes / status.size_bytes) * 100))
    : 0;
  const title = status?.name ?? "Your download";

  if (playing && status) {
    return (
      <div className="flex flex-col gap-5">
        <div className="bleed relative -mt-6 overflow-hidden bg-bg-deep lg:-mt-9">
          {/* Plain <video>: the source is a moving target and the player's own
              buffering heuristics assume a file that is not growing. */}
          <video
            ref={video}
            src={`${API_PUBLIC}/api/stream/live/${infoHash}`}
            controls
            autoPlay
            className="mx-auto max-h-[78dvh] w-full bg-black"
          />
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <div className="min-w-[220px] flex-1">
            <h1 className="text-lg font-extrabold">{title}</h1>
            <p className="mt-1 text-[12.5px] text-text-muted tabular-nums">
              {status.complete
                ? "Fully downloaded"
                : `Watchable up to ${readyPct}% · ${pct}% downloaded`}
            </p>
            {!status.complete && (
              <ProgressBar percent={readyPct} className="mt-2 h-1.5 max-w-[420px]" />
            )}
          </div>
          <ButtonLink href="/" variant="secondary" size="sm">
            Back to browse
          </ButtonLink>
        </div>

        {!status.complete && (
          <p className="max-w-[70ch] text-[12.5px] leading-relaxed text-text-muted">
            Still downloading. You can watch up to the point that has arrived; seeking past
            it will wait rather than skip. It keeps going if you leave this page.
          </p>
        )}
      </div>
    );
  }

  return (
    <section className="flex flex-wrap gap-6 rounded-3xl border border-border bg-surface p-6">
      <div
        className="relative flex aspect-2/3 w-[132px] shrink-0 flex-col justify-end overflow-hidden rounded-2xl border border-border"
        style={{ background: artTint(title) }}
        aria-hidden
      >
        <div
          className="absolute inset-0 opacity-[0.07]"
          style={{ backgroundImage: "repeating-linear-gradient(115deg,#fff 0 1px,transparent 1px 14px)" }}
        />
        <span className="relative p-3 text-[11.5px] font-bold">{title}</span>
      </div>

      <div className="flex min-w-[240px] flex-1 flex-col gap-3">
        <span className="w-fit rounded-full border border-border-hover bg-bg/70 px-3 py-1.5 text-[11px] font-bold">
          {error ? "Can't reach the download" : "Downloading on the PC"}
        </span>

        <h1 className="text-2xl font-extrabold tracking-[-0.02em]">{title}</h1>

        {error ? (
          <>
            <p className="max-w-[60ch] text-[13px] leading-relaxed text-accent">{error}</p>
            <ButtonLink href="/" variant="secondary" size="md" className="w-fit">
              Back to browse
            </ButtonLink>
          </>
        ) : (
          <>
            <ProgressBar percent={pct} className="mt-1 h-1.5 max-w-[420px]" />
            <p className="text-[12.5px] text-text-dim tabular-nums">
              <strong className="text-accent">{pct}%</strong>
              {status?.size_bytes ? ` · ${fileSize(status.size_bytes)}` : ""}
              {status && !status.found_on_disk ? " · waiting for the first pieces" : ""}
            </p>

            <p className="mt-1 max-w-[60ch] text-[13px] font-bold">
              {status?.sequential === false
                ? "This one was queued as a plain download, so it is not in watch order yet."
                : "Playback starts on its own once enough has arrived. You can close this."}
            </p>

            {status?.sequential === false && (
              <Button
                size="md"
                className="w-fit"
                onClick={() => makeWatchable(infoHash)}
              >
                Switch it to watch order
              </Button>
            )}

            <p className="text-[11.5px] text-text-muted">
              Needs about {fileSize(status?.min_bytes ?? 0)} of the beginning before it can
              start.
            </p>

            <Link href="/" className="mt-1 w-fit text-[12.5px] font-bold text-text-muted hover:text-text">
              Back to browse
            </Link>
          </>
        )}
      </div>
    </section>
  );
}
