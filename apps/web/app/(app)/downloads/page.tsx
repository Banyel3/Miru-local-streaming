import { DownloadsManager } from "@/components/DownloadsManager";

export const metadata = { title: "Downloads — Miru" };

/**
 * Every in-flight grab, manageable.
 *
 * The sidebar's mini-list shows progress; this is where things get acted on —
 * pause, resume, keep a stream, stop, and clear the failed rows the wall can
 * only report. Orphans (downloads no card claims, e.g. after two cards merged)
 * appear here too: they are running and using disk, so they need controls even
 * without a home on the wall.
 */
export default function DownloadsPage() {
  return <DownloadsManager />;
}
