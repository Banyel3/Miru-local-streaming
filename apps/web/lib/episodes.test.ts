import assert from "node:assert/strict";
import test from "node:test";
import { groupReleases, threeChoices } from "./episodes.ts";
import type { CatalogRelease } from "./api";

/**
 * Run with: node --test apps/web/lib/
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
