import { describe, expect, it } from "vitest";
import { groupLibrary } from "./library";
import type { MediaFile } from "./api";

const file = (over: Partial<MediaFile>): MediaFile =>
  ({
    id: 1,
    title: "x",
    path: "/m/x.mkv",
    size_bytes: 1,
    duration_ms: null,
    container: "mkv",
    video_codec: null,
    audio_codec: null,
    audio_channels: null,
    width: null,
    height: null,
    subtitle_streams: [],
    playback_strategy: "direct",
    availability: "available",
    availability_note: null,
    hls_url: null,
    series: null,
    ...over,
  }) as MediaFile;

const ref = (work_id: number, title: string) => ({
  work_id,
  title,
  year: 2024,
  kind: "anime",
  overview: null,
  score: null,
  poster_url: `/api/posters/${work_id}`,
});

describe("groupLibrary", () => {
  it("a bare film lands in films", () => {
    const g = groupLibrary([file({ id: 1, title: "Monay 2026 1080p" })]);
    expect(g.films.map((f) => f.id)).toEqual([1]);
    expect(g.series).toEqual([]);
  });

  it("a film WITH a series link stays a film — a kept movie download is linked too", () => {
    // The link says "the catalogue knows this file", not "this is a show".
    // Scary Movie is linked to its work and must not become a one-episode series.
    const g = groupLibrary([file({ id: 2, title: "Scary Movie 2026", series: ref(9, "Scary Movie") })]);
    expect(g.films.map((f) => f.id)).toEqual([2]);
    expect(g.series).toEqual([]);
  });

  it("an episode marker puts the file in series, grouped by its work", () => {
    const g = groupLibrary([
      file({ id: 3, title: "Boku S02E01", series: ref(5, "Boku") }),
      file({ id: 4, title: "Boku S02E02", series: ref(5, "Boku") }),
    ]);
    expect(g.films).toEqual([]);
    expect(g.series).toHaveLength(1);
    expect(g.series[0].title).toBe("Boku");
    expect(g.series[0].files.map((f) => f.id)).toEqual([4, 3]); // newest first
  });

  it("unlinked episodes group by folder rather than becoming one row each", () => {
    const g = groupLibrary([
      file({ id: 5, title: "Show - 01", path: "/m/Show/Show - 01.mkv" }),
      file({ id: 6, title: "Show - E02", path: "/m/Show/Show - E02.mkv" }),
    ]);
    // id 5 has no episode marker our parser reads — it is a film by the rule.
    // id 6 has E02, so it is a series row keyed on its folder.
    expect(g.films.map((f) => f.id)).toEqual([5]);
    expect(g.series).toHaveLength(1);
    expect(g.series[0].title).toBe("Show");
    expect(g.series[0].files.map((f) => f.id)).toEqual([6]);
  });

  it("empty in, empty out", () => {
    expect(groupLibrary([])).toEqual({ films: [], series: [] });
  });
});
