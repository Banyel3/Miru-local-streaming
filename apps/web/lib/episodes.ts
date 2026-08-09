import type { CatalogRelease, WorkDetail } from "./api";

/**
 * Episodes, derived from the releases we actually hold.
 *
 * AniList says One Piece has 1,172 episodes and the catalogue holds 8. The
 * episode list is therefore built from releases rather than from the provider's
 * count: a row nobody can grab is a dead end, and 1,164 of them is worse than
 * the single card this replaced.
 *
 * A batch is not an episode. You cannot pull episode 743 out of a "741-746"
 * torrent, so a batch is its own scope and its own row rather than being
 * exploded into six rows that all grab the same 4 GB.
 *
 * Kept free of runtime imports so `node --test` can run it directly — lib/api
 * has a parameter property in ApiError, which node's type stripping rejects.
 */

export type Group = {
  key: string;
  kind: "batch" | "single" | "unsorted";
  /** First episode in the group, and the sort key. -1 when unnumbered. */
  from: number;
  releases: CatalogRelease[];
};

/** What the picker is willing to offer. A stale row is refused on click, so
 *  listing an episode whose every release is stale promises a dead end. */
export const grabbable = (r: CatalogRelease) => r.grabbable && !r.stale;

export const SECTION: Record<Group["kind"], string> = {
  batch: "Batches",
  single: "Episodes",
  unsorted: "Unsorted releases",
};

export function groupReleases(releases: CatalogRelease[]): Group[] {
  const by = new Map<string, Group>();
  for (const r of releases) {
    const batch = r.episode != null && r.episode_end != null && r.episode_end !== r.episode;
    const kind = r.episode == null ? "unsorted" : batch ? "batch" : "single";
    // A single is keyed on its number alone: "E741 end 741" and "E741 end null"
    // are the same episode, and keying on both split it into two identical rows.
    // Unnumbered releases are one group, not one group each — without that they
    // vanish and the sheet stops reaching releases it counted.
    const key =
      kind === "unsorted"
        ? "unsorted"
        : kind === "batch"
          ? `batch:${r.episode}-${r.episode_end}`
          : `single:${r.episode}`;
    let g = by.get(key);
    if (!g) {
      g = { key, kind, from: r.episode ?? -1, releases: [] };
      by.set(key, g);
    }
    g.releases.push(r);
  }

  // Batches first — a batch covers episodes that also appear below it, and
  // burying it under the singles it contains is how you download the same six
  // episodes twice. Newest episode first within each group.
  const RANK = { batch: 0, single: 1, unsorted: 2 };
  return [...by.values()]
    .filter((g) => g.releases.some(grabbable))
    .sort((a, b) => RANK[a.kind] - RANK[b.kind] || b.from - a.from);
}

// Mirrors rank.QUALITY_PREFERENCE and rank._DESC.
const PREFERRED = ["1080p", "720p", "2160p", "576p", "480p", "360p"];
const DESCENDING = ["2160p", "1080p", "720p", "576p", "480p", "360p"];

const rankIn = (order: string[], q: string | null) => {
  const i = order.indexOf(q ?? "");
  return i < 0 ? order.length : i;
};

/** Lowest by a tuple of keys, compared left to right — Python's `min(key=…)`. */
function least(list: CatalogRelease[], key: (r: CatalogRelease) => number[]): CatalogRelease {
  return list.reduce((a, b) => {
    const [x, y] = [key(a), key(b)];
    for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i] ? a : b;
    return a;
  });
}

/**
 * The three named choices for one episode's releases.
 *
 * ponytail: a client-side mirror of rank.three_choices, minus the per-indexer
 * seeder percentile — the API already returned `releases` sorted by it, so a
 * release's position in that array stands in for its standing. Move this behind
 * an `?episode=` parameter on the work endpoint if the two ever disagree.
 */
export function threeChoices(releases: CatalogRelease[]): WorkDetail["choices"] {
  const pool = releases.filter((r) => grabbable(r) && r.seeders > 0);
  if (!pool.length) return { best: null, smallest: null, best_quality: null };

  const at = new Map(releases.map((r, i) => [r.info_hash, i]));
  // Below five seeders a torrent is a coin flip, so anything viable outranks a
  // better-looking corpse — but a pool of nothing but corpses still has to
  // offer someone rather than showing an empty picker.
  const healthy = pool.filter((r) => r.seeders >= 5);
  const ranked = healthy.length ? healthy : pool;

  return {
    best: least(ranked, (r) => [
      rankIn(PREFERRED, r.quality),
      Number(r.needs_pc),
      at.get(r.info_hash)!,
    ]),
    smallest: least(pool, (r) => [r.size_bytes]),
    best_quality: least(pool, (r) => [rankIn(DESCENDING, r.quality), -r.seeders]),
  };
}

/** An episode already in the library, as the work payload reports it. */
export type OwnedEpisode = { episode: number; episode_end: number | null; file_id: number };

/**
 * The library file behind a row, or null.
 *
 * Exact scope match on purpose: owning episode 5 is not owning the 1-12 batch
 * — marking the batch owned would hide the very download that completes the
 * series, and a wrong "you have this" is worse than a redundant offer.
 */
export function ownedFileFor(group: Group, owned: OwnedEpisode[]): number | null {
  if (group.kind === "unsorted") return null;
  const r = group.releases[0];
  const hit = owned.find(
    (o) => o.episode === r.episode && (o.episode_end ?? null) === (r.episode_end ?? null),
  );
  return hit?.file_id ?? null;
}
