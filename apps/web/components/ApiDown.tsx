import { EmptyState } from "@/components/ui";

/** The failure the user will actually hit: the media box is asleep, or the API
 *  is not running. Says which, and what to do — not "something went wrong". */
export function ApiDown() {
  return (
    <EmptyState title="Can't reach the Miru API">
      <p>
        The library server isn&apos;t answering. On the split setup that usually means the media box
        is asleep or the API process isn&apos;t running.
      </p>
      <pre className="mt-3 overflow-x-auto rounded-lg border border-border bg-bg px-4 py-3 font-mono text-xs text-highlight">
        ./scripts/start-api.sh
      </pre>
    </EmptyState>
  );
}
