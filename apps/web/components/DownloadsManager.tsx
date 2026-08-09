"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ActiveDownload, fileSize } from "@/lib/api";
import { downloadAction, keepStream, pollDownloads } from "@/app/actions";
import { Button, ButtonLink, EmptyState, ProgressBar } from "@/components/ui";

const STATE_LABEL: Record<ActiveDownload["state"], string> = {
  queued: "Queued",
  downloading: "Downloading",
  paused: "Paused",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

function Row({ d, onAct }: { d: ActiveDownload; onAct: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const pct = Math.round((d.progress ?? 0) * 100);
  const active = d.state === "downloading" || d.state === "queued";

  const act = async (
    action: "pause" | "resume" | "cancel" | "dismiss",
    deleteFiles = false,
  ) => {
    setBusy(true);
    await downloadAction(d.job_id, action, { deleteFiles });
    setBusy(false);
    setConfirming(false);
    onAct();
  };

  return (
    <li className="flex flex-col gap-2.5 rounded-2xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[13.5px] font-bold">
          {d.work_id != null ? d.title : `${d.title} · not on the wall`}
        </span>
        <span
          className={`rounded-full px-2.5 py-0.5 text-[11px] font-extrabold ${
            d.state === "failed"
              ? "bg-accent/15 text-accent"
              : d.state === "done"
                ? "bg-primary/15 text-primary"
                : "bg-bg text-text-dim"
          }`}
        >
          {STATE_LABEL[d.state]}
        </span>
        {d.ephemeral && (
          <span className="rounded-full bg-bg px-2.5 py-0.5 text-[11px] font-extrabold text-text-dim">
            Stream — cleans up after
          </span>
        )}
      </div>

      {active && (
        <>
          <ProgressBar percent={pct} className="h-1.5" />
          <p className="text-[12px] text-text-muted tabular-nums">
            {pct}%
            {d.speed_bps ? ` · ${fileSize(d.speed_bps)}/s` : ""}
            {d.eta_seconds ? ` · ${Math.ceil(d.eta_seconds / 60)} min left` : ""}
          </p>
        </>
      )}
      {d.state === "failed" && d.error && (
        <p className="text-[12px] text-text-muted">{d.error}</p>
      )}

      {confirming ? (
        <div className="flex flex-wrap items-center gap-2.5">
          <p className="text-[12.5px] font-bold">Stop this download?</p>
          <Button size="sm" onClick={() => setConfirming(false)} disabled={busy}>
            Keep downloading
          </Button>
          <Button size="sm" variant="secondary" disabled={busy}
                  onClick={() => act("cancel")}>
            Stop, keep files
          </Button>
          <Button size="sm" variant="ghost" disabled={busy}
                  onClick={() => act("cancel", true)}>
            Stop &amp; delete files
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {active && d.ephemeral && (
            <ButtonLink href={`/watching/${d.job_id}`} size="sm">
              Watch
            </ButtonLink>
          )}
          {active && (
            <Button size="sm" variant="secondary" disabled={busy}
                    onClick={() => act("pause")}>
              Pause
            </Button>
          )}
          {d.state === "paused" && (
            <Button size="sm" disabled={busy} onClick={() => act("resume")}>
              Resume
            </Button>
          )}
          {d.ephemeral && d.state !== "failed" && (
            <Button size="sm" variant="secondary" disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      await keepStream(d.job_id);
                      setBusy(false);
                      onAct();
                    }}>
              Keep in library
            </Button>
          )}
          {d.state === "failed" ? (
            // The one row cancel could never touch: the downloader has
            // forgotten the torrent, so this clears our side only.
            <Button size="sm" variant="ghost" disabled={busy}
                    onClick={() => act("dismiss")}>
              Remove
            </Button>
          ) : (
            (active || d.state === "paused") && (
              <Button size="sm" variant="ghost" disabled={busy}
                      onClick={() => setConfirming(true)}>
                Cancel
              </Button>
            )
          )}
          {d.work_id != null && d.in_library && (
            <Link href={`/library`} className="text-[12.5px] font-bold text-text-muted hover:text-text">
              In your library →
            </Link>
          )}
        </div>
      )}
    </li>
  );
}

export function DownloadsManager() {
  const [rows, setRows] = useState<ActiveDownload[] | null>(null);
  const [pcUp, setPcUp] = useState(true);
  const bump = useRef(0);
  const [, force] = useState(0);
  const refresh = useCallback(() => {
    bump.current += 1;
    force(bump.current);
  }, []);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const res = await pollDownloads();
      if (!live) return;
      if (!("error" in res)) {
        setRows(res.downloads);
        setPcUp(res.pcReachable);
      }
      timer = setTimeout(tick, 3000);
    };
    tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [bump.current]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-extrabold tracking-[-0.02em]">Downloads</h1>
        <p className="mt-1 text-[13px] text-text-muted">
          Everything in flight, including streams and downloads no card claims.
        </p>
      </div>

      {!pcUp && (
        <p className="rounded-2xl border border-border bg-surface p-4 text-[13px] text-text-muted">
          The PC is asleep, so nothing can be listed or changed right now.
        </p>
      )}

      {rows === null ? (
        <p className="text-[13px] text-text-muted">Checking…</p>
      ) : rows.length === 0 && pcUp ? (
        <EmptyState title="Nothing downloading">
          <p>Watch Now streams and Download grabs land here while they run.</p>
        </EmptyState>
      ) : (
        <ul className="flex flex-col gap-3">
          {rows.map((d) => (
            <Row key={d.job_id} d={d} onAct={refresh} />
          ))}
        </ul>
      )}
    </div>
  );
}
