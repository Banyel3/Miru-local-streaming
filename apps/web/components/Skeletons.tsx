/** Skeletons match the real layout's shape, so nothing shifts when data lands.
 *  Spinners in the middle of content are the thing these replace. */

export function CardSkeleton() {
  return (
    <div className="flex flex-col gap-2.5 rounded-2xl border border-border bg-surface p-2.5">
      <div className="skeleton aspect-2/3 rounded-[11px]" />
      <div className="flex flex-col gap-2 px-1 pb-1">
        <div className="skeleton h-3.5 w-4/5 rounded" />
        <div className="skeleton h-3 w-2/5 rounded" />
      </div>
    </div>
  );
}

export function GridSkeleton({ count = 12 }: { count?: number }) {
  return (
    <div
      className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4 sm:grid-cols-[repeat(auto-fill,minmax(170px,1fr))] xl:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]"
      aria-busy="true"
      aria-label="Loading library"
    >
      {Array.from({ length: count }, (_, i) => (
        <CardSkeleton key={i} />
      ))}
    </div>
  );
}

export function HeroSkeleton() {
  return <div className="skeleton h-[300px] rounded-3xl sm:h-[380px]" aria-hidden />;
}
