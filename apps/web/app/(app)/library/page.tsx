import { ApiDown } from "@/components/ApiDown";
import { LibraryControls } from "@/components/LibraryControls";
import { LibraryList } from "@/components/LibraryList";
import { EmptyState, SectionHeading } from "@/components/ui";
import { MediaFile, getLibrary } from "@/lib/api";

export const metadata = { title: "Library — Miru" };

/** Home is the browsing view; Library is the shelf you own: what you have,
 *  how far you are through it, and what to do with each file. Rows are
 *  poster-led with parsed titles — the raw filename lives on the file page. */
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
        <LibraryList files={files} />
      )}
    </div>
  );
}
