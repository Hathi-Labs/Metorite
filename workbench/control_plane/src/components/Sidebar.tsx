"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useSession, signOut } from "next-auth/react";
import { NAV_SECTIONS, visibleSections, type NavPane, type NavSection } from "@/lib/nav";
import { useAccess } from "@/components/AccessProvider";
import { shouldPollWorkspace } from "@/lib/access";
import { autoFoldEnabled, floatingOpen, isWorkEvent, shouldFold } from "@/lib/sidebarFold";
import Icon, { themedIcon } from "@/components/Icon";
import NavBadge, { type NavBadgeTone } from "@/components/NavBadge";
import { useRunActivity } from "@/hooks/useActiveSessions";
import { runBadge } from "@/lib/runActivity";
import OrgBrandLockup from "@/components/OrgBrandLockup";
import { SidebarFoldButton, useSidebarFold } from "@/components/SidebarFold";
import { ActivityControl } from "@/lib/shell/ActivityControl";
import { shellDockOn } from "@/lib/shell/dockFlag";
import { ShellBell } from "@/lib/shell/ShellBell";
import { shellBarOn } from "@/lib/shell/registry";
import ThemeToggle from "@/components/ThemeToggle";
import { SidebarAccountFooter, useAccounts } from "@/components/AccountSwitcher";
import AppLauncher from "@/lib/shell/AppLauncher";
import { ADD_APPS_HINT, accountLinks, homePane, isActive, launcherGroups, shellNavOn, shellSidebar } from "@/lib/shell/shellNav";
import { latestShell, saveShellPrefs, useShellPrefs } from "@/lib/shell/shellPrefs";
import { EMPTY_SHELL, toggleStar } from "@/lib/shell/presets";
import { foldHeading, isFolded, readFolds, writeFolds, type Folds } from "@/lib/shell/sidebarFolds";
import { chatScope } from "@/lib/sessions";
import { ContextMenu } from "@/components/ContextMenu";
import { useToast } from "@/components/ui/Toast";
import Button from "@/components/ui/Button";

/** Mirrors gateway/routes/apps/pins.py's PinnedApp — GET /api/apps/pins. */
type PinnedApp = { slug: string; name: string; icon?: string };

// ---------------------------------------------------------------------------
// Sidebar — sectioned navigation (Apps / Configure / Build)
// RapidTool design language — refined dark theme with blue primary accent.
// ---------------------------------------------------------------------------

export default function Sidebar() {
  const pathname = usePathname();
  // The fold state has one owner, `SidebarFold.tsx`, because the fold control
  // may live in the shell bar rather than in this rail's head (owner,
  // 2026-10-09). This rail reads it, and runs the work listener below, which
  // needs to know what is inside the rail.
  const fold = useSidebarFold();
  const { collapsed, armedRef, foldForWork } = fold;
  const armFold = fold.arm;
  // `placement === "bar"`: the full-width bar carries the logo and the
  // control, so this rail draws no head.
  const head = fold.placement === "rail";
  // The shell bar draws the activity control when it is on (WS-51 S3).
  const [barOn] = useState(() => shellBarOn());
  // NS-6: and the one bell, with its own flag on, beside the activity control.
  const [dockOn] = useState(() => shellDockOn());
  const asideRef = useRef<HTMLElement>(null);
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
  // The shell nav (NS-2, `lib/shell/shellNav.ts`). Read once: the flag is
  // build-time, and the dev override must not flip mid-session.
  const [shellNav] = useState(() => shellNavOn());
  // "Home", or "My Day" with that flag on (NS-3). Read once, as above.
  // ⚠️ Read in `useState`, as `shellNavOn` is: with the dev-only
  // `localStorage` override the server and the browser can disagree, and
  // React warns once. Production reads the build-time flag on both sides,
  // so it never disagrees. `app/page.tsx` needs `useSyncExternalStore`
  // because it swaps the whole page; a label here does not.
  const [home] = useState(() => homePane());
  const [launcherOpen, setLauncherOpen] = useState(false);
  // The hint All apps shows when "Add apps" opened it, and none otherwise.
  const [launcherHint, setLauncherHint] = useState<string | undefined>(undefined);
  const openLauncher = (hint?: string) => {
    setLauncherHint(hint);
    setLauncherOpen(true);
  };
  // The member's stars (owner, 2026-10-11): each category lists only the
  // apps they starred. While the first read is out the groups draw every
  // held app, which is complete, not blank. A failed read draws every held
  // app too (`shellLayout`).
  const shell = useShellPrefs();
  const railSections = shellNav
    ? shellSidebar(sections, shell.loading ? undefined : shell.layout)
    : sections;
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
  // The run badge (WS-51 S1): live assistant runs, counted on their app. A
  // run on a pane this rail does not show counts on Chat. A run that waits on
  // the member's answer (S2) turns the badge amber.
  const railHrefs = useMemo(
    () => new Set(railSections.flatMap((s) => s.items.map((p) => p.href))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [railSections.map((s) => s.items.map((p) => p.href).join(",")).join("|")],
  );
  const { byApp: runCounts, needsByApp: needsCounts, unreadByApp: unreadCounts } = useRunActivity(railHrefs, canPoll);

  // Per-category fold state, kept per ACCOUNT (owner, 2026-10-11), so a
  // reload keeps it and another account on this browser never reads it
  // (`lib/shell/sidebarFolds.ts`). Stored as a map of FOLDED ids, so a new
  // category starts open.
  const foldScope = accessLoading ? null : chatScope(access.email, access.organization?.id);
  const [foldedSections, setFoldedSections] = useState<Folds>({});
  useEffect(() => {
    setFoldedSections(readFolds(foldScope));
  }, [foldScope]);
  const toggleSection = (id: string) =>
    setFoldedSections((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      writeFolds(foldScope, next);
      return next;
    });

  // "Hide from sidebar" on a row (owner, 2026-10-11). The same optimistic
  // save as the star in All apps, built on the latest layout, and it goes
  // back when the server refuses it. The toast offers Undo.
  const toast = useToast();
  const canHide = shellNav && shell.enabled && shell.stored !== undefined;
  const flipStar = (href: string) => {
    const base = { ...EMPTY_SHELL, ...(latestShell() ?? shell.stored) };
    return saveShellPrefs({ ...base, ...toggleStar(base, sections, href) });
  };
  const hideFromSidebar = (pane: NavPane) => {
    flipStar(pane.href).then(
      () =>
        toast.show({
          key: "sidebar-hide",
          title: `${pane.label} hidden from your sidebar.`,
          action: {
            label: "Undo",
            onClick: () => {
              flipStar(pane.href).catch(() =>
                toast.show({ key: "sidebar-hide", variant: "error", title: "Your sidebar was not saved. Try again." }),
              );
            },
          },
        }),
      () => toast.show({ key: "sidebar-hide", variant: "error", title: "Your sidebar was not saved. Try again." }),
    );
  };
  // The row menu: at the pointer for a right-click, under the "···" for a
  // press. `trigger` gets the focus back when the menu closes.
  const [rowMenu, setRowMenu] = useState<{ pane: NavPane; x: number; y: number; keyboard: boolean; trigger: HTMLElement | null } | null>(null);
  const closeRowMenu = () => {
    const back = rowMenu?.trigger;
    setRowMenu(null);
    back?.focus();
  };
  const rowMenuFor = canHide
    ? (pane: NavPane, at: { x: number; y: number; keyboard: boolean; trigger: HTMLElement | null }) =>
        setRowMenu({ pane, ...at })
    : undefined;

  // Navigating into a folded section unfolds it (once per navigation — the
  // user can still fold it again while staying on the page).
  useEffect(() => {
    if (!pathname) return;
    setFoldedSections((prev) => {
      // The shape on screen. The shell nav groups by team, so its ids differ.
      // The FIRST owner only, as before: on /people/me the "Apps" group (it
      // holds /people) keeps the fold its member chose.
      const shape = shellNav ? shellSidebar(NAV_SECTIONS) : NAV_SECTIONS;
      const owner = shape.find((s) => s.items.some((p) => pathname.startsWith(p.href)));
      if (!owner || !prev[owner.id]) return prev;
      const next = { ...prev, [owner.id]: false };
      writeFolds(foldScope, next);
      return next;
    });
  }, [pathname, shellNav, foldScope]);

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
        foldForWork();
      });
    };
    document.addEventListener("click", onWork, true);
    document.addEventListener("keydown", onWork, true);
    return () => {
      document.removeEventListener("click", onWork, true);
      document.removeEventListener("keydown", onWork, true);
    };
  }, [collapsed, foldForWork, armedRef]);

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
      {/* Header. With the full-width shell bar, the bar carries the logo and
          the fold control instead, so the logo stays in view when this rail
          folds (owner, 2026-10-09). */}
      {head && (
        <div className={`flex items-center border-b border-sidebar-border ${collapsed ? "justify-center p-3" : "justify-between px-4 py-4"}`}>
          {!collapsed && (
            // The customer's mark when they have uploaded one, ours when they
            // have not. `maxWidth` is what stops a wide wordmark from pushing the
            // collapse control off the 256px rail.
            <OrgBrandLockup fallbackCaption="Control Plane" maxWidth={152} />
          )}
          {/* WS-51 S3: with the shell bar off, this head is the top of the
              page, so the activity control sits here, beside the fold. The
              one bell (NS-6) sits before it while its flag is on. */}
          <div className={`flex items-center gap-1 ${collapsed ? "flex-col" : ""}`}>
            {!barOn && dockOn ? <ShellBell /> : null}
            {!barOn ? <ActivityControl /> : null}
            <SidebarFoldButton />
          </div>
        </div>
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
          <>
          {shellNav && (
            // The same column as a group's items, so Home lines up with them.
            <div className={collapsed ? "flex flex-col gap-1 p-2 pb-0" : "flex flex-col gap-0.5 px-2 pt-2"}>
              <NavLink pane={home} pathname={pathname} collapsed={collapsed} onNavigate={armFold} shellNav />
            </div>
          )}
          {railSections.map((section) => (
            <NavSectionBlock
              key={section.id}
              section={section}
              pathname={pathname}
              collapsed={collapsed}
              folded={isFolded(
                foldedSections,
                section.id,
                section.items.some((p) => (shellNav ? isActive(pathname, p.href) : pathname?.startsWith(p.href))),
              )}
              onRowMenu={rowMenuFor}
              onToggle={() => toggleSection(section.id)}
              onNavigate={armFold}
              agentUpdateCount={agentUpdateCount}
              runCounts={runCounts}
              needsCounts={needsCounts}
              unreadCounts={unreadCounts}
              pinnedApps={pinnedApps}
              shellNav={shellNav}
            />
          ))}
          {shellNav && <AddAppsRow collapsed={collapsed} onOpen={() => openLauncher(ADD_APPS_HINT)} />}
          </>
        )}
      </nav>

      {rowMenu && (
        <ContextMenu
          x={rowMenu.x}
          y={rowMenu.y}
          autoFocus={rowMenu.keyboard}
          onClose={closeRowMenu}
          items={[
            {
              kind: "item",
              label: "Hide from sidebar",
              icon: themedIcon("EyeOff"),
              onSelect: () => hideFromSidebar(rowMenu.pane),
            },
            {
              kind: "item",
              label: "Open in new tab",
              icon: themedIcon("ExternalLink"),
              onSelect: () => window.open(rowMenu.pane.href, "_blank", "noopener"),
            },
          ]}
        />
      )}

      {/* All apps (NS-2): every app the member holds, each with its purpose. */}
      {shellNav && !accessLoading && launcherGroups(sections).length > 0 && (
        <div className={`border-t border-sidebar-border ${collapsed ? "flex justify-center p-2" : "px-2 py-2"}`}>
          <button
            type="button"
            onClick={() => openLauncher()}
            title={collapsed ? "All apps" : undefined}
            aria-label="All apps"
            aria-haspopup="dialog"
            className={`flex items-center gap-2.5 rounded-lg text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground tech-transition ${
              collapsed ? "justify-center p-2.5" : "w-full px-3 py-2 text-[13px] font-medium"
            }`}
          >
            <Icon name="LayoutGrid" size={collapsed ? 18 : 16} />
            {!collapsed && <span>All apps</span>}
          </button>
        </div>
      )}
      {shellNav && (
        <AppLauncher
          open={launcherOpen}
          onClose={() => setLauncherOpen(false)}
          sections={sections}
          pathname={pathname}
          hint={launcherHint}
        />
      )}

      {/* User / sign-out footer */}
      <SidebarAccountFooter
        collapsed={collapsed}
        accounts={accounts}
        reload={reloadAccounts}
        you={shellNav ? accountLinks(sections) : undefined}
      />
      {!shellNav && !accounts.enabled && !collapsed && (
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
// "Add apps" — the last item of the list (owner, 2026-10-11)
// ---------------------------------------------------------------------------

/**
 * The way to add an app, always at the end of the list, so a member who
 * starred nothing still sees where to start. It opens All apps, where the
 * star on each app says what the sidebar shows.
 *
 * ⚠️ It takes the geometry of `NavLink` on purpose, and not that of
 * `RailRow`. The block is `px-2`, the row is `px-3 py-2`, and the icon
 * column is the same, so its icon sits in line with the app icons above it,
 * open or folded. `e2e/shell-presets.spec.ts` measures that. It is a button
 * that opens a dialog, not a place, so it has no active state.
 */
function AddAppsRow({ collapsed, onOpen }: { collapsed: boolean; onOpen: () => void }) {
  if (collapsed) {
    return (
      <div className="flex flex-col gap-1 p-2" data-nav-add-apps="">
        <button
          type="button"
          onClick={onOpen}
          title="Add apps"
          aria-label="Add apps"
          aria-haspopup="dialog"
          className="flex items-center justify-center rounded-lg p-2.5 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground tech-transition"
        >
          <Icon name="Plus" size={18} />
        </button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-0.5 px-2 py-1.5" data-nav-add-apps="">
      <button
        type="button"
        onClick={onOpen}
        aria-haspopup="dialog"
        className="rounded-lg px-3 py-2 text-left text-sm text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground tech-transition"
      >
        <span className="flex items-center gap-2.5">
          <Icon name="Plus" size={16} />
          <span className="text-[13px]">Add apps</span>
        </span>
      </button>
    </div>
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

/** Opens a row's menu at a point. `keyboard` moves the focus into it. */
type RowMenuOpen = (
  pane: NavPane,
  at: { x: number; y: number; keyboard: boolean; trigger: HTMLElement | null },
) => void;

function NavSectionBlock({
  section,
  pathname,
  collapsed,
  folded,
  onToggle,
  onNavigate,
  agentUpdateCount = 0,
  runCounts = {},
  needsCounts = {},
  unreadCounts = {},
  pinnedApps = [],
  shellNav = false,
  onRowMenu,
}: {
  section: NavSection;
  pathname: string | null;
  collapsed: boolean;
  folded: boolean;
  onToggle: () => void;
  /** Arms the fold (`lib/sidebarFold.ts`). Every link in the rail calls it. */
  onNavigate: (e: MouseEvent) => void;
  agentUpdateCount?: number;
  /** Live assistant runs per pane href (WS-51 S1). */
  runCounts?: Record<string, number>;
  /** The runs per pane href that wait on the member's answer (WS-51 S2). */
  needsCounts?: Record<string, number>;
  /** The unread chats per pane href (WS-51 S5). */
  unreadCounts?: Record<string, number>;
  pinnedApps?: PinnedApp[];
  /** The shell nav's line under each item: the manifest's purpose. */
  shellNav?: boolean;
  /** Opens a row's menu (owner, 2026-10-11). Absent: the rows have none. */
  onRowMenu?: RowMenuOpen;
}) {
  const badgeFor = (href: string) => paneBadge(href, agentUpdateCount, runCounts, needsCounts, unreadCounts);
  if (collapsed) {
    return (
      <div data-nav-section={section.id}>
        <div className="flex flex-col gap-1 p-2">
          {section.items.map((p) => (
            <NavLink
              key={p.href}
              pane={p}
              pathname={pathname}
              collapsed
              onNavigate={onNavigate}
              {...badgeFor(p.href)}
              shellNav={shellNav}
              onRowMenu={onRowMenu}
            />
          ))}
        </div>
        <div className="mx-3 border-t border-sidebar-border/50" />
      </div>
    );
  }

  // A folded section keeps showing the item you are ON — the current location
  // must never vanish from the nav. With the shell nav, the category of the
  // current route is never folded at all (`isFolded`).
  const items = folded
    ? section.items.filter((p) => pathname?.startsWith(p.href))
    : section.items;

  return (
    <div className="px-2 py-1.5" data-nav-section={section.id}>
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
        <span>{shellNav ? foldHeading(section.label, section.items.length, folded) : section.label}</span>
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
              {...badgeFor(p.href)}
              pinnedApps={p.href === "/build/apps" ? pinnedApps : undefined}
              shellNav={shellNav}
              onRowMenu={onRowMenu}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The one badge a pane wears. /agents keeps its "updates" count, in the
 * `warning` tone. Every other pane shows its live runs, in `success`
 * (WS-51 S1), unless a run there waits on the member's answer: then the
 * badge counts those runs in `warning` (S2, `runBadge`). Exported for
 * `navBadge.test.ts`.
 */
export function paneBadge(
  href: string,
  agentUpdateCount: number,
  runCounts: Record<string, number>,
  needsCounts: Record<string, number> = {},
  unreadCounts: Record<string, number> = {},
): { badge?: number; badgeTone?: NavBadgeTone; badgeLabel?: string } {
  if (href === "/agents") {
    return agentUpdateCount > 0
      ? {
          badge: agentUpdateCount,
          badgeTone: "warning",
          badgeLabel: `${agentUpdateCount} agent ${agentUpdateCount === 1 ? "update" : "updates"}`,
        }
      : {};
  }
  const b = runBadge(runCounts[href] ?? 0, needsCounts[href] ?? 0, unreadCounts[href] ?? 0);
  return b ? { badge: b.count, badgeTone: b.tone, badgeLabel: b.label } : {};
}

// ---------------------------------------------------------------------------
// Individual nav link
// ---------------------------------------------------------------------------

export function NavLink({
  pane,
  pathname,
  collapsed = false,
  onNavigate,
  badge,
  badgeTone = "warning",
  badgeLabel,
  pinnedApps,
  shellNav = false,
  onRowMenu,
}: {
  pane: NavPane;
  pathname: string | null;
  collapsed?: boolean;
  onNavigate: (e: MouseEvent) => void;
  badge?: number;
  /** The badge's meaning: `warning` waits for the member, `success` runs. */
  badgeTone?: NavBadgeTone;
  /** The badge's spoken name. Defaults to the bare count. */
  badgeLabel?: string;
  pinnedApps?: PinnedApp[];
  shellNav?: boolean;
  /**
   * The row's menu: "Hide from sidebar" and "Open in new tab" (owner,
   * 2026-10-11). A right-click opens it, open or folded. Open, a "···" at
   * the row's end opens it too. It is a SIBLING of the link, so the link
   * keeps its own name and stays the click target. Home passes none.
   */
  onRowMenu?: RowMenuOpen;
}) {
  // The shell nav matches by segment, so Home ("/") is active on Home only.
  const active = shellNav ? isActive(pathname, pane.href) : pathname?.startsWith(pane.href);
  // ⚠️ The shell nav draws ONE line per item, and the purpose is the hover
  // text. Measured on 2026-10-09: a second line under every item made the
  // sidebar taller than a 900px screen. A member who opens it every day reads
  // that line once and then scrolls past it for ever. The purpose matters when
  // a member is finding out what an app is, and All apps prints it there.
  // Off, the older operator line, as before.
  const purpose = pane.blurb ?? pane.note;

  if (collapsed) {
    return (
      <Link
        key={pane.href}
        href={pane.href}
        title={shellNav ? `${pane.label}: ${purpose}` : pane.label}
        onClick={onNavigate}
        onContextMenu={
          onRowMenu
            ? (e) => {
                e.preventDefault();
                onRowMenu(pane, { x: e.clientX, y: e.clientY, keyboard: false, trigger: e.currentTarget });
              }
            : undefined
        }
        className={`rounded-lg tech-transition flex items-center justify-center p-2.5 relative ${
          active
            ? "bg-primary/15 text-primary"
            : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        }`}
      >
        <Icon name={pane.icon} size={18} strokeWidth={active ? 2.5 : 2} />
        {/* The icon has no text, so the link's name starts with the pane's. */}
        {badge !== undefined && badge > 0 && <span className="sr-only">{pane.label}</span>}
        <NavBadge
          count={badge ?? 0}
          tone={badgeTone}
          label={badgeLabel ?? String(badge ?? 0)}
          placement="corner"
        />
      </Link>
    );
  }

  const link = (
      <Link
        key={pane.href}
        href={pane.href}
        onClick={onNavigate}
        onContextMenu={
          onRowMenu
            ? (e) => {
                e.preventDefault();
                onRowMenu(pane, { x: e.clientX, y: e.clientY, keyboard: false, trigger: e.currentTarget });
              }
            : undefined
        }
        title={shellNav ? purpose : undefined}
        className={`rounded-lg tech-transition px-3 py-2 text-sm ${onRowMenu ? "w-full pr-9" : ""} ${
          active
            ? "bg-primary/15 text-primary"
            : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        }`}
      >
        <div className="flex items-center gap-2.5">
          <Icon name={pane.icon} size={16} strokeWidth={active ? 2.5 : 2} />
          <span className="font-medium text-[13px]">{pane.label}</span>
          <NavBadge count={badge ?? 0} tone={badgeTone} label={badgeLabel ?? String(badge ?? 0)} />
        </div>
        {!shellNav && (
          <div className="ml-[26px] text-[11px] text-muted-foreground/60 leading-tight mt-0.5">{pane.note}</div>
        )}
      </Link>
  );

  return (
    <>
      {onRowMenu ? (
        // `group` lets hover, or keyboard focus anywhere in the row, show the
        // "···" (`reveal-on-hover`, `rail-row-actions` in globals.css). The
        // button takes no click at rest, so it never swallows the link's.
        <div className="group relative flex items-center">
          {link}
          <span className="reveal-on-hover rail-row-actions absolute right-1.5 top-1/2 -translate-y-1/2">
            <Button
              variant="ghost"
              size="icon-xs"
              icon="MoreHorizontal"
              aria-label={`More for ${pane.label}`}
              aria-haspopup="menu"
              title="More"
              data-row-more=""
              onClick={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                onRowMenu(pane, { x: r.left, y: r.bottom + 4, keyboard: e.detail === 0, trigger: e.currentTarget });
              }}
            />
          </span>
        </div>
      ) : (
        link
      )}
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
