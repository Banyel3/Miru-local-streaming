export type MediaFile = {
  id: number;
  title: string;
  path: string;
  size_bytes: number;
  duration_ms: number | null;
  container: string | null;
  video_codec: string | null;
  audio_codec: string | null;
  audio_channels: number | null;
  width: number | null;
  height: number | null;
  subtitle_streams: { index: number; codec: string; language: string | null }[];
  playback_strategy: "direct" | "remux" | "transcode_audio" | "transcode_full";
};

export type Job = {
  id: number;
  type: string;
  status: "pending" | "running" | "done" | "failed";
  payload: Record<string, number>;
  attempts: number;
  error: string | null;
};

// ponytail: hand-written until the shapes stop moving. Generated from OpenAPI
// into packages/types at M2 — see packages/types/README.md.

/** Server-to-server. Private, never shipped to the browser, so it can point at
 *  a LAN address the browser could not reach. */
const API_INTERNAL = process.env.MIRU_API_URL ?? "http://localhost:8000";

/** Browser-reachable. Only used to build <video src>, which the browser fetches
 *  itself. These are two different values in the two-box deployment and cannot
 *  share one variable. */
export const API_PUBLIC =
  process.env.NEXT_PUBLIC_MIRU_STREAM_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_INTERNAL}${path}`, { cache: "no-store" });
  } catch {
    // Distinguishing unreachable from 4xx matters: one is "start the API", the
    // other is "this file is gone". Collapsing them into notFound() hides both.
    throw new ApiError(0, "unreachable");
  }
  if (!res.ok) throw new ApiError(res.status, `${path} → ${res.status}`);
  return res.json();
}

export const getLibrary = (params?: { q?: string; sort?: string }) => {
  const qs = new URLSearchParams();
  if (params?.q) qs.set("q", params.q);
  if (params?.sort) qs.set("sort", params.sort);
  const suffix = qs.toString();
  return get<MediaFile[]>(`/api/library${suffix ? `?${suffix}` : ""}`);
};

export const getFile = (id: number) => get<MediaFile>(`/api/files/${id}`);
export const getJob = (id: number) => get<Job>(`/api/jobs/${id}`);
export const getHealth = () => get<{ ok: boolean; libraries: string[] }>("/api/health");

export const streamUrl = (id: number) => `${API_PUBLIC}/api/stream/${id}`;

/* ---------- derived display helpers ---------- */

export const folderOf = (path: string) => path.slice(0, path.lastIndexOf("/"));

/** Siblings in the same directory, in natural order — the closest thing M1 has
 *  to an episode list until the metadata module lands. */
export function siblingsOf(file: MediaFile, all: MediaFile[]): MediaFile[] {
  const dir = folderOf(file.path);
  return all
    .filter((f) => folderOf(f.path) === dir)
    .sort((a, b) => a.title.localeCompare(b.title, undefined, { numeric: true }));
}

export const nextAfter = (file: MediaFile, all: MediaFile[]): MediaFile | null => {
  const sibs = siblingsOf(file, all);
  const i = sibs.findIndex((f) => f.id === file.id);
  return i >= 0 && i < sibs.length - 1 ? sibs[i + 1] : null;
};

export const resolution = (f: MediaFile) =>
  !f.height ? null : f.height >= 2000 ? "4K" : f.height >= 1000 ? "1080p" : `${f.height}p`;

export function runtime(ms: number | null) {
  if (!ms) return null;
  const mins = Math.round(ms / 60000);
  return mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins} min`;
}

/** Clock format for the player and resume labels: 23:41, or 1:04:12. */
export function clock(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

export const fileSize = (bytes: number) => {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return `${Math.round(bytes / 1024 ** 2)} MB`;
};

export const audioLayout = (f: MediaFile) => {
  if (!f.audio_codec) return null;
  const layout = f.audio_channels === 6 ? "5.1" : f.audio_channels === 2 ? "2.0" : null;
  return [f.audio_codec.toUpperCase(), layout].filter(Boolean).join(" ");
};

export const subtitleSummary = (f: MediaFile) => {
  if (!f.subtitle_streams?.length) return null;
  const codecs = [...new Set(f.subtitle_streams.map((s) => s.codec?.toUpperCase()))];
  const langs = [
    ...new Set(f.subtitle_streams.map((s) => s.language?.toUpperCase()).filter(Boolean)),
  ];
  return `subs: ${codecs.join("/")}${langs.length ? ` (${langs.join("/")})` : ""}`;
};

export const STRATEGY: Record<
  MediaFile["playback_strategy"],
  { label: string; playable: boolean; note: string }
> = {
  direct: { label: "Direct Play", playable: true, note: "Served straight from disk." },
  remux: {
    label: "Remux",
    playable: false,
    note: "The video stream is fine but the container is not. Remuxing lands in M3.",
  },
  transcode_audio: {
    label: "Transcoding",
    playable: false,
    note: "Video copies through; the audio track needs re-encoding. Lands in M3.",
  },
  transcode_full: {
    label: "Transcoding",
    playable: false,
    note: "This codec needs a full GPU transcode. Lands in M4.",
  },
};
