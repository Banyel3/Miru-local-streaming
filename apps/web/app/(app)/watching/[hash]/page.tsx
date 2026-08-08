import { LiveWatch } from "@/components/LiveWatch";

/**
 * Watch Now, after the click.
 *
 * The promise Watch Now makes is not "this is already downloaded" — it is
 * "you will not have to babysit a progress bar". This screen is where that is
 * kept: it waits, it says exactly what it is waiting for, and it starts on its
 * own the moment enough of the front has landed.
 */
export default async function WatchingPage({ params }: { params: Promise<{ hash: string }> }) {
  const { hash } = await params;
  return <LiveWatch infoHash={hash} />;
}
