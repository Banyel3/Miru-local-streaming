"use client";

import { useEffect, useRef } from "react";

/**
 * ASS/SSA rendering via JASSUB.
 *
 * A `<track>` element can only carry WebVTT, which throws away everything that
 * makes ASS worth having: positioning, per-line styles, karaoke, typesetting on
 * signs. JASSUB renders the real thing to a canvas over the video, in a worker,
 * so none of it costs a transcode — spec §7.
 *
 * The VTT form of the same track stays registered with the player as the
 * fallback. If the wasm fails to load, captions still work, just unstyled.
 */
export function AssOverlay({
  video,
  src,
  enabled,
  onFailed,
}: {
  video: HTMLVideoElement | null;
  src: string | null;
  enabled: boolean;
  onFailed?: () => void;
}) {
  const instance = useRef<{ destroy: () => void } | null>(null);

  useEffect(() => {
    if (!video || !src || !enabled) return;

    let cancelled = false;

    (async () => {
      try {
        const { default: JASSUB } = await import("jassub");
        if (cancelled) return;

        const renderer = new JASSUB({
          video,
          subUrl: src,
          debug: process.env.NODE_ENV !== "production",
          // No workerUrl/wasmUrl on purpose. JASSUB ships its worker
          // unbundled — dist/worker/worker.js imports abslink and lfa-ponyfill
          // as bare specifiers — so it must be built by the app's bundler.
          // Pointing these at hand-copied files in /public loads the emscripten
          // glue instead of the worker entry: it starts, handshakes with
          // nothing, and renders nothing, silently. Letting webpack resolve
          // JASSUB's own `new URL(...)` defaults is the supported path.
          // libass picks glyphs from the fonts it is given. The bundled font
          // is Latin-only, so Japanese renders as tofu boxes without this —
          // which for an anime library is the case that matters most.
          fonts: ["/jassub/default.woff2", "/jassub/noto-sans-jp.woff2"],
          // KNOWN GAP: Latin renders correctly with full styling, but CJK
          // still falls back to tofu boxes — libass resolves the style's font
          // (usually Arial) to the bundled Latin face and does not reach into
          // the Noto file for missing glyphs. Setting availableFonts +
          // defaultFont made it worse: the line vanished silently instead.
          // Needs the font-name mapping worked out properly against libass's
          // fontselect, not more guessing. Tracked in docs/reviews.
          // Render at source resolution rather than display resolution: a 4K
          // panel should not burn CPU upscaling a 720p sub track.
          prescaleFactor: 1,
        });
        instance.current = renderer as unknown as { destroy: () => void };
        const worker = (renderer as unknown as { _worker?: Worker })._worker;
        worker?.addEventListener("error", (e) =>
          console.error("[miru] jassub worker error:", (e as ErrorEvent).message, (e as ErrorEvent).filename),
        );
        await renderer.ready;
        console.info("[miru] jassub ready");
      } catch (err) {
        console.error("[miru] JASSUB failed to start, falling back to VTT", err);
        onFailed?.();
      }
    })();

    return () => {
      cancelled = true;
      try {
        instance.current?.destroy();
      } catch {
        // Destroying a half-constructed renderer is not worth a crash on unmount.
      }
      instance.current = null;
    };
  }, [video, src, enabled, onFailed]);

  return null;
}
