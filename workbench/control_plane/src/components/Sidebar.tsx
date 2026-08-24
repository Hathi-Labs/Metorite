"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useSession, signOut } from "next-auth/react";
import { NAV_SECTIONS, visibleSections, type NavPane, type NavSection } from "@/lib/nav";
import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import OrgBrandLockup from "@/components/OrgBrandLockup";
import ThemeToggle from "@/components/ThemeToggle";

/** Mirrors gateway/routes/apps/pins.py's PinnedApp — GET /api/apps/pins. */
type PinnedApp = { slug: string; name: string; icon?: string };

// ---------------------------------------------------------------------------
// Sidebar — sectioned navigation (Apps / Configure / Build)
// RapidTool design language — refined dark theme with blue primary accent.
// ---------------------------------------------------------------------------

export default function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const { data: session } = useSession();
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
  }, []);

  // Pinned Custom Apps — same polling shape as the agent-updates badge above.
  useEffect(() => {
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
  }, []);

  // A signed-in person with NO organization is mid-onboarding, not in a
  // workspace — AccessGate is showing them the join-vs-create chooser, and a
  // sidebar beside it (My Profile, My Access, Appearance) narrates a product
  // they have not joined (owner directive 2026-08-24, riding D51.2's "the
  // org is unmistakable": no org, no workspace chrome). Gated on the
  // resolution having LANDED — hiding on `loading` would flash the whole
  // layout on every ordinary sign-in. AFTER every hook, deliberately.
  if (!accessLoading && access.authenticated && !access.organization?.slug) {
    return null;
  }

  return (
    <aside
      className={`shrink-0 border-r flex flex-col transition-all duration-200 bg-sidebar border-sidebar-border ${
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
          onClick={() => setCollapsed((c) => !c)}
          className="shrink-0 rounded-lg p-1.5 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground tech-transition"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <Icon name="ChevronRight" size={16} /> : <Icon name="ChevronLeft" size={16} />}
        </button>
      </div>

      {/* Nav sections */}
      {/* `scrollbar-thin` is not cosmetic here. Without it this container gets the
          platform's default scrollbar, which on Linux/Windows is ~15px wide with
          stepper arrows — a quarter of the 56px COLLAPSED rail, shoving every
          icon off-centre and painting a light-grey bar down a dark sidebar. The
          themed utility already existed in globals.css; this scroller just never
          opted in. */}
      <nav className="scrollbar-thin flex flex-col flex-1 overflow-y-auto">
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
              agentUpdateCount={agentUpdateCount}
              pinnedApps={pinnedApps}
            />
          ))
        )}
      </nav>

      {/* User / sign-out footer */}
      {!collapsed && (
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
  agentUpdateCount = 0,
  pinnedApps = [],
}: {
  section: NavSection;
  pathname: string | null;
  collapsed: boolean;
  folded: boolean;
  onToggle: () => void;
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
  badge,
  pinnedApps,
}: {
  pane: NavPane;
  pathname: string | null;
  collapsed?: boolean;
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
        className={`rounded-lg tech-transition flex items-center justify-center p-2.5 relative ${
          active
            ? "bg-primary/15 text-primary"
            : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        }`}
      >
        <Icon name={pane.icon} size={18} strokeWidth={active ? 2.5 : 2} />
        {badge !== undefined && badge > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-amber-500 text-[8px] font-bold text-white">
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
            <span className="ml-auto rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-bold text-white">
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