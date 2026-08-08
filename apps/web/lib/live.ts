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
