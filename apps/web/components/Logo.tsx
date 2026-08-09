/**
 * Miru's mark: an eye whose pupil is a play triangle.
 *
 * 見る is "to watch", so the mark says it twice over — the lens shape is the
 * eye, and the triangle knocked out of it is the thing you press. One path
 * with evenodd does both, which is what keeps it legible at 16px: at favicon
 * size there is exactly one silhouette and one hole, not a stack of parts
 * that mush together.
 *
 * The tile is drawn here rather than left to the page background so the mark
 * survives on a browser tab, a bookmark bar, or anything else that puts it on
 * a surface Miru does not control.
 */
export function LogoMark({ className = "size-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} role="img" aria-label="Miru">
      <defs>
        <linearGradient id="miru-lens" x1="4" y1="10" x2="28" y2="22" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#c9b8e4" />
          <stop offset="1" stopColor="#7c6ba8" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="7.5" fill="#15131a" />
      <path
        fill="url(#miru-lens)"
        fillRule="evenodd"
        d="M4 16 Q16 4 28 16 Q16 28 4 16 Z M13 12.4 L21 16 L13 19.6 Z"
      />
      {/* The catchlight. One dot, in the brand's gold, so the eye reads as an
          eye rather than as a lens diagram. */}
      <circle cx="10.4" cy="14.2" r="1.3" fill="#d9a441" />
    </svg>
  );
}

/** Mark plus wordmark. `size` drives both so the lockup scales as one thing. */
export function Logo({
  size = "md",
  className = "",
}: {
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const mark = { sm: "size-7", md: "size-8", lg: "size-10" }[size];
  const word = { sm: "text-xl", md: "text-[26px]", lg: "text-3xl" }[size];
  const jp = { sm: "text-xs", md: "text-sm", lg: "text-[15px]" }[size];

  return (
    <span className={`flex items-center gap-2.5 ${className}`}>
      <LogoMark className={`${mark} shrink-0`} />
      <span className="flex items-baseline gap-2">
        <span className={`${word} font-extrabold tracking-[-0.02em]`}>Miru</span>
        <span className={`font-jp ${jp} text-text-muted`}>見る</span>
      </span>
    </span>
  );
}
