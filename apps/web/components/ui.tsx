import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

/** One button vocabulary for the whole app. If the same action looks different
 *  on two screens, one of them is wrong. */
const BUTTON =
  "inline-flex items-center justify-center gap-2.5 rounded-[13px] font-bold transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50";

const VARIANT = {
  primary: "bg-primary text-white hover:bg-primary-hover",
  secondary: "border border-border text-text hover:border-border-hover",
  ghost: "text-text-muted hover:bg-border hover:text-text",
} as const;

const SIZE = {
  sm: "px-3.5 py-2 text-xs",
  md: "px-5 py-3 text-[13.5px]",
  lg: "px-6 py-3.5 text-sm",
} as const;

type Variant = keyof typeof VARIANT;
type Size = keyof typeof SIZE;

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ComponentProps<"button"> & { variant?: Variant; size?: Size }) {
  return <button className={`${BUTTON} ${VARIANT[variant]} ${SIZE[size]} ${className}`} {...props} />;
}

export function ButtonLink({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ComponentProps<typeof Link> & { variant?: Variant; size?: Size }) {
  return <Link className={`${BUTTON} ${VARIANT[variant]} ${SIZE[size]} ${className}`} {...props} />;
}

/** Monospace file facts. On a self-hosted server these are a feature, not
 *  clutter — spec §10. */
export function FactChip({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "bright" }) {
  return (
    <span
      className={`rounded-md border border-border bg-surface px-2 py-1 font-mono text-[11px] font-medium ${
        tone === "bright" ? "text-highlight" : "text-text-muted"
      }`}
    >
      {children}
    </span>
  );
}

/** Compact variant used inside cards, where vertical space is scarce. */
export function MicroChip({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "bright" }) {
  return (
    <span
      className={`rounded-[5px] bg-border px-1.5 py-0.5 font-mono text-[9.5px] font-semibold ${
        tone === "bright" ? "text-highlight" : "text-text-muted"
      }`}
    >
      {children}
    </span>
  );
}

/** Watch state. --accent lives here and in the player timeline, nowhere else. */
export function ProgressBar({ percent, className = "" }: { percent: number; className?: string }) {
  return (
    <div
      className={`overflow-hidden rounded-full bg-border ${className}`}
      role="progressbar"
      aria-valuenow={Math.round(percent)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Watch progress"
    >
      <div className="h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
    </div>
  );
}

export function SectionHeading({
  title,
  jp,
  children,
}: {
  title: string;
  jp: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
      <h2 className="text-xl font-extrabold tracking-[-0.01em]">{title}</h2>
      <span className="font-jp text-xs text-text-muted">{jp}</span>
      {children && <div className="ml-auto flex items-center gap-3">{children}</div>}
    </div>
  );
}

/** Empty states teach the interface. "Nothing here" teaches nothing. */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-2xl border border-border bg-surface p-8">
      <h3 className="text-[15px] font-bold">{title}</h3>
      <div className="max-w-[65ch] text-sm leading-relaxed text-text-dim">{children}</div>
      {action}
    </div>
  );
}

/** Deterministic tint so a grid of unartworked files reads as a grid rather
 *  than as identical grey rectangles. Replaced by real posters at M2.
 *
 *  Lightness sits meaningfully above --surface: at M1 there is no artwork, so
 *  this tile IS the visual, and a gradient that blends into the page background
 *  reads as a rendering failure rather than as a placeholder. */
export function artTint(seed: string) {
  const n = [...seed].reduce((a, c) => a + c.charCodeAt(0), 0);
  const hue = 228 + (n % 84);
  return `linear-gradient(155deg, hsl(${hue} 22% 26%), hsl(${hue - 16} 20% 15%))`;
}

/**
 * The artwork stand-in. Until posters arrive the filename is the only content
 * there is, so it gets set as real type instead of being shrunk into a corner
 * of an empty rectangle.
 */
export function ArtTile({
  seed,
  episode,
  label,
  className = "",
  compact,
  children,
}: {
  seed: string;
  episode?: string | null;
  label?: string;
  className?: string;
  compact?: boolean;
  children?: ReactNode;
}) {
  return (
    <div
      className={`relative flex flex-col justify-end overflow-hidden ${className}`}
      style={{ background: artTint(seed) }}
    >
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            "repeating-linear-gradient(115deg, #fff 0 1px, transparent 1px 14px)",
        }}
        aria-hidden
      />
      {/* Episode marker when there is one, the filename only when there isn't.
          The caption beneath already carries the title, so printing both here
          says the same thing twice — and once real posters land at M2 this
          layer goes away entirely. */}
      {(episode || label) && (
        <div className={`relative flex flex-col gap-1 ${compact ? "p-2.5" : "p-4"}`}>
          {episode ? (
            <span
              className={`font-mono font-extrabold tracking-tight text-highlight ${
                compact ? "text-[13px]" : "text-[22px]"
              }`}
            >
              {episode}
            </span>
          ) : (
            <span
              className={`font-bold text-text ${
                compact
                  ? "line-clamp-2 text-[11.5px] leading-tight"
                  : "line-clamp-3 text-[15px] leading-snug"
              }`}
            >
              {label}
            </span>
          )}
        </div>
      )}
      {children}
    </div>
  );
}
