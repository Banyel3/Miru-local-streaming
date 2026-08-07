/** One icon set for the whole app, lifted from the design mock: 24px grid,
 *  2px strokes, round caps. Don't mix in a second library. */

type P = { className?: string };

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

export const Home = ({ className = "size-[18px]" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} aria-hidden>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.5V21h14V9.5" />
  </svg>
);

export const Library = ({ className = "size-[18px]" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} aria-hidden>
    <path d="M4 4h4.5v16H4z" />
    <path d="M11 4h4.5v16H11z" />
    <path d="M18 5.5 21 20" />
  </svg>
);

export const Heart = ({ className = "size-[18px]", filled = true }: P & { filled?: boolean }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill={filled ? "currentColor" : "none"}
    stroke="currentColor"
    strokeWidth={filled ? 0 : 2}
    strokeLinejoin="round"
    aria-hidden
  >
    <path d="M12 20.3C6.7 16.6 3 13.4 3 9.7 3 7.2 5 5.3 7.4 5.3c1.8 0 3.4 1 4.6 2.7 1.2-1.7 2.8-2.7 4.6-2.7C19 5.3 21 7.2 21 9.7c0 3.7-3.7 6.9-9 10.6z" />
  </svg>
);

export const Settings = ({ className = "size-[18px]" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} aria-hidden>
    <path d="M4 7h16M4 12h16M4 17h16" />
    <circle cx="9" cy="7" r="2.4" fill="var(--color-surface)" />
    <circle cx="15" cy="12" r="2.4" fill="var(--color-surface)" />
    <circle cx="8" cy="17" r="2.4" fill="var(--color-surface)" />
  </svg>
);

export const Search = ({ className = "size-[17px]" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} aria-hidden>
    <circle cx="11" cy="11" r="7" />
    <path d="M16.5 16.5 21 21" />
  </svg>
);

export const Play = ({ className = "size-3.5" }: P) => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
    <path d="M8 5v14l11-7z" />
  </svg>
);

export const ChevronLeft = ({ className = "size-4" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} aria-hidden>
    <path d="M15 5l-7 7 7 7" />
  </svg>
);

export const ChevronDown = ({ className = "size-3.5" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} aria-hidden>
    <path d="M6 9l6 6 6-6" />
  </svg>
);

export const Check = ({ className = "size-3" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} strokeWidth={2.5} aria-hidden>
    <path d="M5 13l4 4L19 7" />
  </svg>
);

export const Menu = ({ className = "size-5" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} aria-hidden>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </svg>
);

export const Close = ({ className = "size-5" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} aria-hidden>
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);

export const Refresh = ({ className = "size-4" }: P) => (
  <svg className={className} viewBox="0 0 24 24" {...stroke} aria-hidden>
    <path d="M20 12a8 8 0 1 1-2.3-5.6" />
    <path d="M20 4v4h-4" />
  </svg>
);
