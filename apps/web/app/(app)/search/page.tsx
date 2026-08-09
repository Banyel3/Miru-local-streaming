import { SearchFilterRow } from "@/components/SearchFilterRow";
import { SearchResults } from "@/components/SearchResults";
import { WallSearch } from "@/components/WallSearch";
import { EmptyState } from "@/components/ui";
import { SearchResult, searchIndexers } from "@/lib/api";

/**
 * Live search, on its own route.
 *
 * Not a mode of the wall, because the two show different things: the wall shows
 * works and this shows releases, and one card cannot honestly be both. It is
 * also slow in a way the wall is not — a live query fans out to every indexer
 * and measured 2.7 to 4.1 seconds — so it gets a screen that can say so.
 */
export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; kind?: string; quality?: string; max_size_gb?: string }>;
}) {
  const { q, kind, quality, max_size_gb } = await searchParams;

  let results: SearchResult[] | null = null;
  let error: string | null = null;

  if (q && q.trim().length >= 2) {
    try {
      results = await searchIndexers(q.trim(), { kind, quality, max_size_gb });
    } catch (e) {
      // Never an empty list: "the indexers are down" and "no matches" look
      // identical to a person, and the search stack this replaced failed
      // exactly that way and stayed broken unnoticed.
      error =
        e instanceof Error && e.message === "unreachable"
          ? "Can't reach the API."
          : "Couldn't reach your indexers. They live on the PC — is it awake?";
    }
  }

  return (
    <div className="flex flex-col gap-7">
      <WallSearch initial={q ?? ""} autoFocus />
      {q && <SearchFilterRow kind={kind} quality={quality} maxSizeGb={max_size_gb} />}

      {!q ? (
        <EmptyState title="Search every indexer at once">
          <p>
            This asks your indexers directly rather than reading the browse
            catalogue, so it finds things the wall has not seen yet. It takes a few
            seconds and needs the PC awake.
          </p>
        </EmptyState>
      ) : error ? (
        <EmptyState title="Couldn't run that search">
          <p>{error}</p>
        </EmptyState>
      ) : (
        <SearchResults query={q} results={results ?? []} />
      )}
    </div>
  );
}
