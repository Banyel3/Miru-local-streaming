import Link from "next/link";
import { notFound } from "next/navigation";
import { Player } from "./Player";
import { MediaFile, STRATEGY_LABEL, getFile, streamUrl } from "@/lib/api";

function StrategyChip({ file }: { file: MediaFile }) {
  const direct = file.playback_strategy === "direct";
  return (
    <div
      className="flex items-center gap-2 rounded-full border bg-surface/60 px-3.5 py-1.5 font-mono text-[11.5px] font-semibold backdrop-blur-md"
      style={{
        color: direct ? "#B4A5D0" : "#D9A441",
        borderColor: direct ? "#2A2534" : "#453D55",
      }}
    >
      <span
        className="h-[7px] w-[7px] rounded-full"
        style={{ background: direct ? "#B4A5D0" : "#D9A441" }}
      />
      {STRATEGY_LABEL[file.playback_strategy]}
    </div>
  );
}

export default async function Watch({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let file: MediaFile;
  try {
    file = await getFile(Number(id));
  } catch {
    notFound();
  }

  const direct = file.playback_strategy === "direct";

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-[#100E14]">
      <div className="absolute inset-0">
        {direct ? (
          <Player src={streamUrl(file.id)} title={file.title} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
            <p className="text-lg font-bold">This file needs {STRATEGY_LABEL[file.playback_strategy].toLowerCase()}</p>
            <p className="max-w-md text-sm text-text-dim">
              {file.video_codec}
              {file.container ? ` in ${file.container.toUpperCase()}` : ""} — resolved as{" "}
              <span className="font-mono text-highlight">{file.playback_strategy}</span>. M1 serves
              direct play only; the remux and transcode rungs land in M3 and M4.
            </p>
          </div>
        )}
      </div>

      {/* Top chrome sits above the player and stays out of its pointer path. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between p-7">
        <div className="pointer-events-auto flex items-center gap-3.5">
          <Link
            href="/"
            className="flex h-[38px] w-[38px] items-center justify-center rounded-xl border border-border bg-surface/60 backdrop-blur-md hover:border-border-hover"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="#EDEAF2" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 5l-7 7 7 7" />
            </svg>
          </Link>
          <div className="flex flex-col gap-0.5">
            <span className="text-[15px] font-extrabold">{file.title}</span>
            <span className="text-xs text-text-muted">
              {[file.container?.toUpperCase(), file.video_codec, file.audio_codec]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </div>
        </div>
        <StrategyChip file={file} />
      </div>
    </div>
  );
}
