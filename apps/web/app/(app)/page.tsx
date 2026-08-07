import { ApiDown } from "@/components/ApiDown";
import { LibraryControls } from "@/components/LibraryControls";
import { MediaGrid } from "@/components/MediaCard";
import { ResumeHero } from "@/components/ResumeHero";
import { ButtonLink, EmptyState, FactChip, SectionHeading, artTint } from "@/components/ui";
import { Play } from "@/components/icons";
import { MediaFile, displayTitle, getLibrary, resolution, runtime } from "@/lib/api";

function Hero({ file }: { file: MediaFile }) {
  const { episode, label } = displayTitle(file);
  const facts = [file.container?.toUpperCase(), file.video_codec, runtime(file.duration_ms)].filter(
    Boolean,
  );

  return (
    <section
      className="relative isolate overflow-hidden rounded-3xl border border-border"
      style={{ background: artTint(file.title) }}
    >
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.06]"
        style={{ backgroundImage: "repeating-linear-gradient(115deg, #fff 0 1px, transparent 1px 18px)" }}
        aria-hidden
      />
      {/* Two gradients: one horizontal for the text bed on wide screens, one
          vertical so the copy stays legible when the card goes narrow. */}
      <div className="absolute inset-0 bg-gradient-to-t from-bg via-bg/75 to-bg/20 sm:bg-gradient-to-r sm:from-bg sm:via-bg/80 sm:to-transparent" />
      {/* Height follows content. The design's 380px assumed a synopsis and key
          art; with neither, a fixed height is 200px of void. */}
      <div className="relative flex flex-col justify-end gap-3.5 p-6 sm:max-w-[640px] sm:p-9">
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
          {episode && (
            <p className="mb-1.5 font-mono text-[13px] font-bold text-highlight">{episode}</p>
          )}
          <h1 className="text-[clamp(1.75rem,5vw,2.625rem)] leading-[1.05] font-extrabold tracking-[-0.02em] text-balance">
            {label}
          </h1>
          <p className="mt-2 text-[13px] text-text-muted">
            {facts.length ? facts.join(" · ") : "Not probed yet — install ffmpeg and rescan"}
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
