"use server";

import { revalidatePath } from "next/cache";
import { Job, getJob } from "@/lib/api";

const API_INTERNAL = process.env.MIRU_API_URL ?? "http://localhost:8000";

/** Scans run through a server action rather than a browser fetch: the API stays
 *  server-to-server, so the browser never needs credentials for JSON. */
export async function startScan(): Promise<{ job: Job } | { error: string }> {
  try {
    const res = await fetch(`${API_INTERNAL}/api/library/scan`, {
      method: "POST",
      cache: "no-store",
    });
    if (!res.ok) return { error: `The API refused the scan (HTTP ${res.status}).` };
    return { job: (await res.json()) as Job };
  } catch {
    return { error: "Can't reach the API. Is it running?" };
  }
}

export async function pollJob(id: number): Promise<{ job: Job } | { error: string }> {
  try {
    return { job: await getJob(id) };
  } catch {
    return { error: "Lost contact with the API while the scan was running." };
  }
}

/** Called once a scan finishes so the library reflects it without a reload. */
export async function refreshLibrary() {
  revalidatePath("/", "layout");
}

/* ── The wall ─────────────────────────────────────────────────────────────
 *
 * Same reason as scans: the API stays server-to-server. The browser holds no
 * token, and the API address it would need is not the one the browser can
 * reach in the two-box deployment.
 */

import type { ActiveDownload, CatalogWork, WorkDetail } from "@/lib/api";

export async function loadRail(
  key: string,
  kind: string,
  cursor: string,
): Promise<{ items: CatalogWork[]; next_cursor: string | null } | { error: string }> {
  try {
    const res = await fetch(
      `${API_INTERNAL}/api/catalog/rail/${key}?kind=${kind}&cursor=${encodeURIComponent(cursor)}`,
      { cache: "no-store" },
    );
    if (!res.ok) return { error: `HTTP ${res.status}` };
    return await res.json();
  } catch {
    return { error: "unreachable" };
  }
}

export async function loadWork(id: number): Promise<{ work: WorkDetail } | { error: string }> {
  try {
    const res = await fetch(`${API_INTERNAL}/api/catalog/works/${id}`, { cache: "no-store" });
    if (!res.ok) return { error: `HTTP ${res.status}` };
    return { work: await res.json() };
  } catch {
    return { error: "Can't reach the API." };
  }
}

export async function startDownload(
  workId: number,
  infoHash: string | null,
  watch: boolean,
): Promise<{ jobId: string } | { error: string }> {
  try {
    const res = await fetch(`${API_INTERNAL}/api/catalog/works/${workId}/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ info_hash: infoHash, watch }),
      cache: "no-store",
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      // The API's own sentence is better than anything invented here: it knows
      // whether the PC is asleep, the swarm is dead, or the indexer refused.
      return { error: body?.detail ?? `The download was refused (HTTP ${res.status}).` };
    }
    return { jobId: body.job_id };
  } catch {
    return { error: "Can't reach the API." };
  }
}

export async function pollDownloads(): Promise<
  { downloads: ActiveDownload[]; pcReachable: boolean } | { error: string }
> {
  try {
    const res = await fetch(`${API_INTERNAL}/api/catalog/downloads`, { cache: "no-store" });
    if (!res.ok) return { error: `HTTP ${res.status}` };
    const body = await res.json();
    return { downloads: body.downloads, pcReachable: body.pc_reachable };
  } catch {
    return { error: "unreachable" };
  }
}

export async function liveStatus(infoHash: string): Promise<
  | {
      name: string | null;
      file: string | null;
      progress: number;
      playable_bytes: number;
      size_bytes: number;
      watchable: boolean;
      complete: boolean;
      found_on_disk: boolean;
      min_bytes: number;
      sequential: boolean;
    }
  | { error: string }
> {
  try {
    const res = await fetch(`${API_INTERNAL}/api/stream/live/${infoHash}/status`, {
      cache: "no-store",
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) return { error: body?.detail ?? `HTTP ${res.status}` };
    return body;
  } catch {
    return { error: "Can't reach the API." };
  }
}

/** Turn a download that is already running into a watchable one.
 *
 *  The reason there is one downloader rather than two: changing your mind costs
 *  an API call instead of losing everything downloaded so far. */
export async function makeWatchable(infoHash: string): Promise<{ ok: true } | { error: string }> {
  try {
    const res = await fetch(`${API_INTERNAL}/api/catalog/downloads/${infoHash}/sequential`, {
      method: "POST",
      cache: "no-store",
    });
    if (!res.ok) return { error: `HTTP ${res.status}` };
    return { ok: true };
  } catch {
    return { error: "Can't reach the API." };
  }
}
