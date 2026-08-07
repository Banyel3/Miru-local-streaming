"use client";

import { useEffect, useRef, useState } from "react";
import { Job } from "@/lib/api";
import { pollJob, refreshLibrary, startScan } from "@/app/actions";
import { Button } from "@/components/ui";
import { Refresh } from "@/components/icons";

type State =
  | { kind: "idle" }
  | { kind: "running"; job: Job }
  | { kind: "done"; job: Job }
  | { kind: "error"; message: string };

const summarise = (payload: Record<string, number>) => {
  const parts = Object.entries(payload)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => `${v} ${k}`);
  return parts.length ? parts.join(", ") : "no changes";
};

export function ScanPanel({ disabled }: { disabled?: boolean }) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  async function run() {
    setState({ kind: "idle" });
    const started = await startScan();
    if ("error" in started) return setState({ kind: "error", message: started.error });
    setState({ kind: "running", job: started.job });
    track(started.job.id);
  }

  function track(id: number) {
    timer.current = setTimeout(async () => {
      const result = await pollJob(id);
      if ("error" in result) return setState({ kind: "error", message: result.error });
      if (result.job.status === "done") {
        setState({ kind: "done", job: result.job });
        await refreshLibrary();
        return;
      }
      if (result.job.status === "failed") {
        return setState({
          kind: "error",
          message: result.job.error ?? "The scan failed without reporting a reason.",
        });
      }
      setState({ kind: "running", job: result.job });
      track(id);
    }, 900);
  }

  const busy = state.kind === "running";

  return (
    <section className="flex flex-col gap-4 rounded-2xl border border-border bg-surface p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h3 className="text-[15px] font-bold">Scan for new files</h3>
          <p className="max-w-[55ch] text-sm leading-relaxed text-text-dim">
            Walks your library paths and probes anything new. Files whose size and modified time
            haven&apos;t changed are skipped, so repeat scans are quick.
          </p>
        </div>
        <Button onClick={run} disabled={busy || disabled} aria-busy={busy}>
          <Refresh className={busy ? "size-4 motion-safe:animate-spin" : "size-4"} />
          {busy ? "Scanning…" : "Scan now"}
        </Button>
      </div>

      <div aria-live="polite" className="min-h-6 text-sm">
        {state.kind === "running" && (
          <p className="text-text-dim">
            Job #{state.job.id} is {state.job.status}. Large first scans over a network mount can
            take a while.
          </p>
        )}
        {state.kind === "done" && (
          <p className="text-text-dim">
            Job #{state.job.id} finished — {summarise(state.job.payload)}.
          </p>
        )}
        {state.kind === "error" && (
          <p className="rounded-lg border border-border-hover bg-bg px-4 py-3 text-accent">
            {state.message}
          </p>
        )}
      </div>
    </section>
  );
}
