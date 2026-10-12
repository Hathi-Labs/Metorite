/**
 * The shell's navigation, read from the manifest (NS-2, `navigation_shell.md`
 * §3.2, §3.3 and §5). One input, `visibleSections()`, so nothing here can show
 * a pane the member does not hold or that we have not launched.
 *
 * Three outputs:
 *
 * - `shellSidebar` — the sidebar's groups. Only places where the member works.
 * - `accountPanes` — the account menu's links: pages about the member (My
 *   Profile, Appearance). Organisation is an app in Admin since 2026-10-09.
 * - `launcherGroups` — All apps: every app the member holds, by team, each
 *   with its purpose. A `setting` is not an app, so it never shows there.
 *
 * ⚠️ Three choices differ from §3.2 on purpose (owner request of 2026-10-09,
 * "make sure it is genuinely a step ahead"). The spec records them in §3.2a.
 *
 * 1. The account menu lives at the sidebar's FOOT, where the account switcher
 *    already is, not in a new avatar at the top. One place for "you".
 *    ⚠️ Superseded on 2026-10-12 by the owner, with the shell bar on: the
 *    account acts are an avatar at the bar's right end (`BarAccount`), and
 *    the foot is the Metorite menu (`metoriteMenu` below). The classic frame
 *    keeps the foot as it was.
 * 2. Chat and Approvals keep a sidebar door until NS-6 builds the dock and
 *    the bell that would replace them. Before that, moving them away would
 *    hide the assistant and an approver's queue.
 * 3. A group with no item is dropped, and Home is called "Home" until My
 *    Day is on (NS-3, `homePane`). A heading over nothing, or a name the
 *    page does not keep, costs trust.
 */
import type { NavPane, NavSection } from "@/lib/nav";
import { myDayOn } from "@/lib/shell/myDayFlag";
import { starredHrefs, type Stars } from "@/lib/shell/presets";

/** The flag for the shell nav. Off, the sidebar and the drawer are as before. */
export function shellNavOn(): boolean {
  // The literal member expression: the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_SHELL_NAV === "1") return true;
  // ⚠️ Development and test builds ONLY, as `shellBarOn` does: a browser may
  // turn it on for itself (`localStorage["cc-shell-nav"] = "1"`), so the
  // browser suite runs both sides. A production build drops this branch.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-shell-nav") === "1";
    } catch {
      return false;
    }
  }
  return false;
}

/**
 * The desktop frame, from the two shell flags (`navigation_shell.md` §3.1).
 *
 * - `classic` — no shell bar. The sidebar carries the logo, as before NS-1.
 * - `column` — the shell bar only (NS-1). The bar sits over the page column,
 *   and the sidebar still carries the logo and the fold control.
 * - `full` — both flags (owner, 2026-10-09). One bar spans the whole width
 *   and carries the fold control and the organization's logo, so the logo
 *   stays in view when the sidebar is folded. The sidebar has no head.
 *
 * ⚠️ The shell nav alone, with no bar, stays `classic`: there is no bar to
 * carry the logo, and the sidebar must not lose it.
 */
export type DesktopFrame = "classic" | "column" | "full";

export function desktopFrame(bar: boolean, nav: boolean): DesktopFrame {
  if (!bar) return "classic";
  return nav ? "full" : "column";
}

/** The groups, in the order the sidebar and All apps show them. */
export const TEAM_GROUPS: readonly { team: string; label: string }[] = [
  { team: "personal", label: "Personal Center" },
  { team: "across", label: "Across teams" },
  { team: "studio", label: "AI Studio" },
  { team: "admin", label: "Admin" },
];

/** The first item of the sidebar, as it reads with My Day off. */
export const HOME_PANE: NavPane = {
  href: "/",
  label: "Home",
  icon: "Home",
  note: "Your apps",
  team: "personal",
  blurb: "Where you start",
  launch: "live",
};

/**
 * The first item of the sidebar and the drawer. With My Day on (NS-3), `/`
 * IS My Day, so the item carries that name. Off, it stays "Home", because a
 * name the page does not keep costs trust.
 */
export function homePane(on: boolean = myDayOn()): NavPane {
  if (!on) return HOME_PANE;
  return { ...HOME_PANE, label: "My Day", note: "What needs you today", blurb: "What needs you today" };
}

function held(sections: readonly NavSection[]): NavPane[] {
  return sections.flatMap((s) => s.items);
}

/** Group panes by team, in `TEAM_GROUPS` order. An empty group is dropped. */
function byTeam(panes: readonly NavPane[]): NavSection[] {
  const known = new Set(TEAM_GROUPS.map((g) => g.team));
  const groups = TEAM_GROUPS.map((g) => ({
    id: g.team,
    label: g.label,
    items: panes.filter((p) => p.team === g.team),
  }));
  // A team slug the list does not name (a Center's) joins "Across teams", so
  // a pane with a new team still renders rather than vanishing.
  const across = groups.find((g) => g.id === "across")!;
  across.items.push(...panes.filter((p) => !known.has(p.team)));
  return groups.filter((g) => g.items.length > 0);
}

/**
 * The sidebar's groups: every held pane whose door is the sidebar, by team,
 * in `TEAM_GROUPS` order, and each item in `nav.ts` order.
 *
 * **The star model** (owner, 2026-10-11). With `stars`, a group lists only
 * the apps the member starred (`starredHrefs`), never in star order, and a
 * group with no starred app is dropped. Nothing moves between groups, and
 * there is no "My apps" group (NS-7 had one, and this replaces it). With no
 * stars, or with `stars.pins` null, every held app shows, as before stars.
 * Home is not a group item, and the sidebar draws it on its own, always.
 * `starredHrefs` picks from `sections` alone, so a star never draws a pane
 * the member does not hold or that is not live.
 */
export function shellSidebar(sections: readonly NavSection[], stars?: Stars | null): NavSection[] {
  const doors = held(sections).filter((p) => (p.door ?? "sidebar") === "sidebar");
  const shown = starredHrefs(stars, sections);
  return byTeam(shown === null ? doors : doors.filter((p) => shown.has(p.href)));
}

/** The line All apps shows when the sidebar's "Add apps" opens it. */
export const ADD_APPS_HINT = "Star the apps you want in your sidebar.";

/**
 * The count on panes the sidebar does not show. A run on a hidden pane
 * folds to Chat (`countRunsByApp`), and when Chat is hidden too its badge
 * had no row. The sidebar shows this count on "Add apps", the door to the
 * hidden apps, so a run is never out of sight.
 */
export function strayCount(counts: Record<string, number>, shown: ReadonlySet<string>): number {
  let n = 0;
  for (const [href, c] of Object.entries(counts)) if (!shown.has(href)) n += c;
  return n;
}

/** The account menu's panes, in their manifest order. */
export function accountPanes(sections: readonly NavSection[]): NavPane[] {
  return held(sections).filter((p) => p.door === "account");
}

/** One row of the account menu. */
export interface AccountLink {
  href: string;
  label: string;
  icon: string;
}

/**
 * "My access" is a People tab, not a pane, and every member may open it
 * (`lib/access.ts` ALWAYS_ALLOWED, owner directive of 2026-10-05). The menu
 * links to that tab and holds no second copy of the page (§3.3).
 */
export const MY_ACCESS: AccountLink = { href: "/people/access", label: "My access", icon: "KeyRound" };

/**
 * The account menu's rows: the member's own pages, with "My access" after My
 * Profile. A member sees only what `visibleSections` gave. An `account` pane
 * of another team would follow them, and none exists since Organisation
 * became an app in Admin (owner, 2026-10-09).
 */
export function accountLinks(sections: readonly NavSection[]): AccountLink[] {
  // No sections is an unresolved viewer (`visibleSections(null)`). A member
  // who holds nothing still gets My Profile and Appearance, which no grant
  // gates. So nothing here means "not known yet", and it shows nothing (§8.1).
  if (sections.length === 0) return [];
  const panes = accountPanes(sections);
  const row = (p: NavPane): AccountLink => ({ href: p.href, label: p.label, icon: p.icon });
  const mine = panes.filter((p) => p.team === "personal").map(row);
  const org = panes.filter((p) => p.team !== "personal").map(row);
  // Right after My Profile, because both describe the member.
  const at = mine.findIndex((l) => l.href === "/people/me") + 1;
  return [...mine.slice(0, at), MY_ACCESS, ...mine.slice(at), ...org];
}

/**
 * One row of the Metorite menu at the sidebar's foot (owner, 2026-10-12). A
 * `link` opens a page. `colour-mode` is the light and dark toggle. An
 * `action` sends a shell event, and `MetoriteFoot.tsx` maps each id to it.
 */
export type MetoriteItem =
  | ({ kind: "link" } & AccountLink)
  | { kind: "colour-mode" }
  | { kind: "action"; id: "change-layout"; label: string; icon: string };

export interface MetoriteGroup {
  id: string;
  label: string;
  items: MetoriteItem[];
}

/** The account pages that are settings, not facts about the member. */
const SETTINGS_PAGES: ReadonlySet<string> = new Set(["/settings/appearance"]);

/** NS-7: ask the first sign-in question again (§8.4 rule 3). */
export const CHANGE_LAYOUT_ITEM: MetoriteItem = {
  kind: "action",
  id: "change-layout",
  label: "Change my layout",
  icon: "LayoutTemplate",
};

/**
 * The Metorite menu, in its groups (owner, 2026-10-12: "Clicking the
 * Metorite logo opens information and settings for configuring Metorite …
 * The user's profile and access controls also appear alongside").
 *
 * - **You**: the member's own pages, My Profile and My access.
 * - **Settings**: Appearance, the colour mode, and "Change my layout" while
 *   the shell nav is on (NS-7 asks the question only then).
 *
 * It reads `accountLinks`, so a page is listed once, in one place. ⚠️ A new
 * option is ONE line here, in the group it belongs to. Do not add a row in
 * the component. The menu holds no account act: those are in the shell bar's
 * account menu (`BarAccount`). Fence: `shellAccount.test.ts`.
 */
export function metoriteMenu(sections: readonly NavSection[], opts: { layout: boolean }): MetoriteGroup[] {
  const links = accountLinks(sections);
  const link = (l: AccountLink): MetoriteItem => ({ kind: "link", ...l });
  const groups: MetoriteGroup[] = [
    { id: "you", label: "You", items: links.filter((l) => !SETTINGS_PAGES.has(l.href)).map(link) },
    {
      id: "settings",
      label: "Settings",
      items: [
        ...links.filter((l) => SETTINGS_PAGES.has(l.href)).map(link),
        { kind: "colour-mode" },
        ...(opts.layout ? [CHANGE_LAYOUT_ITEM] : []),
      ],
    },
  ];
  return groups.filter((g) => g.items.length > 0);
}

/** All apps: every held pane that is an app, by team. */
export function launcherGroups(sections: readonly NavSection[]): NavSection[] {
  return byTeam(held(sections).filter((p) => !p.setting));
}

/** Whether `pathname` is inside `href`. Home matches only itself. */
export function isActive(pathname: string | null, href: string): boolean {
  if (!pathname) return false;
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}
