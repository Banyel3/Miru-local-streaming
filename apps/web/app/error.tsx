"use client";

import { useEffect } from "react";
import { Button, ButtonLink } from "@/components/ui";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="grid min-h-dvh place-items-center p-8">
      <div className="flex max-w-lg flex-col items-start gap-4">
        <h1 className="text-3xl font-extrabold tracking-[-0.02em]">Something broke</h1>
        <p className="text-sm leading-relaxed text-text-dim">
          Miru hit an error rendering this page. The details are in the browser console; if the
          library server is the problem, the API log on the media box will say so.
        </p>
        {error.digest && (
          <p className="font-mono text-xs text-text-muted">digest: {error.digest}</p>
        )}
        <div className="flex flex-wrap gap-3">
          <Button onClick={reset}>Try again</Button>
          <ButtonLink href="/" variant="secondary">
            Back to library
          </ButtonLink>
        </div>
      </div>
    </main>
  );
}
