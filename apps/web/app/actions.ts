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
