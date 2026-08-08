import { ScanPanel } from "@/components/ScanPanel";
import { FactChip, SectionHeading } from "@/components/ui";
import { API_PUBLIC, getHealth } from "@/lib/api";

export const metadata = { title: "Settings — Miru" };

export default async function SettingsPage() {
  let health: { ok: boolean; libraries: string[] } | null = null;
  try {
    health = await getHealth();
  } catch {
    health = null;
  }

  return (
    <div className="flex max-w-[75ch] flex-col gap-8">
      <SectionHeading title="Settings" jp="せってい" />

      <section className="flex flex-col gap-4 rounded-2xl border border-border bg-surface p-6">
        <h3 className="text-[15px] font-bold">Library</h3>

        <dl className="flex flex-col gap-3 text-sm">
          <div className="flex flex-wrap items-center gap-3">
            <dt className="w-28 shrink-0 text-text-muted">API</dt>
            <dd className="flex items-center gap-2">
              <span
                className={`size-2 rounded-full ${health ? "bg-highlight" : "bg-accent"}`}
                aria-hidden
              />
              {health ? "Reachable" : "Not reachable"}
            </dd>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <dt className="w-28 shrink-0 text-text-muted">Stream host</dt>
            <dd className="font-mono text-xs break-all text-text-dim">{API_PUBLIC}</dd>
          </div>
          <div className="flex flex-wrap items-start gap-3">
            <dt className="w-28 shrink-0 pt-1 text-text-muted">Scan paths</dt>
            <dd className="flex flex-wrap gap-2">
              {health?.libraries.length ? (
                health.libraries.map((p) => <FactChip key={p}>{p}</FactChip>)
              ) : (
                <span className="text-text-dim">
                  None configured — set <FactChip>MIRU_LIBRARY_PATHS</FactChip> in <code>.env</code>
                </span>
              )}
            </dd>
          </div>
        </dl>
      </section>

      <section className="flex flex-col gap-4 rounded-2xl border border-border bg-surface p-6">
        <h3 className="text-[15px] font-bold">Artwork</h3>
        <p className="text-sm leading-relaxed text-text-dim">
          Anime covers come from AniList and series covers from TVmaze. Neither needs a key
          and both work out of the box. Films are the exception: TMDB is the only source
          with real film coverage, and it is the only one that asks for a key.
        </p>
        <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm text-text-dim">
          <li>
            Make a free account at{" "}
            <a
              href="https://www.themoviedb.org/settings/api"
              target="_blank"
              rel="noreferrer"
              className="font-bold text-highlight underline underline-offset-2"
            >
              themoviedb.org/settings/api
            </a>{" "}
            and copy the API Read Access key.
          </li>
          <li>
            Put it in <code>.env</code> as <FactChip>MIRU_TMDB_API_KEY</FactChip>.
          </li>
          <li>Restart the API. Covers fill in on the next refresh, a few dozen at a time.</li>
        </ol>
        <p className="text-[12.5px] text-text-muted">
          Without it nothing breaks — films keep their generated title cards.
        </p>
      </section>

      <section className="flex flex-col gap-4 rounded-2xl border border-border bg-surface p-6">
        <h3 className="text-[15px] font-bold">What the wall looks for</h3>
        <p className="text-sm leading-relaxed text-text-dim">
          Browsing returns each indexer&rsquo;s front page, which is whatever the world is
          downloading — so anything regional never reaches the rails on its own. The terms
          in <FactChip>MIRU_CATALOG_QUERIES</FactChip> are searched on every refresh and
          their results land in the catalogue like anything else.
        </p>
        <p className="text-[12.5px] text-text-muted">
          Currently seeded with <FactChip>tagalog</FactChip> <FactChip>filipino</FactChip>{" "}
          <FactChip>pinoy</FactChip>. Add your own, comma separated.
        </p>
      </section>

      <ScanPanel disabled={!health} />

      <section className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-6">
        <h3 className="text-[15px] font-bold">This device</h3>
        <p className="text-sm leading-relaxed text-text-dim">
          Watch progress and favourites are stored in this browser. They move to the server with
          progress tracking, at which point they follow you between devices.
        </p>
      </section>
    </div>
  );
}
