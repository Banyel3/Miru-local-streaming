"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ActiveDownload, CatalogRail, CatalogWork } from "@/lib/api";
import { loadRail } from "@/app/actions";
import { CatalogCard } from "@/components/CatalogCard";
import { SectionHeading } from "@/components/ui";

/**
 * One row of the wall.
 *
 * Renders as a horizontal rail when it has enough to fill one, and as a grid
 * when it does not. A four-item rail shows all four and most of an empty track,
 * which reads as a rendering failure; a four-item grid reads as deliberate. The
 * API decides which, because it knows the counts.
 *
 * Accessibility: cards are ordinary tab stops inside a list. No roving
 * tabindex — `scroll-snap-align` plus the browser scrolling focus into view is
 * native behaviour that beats a hand-written key handler, and the arrow buttons
 * are aria-hidden because they duplicate what Tab already does.
 */
export function Rail({
  rail,
  kind,
  downloads,
  pcReachable,
  onOpen,
  note,
}: {
  rail: CatalogRail;
  kind: string;
  downloads: Map<number, ActiveDownload>;
  pcReachable: boolean;
  onOpen: (w: CatalogWork) => void;
  note?: string | null;
}) {
  const [items, setItems] = useState(rail.items);
  const [cursor, setCursor] = useState(rail.next_cursor);
  const [loading, setLoading] = useState(false);
  const track = useRef<HTMLUListElement>(null);
  const sentinel = useRef<HTMLLIElement>(null);

  useEffect(() => {
    setItems(rail.items);
    setCursor(rail.next_cursor);
  }, [rail]);

  const loadMore = useCallback(async () => {
    if (!cursor || loading) return;
    setLoading(true);
    try {
      const next = await loadRail(rail.key, kind, cursor);
      if ("error" in next) {
        setCursor(null); // stop asking; the row simply ends here
        return;
      }
      // Guard against a work arriving twice: the catalog grows underneath the
      // cursor, and React keys must stay unique.
      setItems((prev) => {
        const seen = new Set(prev.map((w) => w.id));
        return [...prev, ...next.items.filter((w) => !seen.has(w.id))];
      });
      setCursor(next.next_cursor);
    } catch {
      setCursor(null); // stop asking; the row simply ends
    } finally {
      setLoading(false);
    }
  }, [cursor, loading, rail.key, kind]);

  // The queue: one page is fetched while the previous one is still on screen,
  // so the next arrow never waits on the network.
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !cursor) return;
    const io = new IntersectionObserver(
      (entries) => entries[0]?.isIntersecting && loadMore(),
      { root: track.current, rootMargin: "600px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [cursor, loadMore]);

  if (rail.layout === "empty" || items.length === 0) return null;

  const nudge = (dir: 1 | -1) =>
    track.current?.scrollBy({ left: dir * track.current.clientWidth * 0.8, behavior: "smooth" });

  const cards = items.map((w) => (
    <li key={w.id} className={rail.layout === "rail" ? "w-[150px] shrink-0 sm:w-[170px] xl:w-[190px]" : ""}>
      <CatalogCard
        work={w}
        download={downloads.get(w.id)}
        pcReachable={pcReachable}
        onOpen={onOpen}
        sortedBy={rail.key === "latest" ? "latest" : "trending"}
      />
    </li>
  ));

  return (
    <section className="flex flex-col gap-3.5">
      <SectionHeading title={rail.title} jp={rail.jp}>
        {rail.layout === "rail" && (
          <span className="hidden gap-1.5 [@media(hover:hover)]:flex" aria-hidden>
            {([-1, 1] as const).map((d) => (
              <button
                key={d}
                type="button"
                tabIndex={-1}
                onClick={() => nudge(d)}
                className="grid size-8 place-items-center rounded-lg border border-border text-text-muted transition-colors hover:border-border-hover hover:text-text"
              >
                {d === -1 ? "‹" : "›"}
              </button>
            ))}
          </span>
        )}
      </SectionHeading>

      {note && <p className="-mt-1 max-w-[70ch] text-[12.5px] leading-relaxed text-text-muted">{note}</p>}

      {rail.layout === "rail" ? (
        <ul ref={track} aria-label={rail.title} className="rail bleed bleed-pad flex gap-4 overflow-x-auto pb-2">
          {cards}
          {cursor && <li ref={sentinel} className="w-px shrink-0" aria-hidden />}
        </ul>
      ) : (
        <ul
          aria-label={rail.title}
          className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4 sm:grid-cols-[repeat(auto-fill,minmax(170px,1fr))] xl:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]"
        >
          {cards}
        </ul>
      )}
    </section>
  );
}
