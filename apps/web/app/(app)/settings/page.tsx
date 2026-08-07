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
