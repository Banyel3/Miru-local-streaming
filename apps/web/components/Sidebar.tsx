"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { MediaFile, clock } from "@/lib/api";
import { percentOf, useContinueWatching } from "@/lib/store";
import { Close, Heart, Home, Library, Menu, Settings } from "@/components/icons";
import { ProgressBar, artTint } from "@/components/ui";

const NAV = [
  { href: "/", label: "Home", jp: "ホーム", Icon: Home },
  { href: "/library", label: "Library", jp: "ライブラリ", Icon: Library },
  { href: "/favourites", label: "Favourites", jp: "おきにいり", Icon: Heart },
];

function NavItem({
  href,
  label,
  Icon,
  active,
  onNavigate,
}: {
  href: string;
  label: string;
  Icon: (p: { className?: string }) => React.ReactElement;
  active: boolean;
  onNavigate?: () => void;
}) {
  return (
    <Link
      href={href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={`flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-[14.5px] transition-colors duration-150 ${
        active
          ? "bg-primary font-bold text-white"
          : "font-semibold text-text-muted hover:bg-border hover:text-text"
      }`}
    >
      <Icon className="size-[18px] shrink-0" />
      {label}
    </Link>
  );
}

function ContinueWatching({ files, onNavigate }: { files: MediaFile[]; onNavigate?: () => void }) {
  const entries = useContinueWatching(3);
  const byId = new Map(files.map((f) => [f.id, f]));

  return (
    <section className="mt-auto pt-8">
      <div className="flex items-baseline gap-2 px-2.5 pb-3.5">
        <h2 className="text-[11px] font-extrabold tracking-[0.14em] text-text-muted">
          CONTINUE WATCHING
        </h2>
        <span className="font-jp text-[10px] text-text-muted/80">つづき</span>
      </div>

      {entries === null ? (
        <div className="flex flex-col gap-2.5 px-2.5" aria-hidden>
          {[0, 1].map((i) => (
            <div key={i} className="skeleton h-[38px] rounded-lg" />
          ))}
        </div>
      ) : entries.length === 0 ? (
        <p className="px-2.5 text-[11.5px] leading-relaxed text-text-muted">
          Nothing in progress. Start something and it lands here.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {entries.map(([id, p]) => {
            const file = byId.get(id);
            if (!file) return null;
            const left = Math.max(0, p.durationS - p.positionS);
            return (
              <li key={id}>
                <Link
                  href={`/watch/${id}`}
                  onClick={onNavigate}
                  className="flex items-center gap-3 rounded-xl p-2 transition-colors duration-150 hover:bg-border"
                >
                  <span
                    className="h-[38px] w-[58px] shrink-0 rounded-lg border border-border"
                    style={{ background: artTint(file.title) }}
                  />
                  <span className="flex min-w-0 flex-1 flex-col gap-1">
                    <span className="truncate text-[12.5px] font-bold">{file.title}</span>
                    <span className="text-[10.5px] text-text-muted">
                      {left > 0 ? `${clock(left)} left` : "Almost done"}
                    </span>
                    <ProgressBar percent={percentOf(p)} className="h-[3px]" />
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      {entries !== null && entries.length > 0 && (
        <p className="px-2.5 pt-3 text-[10.5px] leading-snug text-text-muted/70">
          Saved on this device. Syncs across devices when progress tracking lands.
        </p>
      )}
    </section>
  );
}

function SidebarBody({ files, onNavigate }: { files: MediaFile[]; onNavigate?: () => void }) {
  const pathname = usePathname();
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <>
      <div className="flex items-baseline gap-2.5 px-2.5 pb-[30px]">
        <span className="text-[26px] font-extrabold tracking-[-0.02em]">Miru</span>
        <span className="font-jp text-sm text-text-muted">見る</span>
      </div>

      <nav aria-label="Main" className="flex flex-col gap-1.5">
        {NAV.map((item) => (
          <NavItem key={item.href} {...item} active={isActive(item.href)} onNavigate={onNavigate} />
        ))}
      </nav>

      <div className="mx-2.5 my-[18px] h-px bg-border" />

      <nav aria-label="Settings" className="flex flex-col gap-1.5">
        <NavItem
          href="/settings"
          label="Settings"
          Icon={Settings}
          active={isActive("/settings")}
          onNavigate={onNavigate}
        />
      </nav>

      <ContinueWatching files={files} onNavigate={onNavigate} />
    </>
  );
}

export function AppShell({ files, children }: { files: MediaFile[]; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  // Escape closes the drawer, and navigation implies dismissal.
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <div className="min-h-dvh p-4 sm:p-8 lg:p-14">
      <div className="mx-auto flex max-w-[1440px] gap-8 lg:rounded-3xl lg:border lg:border-border lg:p-7">
        {/* Desktop: persistent panel. */}
        <aside className="hidden w-[264px] shrink-0 flex-col rounded-[20px] border border-border bg-surface px-[18px] pt-7 pb-[22px] lg:flex">
          <SidebarBody files={files} />
        </aside>

        {/* Mobile: top bar + drawer. Core navigation is never hidden, only
            relocated — a hamburger that drops features is a downgrade. */}
        <div className="flex min-w-0 flex-1 flex-col gap-6 lg:gap-9">
          <div className="flex items-center gap-3 lg:hidden">
            <button
              type="button"
              onClick={() => setOpen(true)}
              aria-label="Open navigation"
              aria-expanded={open}
              className="grid size-11 place-items-center rounded-xl border border-border bg-surface text-text-muted"
            >
              <Menu />
            </button>
            <Link href="/" className="flex items-baseline gap-2">
              <span className="text-xl font-extrabold tracking-[-0.02em]">Miru</span>
              <span className="font-jp text-xs text-text-muted">見る</span>
            </Link>
          </div>

          {children}
        </div>
      </div>

      {open && (
        <>
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-(--z-drawer-backdrop) bg-bg-deep/70 backdrop-blur-sm lg:hidden"
          />
          <aside className="fixed inset-y-0 left-0 z-(--z-drawer) flex w-[280px] max-w-[85vw] flex-col overflow-y-auto border-r border-border bg-surface px-[18px] pt-7 pb-[22px] lg:hidden motion-safe:animate-[miru-slide-in_.22s_var(--ease-out-quart)]">
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close navigation"
              className="absolute top-6 right-4 grid size-9 place-items-center rounded-lg text-text-muted hover:bg-border hover:text-text"
            >
              <Close />
            </button>
            <SidebarBody files={files} onNavigate={() => setOpen(false)} />
          </aside>
        </>
      )}
    </div>
  );
}
