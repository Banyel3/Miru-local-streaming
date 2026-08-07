import { ApiDown } from "@/components/ApiDown";
import { LibraryControls } from "@/components/LibraryControls";
import { MediaGrid } from "@/components/MediaCard";
import { ResumeHero } from "@/components/ResumeHero";
import { ButtonLink, EmptyState, FactChip, SectionHeading, artTint } from "@/components/ui";
import { Play } from "@/components/icons";
import { MediaFile, getLibrary, resolution, runtime, subtitleSummary } from "@/lib/api";

function Hero({ file }: { file: MediaFile }) {
  return (
    <section
      className="relative isolate overflow-hidden rounded-3xl border border-border"
      style={{ background: artTint(file.title) }}
    >
      {/* Two gradients: one horizontal for the text bed on wide screens, one
          vertical so the copy stays legible when the card goes narrow. */}
      <div className="absolute inset-0 bg-gradient-to-t from-bg via-bg/70 to-transparent sm:bg-gradient-to-r sm:from-bg/95 sm:via-bg/65 sm:to-transparent" />
      <div className="relative flex min-h-[300px] flex-col justify-end gap-3.5 p-6 sm:min-h-[380px] sm:max-w-[640px] sm:p-9">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-border-hover bg-bg/70 px-3 py-1.5 text-[11.5px] font-bold backdrop-blur-sm">
            Recently added
          </span>
          {resolution(file) && (
            <span className="rounded-full border border-border-hover px-3 py-1.5 text-[11.5px] text-highlight">
              {resolution(file)}
            </span>
          )}
        </div>

        <div>
          <h1 className="text-[clamp(1.75rem,5vw,2.625rem)] leading-[1.05] font-extrabold tracking-[-0.02em] text-balance">
            {file.title}
          </h1>
          <p className="mt-1.5 font-jp text-[13px] text-text-muted">
            {[file.container?.toUpperCase(), file.video_codec, runtime(file.duration_ms)]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>

        <div className="mt-1 flex flex-wrap gap-3">
          <ResumeHero file={file} />
          <ButtonLink href={`/file/${file.id}`} variant="secondary" size="lg">
            More Info
          </ButtonLink>
        </div>
      </div>
    </section>
  );
}

export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; sort?: string }>;
}) {
  const { q, sort = "added" } = await searchParams;

  let files: MediaFile[];
  try {
    files = await getLibrary({ q, sort });
  } catch {
    return (
      <div className="flex flex-col gap-8">
        <LibraryControls q={q} sort={sort} />
        <ApiDown />
      </div>
    );
  }

  const searching = Boolean(q);

  return (
    <div className="flex flex-col gap-8 lg:gap-9">
      <LibraryControls q={q} sort={sort} />

      {files.length === 0 ? (
        searching ? (
          <EmptyState title={`No files match "${q}"`}>
            <p>
              Search covers filenames and paths. Nothing in the library matched. Try a shorter
              fragment, or clear the search to see everything.
            </p>
          </EmptyState>
        ) : (
          <EmptyState
            title="Your library is empty"
            action={
              <ButtonLink href="/settings" size="md">
                Go to Settings
              </ButtonLink>
            }
          >
            <p>
              Point <FactChip>MIRU_LIBRARY_PATHS</FactChip> at a folder of video files, then run a
              scan. Miru probes each file once and remembers what it found, so scans after the first
              one are quick.
            </p>
          </EmptyState>
        )
      ) : (
        <>
          {!searching && files[0] && <Hero file={files[0]} />}

          <section className="flex flex-col gap-4.5">
            <SectionHeading
              title={searching ? "Results" : "Your library"}
              jp={searching ? "けんさく" : "ライブラリ"}
            >
              <span className="text-[12.5px] text-text-muted">
                {files.length} file{files.length === 1 ? "" : "s"}
              </span>
            </SectionHeading>
            <MediaGrid files={files} />
          </section>
        </>
      )}
    </div>
  );
}
