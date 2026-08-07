import Link from "next/link";
import { MediaFile, duration, resolution } from "@/lib/api";

/** Until posters exist (M2), the tile is a deterministic tint of the palette
 *  so the grid reads as a grid instead of six identical grey rectangles. */
export function artTint(seed: string) {
  const hue = [...seed].reduce((n, c) => n + c.charCodeAt(0), 0) % 60;
  return `linear-gradient(150deg, hsl(${260 + hue - 30} 16% 16%), hsl(${
    260 + hue - 30
  } 14% 11%))`;
}

export function Chip({ children, muted }: { children: React.ReactNode; muted?: boolean }) {
  return (
    <span
      className={`rounded-[5px] bg-border px-1.5 py-0.5 font-mono text-[9.5px] font-semibold ${
        muted ? "text-text-muted" : "text-highlight"
      }`}
    >
      {children}
    </span>
  );
}

export function MediaCard({ file }: { file: MediaFile }) {
  const playable = file.playback_strategy === "direct";

  return (
    <Link
      href={`/watch/${file.id}`}
      className="group flex flex-col gap-2.5 rounded-2xl border border-border bg-surface p-2.5 transition-[transform,border-color] duration-200 hover:-translate-y-1 hover:border-border-hover"
    >
      <div
        className="relative aspect-2/3 overflow-hidden rounded-[11px]"
        style={{ background: artTint(file.title) }}
      >
        <div className="absolute inset-0 flex items-end p-3">
          <span className="font-jp line-clamp-3 text-[11px] leading-snug text-text-muted">
            {file.title}
          </span>
        </div>
        {!playable && (
          <span className="absolute top-2 right-2 rounded-md border border-border-hover bg-bg/80 px-1.5 py-0.5 font-mono text-[9px] text-text-muted">
            {file.playback_strategy}
          </span>
        )}
      </div>
      <div className="flex flex-col gap-1.5 px-1 pb-1">
        <div className="truncate text-[13.5px] font-bold">{file.title}</div>
        <div className="flex items-center gap-1.5">
          {duration(file.duration_ms) && (
            <span className="mr-1 text-[11.5px] text-text-muted">{duration(file.duration_ms)}</span>
          )}
          {resolution(file) && <Chip>{resolution(file)}</Chip>}
          {file.video_codec && <Chip muted>{file.video_codec}</Chip>}
        </div>
      </div>
    </Link>
  );
}
