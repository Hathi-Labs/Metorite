"use client";

/**
 * AppShell — responsive application shell.
 *
 *   • Desktop (or "Request desktop" on a phone): the classic persistent Sidebar
 *     alongside the scrollable main content area.
 *   • Mobile: a minimal top bar (hamburger + centered title + overflow menu),
 *     and a unified slide-in drawer.  Child pages inject their own content
 *     (e.g. conversation list) into the drawer via useMobileDrawer().
 *
 * Layout decisions come from useViewMode(); component-level tweaks elsewhere can
 * rely on plain Tailwind responsive prefixes (kept in sync via the viewport meta).
 */

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon, type ThemedIcon } from "@/components/Icon";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useSession } from "next-auth/react";
import { useChatScope, useChatSignOutClear } from "@/hooks/useChatSessions";
import Sidebar from "@/components/Sidebar";
import { useViewMode } from "@/components/ViewModeProvider";
import { useRunActivity } from "@/hooks/useActiveSessions";
import NavBadge from "@/components/NavBadge";
import { runningLabel } from "@/lib/runActivity";
import { bindIdentity } from "@/lib/dataCache";
import { isChromeless, visibleSections } from "@/lib/nav";
import AccessGate from "@/components/AccessGate";
import AccountStateBanner from "@/components/AccountStateBanner";
import WelcomeDialog from "@/components/WelcomeDialog";
import { useAccess } from "@/components/AccessProvider";
import { ThemeToggleMenuItem } from "@/components/ThemeToggle";
import { DrawerAccountFoot, DrawerAccountHeader } from "@/components/AccountSwitcher";
import { useAccountTabSync } from "@/lib/accountSwitch";
import { ShellFrame } from "@/lib/shell/ShellBar";
import { OPEN_COMMAND_BAR, shellBarOn } from "@/lib/shell/registry";
import AppLauncher from "@/lib/shell/AppLauncher";
import {
  accountLinks,
  desktopFrame,
  homePane,
  isActive,
  launcherGroups,
  shellNavOn,
  shellSidebar,
} from "@/lib/shell/shellNav";
import { shouldPollWorkspace } from "@/lib/access";
import OrgBrandLockup from "@/components/OrgBrandLockup";
import { SidebarFoldButton, SidebarFoldProvider } from "@/components/SidebarFold";
// The task manager's Focus Mode session (room + minimizable timer dock). Lives
// in the SHELL so the running timer stays visible across every app in the
// control plane; renders nothing when no focus session is active.
import { FocusSession } from "@/app/tasks/components/FocusMode";
import { SubtaskPromptHost } from "@/app/tasks/components/SubtaskPromptHost";
// The note-taker's live recording dock — same shell-level pattern, so an
// in-progress meeting recording follows the user across every app (spec §5.2).
import { LiveDock } from "@/app/notes/components/LiveDock";
import { RecordingDock } from "@/app/notes/components/RecordingDock";
// ---------------------------------------------------------------------------
// Mobile drawer context — lets child pages inject content into the hamburger
// drawer without AppShell needing to know about sessions or filters.
// ---------------------------------------------------------------------------

type MobileDrawerCtx = {
  /** True when the drawer is currently open. */
  isOpen: boolean;
  /** Open the drawer with the given React content. */
  open: (content: ReactNode) => void;
  /** Close the drawer. */
  close: () => void;
};

const MobileDrawerCtx = createContext<MobileDrawerCtx>({
  isOpen: false,
  open: () => {},
  close: () => {},
});

export function useMobileDrawer(): MobileDrawerCtx {
  return useContext(MobileDrawerCtx);
}

// ---------------------------------------------------------------------------
// AppShell
// ---------------------------------------------------------------------------

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { isMobile, isNarrow, forceDesktop, toggleView } = useViewMode();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerContent, setDrawerContent] = useState<ReactNode>(null);
  const pathname = usePathname();
  const { loading: accessLoading } = useAccess();

  /**
   * ⚠️ Bind the read cache to whoever is signed in.
   *
   * `src/lib/dataCache.ts` keys cached rows by request path, and a path says
   * nothing about who asked for it. Two members on one browser therefore share
   * every key, so an unbound cache would serve the second one the first one's
   * rows — from memory, below the gateway, somewhere row-level security cannot
   * reach.
   *
   * Bound HERE because this shell wraps every app and already holds the
   * session. Beside the sign-out buttons would be wrong: there are two of them
   * today, and the guard has to hold for the ones nobody has written yet.
   */
  const { data: cacheSession } = useSession();
  const signedInAs = cacheSession?.user?.email ?? null;
  useEffect(() => {
    bindIdentity(signedInAs);
  }, [signedInAs]);
  // ⚠️ A switch (or a sign-in as someone else) in ANOTHER tab changes the
  // cookie under this one. This reloads the tab, so it never writes as an
  // account it does not show (`lib/accountSwitch.ts`).
  useAccountTabSync(signedInAs);
  // The chat caches follow the same rule (PR #652): one namespace per account.
  // `useChatScope` binds the member's namespace, and a switch deletes nothing.
  // `useChatSignOutClear` clears the namespace of an account that signed out,
  // by any path, never on a sign-out button alone.
  useChatScope();
  useChatSignOutClear();

  // The shell nav flag, read once as the Sidebar reads it: the dev override
  // must not flip mid-session, and the two must agree on the frame.
  const [navOn] = useState(() => shellNavOn());

  const openDrawer = useCallback((content: ReactNode) => {
    setDrawerContent(content);
    setDrawerOpen(true);
  }, []);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);

  // ── Onboarding routes carry NO application chrome ────────────────────────
  // Sign-in and sign-up are the doorway, not the house: the sidebar, docks
  // and bottom nav all assert "you are inside a workspace", which is exactly
  // what someone on these pages is not yet (owner directive 2026-08-24 — the
  // signup form rendered beside the full sidebar). One bare main, both form
  // factors.
  if (isChromeless(pathname ?? "")) {
    return (
      <main className="h-screen overflow-auto bg-background">{children}</main>
    );
  }

  // ── Hold the door until access resolves (LS-4 §8.1, whole-shell form) ────
  // The nav already obeys "an unresolved viewer sees nothing, never
  // everything" — but the SHELL didn't: during the resolve round-trip it
  // painted the skeleton sidebar and the page body, so an org-less sign-in
  // flashed a working-looking app before snapping to the org-less card
  // (owner report, 2026-08-24). Until the first resolution lands, show a
  // neutral holding state that asserts nothing about what this person can
  // reach. `loading` is true only until the FIRST resolve (AccessProvider
  // keeps it false across refresh()), so this never strobes afterwards.
  if (accessLoading) {
    // Owner feedback (2026-08-24, r2): the first cut was two faint pulsing
    // blocks, which read as "nothing happening" — the opposite of what a hold
    // screen owes the person waiting. Say it: spinner + sentence. Still
    // neutral about WHAT loads (workspace, org-less card, denial) — the word
    // "workspace" here means "your view of Metorite", not a resolved org.
    return (
      <div
        className="flex h-screen flex-col items-center justify-center gap-3 bg-background"
        aria-busy="true"
        aria-live="polite"
      >
        <AppIcon
          name="Loader2"
          size={24}
          className="animate-spin text-muted-foreground"
        />
        <p className="text-sm text-muted-foreground">
          Loading your workspace…
        </p>
      </div>
    );
  }

  // ── Desktop layout ───────────────────────────────────────────────────────
  if (!isMobile) {
    // Three frames (`desktopFrame`, fenced in `shellNav.test.ts`). `full` is
    // the owner's of 2026-10-09: one bar across the whole width, then the
    // sidebar and the page under it. The other two are exactly as before.
    const frame = desktopFrame(shellBarOn(), navOn);
    return (
      <SidebarFoldProvider placement={frame === "full" ? "bar" : "rail"}>
      <div className={frame === "full" ? "flex h-screen flex-col overflow-hidden" : "flex h-screen overflow-hidden"}>
        {frame === "full" ? (
          <ShellFrame lead={<BarBrand />}>
            <div className="flex min-h-0 flex-1">
              <Sidebar />
              <main className="min-h-0 min-w-0 flex-1 overflow-auto">
                <AccountStateBanner />
                <AccessGate>{children}</AccessGate>
              </main>
            </div>
          </ShellFrame>
        ) : (
          <Sidebar />
        )}
        {/* CP-2j: the banner sits INSIDE the scrolling column, above the page,
            so it scrolls away rather than eating vertical space on every
            screen. It renders nothing for an `active` org, which is the
            overwhelmingly common case. */}
        {/* NS-1: with the shell bar on, one row across the top holds the
            app's name, the command bar and the app's tools. Off, the page
            column is exactly as it was. */}
        {frame === "column" ? (
          <div className="flex min-w-0 flex-1 flex-col">
            <ShellFrame>
              <main className="min-h-0 min-w-0 flex-1 overflow-auto">
                <AccountStateBanner />
                <AccessGate>{children}</AccessGate>
              </main>
            </ShellFrame>
          </div>
        ) : frame === "classic" ? (
          <main className="flex-1 min-w-0 overflow-auto">
            <AccountStateBanner />
            <AccessGate>{children}</AccessGate>
          </main>
        ) : null}
        <WelcomeDialog />
        <FocusSession />
        {/* D-PM-38 (S5) — the store's subtask question. Global, like the
            store: Focus Mode and the Calendar complete tasks too. */}
        <SubtaskPromptHost />
        <RecordingDock />
        <LiveDock />

        {/* Floating "Mobile view" pill — only when desktop is forced on a phone. */}
        {isNarrow && forceDesktop && (
          <button
            onClick={toggleView}
            className="fixed bottom-4 right-4 z-[60] flex items-center gap-1.5 rounded-full border border-border bg-popover/95 px-3 py-2 text-xs text-muted-foreground shadow-lg backdrop-blur hover:border-primary/50 tech-transition"
          >
            <AppIcon name="Smartphone" size={14} />
            Mobile view
          </button>
        )}
      </div>
      </SidebarFoldProvider>
    );
  }

  // ── Mobile layout ────────────────────────────────────────────────────────
  return (
    <MobileDrawerCtx.Provider
      value={{ isOpen: drawerOpen, open: openDrawer, close: closeDrawer }}
    >
      {/* No top app bar on mobile — every screen is reachable from the bottom
          nav, so pages get the full viewport. pt-safe on the shell keeps
          content out of the notch/status bar. */}
      <div className="flex flex-col overflow-hidden bg-background pt-safe" style={{ height: "100dvh" }}>
        {/* Page content — pb-nav reserves the fixed bottom bar's FULL height
            (content + safe-area inset), so nothing hides under it */}
        {shellBarOn() ? (
          // The same command bar, and its keys, with no row on the phone.
          <ShellFrame bar={false}>
            <main className="flex-1 min-h-0 overflow-y-auto pb-nav">
              <AccountStateBanner />
              <AccessGate>{children}</AccessGate>
            </main>
          </ShellFrame>
        ) : (
          <main className="flex-1 min-h-0 overflow-y-auto pb-nav">
            <AccountStateBanner />
            <AccessGate>{children}</AccessGate>
          </main>
        )}
        <WelcomeDialog />

        {/* Bottom navigation bar — fixed at viewport bottom, never scrolls. pb-safe lifts it above the iOS home indicator */}
        <div className="fixed bottom-0 inset-x-0 z-50 border-t border-border bg-card/90 backdrop-blur pb-safe">
          <MobileBottomNavInner pathname={pathname} toggleView={toggleView} />
        </div>

        {/* Focus Mode session: full-screen room, or — minimized — a compact
            timer strip that extends the bottom bar upward. */}
        <FocusSession />
        <SubtaskPromptHost />
        {/* Live recording dock — sits above the bottom nav (and above the Focus
            pill when both are up), so the menu bar never clips it. */}
        <RecordingDock />
        {/* Server-side "live now" presence (bot calls / other devices). */}
        <LiveDock />

        {/* Unified drawer (slide-up panel for bottom-nav tab content) */}
        {drawerOpen && (
          <div className="fixed inset-0 z-[70]">
            <div
              className="absolute inset-0 bg-black/60"
              onClick={closeDrawer}
            />
            {/* Solid, not glass: the page's words read through a translucent
                sheet and made every menu look broken (owner, 2026-10-09). */}
            <aside className="absolute inset-x-0 bottom-0 flex max-h-[85%] flex-col rounded-t-2xl border-t border-border bg-card shadow-2xl chat-fade-in">
              {/* Drag handle */}
              <div className="flex justify-center pt-2 pb-1">
                <div className="w-10 h-1 rounded-full bg-muted-foreground/30" />
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto pb-safe">
                {drawerContent}
              </div>
            </aside>
          </div>
        )}
      </div>
    </MobileDrawerCtx.Provider>
  );
}

// ---------------------------------------------------------------------------
// The full-width bar's first zone: the fold control and the logo
// ---------------------------------------------------------------------------

/**
 * The owner's words, 2026-10-09: "have the top bar extend the full width and
 * also always have the logo of the organization over there at the top. That
 * way, even when the leftmost sidebar is minimized, we'll always see the logo."
 *
 * So the zone is `w-64`, the open sidebar's width, and it KEEPS that width
 * when the sidebar folds. Open, the zone and the rail read as one column.
 * Folded, the logo stays where it was, and only the rail under it narrows.
 *
 * The fold control shows only when a sidebar does. A person with no
 * organization gets no sidebar (`Sidebar`'s `canPoll`), so a control that
 * folds nothing would be a broken button.
 */
function BarBrand() {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  return (
    <div data-shell-brand className="flex w-64 shrink-0 items-center gap-1.5 pl-2 pr-2">
      {workspace ? <SidebarFoldButton /> : null}
      {/* 24px tall, one line: a 44px bar has no room for the sidebar's stack.
          200px is the zone less its padding and the control. */}
      <OrgBrandLockup fallbackCaption="Control Plane" height={24} maxWidth={200} compact />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mobile bottom navigation bar — ChatGPT/DeepSeek-style 3-tab bar
// ---------------------------------------------------------------------------

function MobileBottomNavInner({
  pathname,
  toggleView,
}: {
  pathname: string | null;
  toggleView: () => void;
}) {
  const { isOpen, open, close } = useMobileDrawer();
  // Same access filter as the desktop Sidebar — the two navs must agree, or a
  // pane hidden on desktop reappears in the phone drawer. Same unresolved rule
  // too: `null` yields nothing and the skeleton below holds the space.
  const { access, loading: accessLoading } = useAccess();
  const navSections = visibleSections(
    accessLoading ? null : access.features,
    access.is_admin,
  );
  // The shell nav (NS-2): the drawer takes the sidebar's shape (§9).
  const [shellNav] = useState(() => shellNavOn());
  // "Home", or "My Day" with that flag on (NS-3). Read once, as above.
  // ⚠️ Read in `useState`, as `shellNavOn` is: with the dev-only
  // `localStorage` override the server and the browser can disagree, and
  // React warns once. Production reads the build-time flag on both sides,
  // so it never disagrees. `app/page.tsx` needs `useSyncExternalStore`
  // because it swaps the whole page; a label here does not.
  const [home] = useState(() => homePane());
  const [launcherOpen, setLauncherOpen] = useState(false);
  const drawerSections = shellNav ? shellSidebar(navSections) : navSections;
  // The run badge (WS-51 S1), from the one shared poller. Each drawer link
  // shows its app's count. The bottom bar shows the total on every page: on
  // the Chats tab on /chat, and on the Menu tab everywhere else, because the
  // Chats tab exists on /chat only.
  const drawerKey = drawerSections.map((s) => s.items.map((p) => p.href).join(",")).join("|");
  const drawerHrefs = useMemo(
    () => new Set(drawerKey.split(/[|,]/).filter(Boolean)),
    [drawerKey],
  );
  const { total: activeCount, byApp: runCounts } = useRunActivity(drawerHrefs);

  const menuContent = (
    <>
      {/* The header is the account surface (owner, 2026-10-09): the mark and
          the organization, the signed-in address under it, and a tap opens
          the other accounts and the account actions. */}
      <DrawerAccountHeader onClose={close} />
      {/* NS-1 on the phone: the one search. */}
      {shellBarOn() ? (
        <div className="border-b border-border px-3 py-2">
          <button
            type="button"
            onClick={() => {
              close();
              window.dispatchEvent(new CustomEvent(OPEN_COMMAND_BAR, { detail: { query: "" } }));
            }}
            className="flex h-10 w-full items-center gap-2 rounded-lg border border-border bg-background px-3 text-left text-sm text-muted-foreground"
          >
            <AppIcon name="Sparkles" size={16} className="shrink-0 text-primary" />
            Search or ask anything
          </button>
        </div>
      ) : null}
      <nav className="flex flex-col overflow-y-auto">
        {/* Same rule as the desktop rail (§8.1): an unresolved viewer gets
            placeholders, never the full list. The drawer opens on tap, so a
            list that rearranges under the thumb is worse here than on desktop. */}
        {accessLoading ? (
          <div className="px-2 py-3" aria-hidden data-testid="nav-skeleton">
            {[0, 1, 2, 3, 4, 5].map((row) => (
              <div key={row} className="mb-1 flex items-center gap-2.5 px-3 py-2.5">
                <div className="h-7 w-7 shrink-0 animate-pulse rounded-lg bg-secondary" />
                <div className="h-3 flex-1 animate-pulse rounded bg-secondary" />
              </div>
            ))}
          </div>
        ) : null}
        {shellNav && !accessLoading && (
          <div className="px-2 pt-2">
            <Link
              href={home.href}
              onClick={close}
              className={`rounded-lg px-3 py-2.5 tech-transition flex items-center gap-2.5 ${
                isActive(pathname, home.href)
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground"
              }`}
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground">
                <AppIcon name={home.icon} size={15} />
              </span>
              <span className="text-sm font-medium">{home.label}</span>
            </Link>
          </div>
        )}
        {drawerSections.map((section) => (
          <div key={section.id} className="px-2 pt-1 pb-1.5">
            <div
              className={
                section.sub
                  ? "px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60"
                  : "px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground"
              }
            >
              {section.label}
            </div>
            <div className="flex flex-col gap-0.5">
              {section.items.map((p) => {
                const active = shellNav ? isActive(pathname, p.href) : pathname?.startsWith(p.href);
                return (
                  <Link
                    key={p.href}
                    href={p.href}
                    onClick={close}
                    className={`rounded-lg px-3 py-2.5 tech-transition flex items-center gap-2.5 ${
                      active
                        ? "bg-primary/10 text-primary"
                        : "text-muted-foreground hover:bg-secondary hover:text-foreground"
                    }`}
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground">
                      <AppIcon name={p.icon} size={15} strokeWidth={active ? 2.5 : 2} />
                    </span>
                    <span className="text-sm font-medium">{p.label}</span>
                    <NavBadge
                      count={runCounts[p.href] ?? 0}
                      tone="success"
                      label={runningLabel(runCounts[p.href] ?? 0)}
                    />
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
        {/* All apps (NS-2), only when it would list something. The drawer
            closes first, so one overlay shows. */}
        {shellNav && !accessLoading && launcherGroups(navSections).length > 0 && (
          <div className="px-2 pb-2">
            <button
              type="button"
              onClick={() => {
                close();
                setLauncherOpen(true);
              }}
              className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-muted-foreground hover:bg-secondary hover:text-foreground tech-transition"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground">
                <AppIcon name="LayoutGrid" size={15} />
              </span>
              <span className="text-sm font-medium">All apps</span>
            </button>
          </div>
        )}
      </nav>
      {/* The rare acts, after the apps: the member's pages, the settings and
          sign-out, in one list (owner review, 2026-10-09). */}
      <div className="mt-auto border-t border-border px-2 py-3">
        <DrawerAccountFoot you={shellNav ? accountLinks(navSections) : undefined} onNavigate={close}>
          <ThemeToggleMenuItem onClick={close} />
          <Button variant="ghost" size="none" layout="flex items-center" onClick={() => { toggleView(); close(); }} className="w-full gap-3 px-3 py-2.5 text-sm">
            <AppIcon name="Monitor" size={16} className="shrink-0" />
            Desktop view
          </Button>
        </DrawerAccountFoot>
      </div>
    </>
  );

  const isChatPage = pathname?.startsWith("/chat") ?? false;
  const runningName = runningLabel(activeCount);
  const isEmailPage = pathname?.startsWith("/email") ?? false;
  const isTasksPage = pathname?.startsWith("/tasks") ?? false;
  // Notes actions live on the library page; the meeting/session sub-pages have
  // their own in-view controls, so scope the context tabs to the list.
  const isNotesPage = pathname === "/notes";
  // WhatsApp: "Sections" (the app sub-nav) applies across every /whatsapp page;
  // "Triage" (the stream filter) is inbox-only. Both open bottom drawers.
  const isWhatsAppPage = pathname?.startsWith("/whatsapp") ?? false;
  const isWhatsAppInbox = pathname === "/whatsapp";
  // App Workshop editor: chat and the preview/code/tests pane are full-screen
  // alternatives on mobile (desktop shows both side by side) — the page owns
  // the "workshop-*" cc-mobile-nav detail values.
  const isAppWorkshopEditPage =
    (pathname?.startsWith("/build/apps/") && pathname?.endsWith("/edit")) ?? false;
  // Projects: three things the desktop layout owns have no home on a phone —
  // the project tree (a 240px rail), the five view modes (a toolbar row) and
  // ⌘K search (a keyboard). Each becomes a tab here; the page turns the first
  // two into drawer sheets and the third into its own overlay. Notifications
  // stay in the page's own title row: the bell is self-anchored and has no
  // external open control, so a tab could not raise it.
  const isProjectsPage = pathname?.startsWith("/projects") ?? false;

  // Tasks: the bottom bar reflects which My Tasks section you're in. The page emits
  // `cc-tasks-section` whenever the active view changes.
  const [tasksSection, setTasksSection] = useState("inbox");
  useEffect(() => {
    const h = (e: Event) =>
      setTasksSection((e as CustomEvent<string>).detail || "inbox");
    window.addEventListener("cc-tasks-section", h);
    return () => window.removeEventListener("cc-tasks-section", h);
  }, []);

  // Email: with no mailbox the page shows only "Connect your email", so the
  // Inbox / Automation / AI Chat tabs would open empty sheets. The page emits
  // `cc-email-empty` (true while the empty state shows, false otherwise and on
  // unmount), and the tabs hide while it is true (WS-17 EM-T3b).
  const [emailEmpty, setEmailEmpty] = useState(false);
  useEffect(() => {
    const h = (e: Event) => setEmailEmpty((e as CustomEvent<boolean>).detail === true);
    window.addEventListener("cc-email-empty", h);
    return () => window.removeEventListener("cc-email-empty", h);
  }, []);

  const dispatchNav = (detail: string) => {
    window.dispatchEvent(new CustomEvent("cc-mobile-nav", { detail }));
  };

  return (
    <>
    <nav className="flex items-stretch justify-around gap-0.5 py-1 px-1">
        <button
          onClick={() => { open(menuContent); }}
          className={`relative flex flex-1 min-w-0 flex-col items-center gap-0.5 px-1 py-1 rounded-lg transition-colors ${
            isOpen ? "text-primary" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <AppIcon name="Menu" size={20} />
          {!isChatPage && (
            <NavBadge count={activeCount} tone="success" label={runningName} placement="corner" className="right-2" />
          )}
          <span className="text-[10px] font-medium leading-none">Menu</span>
        </button>
        {isEmailPage && !emailEmpty && (
          <>
            <Button variant="text" size="none" layout="flex items-center" onClick={() => dispatchNav("email-accounts")} className="flex-1 min-w-0 flex-col gap-0.5 px-1 py-1">
              <AppIcon name="Mail" size={20} />
              <span className="text-[10px] font-medium leading-none">Inbox</span>
            </Button>
            <Button variant="text" size="none" layout="flex items-center" onClick={() => dispatchNav("email-automation")} className="flex-1 min-w-0 flex-col gap-0.5 px-1 py-1">
              <AppIcon name="Zap" size={20} />
              <span className="text-[10px] font-medium leading-none">Automation</span>
            </Button>
            <Button variant="text" size="none" layout="flex items-center" onClick={() => dispatchNav("email-ai")} className="flex-1 min-w-0 flex-col gap-0.5 px-1 py-1">
              <AppIcon name="MessageCircle" size={20} />
              <span className="text-[10px] font-medium leading-none">AI Chat</span>
            </Button>
          </>
        )}
        {isChatPage && (
          <>
            <Button variant="text" size="none" layout="flex items-center" onClick={() => dispatchNav("chats")} className="relative flex-1 min-w-0 flex-col gap-0.5 px-1 py-1">
              <AppIcon name="MessageCircle" size={20} />
              <NavBadge count={activeCount} tone="success" label={runningName} placement="corner" className="right-2" />
              <span className="text-[10px] font-medium leading-none">Chats</span>
            </Button>
            <Button variant="text" size="none" layout="flex items-center" onClick={() => dispatchNav("files")} className="flex-1 min-w-0 flex-col gap-0.5 px-1 py-1">
              <AppIcon name="FolderOpen" size={20} />
              <span className="text-[10px] font-medium leading-none">Files</span>
            </Button>
          </>
        )}
        {isTasksPage && (
          <>
            <TaskTab
              active={tasksSection === "inbox"}
              onClick={() => dispatchNav("tasks-inbox")}
              icon={themedIcon("Inbox")}
              label="Inbox"
            />
            <TaskTab
              active={tasksSection !== "inbox"}
              onClick={() => dispatchNav("tasks-lists")}
              icon={themedIcon("ListChecks")}
              label="Lists"
            />
            <TaskTab
              onClick={() => dispatchNav("tasks-capture")}
              icon={themedIcon("Plus")}
              label="Capture"
              accent
            />
            <TaskTab
              onClick={() => dispatchNav("tasks-assistant")}
              icon={themedIcon("Sparkles")}
              label="Assistant"
            />
          </>
        )}
        {isProjectsPage && (
          <>
            <TaskTab
              onClick={() => dispatchNav("projects-tree")}
              icon={themedIcon("FolderKanban")}
              label="Projects"
            />
            <TaskTab
              onClick={() => dispatchNav("projects-views")}
              icon={themedIcon("LayoutGrid")}
              label="Views"
            />
            <TaskTab
              onClick={() => dispatchNav("projects-search")}
              icon={themedIcon("Search")}
              label="Search"
            />
          </>
        )}
        {isNotesPage && (
          <>
            <TaskTab
              onClick={() => dispatchNav("notes-record")}
              icon={themedIcon("Mic")}
              label="Record"
              accent
            />
            <TaskTab
              onClick={() => dispatchNav("notes-join")}
              icon={themedIcon("Video")}
              label="Join call"
            />
            <TaskTab
              onClick={() => dispatchNav("notes-upload")}
              icon={themedIcon("Upload")}
              label="Upload"
            />
            <TaskTab
              onClick={() => dispatchNav("notes-glossary")}
              icon={themedIcon("BookMarked")}
              label="Glossary"
            />
          </>
        )}
        {isWhatsAppInbox && (
          <TaskTab
            onClick={() => dispatchNav("wa-triage")}
            icon={themedIcon("Filter")}
            label="Triage"
          />
        )}
        {isWhatsAppPage && (
          <TaskTab
            onClick={() => dispatchNav("wa-sections")}
            icon={themedIcon("LayoutGrid")}
            label="Sections"
          />
        )}
        {isAppWorkshopEditPage && (
          <>
            <TaskTab
              onClick={() => dispatchNav("workshop-chat")}
              icon={themedIcon("Sparkles")}
              label="Chat"
              accent
            />
            <TaskTab
              onClick={() => dispatchNav("workshop-preview")}
              icon={themedIcon("Play")}
              label="Preview"
            />
            <TaskTab
              onClick={() => dispatchNav("workshop-code")}
              icon={themedIcon("FileCode")}
              label="Code"
            />
            <TaskTab
              onClick={() => dispatchNav("workshop-tests")}
              icon={themedIcon("FlaskConical")}
              label="Tests"
            />
          </>
        )}
    </nav>
    {shellNav && (
      <AppLauncher
        open={launcherOpen}
        onClose={() => setLauncherOpen(false)}
        sections={navSections}
        pathname={pathname}
      />
    )}
    </>
  );
}

function TaskTab({
  active,
  onClick,
  icon: Icon,
  label,
  accent,
}: {
  active?: boolean;
  onClick: () => void;
  icon: ThemedIcon;
  label: string;
  accent?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex flex-1 min-w-0 flex-col items-center gap-0.5 rounded-lg px-1 py-1 transition-colors ${
        accent
          ? "text-primary"
          : active
            ? "text-primary"
            : "text-muted-foreground hover:text-foreground"
      }`}
    >
      <Icon size={20} strokeWidth={active || accent ? 2.4 : 2} />
      <span className="text-[10px] font-medium leading-none">{label}</span>
    </button>
  );
}
