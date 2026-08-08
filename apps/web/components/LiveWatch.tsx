"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { API_PUBLIC, fileSize } from "@/lib/api";
import { downloadAction, liveStatus, makeWatchable } from "@/app/actions";
import { Player } from "@/app/watch/[id]/Player";
import { Button, ButtonLink, ProgressBar } from "@/components/ui";

type Status = Exclude<Awaited<ReturnType<typeof liveStatus>>, { error: string }>;

/** One decimal, because "8 MB of 24 MB" sitting still for six seconds looks
 *  broken and "8.2 MB" visibly moves. fileSize() rounds MB to whole numbers. */
const mb = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MB`;

/**
 * Watching a file that is still downloading.
 *
 * The player is mounted at second zero, at its final size, and buffers in
 * place — the same thing every other player on earth does. It used to be a
 * poster card that swapped itself for a <video> at 24MB, which moved the whole
 * page at the one moment the user was looking at it.
 */
export function LiveWatch({ infoHash }: { infoHash: string }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [bps, setBps] = useState(0);
  const [paused, setPaused] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [stopped, setStopped] = useState(false);
  const sample = useRef<{ bytes: number; at: number } | null>(null);

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
        // Start on its own. That is the whole promise of the button. Sticky:
        // a prefix that momentarily reports short must not tear the player out.
        if (res.watchable) setReady(true);

        // Rate of the *front* of the file, not of the download as a whole —
        // that is what decides when this starts playing.
        const now = Date.now();
        const prev = sample.current;
        if (prev && now > prev.at) {
          setBps(Math.max(0, res.playable_bytes - prev.bytes) / ((now - prev.at) / 1000));
        }
        sample.current = { bytes: res.playable_bytes, at: now };
      }
      // Slow down once it is playing: the poll is then only keeping the
      // scrubbable ceiling honest, not deciding whether to start.
      timer = setTimeout(tick, ready ? 5000 : 1500);
    };

    tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [infoHash, ready]);

  const pct = Math.round((status?.progress ?? 0) * 100);
  const readyPct = status?.size_bytes
    ? Math.min(100, Math.round((status.playable_bytes / status.size_bytes) * 100))
    : 0;
  const bufferPct = status?.min_bytes
    ? Math.min(100, Math.round((status.playable_bytes / status.min_bytes) * 100))
    : 0;
  const title = status?.name ?? "Your download";
  // The API serves the file as it is; only the container decides the provider.
  const mime = status?.file?.toLowerCase().endsWith(".webm") ? "video/webm" : "video/mp4";

  return (
    <div className="flex flex-col gap-5">
      <Player
        embedded
        title={title}
        mime={mime}
        src={ready && !error ? `${API_PUBLIC}/api/stream/live/${infoHash}` : null}
      >
        {/* Inside the frame, over the poster. Cross-fades out; nothing moves. */}
        <div
          className={`absolute inset-0 z-(--z-player-chrome) flex flex-col justify-end gap-2.5 bg-gradient-to-t from-bg-deep/95 via-bg-deep/45 to-transparent p-5 transition-opacity duration-700 sm:p-7 ${
            ready && !error ? "pointer-events-none opacity-0" : "opacity-100"
          }`}
        >
          {error ? (
            <>
              <p className="text-[13px] font-extrabold text-accent">Can&apos;t play this</p>
              <p className="max-w-[60ch] text-[12.5px] leading-relaxed text-text-dim">{error}</p>
              <ButtonLink href="/" variant="secondary" size="sm" className="mt-1 w-fit">
                Back to browse
              </ButtonLink>
            </>
          ) : (
            <>
              <p className="text-[13px] font-extrabold tabular-nums" aria-live="polite">
                Buffering
                {status ? ` · ${mb(status.playable_bytes)} of ${mb(status.min_bytes)}` : ""}
                {bps > 0 ? ` · ${mb(bps)}/s` : ""}
              </p>
              <ProgressBar percent={bufferPct} className="h-1.5 max-w-[420px]" />
              <p className="max-w-[60ch] text-[12px] text-text-muted">
                {status && !status.found_on_disk
                  ? "Waiting for the first pieces to land."
                  : "Playing starts on its own. You can close this page — it keeps going."}
              </p>
            </>
          )}
        </div>
      </Player>

      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-[220px] flex-1">
          <h1 className="text-lg font-extrabold tracking-[-0.02em]">{title}</h1>
          <p className="mt-1 text-[12.5px] text-text-muted tabular-nums">
            {status?.complete
              ? "Fully downloaded"
              : `Watchable up to ${readyPct}% · ${pct}% downloaded${
                  status?.size_bytes ? ` of ${fileSize(status.size_bytes)}` : ""
                }`}
          </p>
          {!status?.complete && (
            <ProgressBar percent={readyPct} className="mt-2 h-1.5 max-w-[420px]" />
          )}
        </div>
        <ButtonLink href="/" variant="secondary" size="sm">
          Back to browse
        </ButtonLink>
      </div>

      {status?.sequential === false && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-[12.5px] font-bold">
            This one was queued as a plain download, so it is not in watch order yet.
          </p>
          <Button size="sm" onClick={() => makeWatchable(infoHash)}>
            Switch it to watch order
          </Button>
        </div>
      )}

      {/* Below the player, not in the sidebar row: that row has about 158px of
          usable width, which is not enough for two 44px targets — and a
          destructive action does not belong in truncated text. */}
      {stopped ? (
        <p className="text-[13px] font-bold text-text-dim">
          Stopped. What had downloaded is still on the PC.
        </p>
      ) : confirming ? (
        <div className="flex max-w-[46ch] flex-col gap-2.5 rounded-2xl border border-border bg-surface p-4">
          <p className="text-[13px] font-bold">Stop downloading this?</p>
          <p className="text-[12.5px] leading-relaxed text-text-muted">
            What has already downloaded is kept on the PC — you can start it again later.
            Nothing is deleted.
          </p>
          <div className="flex flex-wrap gap-2.5">
            <Button size="sm" onClick={() => setConfirming(false)}>
              Keep downloading
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={async () => {
                await downloadAction(infoHash, "cancel");
                setConfirming(false);
                setStopped(true);
              }}
            >
              Stop
            </Button>
          </div>
        </div>
      ) : (
        !status?.complete && (
          <div className="flex flex-wrap items-center gap-2.5">
            <Button
              size="sm"
              variant="secondary"
              onClick={async () => {
                const next = !paused;
                setPaused(next);
                await downloadAction(infoHash, next ? "pause" : "resume");
              }}
            >
              {paused ? "Resume" : "Pause"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
              Cancel
            </Button>
            <Link
              href="/"
              className="ml-1 text-[12.5px] font-bold text-text-muted hover:text-text"
            >
              Back to browse
            </Link>
          </div>
        )
      )}
    </div>
  );
}
