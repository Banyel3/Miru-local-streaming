import { notFound } from "next/navigation";
import Link from "next/link";
import { Player } from "./Player";
import { ApiDown } from "@/components/ApiDown";
import { ButtonLink } from "@/components/ui";
import { ChevronLeft } from "@/components/icons";
import {
  ApiError,
  MediaFile,
  STRATEGY,
  getFile,
  getLibrary,
  mimeType,
  isPlayable,
  nextAfter,
  playbackUrl,
  subtitleTracks,
} from "@/lib/api";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    return { title: `${(await getFile(Number(id))).title} — Miru` };
  } catch {
    return { title: "Miru" };
  }
}

export default async function WatchPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ restart?: string }>;
}) {
  const [{ id }, { restart }] = await Promise.all([params, searchParams]);

  let file: MediaFile;
  let all: MediaFile[] = [];
  try {
    [file, all] = await Promise.all([getFile(Number(id)), getLibrary({ sort: "title" })]);
  } catch (err) {
    if (err instanceof ApiError && err.status === 0) {
      return (
        <main className="grid min-h-dvh place-items-center bg-bg-deep p-8">
          <div className="w-full max-w-lg">
            <ApiDown />
          </div>
        </main>
      );
    }
    notFound();
  }

  const strategy = STRATEGY[file.playback_strategy];
  const next = nextAfter(file, all);

  if (!isPlayable(file)) {
    return (
      <main className="grid min-h-dvh place-items-center bg-bg-deep p-8">
        <div className="flex max-w-lg flex-col items-start gap-4">
          <Link
            href={`/file/${file.id}`}
            className="inline-flex items-center gap-2 text-sm font-semibold text-text-muted transition-colors hover:text-text"
          >
            <ChevronLeft />
            Back
          </Link>
          <h1 className="text-2xl font-extrabold text-balance">{file.title}</h1>
          <p className="flex items-center gap-2 text-sm font-bold text-accent">
            <span className="size-2 rounded-full bg-accent" aria-hidden />
            Not playable right now
          </p>
          <p className="text-sm leading-relaxed text-text-dim">
            {file.availability_note ?? strategy.note} The file is catalogued and its details are on
            the info page.
          </p>
          <ButtonLink href={`/file/${file.id}`} variant="secondary">
            See file details
          </ButtonLink>
        </div>
      </main>
    );
  }

  return (
    <Player
      file={file}
      src={playbackUrl(file)}
      mime={mimeType(file)}
      tracks={subtitleTracks(file)}
      next={next}
      restart={restart === "1"}
    />
  );
}
