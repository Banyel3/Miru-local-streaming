"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/** The search field. Submits rather than searching as you type: a live query
 *  fans out to every indexer and takes seconds, so firing one per keystroke
 *  would rate-limit the indexers and return the wrong answer late. */
export function WallSearch({ initial = "", autoFocus }: { initial?: string; autoFocus?: boolean }) {
  const [q, setQ] = useState(initial);
  const router = useRouter();

  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        if (q.trim().length >= 2) router.push(`/search?q=${encodeURIComponent(q.trim())}`);
      }}
      className="flex items-center gap-2.5 rounded-[14px] border border-border bg-surface px-4 py-3 transition-colors focus-within:border-border-hover"
    >
      <span aria-hidden className="text-text-muted">
        ⌕
      </span>
      <label htmlFor="indexer-search" className="sr-only">
        Search every indexer
      </label>
      <input
        id="indexer-search"
        value={q}
        autoFocus={autoFocus}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search every indexer…"
        className="min-w-0 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-text-muted"
      />
      {q && (
        <button
          type="submit"
          className="rounded-lg bg-primary px-3 py-1.5 text-[12px] font-bold text-white hover:bg-primary-hover"
        >
          Search
        </button>
      )}
    </form>
  );
}
