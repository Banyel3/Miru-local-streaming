import Link from "next/link";

const ICON = "h-[18px] w-[18px]";

function NavItem({
  href,
  label,
  active,
  children,
}: {
  href: string;
  label: string;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-[14.5px] transition-colors ${
        active
          ? "bg-primary font-bold text-white"
          : "font-semibold text-text-muted hover:bg-border hover:text-text"
      }`}
    >
      {children}
      {label}
    </Link>
  );
}

export function Sidebar() {
  return (
    <aside className="flex w-[264px] flex-none flex-col rounded-[20px] border border-border bg-surface px-[18px] pt-7 pb-[22px]">
      <div className="flex items-baseline gap-2.5 px-2.5 pb-[30px]">
        <span className="text-[26px] font-extrabold tracking-[-0.02em]">Miru</span>
        <span className="font-jp text-sm text-text-muted">見る</span>
      </div>

      <nav className="flex flex-col gap-1.5">
        <NavItem href="/" label="Home" active>
          <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 10.5 12 3l9 7.5" />
            <path d="M5 9.5V21h14V9.5" />
          </svg>
        </NavItem>
        <NavItem href="/?sort=added" label="Library">
          <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
            <path d="M4 4h4.5v16H4z" />
            <path d="M11 4h4.5v16H11z" />
            <path d="M18 5.5 21 20" />
          </svg>
        </NavItem>
        <NavItem href="/" label="Favourites">
          <svg className={ICON} viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 20.3C6.7 16.6 3 13.4 3 9.7 3 7.2 5 5.3 7.4 5.3c1.8 0 3.4 1 4.6 2.7 1.2-1.7 2.8-2.7 4.6-2.7C19 5.3 21 7.2 21 9.7c0 3.7-3.7 6.9-9 10.6z" />
          </svg>
        </NavItem>
      </nav>

      <div className="mx-2.5 my-[18px] h-px bg-border" />

      <nav className="flex flex-col gap-1.5">
        <NavItem href="/" label="Settings">
          <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
            <path d="M4 7h16M4 12h16M4 17h16" />
            <circle cx="9" cy="7" r="2.4" fill="#1E1B26" />
            <circle cx="15" cy="12" r="2.4" fill="#1E1B26" />
            <circle cx="8" cy="17" r="2.4" fill="#1E1B26" />
          </svg>
        </NavItem>
      </nav>

      <div className="mt-auto pt-8">
        <div className="flex items-baseline gap-2 px-2.5 pb-3.5">
          <span className="text-[11px] font-extrabold tracking-[0.14em] text-text-muted">
            CONTINUE WATCHING
          </span>
          <span className="font-jp text-[10px] text-text-muted/80">つづき</span>
        </div>
        {/* Watch state is M3. An empty shelf is the honest render until the
            progress table exists — a fake one would be a lie in the chrome. */}
        <p className="px-2.5 text-[11.5px] leading-relaxed text-text-muted">
          Nothing in progress yet. Resume points arrive with progress tracking.
        </p>
      </div>
    </aside>
  );
}
