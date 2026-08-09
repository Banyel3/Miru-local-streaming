import assert from "node:assert/strict";
import { test } from "vitest";
import { groupReleases, ownedFileFor, threeChoices, visibleBatches } from "./episodes";
import type { CatalogRelease } from "./api";

/**
 * Run with: npm test
 *
 * These name the failures they prevent rather than restating the code: each one
 * is a way the episode list stops being usable, not a way the function stops
 * matching itself.
 */

let n = 0;
const rel = (r: Partial<CatalogRelease>): CatalogRelease => ({
  info_hash: `h${n++}`,
  title: "release",
  indexer: "nyaa",
  quality: "1080p",
  group: null,
  size_bytes: 1_000_000_000,
  seeders: 50,
  season: null,
  episode: null,
  episode_end: null,
  needs_pc: false,
  predicted_strategy: "direct",
  stale: false,
  grabbable: true,
  ...r,
});

test("a batch is one row, not one row per episode it contains", () => {
  const groups = groupReleases([rel({ episode: 741, episode_end: 746 })]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].kind, "batch");
  // Six rows here would be six ways to grab the same 4 GB torrent.
  assert.equal(groups[0].releases.length, 1);
});

test("a batch and a single covering the same number stay separate scopes", () => {
  const groups = groupReleases([
    rel({ episode: 741, episode_end: 746 }),
    rel({ episode: 741, episode_end: 741 }),
    rel({ episode: 741 }),
  ]);
  assert.deepEqual(
    groups.map((g) => g.kind),
    ["batch", "single"],
  );
  // episode_end === episode is a single, not a one-episode batch: grouping them
  // together would put the batch's 4 GB behind a row labelled "Episode 741".
  assert.equal(groups[1].releases.length, 2);
});

test("episodes are newest first, batches above singles", () => {
  const groups = groupReleases([
    rel({ episode: 3 }),
    rel({ episode: 12 }),
    rel({ episode: 1, episode_end: 12 }),
    rel({ episode: 7 }),
  ]);
  assert.deepEqual(
    groups.map((g) => `${g.kind}:${g.from}`),
    ["batch:1", "single:12", "single:7", "single:3"],
  );
});

test("an episode whose every release is ungrabbable is not listed", () => {
  const groups = groupReleases([
    rel({ episode: 2, grabbable: false }),
    rel({ episode: 3, stale: true }),
    rel({ episode: 4 }),
  ]);
  // Listing them is what turns "8 episodes" into 1,164 dead rows.
  assert.deepEqual(
    groups.map((g) => g.from),
    [4],
  );
});

test("releases with no episode number survive as one unsorted group at the end", () => {
  const groups = groupReleases([rel({}), rel({}), rel({ episode: 5 })]);
  assert.deepEqual(
    groups.map((g) => g.kind),
    ["single", "unsorted"],
  );
  // Two groups of one would read as two episodes; dropping them would make the
  // sheet unable to reach releases it counted.
  assert.equal(groups[1].releases.length, 2);
});

test("the picker prefers 1080p over 2160p, and avoids the PC only within a tier", () => {
  const uhd = rel({ quality: "2160p", needs_pc: true });
  const hd = rel({ quality: "1080p", needs_pc: true });
  const sd = rel({ quality: "480p", needs_pc: false });
  const choices = threeChoices([uhd, hd, sd]);
  // Ranking on needs_pc first picked a 480p rip over a 1080p one to spare a GPU
  // that exists precisely to be used — the bug rank.pick_default documents.
  assert.equal(choices.best?.info_hash, hd.info_hash);
  assert.equal(choices.best_quality?.info_hash, uhd.info_hash);
});

test("the picker will not recommend a corpse while anything viable exists", () => {
  const dead = rel({ quality: "1080p", seeders: 1 });
  const alive = rel({ quality: "720p", seeders: 40 });
  assert.equal(threeChoices([dead, alive]).best?.info_hash, alive.info_hash);
  // But an episode where everything is dying still has to offer something,
  // rather than rendering an empty picker under a row that promised releases.
  assert.equal(threeChoices([dead]).best?.info_hash, dead.info_hash);
});

test("an episode with nothing grabbable yields no choices at all", () => {
  const choices = threeChoices([rel({ seeders: 0 }), rel({ grabbable: false })]);
  assert.equal(choices.best, null);
  assert.equal(choices.smallest, null);
});

// ── ownership ────────────────────────────────────────────────────────────────
// The sheet's rows are built from indexer releases and used to offer a
// re-download of an episode already sitting in the library. The API now names
// what is owned; this maps a row to its file.

test("an owned single episode maps to its library file", () => {
  const g = { key: "single:5", kind: "single" as const, from: 5, releases: [rel({ episode: 5 })] };
  assert.equal(ownedFileFor(g, [{ episode: 5, episode_end: null, file_id: 77 }]), 77);
});

test("an unowned episode maps to nothing", () => {
  const g = { key: "single:6", kind: "single" as const, from: 6, releases: [rel({ episode: 6 })] };
  assert.equal(ownedFileFor(g, [{ episode: 5, episode_end: null, file_id: 77 }]), null);
});

test("a batch only matches the batch that was actually downloaded", () => {
  // Owning episode 5 is not owning the 1-12 batch — marking the batch owned
  // would hide the very download that completes the series.
  const g = {
    key: "batch:1-12", kind: "batch" as const, from: 1,
    releases: [rel({ episode: 1, episode_end: 12 })],
  };
  assert.equal(ownedFileFor(g, [{ episode: 5, episode_end: null, file_id: 77 }]), null);
  assert.equal(ownedFileFor(g, [{ episode: 1, episode_end: 12, file_id: 88 }]), 88);
});

test("unsorted rows are never marked owned", () => {
  const g = { key: "unsorted", kind: "unsorted" as const, from: -1, releases: [rel({})] };
  assert.equal(ownedFileFor(g, [{ episode: 5, episode_end: null, file_id: 77 }]), null);
});

// ── batch ordering ───────────────────────────────────────────────────────────
// Naruto Shippuuden's sheet showed 15+ overlapping batch rows sorted by newest
// start — "110-143" on top, the whole-run "1-500" buried mid-list. A batch list
// is a menu of scopes; the widest scope is the answer most openers came for.

test("the widest batch comes first, not the newest-starting one", () => {
  const groups = groupReleases([
    rel({ episode: 110, episode_end: 143 }),
    rel({ episode: 1, episode_end: 500 }),
    rel({ episode: 80, episode_end: 426 }),
  ]);
  assert.deepEqual(
    groups.map((g) => g.key),
    ["batch:1-500", "batch:80-426", "batch:110-143"],
  );
});

test("equal spans fall back to earliest start", () => {
  const groups = groupReleases([
    rel({ episode: 51, episode_end: 100 }),
    rel({ episode: 1, episode_end: 50 }),
  ]);
  assert.deepEqual(groups.map((g) => g.key), ["batch:1-50", "batch:51-100"]);
});

test("singles keep their newest-first order", () => {
  const groups = groupReleases([
    rel({ episode: 5 }),
    rel({ episode: 9 }),
  ]);
  assert.deepEqual(groups.map((g) => g.key), ["single:9", "single:5"]);
});

// ── the visible slice ────────────────────────────────────────────────────────

test("a long batch list is cut and counted, never silently truncated", () => {
  const groups = groupReleases(
    Array.from({ length: 12 }, (_, i) => rel({ episode: i * 10 + 1, episode_end: i * 10 + 5 })),
  );
  const batches = groups.filter((g) => g.kind === "batch");
  const cut = visibleBatches(batches, false);
  assert.equal(cut.shown.length, 6);
  assert.equal(cut.hidden, 6);
});

test("expanded shows everything", () => {
  const groups = groupReleases(
    Array.from({ length: 12 }, (_, i) => rel({ episode: i * 10 + 1, episode_end: i * 10 + 5 })),
  );
  const batches = groups.filter((g) => g.kind === "batch");
  assert.equal(visibleBatches(batches, true).shown.length, 12);
  assert.equal(visibleBatches(batches, true).hidden, 0);
});

test("a short list is never cut", () => {
  const groups = groupReleases([rel({ episode: 1, episode_end: 5 })]);
  const batches = groups.filter((g) => g.kind === "batch");
  assert.equal(visibleBatches(batches, false).hidden, 0);
});
