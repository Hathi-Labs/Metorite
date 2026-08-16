"use client";

// Shared WhatsApp app shell — a persistent sub-navigation across every WhatsApp
// route (inbox + Pulse + settings + Numbers). Responsive, matching the rest of
// Metorite:
//   • Desktop: a persistent LEFT column (like email's AccountSidebar / tasks'
//     ListsSidebar) — icon rail that widens to labels at md.
//   • Mobile: no in-page nav chrome. The section list lives in a slide-up
//     drawer opened by the shell's "Sections" bottom-nav tab (which dispatches
//     the `cc-mobile-nav` "wa-sections" event) — the same drawer pattern
//     email/tasks use for their mobile sub-navigation.
// Icons are the native lucide set.

import AppIcon, { themedIcon } from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { useViewMode } from "@/components/ViewModeProvider";
import { useMobileDrawer } from "@/components/AppShell";

type Tab = { href: string; label: string; icon: ThemedIcon; exact?: boolean };

const TABS: Tab[] = [
  { href: "/whatsapp", label: "Inbox", icon: themedIcon("Inbox"), exact: true },
  { href: "/whatsapp/calls", label: "Calls", icon: themedIcon("Phone") },
  { href: "/whatsapp/insights", label: "Pulse", icon: themedIcon("Activity") },
  { href: "/whatsapp/settings/categories", label: "Categories", icon: themedIcon("Tags") },
  { href: "/whatsapp/settings/replies", label: "Replies", icon: themedIcon("MessageSquareText") },
  { href: "/whatsapp/settings/rules", label: "Rules", icon: themedIcon("SlidersHorizontal") },
  { href: "/whatsapp/numbers", label: "Numbers", icon: themedIcon("Smartphone") },
];

const isTabActive = (t: Tab, pathname: string | null) =>
  t.exact ? pathname === t.href : pathname?.startsWith(t.href) ?? false;

// The section list as it appears inside the mobile bottom-nav drawer.
function SectionNavDrawer({
  pathname,
  onNavigate,
}: {
  pathname: string | null;
  onNavigate: () => void;
}) {
  return (
    <div className="flex flex-col p-2">
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary/15 text-primary">
          <AppIcon name="MessageSquare" className="h-3.5 w-3.5" />
        </span>
        <span className="text-[13px] font-semibold">WhatsApp</span>
      </div>
      {TABS.map((t) => {
        const Icon = t.icon;
        const active = isTabActive(t, pathname);
        return (
          <Link
            key={t.href}
            href={t.href}
            onClick={onNavigate}
            className={`flex items-center gap-2.5 rounded-md px-3 py-2.5 transition-colors ${
              active
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span className="text-sm">{t.label}</span>
          </Link>
        );
      })}
      <div className="mt-1 border-t border-border pt-2">
        <Link
          href="/whatsapp/connect"
          onClick={onNavigate}
          className="flex items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-2.5 text-sm font-semibold text-primary-foreground hover:opacity-90"
        >
          <AppIcon name="Plus" className="h-4 w-4 shrink-0" />
          Connect a number
        </Link>
      </div>
    </div>
  );
}

export default function WhatsAppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { isMobile } = useViewMode();
  const { open: openDrawer, close: closeDrawer } = useMobileDrawer();
  const isActive = (t: Tab) => isTabActive(t, pathname);

  // The shell's "Sections" bottom-nav tab dispatches "wa-sections"; open the
  // section list in the slide-up drawer. Rebuilt on each open so its active
  // row reflects the current route.
  useEffect(() => {
    const handler = (e: Event) => {
      if ((e as CustomEvent<string>).detail !== "wa-sections") return;
      openDrawer(
        <SectionNavDrawer pathname={pathname} onNavigate={closeDrawer} />
      );
    };
    window.addEventListener("cc-mobile-nav", handler);
    return () => window.removeEventListener("cc-mobile-nav", handler);
  }, [openDrawer, closeDrawer, pathname]);

  // ── Mobile: no in-page chrome; the section nav lives in the bottom drawer ─
  if (isMobile) {
    return (
      <div className="flex h-full min-h-0 w-full flex-col bg-background text-foreground">
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </div>
    );
  }

  // ── Desktop: persistent left column ─────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 bg-background text-foreground">
      <aside className="flex w-14 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground md:w-52">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-sidebar-border px-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary/15 text-primary">
            <AppIcon name="MessageSquare" className="h-3.5 w-3.5" />
          </span>
          <span className="hidden text-[13px] font-semibold md:block">WhatsApp</span>
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = isActive(t);
            return (
              <Link
                key={t.href}
                href={t.href}
                title={t.label}
                className={`flex items-center justify-center gap-2.5 rounded-lg px-2.5 py-2 text-[12.5px] transition md:justify-start ${
                  active
                    ? "bg-primary/15 font-semibold text-primary"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                }`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="hidden md:inline">{t.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="shrink-0 border-t border-sidebar-border p-2">
          <Link
            href="/whatsapp/connect"
            title="Connect a number"
            className="flex items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-[12px] font-semibold text-primary-foreground hover:opacity-90"
          >
            <AppIcon name="Plus" className="h-3.5 w-3.5 shrink-0" />
            <span className="hidden md:inline">Connect</span>
          </Link>
        </div>
      </aside>

      <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
    </div>
  );
}
