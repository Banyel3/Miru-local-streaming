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

// ponytail: hand-written until the shapes stop moving. Generated from OpenAPI
// into packages/types at M2 — see packages/types/README.md.

/** Browser-reachable, because <video src> is fetched by the browser. */
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

export const getLibrary = (q?: string) =>
  get<MediaFile[]>(`/api/library${q ? `?q=${encodeURIComponent(q)}` : ""}`);

export const getFile = (id: number) => get<MediaFile>(`/api/files/${id}`);

export const streamUrl = (id: number) => `${API}/api/stream/${id}`;

export const resolution = (f: MediaFile) =>
  !f.height ? null : f.height >= 2000 ? "4K" : f.height >= 1000 ? "1080p" : `${f.height}p`;

export const duration = (ms: number | null) => {
  if (!ms) return null;
  const mins = Math.round(ms / 60000);
  return mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins} min`;
};

export const STRATEGY_LABEL: Record<MediaFile["playback_strategy"], string> = {
  direct: "Direct Play",
  remux: "Remux",
  transcode_audio: "Transcoding",
  transcode_full: "Transcoding",
};
