"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { checkHealth } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Icon } from "@/components/ui/Icon";

function HealthDot({ ok }: { ok: boolean | null }) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 rounded-full",
        ok === null && "animate-pulse bg-outline",
        ok === true && "bg-tertiary",
        ok === false && "bg-error"
      )}
      title={
        ok === null
          ? "Checking API…"
          : ok
            ? "API connected"
            : "API unreachable"
      }
      aria-hidden
    />
  );
}

const NAV = [
  { href: "/", label: "Create", match: (p: string) => p === "/" },
  {
    href: "/jobs",
    label: "Jobs",
    match: (p: string) => p.startsWith("/jobs") || p.startsWith("/books"),
  },
];

export function Header() {
  const pathname = usePathname();
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      const ok = await checkHealth();
      if (!cancelled) setHealthy(ok);
    }
    poll();
    const id = setInterval(poll, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <header className="fixed left-0 right-0 top-0 z-50 border-b border-white/[0.06] bg-surface/70 backdrop-blur-xl">
      <div className="mx-auto flex max-w-container-max items-center justify-between px-margin-mobile py-4 md:px-margin-desktop">
        <Link
          href="/"
          className="group flex items-center gap-2.5 font-display text-display-lg-mobile md:text-display-lg"
        >
          <span className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-on-primary ember-glow md:h-9 md:w-9">
            <Icon name="local_fire_department" size={20} className="filled" />
          </span>
          <span className="text-ember-gradient">BookForge</span>
        </Link>

        <nav className="hidden items-center gap-8 md:flex">
          {NAV.map((item) => {
            const active = item.match(pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "font-label text-label-md transition-colors duration-200",
                  active
                    ? "border-b-2 border-primary pb-1 font-bold text-primary"
                    : "text-secondary hover:text-primary-container"
                )}
              >
                {item.label}
              </Link>
            );
          })}
          <span className="flex items-center gap-2 text-secondary">
            <Icon name="sensors" size={18} />
            <HealthDot ok={healthy} />
            <span className="font-label text-label-md">API</span>
          </span>
          <div
            className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-full border border-outline-variant bg-surface-container-highest font-label text-label-sm text-secondary"
            aria-hidden
          >
            BF
          </div>
        </nav>

        <button
          type="button"
          className="text-primary md:hidden"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label="Toggle menu"
          aria-expanded={mobileOpen}
        >
          <Icon name={mobileOpen ? "close" : "menu"} />
        </button>
      </div>

      {mobileOpen && (
        <nav className="border-t border-outline-variant/20 bg-surface/95 px-margin-mobile py-4 md:hidden">
          <div className="flex flex-col gap-4">
            {NAV.map((item) => {
              const active = item.match(pathname);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setMobileOpen(false)}
                  className={cn(
                    "font-label text-label-md",
                    active ? "font-bold text-primary" : "text-secondary"
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
            <span className="flex items-center gap-2 text-secondary">
              <HealthDot ok={healthy} />
              <span className="font-label text-label-md">API</span>
            </span>
          </div>
        </nav>
      )}
    </header>
  );
}

export function PageShell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Header />
      <main className="mx-auto max-w-container-max px-margin-mobile pb-16 pt-28 md:px-margin-desktop">
        {children}
      </main>
    </>
  );
}
