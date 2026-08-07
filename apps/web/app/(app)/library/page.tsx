import Link from "next/link";
import { ApiDown } from "@/components/ApiDown";
import { LibraryControls } from "@/components/LibraryControls";
import { WatchState } from "@/components/WatchState";
import { EmptyState, FactChip, SectionHeading } from "@/components/ui";
import {
  MediaFile,
  audioLayout,
  fileSize,
  folderOf,
  getLibrary,
  resolution,
  runtime,
} from "@/lib/api";

export const metadata = { title: "Library — Miru" };

/** Home is the browsing view; Library is the dense one. Same data, different
 *  job: this is where you go to see file facts, not artwork. */
export default async function LibraryPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; sort?: string }>;
}) {
  const { q, sort = "title" } = await searchParams;

  let files: MediaFile[];
  try {
    files = await getLibrary({ q, sort });
  } catch {
    return (
      <div className="flex flex-col gap-8">
        <LibraryControls action="/library" q={q} sort={sort} />
        <ApiDown />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8 lg:gap-9">
      <LibraryControls action="/library" q={q} sort={sort} />

      <SectionHeading title="Library" jp="ライブラリ">
        <span className="text-[12.5px] text-text-muted">
          {files.length} file{files.length === 1 ? "" : "s"}
        </span>
      </SectionHeading>

      {files.length === 0 ? (
        <EmptyState title={q ? `No files match "${q}"` : "Nothing scanned yet"}>
          <p>
            {q
              ? "Search covers filenames and paths."
              : "Run a scan from Settings once MIRU_LIBRARY_PATHS points at your media."}
          </p>
        </EmptyState>
      ) : (
        <ul className="flex flex-col gap-2">
          {files.map((file) => (
            <li key={file.id}>
              <Link
                href={`/file/${file.id}`}
                className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-4 transition-colors duration-150 hover:border-border-hover sm:flex-row sm:items-center sm:gap-5"
              >
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <div className="flex items-center gap-2.5">
                    <WatchState id={file.id} />
                    <h3 className="truncate text-[14.5px] font-bold">{file.title}</h3>
                  </div>
                  <p className="truncate font-mono text-[11px] text-text-muted" title={file.path}>
                    {folderOf(file.path) || "/"}
                  </p>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {runtime(file.duration_ms) && <FactChip>{runtime(file.duration_ms)}</FactChip>}
                  {file.container && <FactChip>{file.container.toUpperCase()}</FactChip>}
                  {resolution(file) && <FactChip tone="bright">{resolution(file)}</FactChip>}
                  {file.video_codec && <FactChip>{file.video_codec}</FactChip>}
                  {audioLayout(file) && <FactChip>{audioLayout(file)}</FactChip>}
                  <FactChip>{fileSize(file.size_bytes)}</FactChip>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
