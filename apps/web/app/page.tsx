import Link from "next/link";
import { Sidebar } from "@/components/Sidebar";
import { Chip, MediaCard, artTint } from "@/components/MediaCard";
import { MediaFile, duration, getLibrary, resolution } from "@/lib/api";

function Hero({ file }: { file: MediaFile }) {
  return (
    <div
      className="relative h-[380px] overflow-hidden rounded-3xl border border-border"
      style={{ background: artTint(file.title) }}
    >
      <div className="absolute inset-0 bg-gradient-to-r from-bg/95 via-bg/60 to-transparent" />
      <div className="absolute inset-0 flex max-w-[640px] flex-col justify-end gap-3.5 p-9">
        <div className="flex items-center gap-2.5">
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
          <h1 className="text-[42px] leading-[1.05] font-extrabold tracking-[-0.02em]">
            {file.title}
          </h1>
          <p className="font-jp mt-1.5 text-[13px] text-text-muted">
            {[file.container?.toUpperCase(), file.video_codec, duration(file.duration_ms)]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <div className="mt-1 flex gap-3">
          <Link
            href={`/watch/${file.id}`}
            className="flex items-center gap-2.5 rounded-[13px] bg-primary px-6 py-3.5 text-sm font-bold text-white transition-colors hover:bg-primary-hover"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
            Watch Now
          </Link>
        </div>
      </div>
    </div>
  );
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; sort?: string }>;
}) {
  const { q } = await searchParams;

  let files: MediaFile[] = [];
  let error: string | null = null;
  try {
    files = await getLibrary(q);
  } catch {
    error = "Can't reach the API. Is it running on :8000?";
  }

  return (
    <div className="min-h-screen p-14">
      <div className="mx-auto flex max-w-[1440px] gap-8 rounded-3xl border border-border p-7">
        <Sidebar />

        <main className="flex min-w-0 flex-1 flex-col gap-9">
          {/* Plain GET form: search is a server render, no client JS. */}
          <form action="/" className="flex items-center gap-3.5">
            <div className="flex flex-1 items-center gap-3 rounded-[14px] border border-border bg-surface px-4.5 py-3 focus-within:border-border-hover">
              <svg className="h-[17px] w-[17px] flex-none" viewBox="0 0 24 24" fill="none" stroke="#918A9E" strokeWidth={2} strokeLinecap="round">
                <circle cx="11" cy="11" r="7" />
                <path d="M16.5 16.5 21 21" />
              </svg>
              <input
                name="q"
                defaultValue={q ?? ""}
                placeholder="Search your library — タイトル・ジャンル…"
                className="w-full bg-transparent text-[13.5px] outline-none placeholder:text-text-muted"
              />
            </div>
            <select
              name="sort"
              className="cursor-pointer rounded-[14px] border border-border bg-surface px-4.5 py-3 text-[13.5px] font-semibold text-highlight hover:border-border-hover"
            >
              <option value="title">A–Z</option>
              <option value="added">Recently added</option>
            </select>
          </form>

          {error && (
            <div className="rounded-2xl border border-border bg-surface p-6 text-sm text-text-dim">
              {error}
            </div>
          )}

          {files[0] && <Hero file={files[0]} />}

          <section className="flex flex-col gap-4.5">
            <div className="flex items-baseline gap-2.5">
              <h2 className="text-xl font-extrabold tracking-[-0.01em]">
                {q ? "Results" : "Your library"}
              </h2>
              <span className="font-jp text-xs text-text-muted">
                {q ? "けんさく" : "ライブラリ"}
              </span>
              <span className="ml-auto text-[12.5px] text-text-muted">
                {files.length} file{files.length === 1 ? "" : "s"}
              </span>
            </div>

            {files.length === 0 && !error ? (
              <div className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-8">
                <p className="text-sm text-text-dim">
                  Nothing scanned yet. Point <Chip muted>MIRU_LIBRARY_PATHS</Chip> at a directory,
                  then run a scan:
                </p>
                <code className="rounded-lg border border-border bg-bg px-4 py-3 font-mono text-xs text-highlight">
                  curl -X POST localhost:8000/api/library/scan
                </code>
              </div>
            ) : (
              <div className="grid grid-cols-6 gap-4.5">
                {files.map((f) => (
                  <MediaCard key={f.id} file={f} />
                ))}
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
