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
import OrgBrandLockup from "@/components/OrgBrandLockup";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useSession, signOut } from "next-auth/react";
import Sidebar from "@/components/Sidebar";
import { useViewMode } from "@/components/ViewModeProvider";
import { useActiveSessions } from "@/hooks/useActiveSessions";
import { isChromeless, visibleSections } from "@/lib/nav";
import AccessGate from "@/components/AccessGate";
import WelcomeDialog from "@/components/WelcomeDialog";
import { useAccess } from "@/components/AccessProvider";
import { ThemeToggleMenuItem } from "@/components/ThemeToggle";
// The task manager's Focus Mode session (room + minimizable timer dock). Lives
// in the SHELL so the running timer stays visible across every app in the
// control plane; renders nothing when no focus session is active.
import { FocusSession } from "@/app/tasks/components/FocusMode";
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
    return (
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="flex-1 min-w-0 overflow-auto">
          <AccessGate>{children}</AccessGate>
        </main>
        <WelcomeDialog />
        <FocusSession />
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
        <main className="flex-1 min-h-0 overflow-y-auto pb-nav">
          <AccessGate>{children}</AccessGate>
        </main>
        <WelcomeDialog />

        {/* Bottom navigation bar — fixed at viewport bottom, never scrolls. pb-safe lifts it above the iOS home indicator */}
        <div className="fixed bottom-0 inset-x-0 z-50 border-t border-border bg-card/90 backdrop-blur pb-safe">
          <MobileBottomNavInner pathname={pathname} toggleView={toggleView} />
        </div>

        {/* Focus Mode session: full-screen room, or — minimized — a compact
            timer strip that extends the bottom bar upward. */}
        <FocusSession />
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
            <aside className="absolute inset-x-0 bottom-0 flex max-h-[85%] flex-col rounded-t-2xl border-t border-border bg-card shadow-2xl chat-fade-in tech-glass-subtle">
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
  const { data: session } = useSession();
  const activeRunIds = useActiveSessions();
  const activeCount = activeRunIds.size;
  // Same access filter as the desktop Sidebar — the two navs must agree, or a
  // pane hidden on desktop reappears in the phone drawer. Same unresolved rule
  // too: `null` yields nothing and the skeleton below holds the space.
  const { access, loading: accessLoading } = useAccess();
  const navSections = visibleSections(
    accessLoading ? null : access.features,
    access.is_admin,
  );

  const menuContent = (
    <>
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        {/* Branding doubles as the way home — the mobile shell has no top bar.
            Same component as the desktop sidebar on purpose: a brand mark that
            differs by window width is worse than no brand mark. */}
        <OrgBrandLockup fallbackCaption="Home" onNavigate={close} maxWidth={180} />
        <button
          onClick={close}
          className="rounded-lg p-1.5 text-muted-foreground hover:bg-secondary"
          aria-label="Close"
        >
          <AppIcon name="X" size={16} />
        </button>
      </div>
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
        {navSections.map((section) => (
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
                const active = pathname?.startsWith(p.href);
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
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="mt-auto border-t border-border p-3 space-y-2">
        <ThemeToggleMenuItem onClick={close} />
        <Button variant="ghost" size="none" layout="flex items-center" onClick={() => { toggleView(); close(); }} className="w-full gap-3 px-3 py-2.5 text-sm">
          <AppIcon name="Monitor" size={16} className="shrink-0" />
          Desktop view
        </Button>
        {session?.user && (
          <Button variant="ghost" size="none" layout="flex items-center" onClick={() => signOut({ callbackUrl: "/signin" })} className="w-full gap-3 px-3 py-2.5 text-sm">
            <AppIcon name="LogOut" size={16} className="shrink-0" />
            Sign out
          </Button>
        )}
        {session?.user && (
          <div className="px-3 pt-1">
            <div className="truncate text-[11px] font-medium text-muted-foreground">
              {session.user.name ?? session.user.email}
            </div>
          </div>
        )}
      </div>
    </>
  );

  const isChatPage = pathname?.startsWith("/chat") ?? false;
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

  // Tasks: the bottom bar reflects which GTD section you're in. The page emits
  // `cc-tasks-section` whenever the active view changes.
  const [tasksSection, setTasksSection] = useState("inbox");
  useEffect(() => {
    const h = (e: Event) =>
      setTasksSection((e as CustomEvent<string>).detail || "inbox");
    window.addEventListener("cc-tasks-section", h);
    return () => window.removeEventListener("cc-tasks-section", h);
  }, []);

  const dispatchNav = (detail: string) => {
    window.dispatchEvent(new CustomEvent("cc-mobile-nav", { detail }));
  };

  return (
    <nav className="flex items-stretch justify-around gap-0.5 py-1 px-1">
        <button
          onClick={() => { open(menuContent); }}
          className={`flex flex-1 min-w-0 flex-col items-center gap-0.5 px-1 py-1 rounded-lg transition-colors ${
            isOpen ? "text-primary" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <AppIcon name="Menu" size={20} />
          <span className="text-[10px] font-medium leading-none">Menu</span>
        </button>
        {isEmailPage && (
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
              {activeCount > 0 && (
                <span className="absolute -top-0.5 right-2 flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-success text-success-foreground text-[9px] font-bold animate-pulse">
                  {activeCount}
                </span>
              )}
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
