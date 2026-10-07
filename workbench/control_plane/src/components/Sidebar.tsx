"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type MouseEvent } from "react";
import { useSession, signOut } from "next-auth/react";
import { NAV_SECTIONS, visibleSections, type NavPane, type NavSection } from "@/lib/nav";
import { useAccess } from "@/components/AccessProvider";
import { shouldPollWorkspace } from "@/lib/access";
import {
  HINT_DISMISS_MS,
  autoFoldEnabled,
  floatingOpen,
  isPlainNavClick,
  isWorkEvent,
  readCollapsed,
  setAutoFoldEnabled,
  shouldFold,
  takeHint,
  writeCollapsed,
} from "@/lib/sidebarFold";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import OrgBrandLockup from "@/components/OrgBrandLockup";
import ThemeToggle from "@/components/ThemeToggle";
import { SidebarAccountFooter, useAccounts } from "@/components/AccountSwitcher";

/** Mirrors gateway/routes/apps/pins.py's PinnedApp — GET /api/apps/pins. */
type PinnedApp = { slug: string; name: string; icon?: string };

// ---------------------------------------------------------------------------
// Sidebar — sectioned navigation (Apps / Configure / Build)
// RapidTool design language — refined dark theme with blue primary accent.
// ---------------------------------------------------------------------------

export default function Sidebar() {
  const pathname = usePathname();
  // ⚠️ Read in the initializer, not in an effect. AppShell mounts this rail
  // only after access resolves, on the client, so there is no server markup to
  // disagree with. An effect would draw the open rail and then animate it shut
  // on every reload.
  const [collapsed, setCollapsedState] = useState(() =>
    typeof window === "undefined" ? false : readCollapsed(),
  );
  // The fold while you work (`lib/sidebarFold.ts`). `armed` is a ref because
  // arming must not re-render, and the document listener reads it live.
  const asideRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const armedRef = useRef(false);
  // `beacon` counts folds and keys the button, so a second fold restarts the
  // pulse. `pulsing` says whether this fold's pulse still runs. It ends by
  // itself, and on the member's own toggle, so a later manual collapse does
  // not pulse and the reduced-motion tint does not stay.
  const [beacon, setBeacon] = useState(0);
  const [pulsing, setPulsing] = useState(false);
  const [tipOpen, setTipOpen] = useState(false);
  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedState(next);
    writeCollapsed(next);
  }, []);
  /** The member's own toggle. It wins over the fold until the next app. */
  const toggleByMember = () => {
    armedRef.current = false;
    setTipOpen(false);
    setPulsing(false);
    setCollapsed(!collapsed);
  };
  const armFold = useCallback((e: MouseEvent) => {
    if (isPlainNavClick(e)) armedRef.current = true;
  }, []);
  const { data: session } = useSession();
  // The account switcher (MT-1k A2). Off, `accounts.enabled` is false and the
  // footer below is the one this sidebar always had.
  const { accounts, reload: reloadAccounts } = useAccounts();
  const [agentUpdateCount, setAgentUpdateCount] = useState(0);
  const [pinnedApps, setPinnedApps] = useState<PinnedApp[]>([]);
  // Org access control (spec §5, seam 5): the sidebar shows only what this
  // member can reach, and only what we have launched (`launch_surface.md` §2).
  //
  // `null` while unresolved now yields NO sections, and the skeleton below
  // holds the space. It used to yield the FULL list, on the reasoning that the
  // nav should not visibly shrink on first paint — but showing everything IS
  // the shrink: every sign-in painted the complete application and then removed
  // most of it, for as long as `/api/auth/me` took. That is the reported
  // "sometimes all the apps appear, sometimes they don't" (§8.1).
  const { access, loading: accessLoading } = useAccess();
  const sections = visibleSections(
    accessLoading ? null : access.features,
    access.is_admin,
  );
  /**
   * ONE predicate for "is there a workspace behind this person yet", used by
   * the two polls below AND by the `return null` further down.
   *
   * ⚠️ They were two separate conditions, and the polls simply did not have
   * one — which is how this sidebar came to be hidden and still hammering a
   * tenant-scoped API once a minute. Reading the same function in both places
   * is what stops that shape coming back.
   */
  const canPoll = shouldPollWorkspace(access, accessLoading);

  // Per-section fold state, persisted so the layout survives reloads. Stored
  // as a map of FOLDED ids — unknown/new sections therefore default to open.
  const [foldedSections, setFoldedSections] = useState<Record<string, boolean>>({});
  useEffect(() => {
    try {
      const raw = localStorage.getItem(FOLD_KEY);
      if (raw) setFoldedSections(JSON.parse(raw));
    } catch {
      // Corrupt/unavailable storage — start with everything open.
    }
  }, []);
  const toggleSection = (id: string) =>
    setFoldedSections((prev) => persistFolds({ ...prev, [id]: !prev[id] }));

  // Navigating into a folded section unfolds it (once per navigation — the
  // user can still fold it again while staying on the page).
  useEffect(() => {
    if (!pathname) return;
    setFoldedSections((prev) => {
      const owner = NAV_SECTIONS.find((s) =>
        s.items.some((p) => pathname.startsWith(p.href)),
      );
      if (!owner || !prev[owner.id]) return prev;
      return persistFolds({ ...prev, [owner.id]: false });
    });
  }, [pathname]);

  // Poll agent list for behind_by counts — shows "N updates" badge on Agents
  useEffect(() => {
    // ⚠️ A person with NO organization must not be polled at. The `return null`
    // below hides this whole sidebar for them, and hiding a component does not
    // stop its effects — that is what put a 500 a minute into the production
    // log for nine hours. See `access.shouldPollWorkspace`.
    if (!canPoll) return;
    let alive = true;
    const check = async () => {
      try {
        const res = await fetch("/api/agent/list", {
          signal: AbortSignal.timeout(5000),
        });
        if (!res.ok || !alive) return;
        const agents = await res.json();
        if (!Array.isArray(agents) || !alive) return;
        const count = agents.reduce(
          (sum: number, a: any) =>
            sum + (typeof a.behind_by === "number" && a.behind_by > 0 ? 1 : 0),
          0,
        );
        if (alive) setAgentUpdateCount(count);
      } catch {
        // Gateway down — keep last known count
      }
    };
    check();
    const interval = setInterval(check, 60_000); // refresh every minute
    return () => {
      alive = false;
      clearInterval(interval);
    };
    // ⚠️ `canPoll`, not `[]`. It flips the moment an admin adds this person to
    // an org and they press "check again", and the badges have to start then
    // rather than on the next full reload.
  }, [canPoll]);

  // Pinned Custom Apps — same polling shape as the agent-updates badge above,
  // and gated on the same thing for the same reason.
  useEffect(() => {
    if (!canPoll) return;
    let alive = true;
    const check = async () => {
      try {
        const res = await fetch("/api/apps/pins", {
          signal: AbortSignal.timeout(5000),
        });
        if (!res.ok || !alive) return;
        const pins = await res.json();
        if (alive && Array.isArray(pins)) setPinnedApps(pins);
      } catch {
        // Gateway down — keep last known list
      }
    };
    check();
    const interval = setInterval(check, 60_000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
    // ⚠️ `canPoll`, not `[]`. It flips the moment an admin adds this person to
    // an org and they press "check again", and the badges have to start then
    // rather than on the next full reload.
  }, [canPoll]);

  // Fold on the member's first work inside the app they opened from here.
  // Capture phase, so an app that stops propagation still counts as work.
  useEffect(() => {
    const onWork = (e: Event) => {
      // A script's `.click()` is not the member working.
      if (!armedRef.current || !e.isTrusted) return;
      const target = e.target instanceof Element ? e.target : null;
      if (!target) return;
      const inSidebar = !!asideRef.current?.contains(target);
      const inMain = !!target.closest("main");
      const work = isWorkEvent({
        type: e.type,
        key: (e as KeyboardEvent).key,
        button: (e as globalThis.MouseEvent).button,
      });
      // Work in the app spends the arm, whether or not it folds. A member who
      // chose "Keep it open" must not get a fold three clicks later.
      if (inMain && work && !inSidebar) armedRef.current = false;
      if (!shouldFold({ armed: true, enabled: autoFoldEnabled(), collapsed, inSidebar, inMain, work })) {
        return;
      }
      // One frame later, so the click's own handler has run. Two things need
      // that. If the click opened a menu, the fold waits for the next work
      // (`floatingOpen` says why). And the setting is read AFTER the click, so
      // "Keep it open" on the Appearance page saves before the fold looks.
      requestAnimationFrame(() => {
        if (floatingOpen(document, asideRef.current)) {
          armedRef.current = true;
          return;
        }
        if (!autoFoldEnabled()) return;
        setCollapsed(true);
        setBeacon((n) => n + 1);
        setPulsing(true);
        if (takeHint()) setTipOpen(true);
      });
    };
    document.addEventListener("click", onWork, true);
    document.addEventListener("keydown", onWork, true);
    return () => {
      document.removeEventListener("click", onWork, true);
      document.removeEventListener("keydown", onWork, true);
    };
  }, [collapsed, setCollapsed]);

  // The pulse runs three times (`.sidebar-beacon`, about 3.7s), then ends.
  useEffect(() => {
    if (!pulsing) return;
    const timer = setTimeout(() => setPulsing(false), 4000);
    return () => clearTimeout(timer);
  }, [pulsing, beacon]);

  // The tip closes by itself, and on Escape.
  useEffect(() => {
    if (!tipOpen) return;
    const timer = setTimeout(() => setTipOpen(false), HINT_DISMISS_MS);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setTipOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
    };
  }, [tipOpen]);

  // A signed-in person with NO organization is mid-onboarding, not in a
  // workspace — AccessGate is showing them the join-vs-create chooser, and a
  // sidebar beside it (My Profile, Appearance) narrates a product
  // they have not joined (owner directive 2026-08-24, riding D51.2's "the
  // org is unmistakable": no org, no workspace chrome). Gated on the
  // resolution having LANDED — hiding on `loading` would flash the whole
  // layout on every ordinary sign-in. AFTER every hook, deliberately.
  //
  // ⚠️ The condition used to be spelt out here and NOWHERE ELSE, so the polls
  // above had no equivalent and kept running behind this `null`. Same function
  // now, so "hidden" and "quiet" cannot part company again.
  if (!canPoll) {
    return null;
  }

  return (
    <aside
      ref={asideRef}
      data-collapsed={collapsed ? "true" : "false"}
      // A fold widens <main> but fires no window `resize`, and some layouts
      // measure only on that event. Tell them once the width settles.
      onTransitionEnd={(e) => {
        if (e.target === e.currentTarget && e.propertyName === "width") {
          window.dispatchEvent(new Event("resize"));
        }
      }}
      className={`sidebar-rail shrink-0 border-r flex flex-col overflow-hidden bg-sidebar border-sidebar-border ${
        collapsed ? "w-14" : "w-64"
      }`}
    >
      {/* Header */}
      <div className={`flex items-center border-b border-sidebar-border ${collapsed ? "justify-center p-3" : "justify-between px-4 py-4"}`}>
        {!collapsed && (
          // The customer's mark when they have uploaded one, ours when they
          // have not. `maxWidth` is what stops a wide wordmark from pushing the
          // collapse control off the 256px rail.
          <OrgBrandLockup fallbackCaption="Control Plane" maxWidth={152} />
        )}
        <button
          ref={toggleRef}
          // `key` restarts the pulse on each fold. Zero means no fold yet.
          key={beacon}
          onClick={toggleByMember}
          className={`shrink-0 rounded-lg p-1.5 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground tech-transition ${
            collapsed && pulsing ? "sidebar-beacon" : ""
          }`}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
        >
          {collapsed ? <Icon name="ChevronRight" size={16} /> : <Icon name="ChevronLeft" size={16} />}
        </button>
      </div>
      {tipOpen && collapsed && (
        <FoldTip
          anchorRef={toggleRef}
          onClose={() => setTipOpen(false)}
          onKeepOpen={() => {
            setAutoFoldEnabled(false);
            setTipOpen(false);
            setCollapsed(false);
          }}
        />
      )}

      {/* Nav sections */}
      {/* `scrollbar-thin` is not cosmetic here. Without it this container gets the
          platform's default scrollbar, which on Linux/Windows is ~15px wide with
          stepper arrows — a quarter of the 56px COLLAPSED rail, shoving every
          icon off-centre and painting a light-grey bar down a dark sidebar. The
          themed utility already existed in globals.css; this scroller just never
          opted in. */}
      {/* `key` swaps the whole list on a fold, so the new shape fades in while
          the width moves (`.sidebar-swap`), rather than snapping. */}
      <nav
        key={collapsed ? "rail" : "full"}
        className="sidebar-swap scrollbar-thin flex flex-col flex-1 overflow-y-auto overflow-x-hidden"
      >
        {accessLoading ? (
          <NavSkeleton collapsed={collapsed} />
        ) : (
          sections.map((section) => (
            <NavSectionBlock
              key={section.id}
              section={section}
              pathname={pathname}
              collapsed={collapsed}
              folded={!!foldedSections[section.id]}
              onToggle={() => toggleSection(section.id)}
              onNavigate={armFold}
              agentUpdateCount={agentUpdateCount}
              pinnedApps={pinnedApps}
            />
          ))
        )}
      </nav>

      {/* User / sign-out footer */}
      <SidebarAccountFooter collapsed={collapsed} accounts={accounts} reload={reloadAccounts} />
      {!accounts.enabled && !collapsed && (
        <div className="border-t border-sidebar-border px-4 py-3">
          {session?.user ? (
            <div className="flex items-center justify-between">
              <div className="min-w-0 flex-1">
                <div className="truncate text-[11px] font-medium text-sidebar-foreground/90">
                  {session.user.name ?? session.user.email ?? "Signed in"}
                </div>
                <div className="truncate text-[10px] text-muted-foreground">
                  {session.user.email}
                </div>
              </div>
              <button
                onClick={() => signOut({ callbackUrl: "/signin" })}
                className="ml-2 shrink-0 rounded-lg p-1.5 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground tech-transition"
                title="Sign out"
              >
                <Icon name="LogOut" size={14} />
              </button>
              <ThemeToggle />
            </div>
          ) : session === undefined ? (
            <div className="text-[11px] text-muted-foreground">Loading session…</div>
          ) : (
            <div className="text-[11px] text-muted-foreground">Phase 1 &middot; Self-Mutation Loop</div>
          )}
        </div>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Nav skeleton — what an unresolved viewer sees
// ---------------------------------------------------------------------------

/**
 * Placeholder rows held while access resolves (`launch_surface.md` §8.1).
 *
 * The rail is a fixed width and the sections are a known shape, so the honest
 * thing to draw before we know WHICH panes this member has is the right number
 * of rows in the right places — not a guess at their contents, and not an empty
 * rail that then fills. Nothing here is a link, so there is nothing to click
 * that could turn out not to exist.
 */
function NavSkeleton({ collapsed }: { collapsed: boolean }) {
  // Two groups of four, which is the shape of a typical resolved sidebar.
  return (
    <div className="px-2 py-3" aria-hidden data-testid="nav-skeleton">
      {[0, 1].map((group) => (
        <div key={group} className={group === 0 ? "" : "mt-6"}>
          {!collapsed && (
            <div className="mx-2 mb-2 h-2 w-20 animate-pulse rounded bg-sidebar-accent" />
          )}
          {[0, 1, 2, 3].map((row) => (
            <div
              key={row}
              className={`mb-1 flex items-center gap-2 rounded-lg px-2 py-2 ${
                collapsed ? "justify-center" : ""
              }`}
            >
              <div className="h-4 w-4 shrink-0 animate-pulse rounded bg-sidebar-accent" />
              {!collapsed && (
                <div className="h-2.5 flex-1 animate-pulse rounded bg-sidebar-accent" />
              )}
            </div>
          ))}
        </div>
      ))}
      <span className="sr-only">Loading navigation…</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section block
// ---------------------------------------------------------------------------

/** localStorage key for the folded-section map (Record<sectionId, true>). */
const FOLD_KEY = "cc-nav-folded-sections";

function persistFolds(next: Record<string, boolean>): Record<string, boolean> {
  try {
    localStorage.setItem(FOLD_KEY, JSON.stringify(next));
  } catch {
    // Storage unavailable (private mode) — fold state just won't persist.
  }
  return next;
}

function NavSectionBlock({
  section,
  pathname,
  collapsed,
  folded,
  onToggle,
  onNavigate,
  agentUpdateCount = 0,
  pinnedApps = [],
}: {
  section: NavSection;
  pathname: string | null;
  collapsed: boolean;
  folded: boolean;
  onToggle: () => void;
  /** Arms the fold (`lib/sidebarFold.ts`). Every link in the rail calls it. */
  onNavigate: (e: MouseEvent) => void;
  agentUpdateCount?: number;
  pinnedApps?: PinnedApp[];
}) {
  if (collapsed) {
    return (
      <div>
        <div className="flex flex-col gap-1 p-2">
          {section.items.map((p) => (
            <NavLink
              key={p.href}
              pane={p}
              pathname={pathname}
              collapsed
              onNavigate={onNavigate}
              badge={p.href === "/agents" && agentUpdateCount > 0 ? agentUpdateCount : undefined}
            />
          ))}
        </div>
        <div className="mx-3 border-t border-sidebar-border/50" />
      </div>
    );
  }

  // A folded section keeps showing the item you are ON — the current location
  // must never vanish from the nav.
  const items = folded
    ? section.items.filter((p) => pathname?.startsWith(p.href))
    : section.items;

  return (
    <div className="px-2 py-1.5">
      {/* Section heading — click to fold/unfold */}
      <button
        onClick={onToggle}
        aria-expanded={!folded}
        className={`group flex w-full items-center justify-between rounded-md px-2 text-left uppercase tracking-wider font-semibold hover:text-sidebar-foreground tech-transition ${
          section.sub
            ? "py-1 text-[10px] text-muted-foreground/70"
            : "py-1.5 text-[11px] text-muted-foreground"
        }`}
      >
        <span>{section.label}</span>
        <Icon
          name="ChevronDown"
          size={12}
          className={`shrink-0 text-muted-foreground/50 group-hover:text-muted-foreground tech-transition ${
            folded ? "-rotate-90" : ""
          }`}
        />
      </button>

      {/* Section items */}
      {items.length > 0 && (
        <div className="flex flex-col gap-0.5">
          {items.map((p) => (
            <NavLink
              key={p.href}
              pane={p}
              pathname={pathname}
              onNavigate={onNavigate}
              badge={p.href === "/agents" && agentUpdateCount > 0 ? agentUpdateCount : undefined}
              pinnedApps={p.href === "/build/apps" ? pinnedApps : undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Individual nav link
// ---------------------------------------------------------------------------

function NavLink({
  pane,
  pathname,
  collapsed = false,
  onNavigate,
  badge,
  pinnedApps,
}: {
  pane: NavPane;
  pathname: string | null;
  collapsed?: boolean;
  onNavigate: (e: MouseEvent) => void;
  badge?: number;
  pinnedApps?: PinnedApp[];
}) {
  const active = pathname?.startsWith(pane.href);

  if (collapsed) {
    return (
      <Link
        key={pane.href}
        href={pane.href}
        title={pane.label}
        onClick={onNavigate}
        className={`rounded-lg tech-transition flex items-center justify-center p-2.5 relative ${
          active
            ? "bg-primary/15 text-primary"
            : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        }`}
      >
        <Icon name={pane.icon} size={18} strokeWidth={active ? 2.5 : 2} />
        {badge !== undefined && badge > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-warning text-[8px] font-bold text-warning-foreground">
            {badge > 9 ? "9+" : badge}
          </span>
        )}
      </Link>
    );
  }

  return (
    <>
      <Link
        key={pane.href}
        href={pane.href}
        onClick={onNavigate}
        className={`rounded-lg tech-transition px-3 py-2 text-sm ${
          active
            ? "bg-primary/15 text-primary"
            : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        }`}
      >
        <div className="flex items-center gap-2.5">
          <Icon name={pane.icon} size={16} strokeWidth={active ? 2.5 : 2} />
          <span className="font-medium text-[13px]">{pane.label}</span>
          {badge !== undefined && badge > 0 && (
            <span className="ml-auto rounded-full bg-warning px-1.5 py-0.5 text-[10px] font-bold text-warning-foreground">
              {badge}
            </span>
          )}
        </div>
        <div className="ml-[26px] text-[11px] text-muted-foreground/60 leading-tight mt-0.5">{pane.note}</div>
      </Link>
      {pinnedApps && pinnedApps.length > 0 && (
        <div className="flex flex-col gap-0.5 ml-[26px] mt-0.5 mb-1">
          {pinnedApps.map((a) => {
            const href = `/build/apps/${a.slug}`;
            const appActive = pathname?.startsWith(href);
            return (
              <Link
                key={a.slug}
                href={href}
                onClick={onNavigate}
                className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-[11.5px] tech-transition truncate ${
                  appActive
                    ? "text-primary"
                    : "text-muted-foreground/80 hover:text-sidebar-foreground hover:bg-sidebar-accent"
                }`}
              >
                <span className="shrink-0">{a.icon || "▦"}</span>
                <span className="truncate">{a.name}</span>
              </Link>
            );
          })}
        </div>
      )}
    </>
  );
}
// ---------------------------------------------------------------------------
// The fold tip
// ---------------------------------------------------------------------------

/**
 * The tip beside the expand button, on the first three folds (`HINT_LIMIT`).
 *
 * It names the button, so the member learns where the sidebar went, and it
 * offers the way out. `position: fixed`, so the rail's `overflow-hidden` does
 * not clip it. Measured in a layout effect, because the button remounts on the
 * fold (its `key` restarts the pulse) and the ref is current only after commit.
 */
function FoldTip({
  anchorRef,
  onClose,
  onKeepOpen,
}: {
  anchorRef: React.RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onKeepOpen: () => void;
}) {
  // ⚠️ Top-aligned with the button, never centred on it. The button sits near
  // the top of the window, so a centred tip ran off the top and hid its title.
  const [box, setBox] = useState<{ top: number; caret: number } | null>(null);
  useLayoutEffect(() => {
    const rect = anchorRef.current?.getBoundingClientRect();
    if (!rect) return;
    const top = Math.max(8, rect.top - 6);
    setBox({ top, caret: rect.top + rect.height / 2 - top });
  }, [anchorRef]);
  if (box === null) return null;
  return (
    <div
      role="status"
      data-testid="sidebar-fold-tip"
      // 56px rail plus a 12px gap. The rail is that width when the tip shows.
      style={{ top: box.top, left: 68 }}
      className="sidebar-tip fixed z-[60] w-64 rounded-lg border border-border bg-popover p-3 text-popover-foreground shadow-lg"
    >
      <span
        aria-hidden
        style={{ top: box.caret }}
        className="absolute -left-[5px] h-2.5 w-2.5 -translate-y-1/2 rotate-45 border-b border-l border-border bg-popover"
      />
      <div className="flex items-start gap-2">
        <Icon name="PanelLeftClose" size={15} className="mt-0.5 shrink-0 text-primary" />
        <div className="min-w-0">
          <div className="text-[13px] font-semibold">Sidebar folded</div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            The app has more room now. Select the arrow button to open the
            sidebar again.
          </p>
        </div>
      </div>
      <div className="mt-2.5 flex justify-end gap-1.5">
        <Button variant="ghost" size="sm" onClick={onKeepOpen}>
          Keep it open
        </Button>
        <Button variant="secondary" size="sm" onClick={onClose}>
          Got it
        </Button>
      </div>
    </div>
  );
}
