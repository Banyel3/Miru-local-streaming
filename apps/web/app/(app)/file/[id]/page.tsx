import Link from "next/link";
import { notFound } from "next/navigation";
import { ApiDown } from "@/components/ApiDown";
import { DetailActions } from "@/components/DetailActions";
import { WatchState } from "@/components/WatchState";
import { ArtTile, ButtonLink, FactChip, artTint } from "@/components/ui";
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
  siblingsOf,
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

  const siblings = siblingsOf(file, all);
  const hasSiblings = siblings.length > 1;
  const { episode, label } = displayTitle(file);

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
              seed={file.title}
              episode={episode}
              label={label}
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

      {hasSiblings && (
        <section className="flex flex-col gap-4">
          <div className="flex items-baseline gap-2.5">
            <h2 className="text-xl font-extrabold tracking-[-0.01em]">In this folder</h2>
            <span className="font-jp text-xs text-text-muted">エピソード</span>
            <span className="ml-auto text-[12.5px] text-text-muted">{siblings.length} files</span>
          </div>

          <ul className="rail bleed bleed-pad flex gap-4 overflow-x-auto pb-3">
            {siblings.map((sib) => (
              <li key={sib.id} className="w-[236px] shrink-0">
                <Link
                  href={`/file/${sib.id}`}
                  aria-current={sib.id === file.id ? "page" : undefined}
                  className={`flex h-full flex-col gap-2.5 rounded-[15px] border bg-surface p-2.5 transition-colors duration-150 ${
                    sib.id === file.id
                      ? "border-primary"
                      : "border-border hover:border-border-hover"
                  }`}
                >
                  <ArtTile
                    seed={sib.title}
                    episode={displayTitle(sib).episode}
                    label={displayTitle(sib).label}
                    compact
                    className="aspect-video rounded-[10px]"
                  />
                  <div className="flex flex-col gap-1.5 px-1 pb-1">
                    <div className="flex items-center gap-2">
                      <WatchState id={sib.id} />
                      <h3 className="truncate text-[13px] font-bold" title={sib.title}>
                        {displayTitle(sib).label}
                      </h3>
                    </div>
                    <p className="text-[11px] text-text-muted">
                      {[runtime(sib.duration_ms), resolution(sib)].filter(Boolean).join(" · ") ||
                        "Not probed"}
                    </p>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </section>
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
