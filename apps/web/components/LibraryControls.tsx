"use client";

import { Search } from "@/components/icons";

/** Plain GET form: search works with JavaScript disabled (press Enter), and the
 *  select submits on change when JS is available. Progressive, not clever. */
export function LibraryControls({
  action = "/",
  q = "",
  sort = "title",
}: {
  action?: string;
  q?: string;
  sort?: string;
}) {
  return (
    <form action={action} role="search" className="flex flex-col gap-3 sm:flex-row sm:items-center">
      <div className="flex flex-1 items-center gap-3 rounded-[14px] border border-border bg-surface px-4 py-3 transition-colors focus-within:border-border-hover">
        <Search className="size-[17px] shrink-0 text-text-muted" />
        <input
          type="search"
          name="q"
          defaultValue={q}
          placeholder="Search your library — タイトル・ファイル名…"
          aria-label="Search your library"
          className="w-full bg-transparent text-[13.5px] text-text outline-none placeholder:text-text-muted"
        />
      </div>

      <label className="flex items-center gap-2 rounded-[14px] border border-border bg-surface px-4 py-3 transition-colors hover:border-border-hover">
        <span className="text-[11px] font-bold tracking-wider text-text-muted uppercase">Sort</span>
        <select
          name="sort"
          defaultValue={sort}
          onChange={(e) => e.currentTarget.form?.requestSubmit()}
          className="cursor-pointer bg-transparent text-[13.5px] font-semibold text-highlight outline-none"
        >
          <option value="title">A–Z</option>
          <option value="added">Recently added</option>
        </select>
      </label>

      <noscript>
        <button
          type="submit"
          className="rounded-[14px] border border-border bg-surface px-4 py-3 text-[13.5px] font-semibold text-highlight"
        >
          Apply
        </button>
      </noscript>
    </form>
  );
}
