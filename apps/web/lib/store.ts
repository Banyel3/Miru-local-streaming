"use client";

/**
 * Watch progress and favourites, client-side.
 *
 * The `progress` and `favourites` tables are M3 work. Neither feature needs a
 * server to be useful to one person, so both live in localStorage behind this
 * module. When M3 lands, the four functions below start calling the API and no
 * component changes.
 *
 * The honest limitation: this is per-device. A resume point set on a phone does
 * not appear on the desktop until the server owns it. The sidebar says so.
 */

import { useCallback, useEffect, useState } from "react";

const PROGRESS_KEY = "miru.progress.v1";
const FAV_KEY = "miru.favourites.v1";

/** Below this we assume a misclick, not a watch. Above the completion ratio we
 *  call it finished so it stops cluttering Continue Watching. */
const MIN_RESUME_S = 15;
const COMPLETE_RATIO = 0.94;

export type Progress = {
  positionS: number;
  durationS: number;
  updatedAt: number;
};

type ProgressMap = Record<number, Progress>;

const read = <T,>(key: string, fallback: T): T => {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
};

const write = (key: string, value: unknown) => {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    // Same-tab listeners: the storage event only fires in *other* tabs.
    window.dispatchEvent(new CustomEvent("miru:store", { detail: key }));
  } catch {
    // Private mode or a full quota. Playback still works; it just won't resume.
  }
};

export const isComplete = (p: Progress) =>
  p.durationS > 0 && p.positionS / p.durationS >= COMPLETE_RATIO;

export const percentOf = (p: Progress) =>
  p.durationS > 0 ? Math.min(100, (p.positionS / p.durationS) * 100) : 0;

export function getProgress(id: number): Progress | null {
  return read<ProgressMap>(PROGRESS_KEY, {})[id] ?? null;
}

export function setProgress(id: number, positionS: number, durationS: number) {
  const all = read<ProgressMap>(PROGRESS_KEY, {});
  if (positionS < MIN_RESUME_S) {
    delete all[id];
  } else {
    all[id] = { positionS, durationS, updatedAt: Date.now() };
  }
  write(PROGRESS_KEY, all);
}

export function clearProgress(id: number) {
  const all = read<ProgressMap>(PROGRESS_KEY, {});
  delete all[id];
  write(PROGRESS_KEY, all);
}

export function getFavourites(): number[] {
  return read<number[]>(FAV_KEY, []);
}

export function toggleFavourite(id: number) {
  const favs = getFavourites();
  write(FAV_KEY, favs.includes(id) ? favs.filter((f) => f !== id) : [...favs, id]);
}

/* ---------- hooks ---------- */

/** Subscribes to store writes from this tab and from others. Returns null on
 *  the first render so server and client markup match, then fills in. */
function useStoreValue<T>(compute: () => T, initial: T): T {
  const [value, setValue] = useState<T>(initial);
  const refresh = useCallback(() => setValue(compute()), [compute]);

  useEffect(() => {
    refresh();
    window.addEventListener("miru:store", refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener("miru:store", refresh);
      window.removeEventListener("storage", refresh);
    };
  }, [refresh]);

  return value;
}

export function useProgress(id: number) {
  return useStoreValue<Progress | null>(useCallback(() => getProgress(id), [id]), null);
}

export function useFavourites() {
  return useStoreValue<number[] | null>(useCallback(() => getFavourites(), []), null);
}

/** Continue Watching: in-progress only, most recent first. */
export function useContinueWatching(limit = 3) {
  const all = useStoreValue<[number, Progress][] | null>(
    useCallback(() => Object.entries(read<ProgressMap>(PROGRESS_KEY, {})).map(
      ([id, p]) => [Number(id), p] as [number, Progress],
    ), []),
    null,
  );

  if (all === null) return null;
  return all
    .filter(([, p]) => !isComplete(p))
    .sort((a, b) => b[1].updatedAt - a[1].updatedAt)
    .slice(0, limit);
}
