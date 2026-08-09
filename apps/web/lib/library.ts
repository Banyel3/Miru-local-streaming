import { MediaFile, displayTitle, folderOf } from "./api";

/** One show's shelf row: its files, newest first. */
export type SeriesGroup = {
  key: string;
  title: string;
  poster_url: string | null;
  files: MediaFile[];
};

/**
 * Films vs series, decided by the filename — not by the catalogue link.
 *
 * A kept movie download is linked to a catalog work exactly like an episode
 * is, so "has a series link" cannot mean "is a series": it made Scary Movie a
 * one-episode show. The episode marker is the thing only a series file has.
 * Linked episodes group under their work; unlinked ones group by folder so a
 * hand-dropped season is still one row, not thirteen.
 */
export function groupLibrary(files: MediaFile[]): { films: MediaFile[]; series: SeriesGroup[] } {
  const films: MediaFile[] = [];
  const groups = new Map<string, SeriesGroup>();

  for (const f of files) {
    if (displayTitle(f).episode === null) {
      films.push(f);
      continue;
    }
    const key = f.series ? `work:${f.series.work_id}` : `dir:${folderOf(f.path)}`;
    let g = groups.get(key);
    if (!g) {
      g = {
        key,
        title: f.series?.title ?? folderOf(f.path).split("/").filter(Boolean).at(-1) ?? f.title,
        poster_url: f.series?.poster_url ?? null,
        files: [],
      };
      groups.set(key, g);
    }
    g.files.push(f);
  }

  for (const g of groups.values()) g.files.sort((a, b) => b.id - a.id);
  return { films, series: [...groups.values()] };
}
