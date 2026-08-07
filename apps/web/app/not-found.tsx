import { ButtonLink } from "@/components/ui";

export default function NotFound() {
  return (
    <main className="grid min-h-dvh place-items-center p-8">
      <div className="flex max-w-md flex-col items-start gap-4">
        <p className="font-jp text-sm text-text-muted">みつかりません</p>
        <h1 className="text-3xl font-extrabold tracking-[-0.02em]">Nothing here</h1>
        <p className="text-sm leading-relaxed text-text-dim">
          That file isn&apos;t in the library. It may have been moved or removed on disk since the
          last scan — a rescan from Settings will bring the catalogue back in line.
        </p>
        <div className="flex flex-wrap gap-3">
          <ButtonLink href="/">Back to library</ButtonLink>
          <ButtonLink href="/settings" variant="secondary">
            Run a scan
          </ButtonLink>
        </div>
      </div>
    </main>
  );
}
