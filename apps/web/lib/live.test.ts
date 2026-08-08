/**
 * Use case: the file is still downloading while you are watching it.
 *
 * The server serves the completed prefix and nothing past it, so the media
 * element reaches the end of what exists and reports the video as finished.
 * It is not finished — it is 30% of the way through a file that is still
 * arriving. Everything here is about telling those two apart.
 */

import { describe, expect, it } from "vitest";
import { endedForReal, resumeSrc } from "./live";

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
