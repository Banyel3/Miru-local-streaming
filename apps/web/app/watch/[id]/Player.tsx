"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  MediaPlayer,
  MediaPlayerInstance,
  MediaProvider,
  type MediaTimeUpdateEventDetail,
} from "@vidstack/react";
import { DefaultVideoLayout, defaultLayoutIcons } from "@vidstack/react/player/layouts/default";
import "@vidstack/react/player/styles/default/theme.css";
import "@vidstack/react/player/styles/default/layouts/video.css";

import { MediaFile, PlayerMime, STRATEGY, displayTitle } from "@/lib/api";
import { getProgress, setProgress } from "@/lib/store";
import { ChevronLeft, Play } from "@/components/icons";
import { ArtTile } from "@/components/ui";

/** How close to the end the next-episode card appears, and how long it counts
 *  down before advancing on its own. */
const NEXT_CARD_AT_S = 40;
const COUNTDOWN_S = 10;
/** Progress is written at most this often; a timeupdate fires ~4x a second. */
const SAVE_EVERY_MS = 4000;

function StrategyChip({ file }: { file: MediaFile }) {
  const direct = file.playback_strategy === "direct";
  return (
    <div
      className={`flex items-center gap-2 rounded-full border bg-surface/60 px-3.5 py-1.5 font-mono text-[11.5px] font-semibold backdrop-blur-md ${
        direct ? "border-border text-highlight" : "border-border-hover text-accent"
      }`}
      title={STRATEGY[file.playback_strategy].note}
    >
      <span
        className={`size-[7px] rounded-full ${direct ? "bg-highlight" : "bg-accent"}`}
        aria-hidden
      />
      {STRATEGY[file.playback_strategy].label}
    </div>
  );
}

function NextCard({
  next,
  seconds,
  onPlay,
  onDismiss,
}: {
  next: MediaFile;
  seconds: number;
  onPlay: () => void;
  onDismiss: () => void;
}) {
  const circumference = 2 * Math.PI * 18;
  const offset = circumference * (1 - seconds / COUNTDOWN_S);

  return (
    <div
      role="region"
      aria-label="Up next"
      className="pointer-events-auto flex w-[min(360px,calc(100vw-2rem))] items-center gap-3.5 rounded-2xl border border-border bg-surface/95 p-3.5 backdrop-blur-xl motion-safe:animate-[miru-rise_.3s_var(--ease-out-quart)]"
    >
      <ArtTile
        seed={next.title}
        className="hidden h-14 w-24 shrink-0 rounded-[9px] border border-border sm:flex"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-[10px] font-extrabold tracking-[0.14em] text-accent">
          NEXT <span className="font-jp text-[11px] tracking-normal">つづく</span>
        </span>
        <span className="truncate text-[13px] font-bold" title={next.title}>
          {displayTitle(next).label}
        </span>
        <div className="mt-1.5 flex items-center gap-2">
          <button
            type="button"
            onClick={onPlay}
            className="inline-flex items-center gap-2 rounded-[9px] bg-primary px-3.5 py-1.5 text-[11.5px] font-bold text-white transition-colors hover:bg-primary-hover"
          >
            <Play className="size-2.5" />
            Play Next
          </button>
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-[9px] px-2.5 py-1.5 text-[11.5px] font-semibold text-text-muted transition-colors hover:text-text"
          >
            Dismiss
          </button>
        </div>
      </div>
      <svg width="44" height="44" viewBox="0 0 44 44" className="shrink-0" aria-hidden>
        <circle cx="22" cy="22" r="18" fill="none" stroke="var(--color-border)" strokeWidth="3" />
        <circle
          cx="22"
          cy="22"
          r="18"
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform="rotate(-90 22 22)"
          style={{ transition: "stroke-dashoffset 1s linear" }}
        />
        <text
          x="22"
          y="27"
          textAnchor="middle"
          fontSize="14"
          fontWeight="800"
          fill="var(--color-text)"
          fontFamily="var(--font-sans)"
        >
          {seconds}
        </text>
      </svg>
      <span className="sr-only" aria-live="polite">
        Next episode in {seconds} seconds
      </span>
    </div>
  );
}

export function Player({
  file,
  src,
  mime,
  next,
  restart,
}: {
  file: MediaFile;
  src: string;
  mime: PlayerMime;
  next: MediaFile | null;
  restart: boolean;
}) {
  const router = useRouter();
  const player = useRef<MediaPlayerInstance>(null);
  const lastSave = useRef(0);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [dismissed, setDismissed] = useState(false);

  const goNext = useCallback(() => {
    if (next) router.push(`/watch/${next.id}`);
  }, [next, router]);

  // Resume where the last session stopped. Vidstack fires canPlay once the
  // media is seekable, which is the earliest point a seek will stick.
  const onCanPlay = useCallback(() => {
    if (restart) return;
    const saved = getProgress(file.id);
    if (saved && saved.positionS > 0 && player.current) {
      player.current.currentTime = saved.positionS;
    }
  }, [file.id, restart]);

  const onTimeUpdate = useCallback(
    ({ currentTime }: MediaTimeUpdateEventDetail) => {
      const duration = player.current?.state.duration ?? 0;
      if (!duration) return;

      const now = Date.now();
      if (now - lastSave.current > SAVE_EVERY_MS) {
        lastSave.current = now;
        setProgress(file.id, currentTime, duration);
      }

      const remaining = duration - currentTime;
      if (next && !dismissed && remaining <= NEXT_CARD_AT_S && remaining > 0) {
        setCountdown((c) => (c === null ? Math.min(COUNTDOWN_S, Math.ceil(remaining)) : c));
      }
    },
    [dismissed, file.id, next],
  );

  // Flush the final position on unmount so leaving mid-episode still resumes.
  useEffect(() => {
    const flush = () => {
      const p = player.current;
      if (p && p.state.duration) setProgress(file.id, p.state.currentTime, p.state.duration);
    };
    window.addEventListener("pagehide", flush);
    return () => {
      flush();
      window.removeEventListener("pagehide", flush);
    };
  }, [file.id]);

  // Countdown ticks independently of playback so a paused tail still advances.
  useEffect(() => {
    if (countdown === null) return;
    if (countdown <= 0) {
      goNext();
      return;
    }
    const t = setTimeout(() => setCountdown((c) => (c ?? 1) - 1), 1000);
    return () => clearTimeout(t);
  }, [countdown, goNext]);

  return (
    <main className="relative h-dvh w-full overflow-hidden bg-bg-deep">
      <MediaPlayer
        ref={player}
        className="absolute inset-0 h-full w-full"
        title={file.title}
        // Explicit type, always. See mimeType() — an extensionless stream URL
        // sends Vidstack down a cross-origin header probe that fails silently.
        src={{ src, type: mime }}
        playsInline
        autoPlay
        keyShortcuts={{
          togglePaused: "k Space",
          seekBackward: "j ArrowLeft",
          seekForward: "l ArrowRight",
          toggleFullscreen: "f",
          toggleMuted: "m",
          volumeUp: "ArrowUp",
          volumeDown: "ArrowDown",
        }}
        onCanPlay={onCanPlay}
        onTimeUpdate={onTimeUpdate}
        onEnded={() => next && goNext()}
      >
        <MediaProvider />
        {/* Vidstack's shipped layout, repainted via CSS variables in
            globals.css. Spec §10: do not hand-roll video controls. */}
        <DefaultVideoLayout icons={defaultLayoutIcons} />
      </MediaPlayer>

      {/* Top chrome. Pointer-events off so it never steals clicks from the
          player surface; the interactive parts opt back in. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-(--z-player-chrome) flex items-start justify-between gap-4 bg-gradient-to-b from-bg-deep/80 to-transparent p-4 pb-16 sm:p-7 sm:pb-20">
        <div className="pointer-events-auto flex min-w-0 items-center gap-3.5">
          <Link
            href={`/file/${file.id}`}
            aria-label="Back to file details"
            className="grid size-10 shrink-0 place-items-center rounded-xl border border-border bg-surface/60 text-text backdrop-blur-md transition-colors hover:border-border-hover"
          >
            <ChevronLeft />
          </Link>
          <div className="flex min-w-0 flex-col gap-0.5">
            <h1 className="truncate text-[15px] font-extrabold">{file.title}</h1>
            <p className="truncate text-xs text-text-muted">
              {[file.container?.toUpperCase(), file.video_codec, file.audio_codec]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
        </div>
        <div className="pointer-events-auto hidden sm:block">
          <StrategyChip file={file} />
        </div>
      </div>

      {countdown !== null && next && (
        <div className="pointer-events-none absolute right-4 bottom-32 z-(--z-player-chrome) sm:right-7 sm:bottom-36">
          <NextCard
            next={next}
            seconds={Math.max(0, countdown)}
            onPlay={goNext}
            onDismiss={() => {
              setDismissed(true);
              setCountdown(null);
            }}
          />
        </div>
      )}
    </main>
  );
}
