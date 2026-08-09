"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CatalogRelease,
  CatalogWork,
  WorkDetail,
  episodeLabel,
  fileSize,
  playbackNote,
  releaseSpec,
} from "@/lib/api";
import { Group, SECTION, grabbable, groupReleases, ownedFileFor, threeChoices } from "@/lib/episodes";
import { Button, EmptyState, MicroChip } from "@/components/ui";
import { ChevronLeft, Close } from "@/components/icons";
import { useRouter } from "next/navigation";
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

/** The group's own episode span, worded by the one formatter that already
 *  words it for the footer. Unnumbered releases have no span to word. */
const groupLabel = (g: Group) => episodeLabel(g.releases[0]) ?? "No episode number";

/** One episode, or one batch. The chips preview the release you would get by
 *  clicking, so the row and the picker behind it cannot disagree. */
function EpisodeRow({
  group,
  onOpen,
  ownedFileId = null,
}: {
  group: Group;
  onOpen: () => void;
  ownedFileId?: number | null;
}) {
  const pick = threeChoices(group.releases).best ?? group.releases[0];
  const router = useRouter();
  return (
    <li>
      <button
        type="button"
        // An owned row plays from the library instead of re-offering the
        // download the user already has — the sheet finally knows what is on
        // disk (file-page §5).
        onClick={ownedFileId != null ? () => router.push(`/watch/${ownedFileId}`) : onOpen}
        className="flex min-h-11 w-full items-center gap-3 rounded-2xl border border-border bg-bg px-3.5 py-2 text-left transition-colors hover:border-border-hover"
      >
        <span className="min-w-0 flex-1 truncate text-[13.5px] font-bold">{groupLabel(group)}</span>
        <span className="flex shrink-0 items-center gap-1.5">
          {ownedFileId != null && <MicroChip tone="bright">✓ in library</MicroChip>}
          {pick.quality && <MicroChip>{pick.quality}</MicroChip>}
          <MicroChip>{fileSize(pick.size_bytes)}</MicroChip>
          <MicroChip tone={group.releases.length > 1 ? "bright" : "muted"}>
            {group.releases.length} rel
          </MicroChip>
        </span>
      </button>
    </li>
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
  /** The episode being looked at, or null at the episode list. One sheet, two
   *  levels, no route change — the wall behind it keeps its scroll position. */
  const [scope, setScope] = useState<Group | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const router = useRouter();

  useEffect(() => {
    let live = true;
    loadWork(work.id)
      .then((res) => {
        if (!live) return;
        if ("error" in res) return setError("Couldn't load the releases for this.");
        const d = res.work;
        setDetail(d);
        // Staleness matters in the fallback too. The table's own click handler
        // already refuses stale rows, so without this the *default* selection
        // was the one path that could submit a release the system had decided
        // was a corpse.
        setChosen(
          d.choices.best?.info_hash ??
            d.releases.find((r) => r.grabbable && !r.stale)?.info_hash ??
            null,
        );
      })
      .catch(() => live && setError("Couldn't load the releases for this."));
    return () => {
      live = false;
    };
  }, [work.id]);

  // The pack sweep runs in the background after the card opens. Re-fetch while
  // it lands so the batches appear in THIS sheet — without this they were only
  // visible after close-and-reopen, which read as "it isn't fetching the
  // complete sets". Two refetches cover the sweep's few-second lifetime; the
  // user's own selection is never overwritten.
  useEffect(() => {
    if (!detail?.sweep_started) return;
    let live = true;
    const timers = [6000, 16000].map((ms) =>
      setTimeout(async () => {
        const res = await loadWork(work.id);
        if (!live || "error" in res) return;
        setDetail((old) =>
          old && res.work.releases.length > old.releases.length ? res.work : old,
        );
      }, ms),
    );
    return () => {
      live = false;
      timers.forEach(clearTimeout);
    };
  }, [detail?.sweep_started, work.id]);

  // Escape pops one level and only closes at the top, so backing out of an
  // episode does not also throw away the show.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (scope) setScope(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose, scope]);

  // A film has no episodes to list, and neither does a show whose whole
  // catalogue is one entry — a level with a single row is a level nobody wants.
  const groups = useMemo(
    () =>
      !detail || work.kind === "movie" || work.format === "MOVIE"
        ? []
        : groupReleases(detail.releases),
    [detail, work.kind, work.format],
  );
  const atEpisodes = groups.length > 1 && !scope;

  function open(group: Group) {
    setScope(group);
    setChosen(
      threeChoices(group.releases).best?.info_hash ??
        group.releases.find(grabbable)?.info_hash ??
        null,
    );
    body.current?.scrollTo(0, 0);
  }

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
    // Watch Now goes to the waiting screen, which starts playback on its own.
    // Download stays put: the card becomes the progress state in place, so
    // queueing three things is three clicks and no navigation.
    if (watch) router.push(`/watching/${res.jobId}`);
  }

  // Scoped to an episode the API's three choices are the wrong three — they
  // rank the whole show — so that subset gets its own.
  const releases = scope ? scope.releases : (detail?.releases ?? []);
  const picked = scope ? threeChoices(scope.releases) : detail?.choices;

  // Two choices may be the same release. Showing the identical row twice under
  // different names makes the picker look broken.
  const seen = new Set<string>();
  const choices = Object.entries(picked ?? {}).filter(([, r]) => {
    if (!r || seen.has(r.info_hash)) return false;
    seen.add(r.info_hash);
    return true;
  }) as [string, CatalogRelease][];

  const current = releases.find((r) => r.info_hash === chosen);
  const offline = detail && !detail.pc_reachable;

  // "8 of 1,172" is the honest line. The other 1,164 have no release behind
  // them, so they are counted here and never rendered as rows.
  const listed = groups.filter((g) => g.kind !== "unsorted").length;
  // The provider count belongs to whichever entry the title resolved to, and a
  // later season's releases keep counting from where the previous one stopped —
  // Bookworm S4 is episodes 15 and 17 against an S1 entry of 14. Printing
  // "2 of 14" there states a total the rows visibly exceed, so the count is
  // only shown once it actually covers the highest episode on screen.
  const highest = Math.max(...groups.map((g) => g.from), 0);
  const total = work.episode_count;
  const episodeCount =
    total && total > listed && total >= highest
      ? `${listed} of ${total.toLocaleString()} episodes`
      : `${listed} episode${listed === 1 ? "" : "s"}`;

  return (
    <div
      className="fixed inset-0 z-(--z-drawer) flex items-end justify-center sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={`Get ${work.title}`}
    >
      {/* Ordering is relative to this container, not to the document.
          The wrapper already carries --z-drawer, which is what puts the whole
          dialog above the page; inside it these two only have to be ordered
          against each other. Using --z-drawer-backdrop here put the blurred
          backdrop *above* the sheet, which rendered as a blurred page with
          nothing on it and nothing clickable. */}
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 z-0 bg-bg-deep/70 backdrop-blur-sm"
      />

      <div
        ref={body}
        className="relative z-10 flex max-h-[92dvh] w-full max-w-[560px] flex-col gap-3 overflow-y-auto rounded-t-3xl border border-border bg-surface p-5 sm:rounded-3xl motion-safe:animate-[miru-rise_.25s_var(--ease-out-quart)]"
      >
        <header className="flex items-start gap-3">
          <div className="min-w-0">
            {/* A scope is only reachable from the episode list, so its presence
                is what says there is a level to pop back to. */}
            {scope && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setScope(null)}
                className="-ml-2 mb-1 !px-2"
              >
                <ChevronLeft />
                All episodes
              </Button>
            )}
            <h2 className="text-[17px] font-extrabold">
              Get <span className="text-highlight">{work.title}</span>
              {scope && <span className="text-text-muted"> — {groupLabel(scope)}</span>}
            </h2>
            <p className="mt-0.5 text-[12.5px] text-text-muted">
              {!detail
                ? "Looking at what's available…"
                : atEpisodes
                  ? episodeCount
                  : `${releases.length} release${releases.length === 1 ? "" : "s"}. Miru picked one.`}
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

        {atEpisodes &&
          (["batch", "single", "unsorted"] as const).map((kind) => {
            const rows = groups.filter((g) => g.kind === kind);
            if (!rows.length) return null;
            return (
              <section key={kind} className="flex flex-col gap-2">
                <h3 className="text-[10px] tracking-[0.07em] text-text-muted uppercase">
                  {SECTION[kind]}
                </h3>
                <ul className="flex flex-col gap-2">
                  {rows.map((g) => (
                    <EpisodeRow
                      key={g.key}
                      group={g}
                      onOpen={() => open(g)}
                      ownedFileId={ownedFileFor(g, detail?.owned_episodes ?? [])}
                    />
                  ))}
                </ul>
              </section>
            );
          })}

        {atEpisodes && (
          <p className="border-t border-border pt-3 text-[11.5px] text-text-muted">
            Only episodes your indexers currently list.
          </p>
        )}

        {detail && !atEpisodes && releases.length === 0 && (
          <EmptyState title="No releases behind this one">
            The indexers listed this when the catalogue was built and list nothing for it now.
            The next refresh will either bring it back or drop the card.
          </EmptyState>
        )}

        {!atEpisodes &&
          choices.map(([name, r]) => (
            <Choice
              key={name}
              name={name}
              release={r}
              selected={chosen === r.info_hash}
              onSelect={() => setChosen(r.info_hash)}
            />
          ))}

        {!atEpisodes && releases.length > choices.length && (
          <details className="border-t border-border pt-3">
            <summary className="cursor-pointer text-[12.5px] font-bold text-text-dim">
              All {releases.length} releases
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
                  {releases.map((r) => (
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

        {/* No footer at the episode level: nothing is chosen there, and a
            disabled Watch Now under a list of episodes reads as broken. */}
        {!atEpisodes && (
        <footer className="flex flex-wrap items-center gap-3 border-t border-border pt-3.5">
          <p className="min-w-[180px] flex-1 text-[11.5px] text-text-muted">
            {(current && episodeLabel(current)) ??
              "Downloads to the PC, then moves itself into your library."}
          </p>
          <Button variant="secondary" disabled={busy || !chosen || !!offline} onClick={() => start(false)}>
            Download
          </Button>
          <Button disabled={busy || !chosen || !!offline} onClick={() => start(true)}>
            {busy ? "Starting…" : "Watch Now"}
          </Button>
        </footer>
        )}
      </div>
    </div>
  );
}
