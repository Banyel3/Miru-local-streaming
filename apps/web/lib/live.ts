/**
 * Watching a file that is still arriving.
 *
 * The API serves the completed prefix and refuses everything past it, so the
 * media element runs out of bytes and reports the video as over. It is not
 * over — it is partway through a file that is still downloading. These two
 * functions are the difference between those cases and the way back.
 */

/** The subset of the live status these decisions actually depend on. */
export type LiveProgress = {
  complete: boolean;
  playable_bytes: number;
  size_bytes: number;
} | null;

/**
 * Whether the player running out of bytes means the video is over.
 *
 * `null` is a library file with no download behind it: nothing is going to
 * arrive, so the end is the end. Anything still downloading is not over
 * however much has played, including a download that is paused or stalled —
 * the user can resume it, and announcing the video ended would also throw away
 * their position.
 */
export function endedForReal(progress: LiveProgress): boolean {
  return progress === null || progress.complete;
}

/**
 * The same stream, asked for again.
 *
 * The browser already has the byte range it requested and will not go back for
 * more on its own, so resuming needs a URL it has not seen. One parameter,
 * replaced rather than appended: a slow download resumes many times over
 * several hours and the URL must not grow each time.
 */
export function resumeSrc(src: string, attempt: number): string {
  const url = new URL(src);
  url.searchParams.set("resume", String(attempt));
  return url.toString();
}

/**
 * What the stream itself said when asked for its first bytes.
 *
 * `waiting` is the important one. A 503 means the remux that makes this file
 * playable in a browser is still being written, and a 416 means the completed
 * prefix has not reached the requested range yet — both are "come back", not
 * "broken". Only a 5xx that is not 503 is a failure worth telling the user
 * about, because the remux said it could not do it at all.
 */
export type StreamState = "ready" | "waiting" | "failed";

export function streamState(status: number): StreamState {
  if (status === 200 || status === 206) return "ready";
  if (status === 503 || status === 416 || status === 425) return "waiting";
  return "failed";
}

/**
 * Whether to hand the player a source.
 *
 * Both halves, because they answer different questions. `watchable` says the
 * SOURCE has enough bytes; it says nothing about whether the thing the player
 * will actually be handed can be served — an MKV has to be remuxed first, and
 * that takes time after the bytes exist.
 *
 * Treating `watchable` alone as ready is what left a spinner on a black player
 * forever: the buffering overlay disappeared, the player owned the screen, and
 * the 503 underneath it had nowhere to show.
 */
export function canPlayNow(s: { watchable: boolean; streamReady: boolean }): boolean {
  return s.watchable && s.streamReady;
}

/** What `/api/stream/{id}/status` reports for a library file. */
export type RemuxStatus = {
  state: "ready" | "working" | "failed";
  percent: number | null;
  error: string | null;
};

/**
 * What the watch page should show while a library remux runs.
 *
 * A 3.8 GB MKV took ~7 minutes to remux behind a bare spinner and was reported
 * as "remux is failing" — the correct reading of what the page showed. The
 * percent was on disk the whole time; this is where it reaches the screen.
 */
export function remuxWait(s: RemuxStatus): {
  done: boolean;
  failed: string | null;
  label: string;
} {
  if (s.state === "ready") return { done: true, failed: null, label: "" };
  if (s.state === "failed") {
    return {
      done: false,
      failed: s.error ?? "This file could not be prepared for your browser.",
      label: "",
    };
  }
  return {
    done: false,
    failed: null,
    // No stuck 0% before ffmpeg writes its first byte — "not started" and
    // "just started" are different answers.
    label: s.percent != null ? `Preparing for your browser — ${s.percent}%` : "Preparing for your browser…",
  };
}

/** How long a just-submitted torrent may be unknown to the downloader before
 *  that means failure rather than "the submit is still landing". Covers the
 *  server action → API → qBittorrent-over-tailnet chain with headroom. */
export const STARTUP_GRACE_S = 25;

/**
 * What an early status error means.
 *
 * Watch Now navigates to the player immediately — the player's buffering
 * overlay is the loading state, not a frozen button label — so the submit
 * races the first poll, and "no such torrent" during the race is the normal
 * case, not a failure.
 */
export function startupState(s: {
  elapsedS: number;
  statusError: boolean;
}): "ready" | "starting" | "failed" {
  if (!s.statusError) return "ready";
  return s.elapsedS <= STARTUP_GRACE_S ? "starting" : "failed";
}
