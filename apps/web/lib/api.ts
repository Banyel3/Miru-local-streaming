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
  /** Derived per request by the API from (strategy, worker reachable). */
  availability: "available" | "gpu-ready" | "unavailable";
  availability_note: string | null;
  /** Absolute worker URL when this file needs transcoding, else null. */
  hls_url: string | null;
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

export type SubtitleTrack = {
  index: number;
  label: string;
  language: string | null;
  styled: boolean;
  vttUrl: string;
  assUrl: string | null;
};

const LANGUAGE_NAMES = new Intl.DisplayNames(["en"], { type: "language" });

function languageLabel(code: string | null): string | null {
  if (!code) return null;
  try {
    return LANGUAGE_NAMES.of(code) ?? code;
  } catch {
    return code;
  }
}

/** Tracks are addressed by position in this list, which is the same order the
 *  API published — embedded stream indices are not contiguous and sidecar
 *  files have none at all. */
export function subtitleTracks(file: MediaFile): SubtitleTrack[] {
  return (file.subtitle_streams ?? []).map((s, i) => {
    const styled = ["ass", "ssa"].includes((s.codec ?? "").toLowerCase());
    const name = languageLabel(s.language) ?? (s as { title?: string }).title ?? `Track ${i + 1}`;
    const base = `${API_PUBLIC}/api/subtitles/${file.id}/${i}`;
    return {
      index: i,
      label: styled ? `${name} (styled)` : name,
      language: s.language,
      styled,
      vttUrl: `${base}?format=vtt`,
      assUrl: styled ? base : null,
    };
  });
}

/**
 * MIME type handed to the player.
 *
 * Load-bearing, not a nicety. Stream URLs are `/api/stream/{id}` with no file
 * extension, so a player given a bare URL cannot infer the type and falls back
 * to probing the response headers with fetch(). That probe is a cross-origin
 * XHR; without CORS it fails, and the player then attaches no provider at all —
 * a blank page rather than a broken video. Declaring the type up front means
 * the probe never runs.
 *
 * Only two values are reachable: the player is handed `direct` files only, and
 * the direct rung admits mp4/m4v/mov/webm. `.mov` is declared as video/mp4
 * because a direct-play .mov is H.264 in an ISO-BMFF container, which is what
 * browsers decode it as anyway.
 */
export type PlayerMime = "video/mp4" | "video/webm" | "application/x-mpegurl";

export function mimeType(f: MediaFile): PlayerMime {
  if (needsWorker(f)) return "application/x-mpegurl";
  const ext = f.path.slice(f.path.lastIndexOf(".") + 1).toLowerCase();
  return ext === "webm" || f.container === "webm" ? "video/webm" : "video/mp4";
}

/** Rungs where an encoder has to run, so playback goes through the PC. */
export const needsWorker = (f: MediaFile) =>
  f.playback_strategy === "transcode_audio" || f.playback_strategy === "transcode_full";

/**
 * The URL the player should load, whichever rung this file is on.
 *
 * For transcoded files this is the worker's own URL, taken straight from the
 * API payload — NOT a redirect through the API. A browser following a
 * cross-origin CORS redirect sends `Origin: null` on the second hop, which the
 * worker cannot match against any allowlist, so the manifest fetch fails no
 * matter how CORS is configured on either side.
 */
export const playbackUrl = (f: MediaFile) =>
  needsWorker(f) && f.hls_url ? f.hls_url : streamUrl(f.id);

/** Playable now? Files needing the PC are only playable while it is reachable. */
export const isPlayable = (f: MediaFile) => f.availability !== "unavailable";

/* ---------- derived display helpers ---------- */

export const folderOf = (path: string) => path.slice(0, path.lastIndexOf("/"));

/** Last two path segments. The absolute path is a debugging fact, not a
 *  heading — showing all of it leaks the server's directory layout into every
 *  screenshot and pushes the useful part off the end of the line. */
export function folderLabel(path: string) {
  return folderOf(path).split("/").filter(Boolean).slice(-2).join(" / ");
}

/**
 * Split a filename into an episode marker and a readable label.
 *
 * Release filenames repeat the series name in every entry, so a grid of them
 * truncates to the same prefix and tells you nothing. This pulls SxxEyy (or a
 * bare " - Ep 4 - ") out front and keeps the distinguishing tail as the title.
 *
 * ponytail: deliberately naive — anitopy does this properly at M2, against the
 * whole zoo of release-group conventions. This handles the common shapes only.
 */
export function displayTitle(file: MediaFile): { episode: string | null; label: string } {
  const raw = file.title;

  const se = raw.match(/\bS(\d{1,2})[\s._-]?E(\d{1,3})\b/i);
  if (se) {
    const tail = raw.slice(se.index! + se[0].length).replace(/^[\s._-]+/, "");
    return {
      episode: `S${se[1].padStart(2, "0")}E${se[2].padStart(2, "0")}`,
      label: tail || raw,
    };
  }

  const ep = raw.match(/\b(?:E|EP|Episode)[\s._-]?(\d{1,3})\b/i);
  if (ep) {
    const tail = raw.slice(ep.index! + ep[0].length).replace(/^[\s._-]+/, "");
    return { episode: `E${ep[1].padStart(2, "0")}`, label: tail || raw };
  }

  return { episode: null, label: raw };
}

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

export const STRATEGY: Record<MediaFile["playback_strategy"], { label: string; note: string }> = {
  direct: { label: "Direct Play", note: "Served straight from disk, no re-encoding." },
  remux: { label: "Remux", note: "Container rewritten; the video stream is copied untouched." },
  transcode_audio: {
    label: "Transcoding",
    note: "Video copies through; only the audio track is re-encoded, on the PC.",
  },
  transcode_full: {
    label: "Transcoding",
    note: "This codec cannot be decoded by the browser, so the PC re-encodes it.",
  },
};
