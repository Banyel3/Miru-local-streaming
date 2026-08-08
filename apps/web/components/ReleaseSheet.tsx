"use client";

import { useEffect, useState } from "react";
import {
  CatalogRelease,
  CatalogWork,
  WorkDetail,
  episodeLabel,
  fileSize,
  playbackNote,
  releaseSpec,
} from "@/lib/api";
import { Button } from "@/components/ui";
import { Close } from "@/components/icons";
import { loadWork, startDownload } from "@/app/actions";

/**
 * Choosing a release.
 *
 * Forty rows sorted by seeders is not a choice anyone can make, so this offers
 * three named ones and hides the table behind a disclosure. Each is labelled
 * with what it means for playback rather than with its specs alone, because
 * Miru is the only thing here that knows its own transcode ladder and can say
 * which release lets the PC stay asleep.
 */

const CHOICE_LABEL: Record<string, string> = {
  best: "Best",
  smallest: "Smallest",
  best_quality: "Best quality",
};

function Choice({
  name,
  release,
  selected,
  onSelect,
}: {
  name: string;
  release: CatalogRelease;
  selected: boolean;
  onSelect: () => void;
}) {
  const note = playbackNote(release);
  return (
    <label
      className={`flex cursor-pointer items-start gap-3 rounded-2xl border p-3.5 transition-colors ${
        selected ? "border-primary bg-primary/10" : "border-border bg-bg hover:border-border-hover"
      }`}
    >
      <input
        type="radio"
        name="release"
        checked={selected}
        onChange={onSelect}
        className="mt-0.5 size-4 shrink-0 accent-[var(--color-primary)]"
      />
      <span className="flex min-w-0 flex-col gap-1">
        <span className="flex items-center gap-2 text-[13.5px] font-extrabold">
          {CHOICE_LABEL[name] ?? name}
          {name === "best" && (
            <span className="rounded-full border border-border-hover px-2 py-0.5 text-[9.5px] font-extrabold tracking-[0.08em] text-highlight uppercase">
              recommended
            </span>
          )}
        </span>
        <span className="font-mono text-[11.5px] text-text-muted">{releaseSpec(release)}</span>
        <span
          className={`text-[12px] font-bold ${note.tone === "good" ? "text-[#7fb08a]" : "text-accent"}`}
        >
          {note.text}
        </span>
      </span>
    </label>
  );
}

export function ReleaseSheet({
  work,
  onClose,
  onStarted,
}: {
  work: CatalogWork;
  onClose: () => void;
  onStarted: (workId: number, jobId: string) => void;
}) {
  const [detail, setDetail] = useState<WorkDetail | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    loadWork(work.id)
      .then((res) => {
        if (!live) return;
        if ("error" in res) return setError("Couldn't load the releases for this.");
        const d = res.work;
        setDetail(d);
        setChosen(d.choices.best?.info_hash ?? d.releases.find((r) => r.grabbable)?.info_hash ?? null);
      })
      .catch(() => live && setError("Couldn't load the releases for this."));
    return () => {
      live = false;
    };
  }, [work.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  async function start(watch: boolean) {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    const res = await startDownload(work.id, chosen, watch);
    if ("error" in res) {
      setError(res.error);
      setBusy(false);
      return;
    }
    onStarted(work.id, res.jobId);
    onClose();
  }

  // Two choices may be the same release. Showing the identical row twice under
  // different names makes the picker look broken.
  const seen = new Set<string>();
  const choices = Object.entries(detail?.choices ?? {}).filter(([, r]) => {
    if (!r || seen.has(r.info_hash)) return false;
    seen.add(r.info_hash);
    return true;
  }) as [string, CatalogRelease][];

  const offline = detail && !detail.pc_reachable;

  return (
    <div
      className="fixed inset-0 z-(--z-drawer) flex items-end justify-center sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={`Get ${work.title}`}
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 z-(--z-drawer-backdrop) bg-bg-deep/70 backdrop-blur-sm"
      />

      <div className="relative flex max-h-[92dvh] w-full max-w-[560px] flex-col gap-3 overflow-y-auto rounded-t-3xl border border-border bg-surface p-5 sm:rounded-3xl motion-safe:animate-[miru-rise_.25s_var(--ease-out-quart)]">
        <header className="flex items-start gap-3">
          <div className="min-w-0">
            <h2 className="text-[17px] font-extrabold">
              Get <span className="text-highlight">{work.title}</span>
            </h2>
            <p className="mt-0.5 text-[12.5px] text-text-muted">
              {detail
                ? `${detail.releases.length} release${detail.releases.length === 1 ? "" : "s"}. Miru picked one.`
                : "Looking at what's available…"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="ml-auto grid size-11 shrink-0 place-items-center rounded-xl border border-border text-text-muted hover:border-border-hover hover:text-text"
          >
            <Close />
          </button>
        </header>

        {offline && (
          <p className="rounded-xl border border-border bg-bg px-3.5 py-2.5 text-[12.5px] text-accent">
            The PC is asleep, so nothing can be downloaded right now.
          </p>
        )}

        {detail?.all_dead && (
          <p className="rounded-xl border border-border bg-bg px-3.5 py-2.5 text-[12.5px] text-text-dim">
            Nothing here has many seeders. This may be slow, or may not start at all.
          </p>
        )}

        {!detail && !error && (
          <div className="flex flex-col gap-2.5" aria-hidden>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton h-[86px] rounded-2xl" />
            ))}
          </div>
        )}

        {choices.map(([name, r]) => (
          <Choice
            key={name}
            name={name}
            release={r}
            selected={chosen === r.info_hash}
            onSelect={() => setChosen(r.info_hash)}
          />
        ))}

        {detail && detail.releases.length > choices.length && (
          <details className="border-t border-border pt-3">
            <summary className="cursor-pointer text-[12.5px] font-bold text-text-dim">
              All {detail.releases.length} releases
            </summary>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full border-collapse text-[11.5px]">
                <thead>
                  <tr className="text-text-muted">
                    {["Release", "Quality", "Size", "Seeds", "Playback"].map((h) => (
                      <th
                        key={h}
                        className="border-b border-border px-2 py-1.5 text-left text-[10px] tracking-[0.07em] uppercase whitespace-nowrap"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {detail.releases.map((r) => (
                    <tr
                      key={r.info_hash}
                      onClick={() => r.grabbable && !r.stale && setChosen(r.info_hash)}
                      className={`cursor-pointer border-b border-border ${
                        chosen === r.info_hash ? "bg-primary/10" : ""
                      } ${r.stale || !r.grabbable ? "opacity-45 line-through" : ""}`}
                    >
                      <td className="max-w-[260px] truncate px-2 py-1.5 text-text-dim" title={r.title}>
                        {r.title}
                      </td>
                      <td className="px-2 py-1.5 text-text-dim">{r.quality ?? "—"}</td>
                      <td className="px-2 py-1.5 text-right text-text-dim tabular-nums">
                        {fileSize(r.size_bytes)}
                      </td>
                      <td className="px-2 py-1.5 text-right text-text-dim tabular-nums">
                        {r.seeders.toLocaleString()}
                      </td>
                      <td
                        className={`px-2 py-1.5 font-bold ${r.needs_pc ? "text-accent" : "text-[#7fb08a]"}`}
                      >
                        {r.needs_pc ? "Transcode" : "Direct"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}

        {error && <p className="text-[12.5px] font-bold text-accent">{error}</p>}

        <footer className="flex flex-wrap items-center gap-3 border-t border-border pt-3.5">
          <p className="min-w-[180px] flex-1 text-[11.5px] text-text-muted">
            {chosen && detail
              ? episodeLabel(detail.releases.find((r) => r.info_hash === chosen)!) ??
                "Downloads to the PC, then moves itself into your library."
              : "Downloads to the PC, then moves itself into your library."}
          </p>
          <Button variant="secondary" disabled={busy || !chosen || !!offline} onClick={() => start(false)}>
            Download
          </Button>
          <Button disabled={busy || !chosen || !!offline} onClick={() => start(true)}>
            {busy ? "Starting…" : "Watch Now"}
          </Button>
        </footer>
      </div>
    </div>
  );
}
