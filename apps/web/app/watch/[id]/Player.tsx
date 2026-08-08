"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ElementType, type ReactNode } from "react";
import {
  MediaPlayer,
  MediaPlayerInstance,
  MediaProvider,
  Track,
  useMediaState,
  type MediaTimeUpdateEventDetail,
} from "@vidstack/react";
import { DefaultVideoLayout, defaultLayoutIcons } from "@vidstack/react/player/layouts/default";
import "@vidstack/react/player/styles/default/theme.css";
import "@vidstack/react/player/styles/default/layouts/video.css";

import { MediaFile, PlayerMime, STRATEGY, SubtitleTrack } from "@/lib/api";
import { AssOverlay } from "@/components/AssOverlay";
import { getProgress, setProgress } from "@/lib/store";
import { ChevronLeft, Play } from "@/components/icons";
import { ArtTile, artTint } from "@/components/ui";

/** How close to the end the next-episode card appears, and how long it counts
 *  down before advancing on its own. */
const NEXT_CARD_AT_S = 40;
const COUNTDOWN_S = 10;
/** Progress is written at most this often; a timeupdate fires ~4x a second. */
const SAVE_EVERY_MS = 4000;

/** The next thing to play, flattened. A live download has no MediaFile row, so
 *  the player is told where to go rather than handed one. */
export type NextUp = { href: string; label: string; seed: string };

function StrategyChip({ strategy }: { strategy: MediaFile["playback_strategy"] }) {
  const direct = strategy === "direct";
  return (
    <div
      className={`flex items-center gap-2 rounded-full border bg-surface/60 px-3.5 py-1.5 font-mono text-[11.5px] font-semibold backdrop-blur-md ${
        direct ? "border-border text-highlight" : "border-border-hover text-accent"
      }`}
      title={STRATEGY[strategy].note}
    >
      <span
        className={`size-[7px] rounded-full ${direct ? "bg-highlight" : "bg-accent"}`}
        aria-hidden
      />
      {STRATEGY[strategy].label}
    </div>
  );
}

function NextCard({
  next,
  seconds,
  onPlay,
  onDismiss,
}: {
  next: NextUp;
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
        seed={next.seed}
        className="hidden h-14 w-24 shrink-0 rounded-[9px] border border-border sm:flex"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-[10px] font-extrabold tracking-[0.14em] text-accent">
          NEXT <span className="font-jp text-[11px] tracking-normal">つづく</span>
        </span>
        <span className="truncate text-[13px] font-bold" title={next.label}>
          {next.label}
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

/**
 * The one video player.
 *
 * Props are deliberately not library-file shaped. A file being downloaded right
 * now has no MediaFile row — no id, no strategy, no siblings — and the screen
 * that plays it still needs subtitles, a real control bar and a real frame.
 * Both routes map their own object into this surface instead.
 */
export function Player({
  src,
  mime,
  title,
  subtitle,
  backHref,
  progressKey = null,
  tracks = [],
  strategy,
  next = null,
  restart = false,
  embedded = false,
  children,
}: {
  /** null mounts no media element at all: nothing has arrived yet, or nothing
   *  here can be decoded. The frame is still drawn, at its final size. */
  src: string | null;
  mime: PlayerMime;
  title: string;
  subtitle?: string | null;
  /** Omitted means no top chrome — the embedding page owns the title row. */
  backHref?: string;
  /** Where resume is stored. Live downloads have nowhere to store it: null. */
  progressKey?: number | null;
  tracks?: SubtitleTrack[];
  strategy?: MediaFile["playback_strategy"];
  next?: NextUp | null;
  restart?: boolean;
  /** Sits in a page at aspect-video instead of owning the viewport. */
  embedded?: boolean;
  /** Drawn over the frame — the live screen's buffering state. */
  children?: ReactNode;
}) {
  const router = useRouter();
  const player = useRef<MediaPlayerInstance>(null);
  const lastSave = useRef(0);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null);
  const [assFailed, setAssFailed] = useState(false);

  // Which caption track the user picked, straight from the player's own menu —
  // so ASS and VTT tracks share one control instead of growing a second one.
  const activeTrack = useMediaState("textTrack", player);
  const styled = tracks.find(
    (t) => t.styled && activeTrack && t.label === activeTrack.label && !assFailed,
  );

  const goNext = useCallback(() => {
    if (next) router.push(next.href);
  }, [next, router]);

  // JASSUB draws onto the real <video>, so it needs the element the provider
  // created rather than the player wrapper.
  const captureVideo = useCallback(() => {
    setVideoEl(player.current?.el?.querySelector("video") ?? null);
  }, []);

  // Resume where the last session stopped. Vidstack fires canPlay once the
  // media is seekable, which is the earliest point a seek will stick.
  const onCanPlay = useCallback(() => {
    captureVideo();
    if (restart || progressKey === null) return;
    const saved = getProgress(progressKey);
    if (saved && saved.positionS > 0 && player.current) {
      player.current.currentTime = saved.positionS;
    }
  }, [captureVideo, progressKey, restart]);

  const onTimeUpdate = useCallback(
    ({ currentTime }: MediaTimeUpdateEventDetail) => {
      const duration = player.current?.state.duration ?? 0;
      if (!duration) return;

      const now = Date.now();
      if (progressKey !== null && now - lastSave.current > SAVE_EVERY_MS) {
        lastSave.current = now;
        setProgress(progressKey, currentTime, duration);
      }

      const remaining = duration - currentTime;
      if (next && !dismissed && remaining <= NEXT_CARD_AT_S && remaining > 0) {
        setCountdown((c) => (c === null ? Math.min(COUNTDOWN_S, Math.ceil(remaining)) : c));
      }
    },
    [dismissed, next, progressKey],
  );

  // Flush the final position on unmount so leaving mid-episode still resumes.
  useEffect(() => {
    if (progressKey === null) return;
    const flush = () => {
      const p = player.current;
      if (p && p.state.duration) setProgress(progressKey, p.state.currentTime, p.state.duration);
    };
    window.addEventListener("pagehide", flush);
    return () => {
      flush();
      window.removeEventListener("pagehide", flush);
    };
  }, [progressKey]);

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

  const Frame: ElementType = embedded ? "div" : "main";

  return (
    <Frame
      className={`relative w-full overflow-hidden bg-bg-deep ${
        embedded ? "aspect-video rounded-2xl border border-border" : "h-dvh"
      } ${styled ? "[&_.vds-captions]:hidden" : ""}`}
    >
      {/* The poster. Drawn under the video rather than instead of it, so the
          frame is never empty and never resizes when the source arrives. */}
      <div className="absolute inset-0" style={{ background: artTint(title) }} aria-hidden />

      {src && (
        <MediaPlayer
          ref={player}
          className="absolute inset-0 h-full w-full"
          title={title}
          // Explicit type, always. See mimeType() — an extensionless stream URL
          // sends Vidstack down a cross-origin header probe that fails silently.
          src={{ src, type: mime }}
          // Required, not optional. The stream is cross-origin, and without this
          // the video is a tainted source: JASSUB cannot construct a VideoFrame
          // from it and dies before it ever fetches the subtitle file. The API
          // sends Access-Control-Allow-Origin for the web origin to match.
          crossOrigin
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
          <MediaProvider>
            {tracks.map((t) => (
              <Track
                key={`sub-${t.index}`}
                src={t.vttUrl}
                kind="subtitles"
                label={t.label}
                lang={t.language ?? undefined}
                type="vtt"
              />
            ))}
          </MediaProvider>
          {/* Vidstack's shipped layout, repainted via CSS variables in
              globals.css. Spec §10: do not hand-roll video controls. */}
          <DefaultVideoLayout icons={defaultLayoutIcons} />
        </MediaPlayer>
      )}

      {/* Styled rendering for the active ASS track. The VTT form stays
          registered above as the fallback, so captions survive a wasm failure. */}
      <AssOverlay
        video={videoEl}
        src={styled?.assUrl ?? null}
        enabled={Boolean(styled)}
        onFailed={() => setAssFailed(true)}
      />

      {/* Top chrome. Pointer-events off so it never steals clicks from the
          player surface; the interactive parts opt back in. */}
      {backHref && (
        <div className="pointer-events-none absolute inset-x-0 top-0 z-(--z-player-chrome) flex items-start justify-between gap-4 bg-gradient-to-b from-bg-deep/80 to-transparent p-4 pb-16 sm:p-7 sm:pb-20">
          <div className="pointer-events-auto flex min-w-0 items-center gap-3.5">
            <Link
              href={backHref}
              aria-label="Back to file details"
              className="grid size-10 shrink-0 place-items-center rounded-xl border border-border bg-surface/60 text-text backdrop-blur-md transition-colors hover:border-border-hover"
            >
              <ChevronLeft />
            </Link>
            <div className="flex min-w-0 flex-col gap-0.5">
              <h1 className="truncate text-[15px] font-extrabold">{title}</h1>
              {subtitle && <p className="truncate text-xs text-text-muted">{subtitle}</p>}
            </div>
          </div>
          {strategy && (
            <div className="pointer-events-auto hidden sm:block">
              <StrategyChip strategy={strategy} />
            </div>
          )}
        </div>
      )}

      {children}

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
    </Frame>
  );
}
