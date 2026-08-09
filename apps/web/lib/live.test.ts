/**
 * Use case: the file is still downloading while you are watching it.
 *
 * The server serves the completed prefix and nothing past it, so the media
 * element reaches the end of what exists and reports the video as finished.
 * It is not finished — it is 30% of the way through a file that is still
 * arriving. Everything here is about telling those two apart.
 */

import { describe, expect, it } from "vitest";
import { canPlayNow, endedForReal, remuxWait, resumeSrc, startupState, streamState } from "./live";

const done = { complete: true, playable_bytes: 100, size_bytes: 100 };
const growing = { complete: false, playable_bytes: 30, size_bytes: 100 };

describe("reaching the end of the bytes that exist", () => {
  it("is not the end of the video while the file is still downloading", () => {
    // The reported bug. The player stops at the prefix edge and presents the
    // end-of-video state on a file that is 30% downloaded.
    expect(endedForReal(growing)).toBe(false);
  });

  it("is the end of the video once the file is complete", () => {
    // The other half: a finished file must still be able to finish, or the
    // player would loop forever on the last frame and never advance.
    expect(endedForReal(done)).toBe(true);
  });

  it("is the end when there is no download in flight at all", () => {
    // A library file that was never a live download has no status. Nothing to
    // wait for, so ended means ended.
    expect(endedForReal(null)).toBe(true);
  });

  it("treats a download that has stopped growing as still growing", () => {
    // Paused, stalled or seedless. The honest answer is "not finished" — the
    // user paused it and can resume. Claiming the video ended would be a lie
    // that also loses their position.
    expect(endedForReal({ ...growing, playable_bytes: 30 })).toBe(false);
  });
});

describe("picking the stream back up where it stopped", () => {
  it("re-requests the stream so the newly-arrived bytes are fetched", () => {
    // The browser has the byte range it already asked for and will not ask
    // again on its own. Without a changed URL the resume is a no-op and the
    // video sits on the last frame regardless of how much has since landed.
    const a = resumeSrc("http://api/api/stream/live/abc", 1);
    const b = resumeSrc("http://api/api/stream/live/abc", 2);
    expect(a).not.toBe(b);
  });

  it("keeps the original URL intact underneath", () => {
    const url = new URL(resumeSrc("http://api/api/stream/live/abc", 3));
    expect(url.pathname).toBe("/api/stream/live/abc");
  });

  it("does not stack a parameter per resume", () => {
    // Six hours of a slow download is a lot of resumes. The URL must not grow
    // by one query parameter each time.
    let src = "http://api/api/stream/live/abc";
    for (let i = 1; i <= 5; i++) src = resumeSrc(src, i);
    expect([...new URL(src).searchParams.keys()]).toEqual(["resume"]);
  });

  it("leaves a query the API already put there alone", () => {
    const url = new URL(resumeSrc("http://api/api/stream/live/abc?t=9", 1));
    expect(url.searchParams.get("t")).toBe("9");
  });
});

describe("waiting for the stream to actually be servable", () => {
  it("does not call the video ready just because enough bytes exist", () => {
    // `watchable` says the SOURCE has enough. It says nothing about whether the
    // thing the player will be handed can be served yet — an MKV has to be
    // remuxed first. Treating watchable as ready is what put a spinner on a
    // black player forever: the overlay vanished, the player owned the screen,
    // and the 503 underneath it was invisible.
    expect(canPlayNow({ watchable: true, streamReady: false })).toBe(false);
  });

  it("is ready once the stream itself answers", () => {
    expect(canPlayNow({ watchable: true, streamReady: true })).toBe(true);
  });

  it("is never ready before there are bytes, however the stream answers", () => {
    expect(canPlayNow({ watchable: false, streamReady: true })).toBe(false);
  });

  it("treats a 503 as come-back-later, not as an error", () => {
    // The remux is being made. The overlay should keep waiting and say so.
    expect(streamState(503)).toBe("waiting");
  });

  it("treats a 206 as playable", () => {
    expect(streamState(206)).toBe("ready");
  });

  it("treats a 416 as nothing-yet rather than broken", () => {
    // Seeking past what has arrived on a file that is still growing.
    expect(streamState(416)).toBe("waiting");
  });

  it("treats a 502 as a real failure worth telling the user about", () => {
    // The remux failed. Waiting forever would be a lie.
    expect(streamState(502)).toBe("failed");
  });
});

describe("waiting for a library remux", () => {
  // The watch page showed a bare spinner for the ~7 minutes a 3.8 GB remux
  // took, and it was reported as "remux is failing". The remux was fine; the
  // page had nothing to say. These pin what it says now.
  it("shows the percent while the remux runs", () => {
    expect(remuxWait({ state: "working", percent: 43, error: null })).toEqual({
      done: false,
      failed: null,
      label: "Preparing for your browser — 43%",
    });
  });

  it("does not show a stuck 0% before ffmpeg has written anything", () => {
    const w = remuxWait({ state: "working", percent: null, error: null });
    expect(w.label).toBe("Preparing for your browser…");
  });

  it("is done when the stream is ready", () => {
    expect(remuxWait({ state: "ready", percent: null, error: null }).done).toBe(true);
  });

  it("a failure is a failure, not an eternal wait", () => {
    const w = remuxWait({ state: "failed", percent: null, error: "ffmpeg exited 1" });
    expect(w.done).toBe(false);
    expect(w.failed).toContain("ffmpeg");
  });
});

describe("the first seconds after Watch Now", () => {
  // Search's Watch Now used to hold a static "Starting…" through a
  // several-second submit chain, then hard-navigate — it read as stuck. The
  // player page is the real loading state, so navigation happens immediately
  // and the submit races it: for a short grace window, "the downloader does
  // not know this torrent yet" means STARTING, not broken.
  it("an unknown torrent inside the grace window is still starting", () => {
    expect(startupState({ elapsedS: 4, statusError: true })).toBe("starting");
  });

  it("an unknown torrent after the grace window is a real error", () => {
    expect(startupState({ elapsedS: 40, statusError: true })).toBe("failed");
  });

  it("a healthy status ends the startup phase regardless of clock", () => {
    expect(startupState({ elapsedS: 1, statusError: false })).toBe("ready");
  });
});
