"use client";

import { useState } from "react";
import { SearchResult, fileSize } from "@/lib/api";
import { startDownloadDirect } from "@/app/actions";
import { Button, EmptyState, MicroChip, SectionHeading } from "@/components/ui";

/** Live results are *releases*, so they render as a dense list rather than as
 *  the wall's poster grid. Making them look like wall cards would imply a
 *  grouping that has not happened. */
export function SearchResults({ query, results }: { query: string; results: SearchResult[] }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  if (results.length === 0) {
    return (
      <EmptyState title={`Nothing matched “${query}”`}>
        <p>
          Every configured indexer answered and none of them had it. Try a shorter
          fragment, or browse instead — the wall keeps what the indexers have already
          dropped off their front pages.
        </p>
      </EmptyState>
    );
  }

  async function grab(r: SearchResult, watch: boolean) {
    setBusy(r.id);
    setError(null);
    const res = await startDownloadDirect(r.id, watch, r.info_hash);
    setBusy(null);
    if ("error" in res) return setError(res.error);
    setDone((prev) => new Set(prev).add(r.id));
    if (watch && res.jobId) window.location.href = `/watching/${res.jobId}`;
  }

  return (
    <section className="flex flex-col gap-4">
      <SectionHeading title="Results" jp="けんさく">
        <span className="text-[12.5px] text-text-muted">{results.length} releases</span>
      </SectionHeading>

      {error && <p className="text-[12.5px] font-bold text-accent">{error}</p>}

      <ul className="flex flex-col gap-3">
        {results.map((r) => (
          <li
            key={r.id}
            className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-4 transition-colors hover:border-border-hover sm:flex-row sm:items-center sm:gap-5"
          >
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <h3 className="text-[13.5px] font-bold break-words">{r.title}</h3>
              <div className="flex flex-wrap items-center gap-1.5">
                <MicroChip tone="bright">{r.indexer}</MicroChip>
                <MicroChip>{fileSize(r.size_bytes)}</MicroChip>
                <MicroChip>{r.seeders.toLocaleString()} seeders</MicroChip>
                {r.age_days ? <MicroChip>{r.age_days}d old</MicroChip> : null}
              </div>
            </div>

            <div className="flex shrink-0 gap-2.5">
              {done.has(r.id) ? (
                <span className="self-center text-[12.5px] font-bold text-text-muted">Started</span>
              ) : (
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy === r.id}
                    onClick={() => grab(r, false)}
                  >
                    Download
                  </Button>
                  <Button size="sm" disabled={busy === r.id} onClick={() => grab(r, true)}>
                    {busy === r.id ? "Starting…" : "Watch Now"}
                  </Button>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
