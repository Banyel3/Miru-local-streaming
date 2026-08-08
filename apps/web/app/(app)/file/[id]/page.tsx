import Link from "next/link";
import { notFound } from "next/navigation";
import { ApiDown } from "@/components/ApiDown";
import { DetailActions } from "@/components/DetailActions";
import { WatchState } from "@/components/WatchState";
import { ArtTile, ButtonLink, FactChip, artTint } from "@/components/ui";
import { EpisodeList } from "@/components/EpisodeList";
import { ChevronLeft } from "@/components/icons";
import {
  ApiError,
  MediaFile,
  audioLayout,
  displayTitle,
  fileSize,
  folderLabel,
  getFile,
  getLibrary,
  resolution,
  runtime,
  subtitleSummary,
} from "@/lib/api";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    const file = await getFile(Number(id));
    return { title: `${file.title} — Miru` };
  } catch {
    return { title: "Miru" };
  }
}

export default async function FileDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let file: MediaFile;
  let all: MediaFile[] = [];
  try {
    [file, all] = await Promise.all([getFile(Number(id)), getLibrary({ sort: "title" })]);
  } catch (err) {
    // Unreachable and 404 are different problems with different fixes.
    if (err instanceof ApiError && err.status === 0) return <ApiDown />;
    notFound();
  }
  // The catalogue's title is the name of the thing; the file's is a release
  // name full of group tags and CRCs. Prefer the former when we have it.
  const { episode, label } = displayTitle(file);
  const heading = file.series?.title ?? label;

  return (
    <article className="flex flex-col gap-10">
      <div className="bleed relative isolate -mt-6 overflow-hidden lg:-mt-9">
        <div
          className="absolute inset-0 h-[280px] sm:h-[380px]"
          style={{ background: artTint(file.title) }}
          aria-hidden
        />
        <div
          className="absolute inset-0 h-[280px] bg-gradient-to-t from-bg via-bg/75 to-bg/25 sm:h-[380px] sm:bg-gradient-to-r sm:from-bg sm:via-bg/85 sm:to-transparent"
          aria-hidden
        />
        <div
          className="absolute inset-x-0 top-[180px] h-[100px] bg-gradient-to-b from-transparent to-bg sm:top-[240px] sm:h-[140px]"
          aria-hidden
        />

        <div className="bleed-pad relative flex flex-col gap-6 pt-6 pb-2">
          <Link
            href="/"
            className="inline-flex w-fit items-center gap-3 text-[13px] font-semibold text-text-muted transition-colors hover:text-text"
          >
            <span className="grid size-9 place-items-center rounded-xl border border-border bg-surface/70 backdrop-blur-sm">
              <ChevronLeft />
            </span>
            Back to library
          </Link>

          <div className="flex flex-col gap-6 sm:flex-row sm:items-end sm:gap-10">
            <ArtTile
              seed={file.series?.title ?? file.title}
              episode={episode}
              label={heading}
              className="hidden aspect-2/3 w-[180px] shrink-0 rounded-[20px] border border-border sm:flex lg:w-[240px]"
            />

            <div className="flex min-w-0 flex-1 flex-col gap-4 pb-2">
              <div>
                {episode && (
                  <p className="mb-1.5 font-mono text-[13px] font-bold text-highlight">{episode}</p>
                )}
                <h1 className="text-[clamp(1.75rem,4.5vw,2.875rem)] leading-[1.05] font-extrabold tracking-[-0.02em] text-balance">
                  {label}
                </h1>
                {/* Folder, not the absolute path: the full path is a debugging
                    fact, and it pushes the useful tail off the end of the line. */}
                <p className="mt-2 truncate font-mono text-xs text-text-muted" title={file.path}>
                  {folderLabel(file.path) || "/"}
                </p>
              </div>

              {/* Separators are joined, not interleaved — emitting one per
                  optional field leaves an orphan bullet when the field is null,
                  which is every field until ffprobe has run. */}
              <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2 text-[13px] font-semibold text-text-muted">
                {[
                  runtime(file.duration_ms),
                  resolution(file),
                  file.width && file.height ? `${file.width}×${file.height}` : null,
                  fileSize(file.size_bytes),
                ]
                  .filter(Boolean)
                  .map((part, i) => (
                    <span key={part as string} className="flex items-center gap-3.5">
                      {i > 0 && (
                        <span className="text-border-hover" aria-hidden>
                          •
                        </span>
                      )}
                      {part}
                    </span>
                  ))}
              </div>

              <DetailActions file={file} />
            </div>
          </div>
        </div>
      </div>

      {/* Episodes of this show, owned and available together. This replaces
          "In this folder", which grouped by directory — and since every
          download lands flat in one media folder, "the folder" was the whole
          library: four unrelated shows, a film and a test clip presented as if
          they belonged together. */}
      {file.series && file.episodes && file.episodes.length > 0 && (
        <EpisodeList series={file.series} episodes={file.episodes} currentFileId={file.id} />
      )}

      <section className="flex flex-col gap-3 pb-4">
        <h2 className="text-[11px] font-extrabold tracking-[0.14em] text-text-muted">FILE</h2>
        <div className="flex flex-wrap items-center gap-2">
          {file.container && <FactChip>{file.container.toUpperCase()}</FactChip>}
          {file.video_codec && <FactChip tone="bright">{file.video_codec.toUpperCase()}</FactChip>}
          {resolution(file) && <FactChip>{resolution(file)}</FactChip>}
          {audioLayout(file) && <FactChip>{audioLayout(file)}</FactChip>}
          {subtitleSummary(file) && <FactChip>{subtitleSummary(file)}</FactChip>}
          <FactChip>{fileSize(file.size_bytes)}</FactChip>
          {!file.video_codec && (
            <span className="text-xs text-text-muted">
              Not probed — ffprobe wasn&apos;t available when this file was scanned.
            </span>
          )}
        </div>
      </section>
    </article>
  );
}
