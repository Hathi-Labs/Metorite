"use client";

/**
 * People Center · the app shell.
 *
 * Spec: `project-docs/specs/people_center_app.md` §5 (the sub-app roster).
 *
 * **Why this exists.** Every surface in §5 was built and none of them could be
 * reached. The sidebar offers `/people` and nothing else, so the capability
 * search, the workload dashboard and the rebalancing suggestions — the three
 * things that make a filled-in profile worth filling in — were reachable only
 * by typing the URL. A feature nobody can navigate to is a feature nobody
 * has. Owner directive, 2026-09-20.
 *
 * **One tab vocabulary, not a second.** These tabs are `components/Tabs` with
 * the `href` support added in the same change, rather than a People-local bar.
 * A second tab bar is exactly the app-local look `AGENTS.md` rule 1 refuses,
 * and the one that drifts is always the one in the app.
 *
 * ⚠️ **A member without `feature:people` sees the two PERSONAL tabs only,
 * and that is load-bearing.** This layout wraps `/people/me` and
 * `/people/access`, which are deliberately UNGATED (D-PC-15) — your own
 * record is not the directory. Drawing Directory and Org chart beside them
 * for somebody who cannot open either would turn the surfaces they are
 * entitled to into a wall of refusals. The personal tabs come last for
 * everybody who sees the whole bar, because "where do I fit" and "who else is
 * here" are one question asked from two ends.
 *
 * **My access joined as a tab on 2026-10-05** (owner directive: "remove my
 * access from the sidebar and fold it into the People's app"). Before that,
 * a member without `feature:people` saw no bar at all. Now the bar is that
 * member's door to My access, so it shows the personal tabs to everybody.
 *
 * ⚠️ **Tabs follow the SERVER's permission, read from the resolved access.**
 * Hiding a tab is a courtesy and never a boundary — the gateway's own
 * `can_read_hr_fields` is what refuses the request (`lib/access.ts`). What
 * this avoids is offering a member four tabs that answer 403.
 */

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import Tabs, { type TabDef } from "@/components/Tabs";
import { useAccess } from "@/components/AccessProvider";
import { hasCapability } from "@/lib/access";

/**
 * The roster, in the order a person meets it.
 *
 * `hr` marks the surfaces the gateway gates on `admin:members:read`
 * (§4.2) — measured against the routes, not assumed: `search.py:186`,
 * `dashboard.py:166`, `overview.py:70` and `quality.py:381` each refuse
 * without it. Seats is `hr` for the same reason, one layer along: it reads
 * `/admin/groups` and `/admin/members`, and `require_admin_user` IS
 * `admin:members:read`. Directory, org chart and the working week need only
 * `feature:people`.
 */
const TABS: ReadonlyArray<
  TabDef & { hr?: boolean; exact?: boolean; personal?: boolean }
> = [
  { id: "directory", label: "Directory", icon: "Users", href: "/people",
    exact: true, note: "Everybody in the organization" },
  { id: "chart", label: "Org chart", icon: "Network", href: "/people/chart",
    note: "Who reports to whom" },
  { id: "search", label: "Find skills", icon: "Search", href: "/people/search",
    hr: true, note: "Who can do this — ranked by skills, CV and experience" },
  { id: "workload", label: "Workload", icon: "Activity",
    href: "/people/dashboard", hr: true,
    note: "Who is behind, overloaded or idle — and who could help whom" },
  // ⚠️ Added 2026-09-23 (H-167). This surface was BUILT in 2026-08-15 and
  // unreachable ever since D49 withdrew Centers on 2026-08-24 — its one door
  // was the People Center landing card, which nothing navigates to any more.
  // The 2026-09-20 pass that built this bar reached the other six surfaces and
  // missed this one, because its door was a withdrawn Center and not a missing
  // tab.
  //
  // `hr`, measured and not assumed: `overview.py:70` refuses without
  // `can_read_hr_fields`, which IS `admin:members:read` — the same gate
  // Workload carries. It sits beside Workload because both answer about the
  // organisation rather than about one person, and Overview is the wider of
  // the two questions.
  { id: "overview", label: "Overview", icon: "LayoutDashboard",
    href: "/people/overview", hr: true,
    note: "Headcount, who is away, the load spread and the health of the record" },
  { id: "seats", label: "Seats", icon: "LayoutGrid",
    href: "/people/seats", hr: true,
    note: "Which teams and Centers each person is in" },
  { id: "schedule", label: "Working week", icon: "Clock",
    href: "/people/schedule", note: "The hours the scheduler plans against" },
  // `personal`: ungated, and shown even to a member without `feature:people`.
  // Each one is also in `lib/access.ts` ALWAYS_ALLOWED, or the route would
  // inherit the directory's slug by prefix.
  { id: "me", label: "My profile", icon: "User", href: "/people/me",
    personal: true, note: "What the assignment AI reads about you" },
  // Owner directive, 2026-10-05. It was the sidebar pane `/access`.
  { id: "access", label: "My access", icon: "ShieldCheck",
    href: "/people/access", personal: true,
    note: "What you can reach, and why anything else is hidden" },
];

/**
 * Which tab a path is on.
 *
 * Longest match wins, so `/people/chart` does not resolve to the directory.
 * `/people` needs `exact`, because as a prefix it matches every sibling —
 * the bug that would light two tabs at once on every page in the app.
 */
export function activeTabFor(pathname: string): string {
  let best: { id: string; len: number } | null = null;
  for (const tab of TABS) {
    const href = tab.href as string;
    const hit = tab.exact ? pathname === href : pathname.startsWith(href);
    if (hit && (!best || href.length > best.len)) {
      best = { id: tab.id, len: href.length };
    }
  }
  // A People route with no tab of its own — `/people/quality`, or a person
  // page — leaves every tab unselected rather than lighting the directory,
  // which would say "you are here" about somewhere you are not.
  return best?.id ?? "";
}

/**
 * The tabs one member sees.
 *
 * Without `feature:people`, only the `personal` tabs: My profile and My
 * access, both ungated. With it, every tab, less the `hr` ones the member
 * cannot open.
 *
 * Rebuilt field by field rather than spread-minus-the-extras: `hr`, `exact`
 * and `personal` are this layout's own bookkeeping and must not reach `Tabs`,
 * and an explicit list says which keys cross that boundary.
 */
export function visibleTabs(directory: boolean, hr: boolean): TabDef[] {
  return TABS.filter((t) => (directory ? (t.hr ? hr : true) : t.personal === true))
    .map((t) => ({
      id: t.id,
      label: t.label,
      icon: t.icon,
      href: t.href,
      note: t.note,
    }));
}

export default function PeopleLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "";
  const { access, loading } = useAccess();

  const visible = visibleTabs(
    access.features.includes("people"),
    hasCapability(access, "admin:members:read"),
  );

  // ⚠️ Not while access resolves. `AccessProvider` starts at NO_ACCESS, which
  // would draw the two personal tabs and then jump to the full bar, the same
  // full-then-shrink shape `launch_surface.md` §8.1 forbids for the sidebar.
  return (
    <div className="flex h-full min-h-0 flex-col">
      {!loading && visible.length > 0 && (
        <Tabs
          tabs={visible}
          activeTab={activeTabFor(pathname)}
          // Every tab carries an `href`, so `Tabs` never calls this. Kept
          // because the prop is required, and a throw here would be a trap
          // for whoever later adds a stateful tab to this bar.
          onTabChange={() => {}}
          variant="underline"
        />
      )}
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </div>
  );
}
