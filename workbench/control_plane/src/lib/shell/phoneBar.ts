/**
 * The registry of the phone's bottom bar (MOB-12, task 2005).
 *
 * One entry per app, chosen by the path. The shell (`AppShell.tsx`) draws
 * the bar from this list and has no branch of its own for any app. The bar
 * reads: Menu first, the tabs of the app, then Ask at the right end. The
 * shell owns Menu and Ask. This file owns what lies between them.
 *
 * THE RULE (the owner, 2026-11-07: "make it obvious how to use the app
 * through the menu"):
 *  1. Menu first, Ask last, and at most THREE app tabs between them, counted
 *     with the Ask tab present, as production runs.
 *  2. A PLACE tab goes to a part of the app, and it shows selected
 *     (`aria-current="page"`, the primary colour) while that part shows.
 *  3. An ACT tab does one thing. At most one act per bar is the accent: the
 *     app's main "new".
 *  4. No tab opens an empty sheet. When a tab has nothing to do, the page
 *     hides it (`hide` in the `cc-phone-bar` event).
 *  5. No tab repeats a control that stays on screen. On a phone, the page
 *     drops the top control that the bar now holds.
 *  6. An app with no places and no "new" declares NO tabs, with its reason in
 *     words (`none`).
 *
 * A tab has EITHER an `href` (the shell draws a link and marks it selected by
 * the path) OR an `event` (the shell sends `cc-mobile-nav` with that detail,
 * and the page answers). A page tells the shell what it shows with ONE event,
 * `cc-phone-bar` (`phoneBarEvent.ts`): the selected tab, the tabs to hide and
 * the counts to draw. CRM is the one dynamic bar: its page names the tabs in
 * `cc-crm-nav`. Projects keeps `app/projects/lib/phoneTabs.ts` as its source.
 *
 * Fence: `phoneBar.test.ts`.
 */

import { myWorkEnabled } from "@/app/projects/lib/myWorkFlag";
import { projectsPhoneTabs } from "@/app/projects/lib/phoneTabs";

export interface PhoneTab {
  /** Unique within the bar. A page names a tab by this in `active`, `hide` and `counts`. */
  id: string;
  /** 12 characters at most, or the label truncates at 360 px. */
  label: string;
  /** A Lucide icon name. */
  icon: string;
  /** A place goes somewhere and shows selected. An act does one thing. */
  kind: "place" | "act";
  /** The primary colour. Only an act, and only one per bar. */
  accent?: boolean;
  /** A route. The shell draws a link, selected by the path. */
  href?: string;
  /** The `cc-mobile-nav` detail the page answers. */
  event?: string;
  /** Shown only to a member who holds this feature. */
  feature?: string;
  /** Shown only to a member who holds this capability. */
  capability?: string;
  /** Shown only when the Ask tab is absent (the shell chat flag off). */
  withoutAsk?: boolean;
  /** Shown on the exact path of the entry only, not on its sub pages. */
  atRootOnly?: boolean;
  /** The noun in the count's words: `3 unscheduled`. */
  countLabel?: string;
}

export type PhoneBarEntry =
  | { tabs: PhoneTab[] | (() => PhoneTab[]); defaultActive?: string }
  | { none: string }
  | { dynamic: "cc-crm-nav" };

export interface RegisteredBar {
  /** The app, for the fence's messages. */
  app: string;
  /** The path prefix. With `exact`, only this path. */
  path?: string;
  exact?: boolean;
  /** A matcher for a pattern that a prefix cannot say. It beats every prefix. */
  match?: (pathname: string) => boolean;
  entry: PhoneBarEntry;
}

/** The Projects tabs, from their own source, as the flag reads now. */
export function projectsBarTabs(myWork: boolean): PhoneTab[] {
  return projectsPhoneTabs(myWork).map((t) => ({
    id: t.event,
    event: t.event,
    label: t.label,
    icon: t.icon,
    kind: t.kind,
    accent: t.accent,
  }));
}

const projectsTabs = (): PhoneTab[] => projectsBarTabs(myWorkEnabled());

export const PHONE_BARS: RegisteredBar[] = [
  {
    app: "My Day",
    path: "/",
    exact: true,
    entry: {
      none: "One page. CC-9 (task 1977) puts Capture at the thumb, with CC-1's capture.",
    },
  },
  {
    app: "Calendar",
    path: "/calendar",
    exact: true,
    entry: {
      tabs: [
        { id: "calendar-today", event: "calendar-today", label: "Today", icon: "CalendarCheck", kind: "act" },
        { id: "calendar-schedule", event: "calendar-schedule", label: "Schedule", icon: "CalendarPlus", kind: "act", countLabel: "unscheduled" },
        { id: "calendar-reminders", event: "calendar-reminders", label: "Reminders", icon: "Bell", kind: "act", countLabel: "open" },
      ],
    },
  },
  {
    app: "People",
    path: "/people",
    entry: {
      tabs: [
        { id: "people-directory", href: "/people", label: "Directory", icon: "Users", kind: "place", feature: "people" },
        { id: "people-chart", href: "/people/chart", label: "Org chart", icon: "Network", kind: "place", feature: "people" },
        { id: "people-search", href: "/people/search", label: "Find skills", icon: "Search", kind: "place", feature: "people", capability: "admin:members:read" },
      ],
    },
  },
  {
    app: "Projects",
    path: "/projects",
    entry: { tabs: projectsTabs },
  },
  { app: "CRM", path: "/crm", entry: { dynamic: "cc-crm-nav" } },
  {
    app: "Knowledge",
    path: "/knowledge",
    entry: {
      tabs: [
        { id: "knowledge-rail", event: "knowledge-rail", label: "Spaces", icon: "BookOpen", kind: "place" },
        { id: "knowledge-search", href: "/knowledge/search", label: "Search", icon: "Search", kind: "place" },
        { id: "knowledge-new-page", event: "knowledge-new-page", label: "New page", icon: "SquarePen", kind: "act", accent: true },
      ],
    },
  },
  {
    app: "Email",
    path: "/email",
    exact: true,
    entry: {
      tabs: [
        { id: "email-accounts", event: "email-accounts", label: "Mailboxes", icon: "Inbox", kind: "place" },
        { id: "email-automation", event: "email-automation", label: "Automation", icon: "Zap", kind: "place" },
        { id: "email-compose", event: "email-compose", label: "Write", icon: "SquarePen", kind: "act", accent: true },
        { id: "email-ai", event: "email-ai", label: "AI Chat", icon: "MessageCircle", kind: "act", withoutAsk: true },
      ],
    },
  },
  {
    app: "WhatsApp",
    path: "/whatsapp",
    entry: {
      tabs: [
        { id: "wa-triage", event: "wa-triage", label: "Triage", icon: "Filter", kind: "act", atRootOnly: true },
        { id: "wa-sections", event: "wa-sections", label: "Sections", icon: "LayoutGrid", kind: "place" },
      ],
    },
  },
  {
    app: "Chat",
    path: "/chat",
    exact: true,
    entry: {
      tabs: [
        { id: "chats", event: "chats", label: "Chats", icon: "MessageCircle", kind: "place" },
        { id: "files", event: "files", label: "Files", icon: "FolderOpen", kind: "place" },
        { id: "chat-new", event: "chat-new", label: "New chat", icon: "MessageSquarePlus", kind: "act", accent: true },
      ],
    },
  },
  {
    app: "Studio",
    path: "/studio",
    exact: true,
    entry: {
      tabs: [
        { id: "studio-library", event: "studio-library", label: "Library", icon: "LayoutGrid", kind: "place" },
        { id: "studio-modules", event: "studio-modules", label: "Modules", icon: "Boxes", kind: "place", feature: "workflows" },
        { id: "studio-new", event: "studio-new", label: "New app", icon: "Plus", kind: "act", accent: true, feature: "build.apps" },
      ],
    },
  },
  {
    app: "Artifacts",
    path: "/artifacts",
    exact: true,
    entry: {
      tabs: [
        { id: "artifacts-chats", event: "artifacts-chats", label: "Chats", icon: "MessageCircle", kind: "place" },
        { id: "artifacts-workspaces", event: "artifacts-workspaces", label: "Workspaces", icon: "LayoutGrid", kind: "place" },
      ],
    },
  },
  {
    app: "Approvals",
    path: "/approvals",
    exact: true,
    entry: { none: "One list. Each row holds its own Approve and Reject." },
  },
  {
    app: "Organisation",
    path: "/settings/organization",
    exact: true,
    entry: {
      tabs: [
        { id: "org-members", event: "org-members", label: "Members", icon: "Users", kind: "place" },
        { id: "org-requests", event: "org-requests", label: "Requests", icon: "Inbox", kind: "place", countLabel: "waiting" },
        { id: "org-invite", event: "org-invite", label: "Invite", icon: "UserPlus", kind: "act", accent: true },
      ],
    },
  },
  {
    app: "Appearance",
    path: "/settings/appearance",
    exact: true,
    entry: { none: "One settings page. Nothing to move between, and nothing new to make." },
  },
  {
    app: "Integrations",
    path: "/integrations",
    exact: true,
    entry: {
      tabs: [
        { id: "integrations-views", event: "integrations-views", label: "Views", icon: "LayoutGrid", kind: "place" },
        { id: "integrations-add", event: "integrations-add", label: "Add", icon: "Plus", kind: "act", accent: true },
      ],
    },
  },
  {
    app: "Tasks",
    path: "/tasks",
    entry: {
      defaultActive: "tasks-inbox",
      tabs: [
        { id: "tasks-inbox", event: "tasks-inbox", label: "Inbox", icon: "Inbox", kind: "place" },
        { id: "tasks-lists", event: "tasks-lists", label: "Lists", icon: "ListChecks", kind: "place" },
        { id: "tasks-capture", event: "tasks-capture", label: "Capture", icon: "Plus", kind: "act", accent: true },
        { id: "tasks-assistant", event: "tasks-assistant", label: "Assistant", icon: "Sparkles", kind: "act", withoutAsk: true },
      ],
    },
  },
  {
    app: "Notes",
    path: "/notes",
    exact: true,
    entry: {
      tabs: [
        { id: "notes-record", event: "notes-record", label: "Record", icon: "Mic", kind: "act", accent: true },
        { id: "notes-join", event: "notes-join", label: "Join call", icon: "Video", kind: "act" },
        { id: "notes-upload", event: "notes-upload", label: "Upload", icon: "Upload", kind: "act" },
        { id: "notes-glossary", event: "notes-glossary", label: "Glossary", icon: "BookMarked", kind: "act" },
      ],
    },
  },
  {
    app: "Workshop",
    match: (p) => p.startsWith("/build/apps/") && p.endsWith("/edit"),
    entry: {
      tabs: [
        { id: "workshop-chat", event: "workshop-chat", label: "Chat", icon: "Sparkles", kind: "act", accent: true },
        { id: "workshop-preview", event: "workshop-preview", label: "Preview", icon: "Play", kind: "act" },
        { id: "workshop-code", event: "workshop-code", label: "Code", icon: "FileCode", kind: "act" },
        { id: "workshop-tests", event: "workshop-tests", label: "Tests", icon: "FlaskConical", kind: "act" },
      ],
    },
  },
];

/** The entry for a path: a matcher first, then the longest prefix. `null` when none. */
export function phoneBarFor(pathname: string | null): RegisteredBar | null {
  if (!pathname) return null;
  for (const r of PHONE_BARS) if (r.match?.(pathname)) return r;
  let best: RegisteredBar | null = null;
  for (const r of PHONE_BARS) {
    if (r.path === undefined) continue;
    const hit =
      r.exact || r.path === "/"
        ? pathname === r.path
        : pathname === r.path || pathname.startsWith(`${r.path}/`);
    if (hit && (!best || r.path.length > (best.path?.length ?? -1))) best = r;
  }
  return best;
}

/** What the member can see, and what the page reported. */
export interface PhoneBarView {
  /** The Ask tab is in the bar. */
  ask: boolean;
  /** Whether the member holds a tab's feature and capability. */
  allowed: (tab: PhoneTab) => boolean;
  /** Tab ids the page hides. */
  hide?: readonly string[];
}

/** The tabs of an entry, in order, after every filter. */
export function resolvePhoneTabs(
  reg: RegisteredBar | null,
  pathname: string,
  view: PhoneBarView,
): PhoneTab[] {
  if (!reg || !("tabs" in reg.entry)) return [];
  const list = typeof reg.entry.tabs === "function" ? reg.entry.tabs() : reg.entry.tabs;
  const hidden = new Set(view.hide ?? []);
  return list.filter((t) => {
    if (hidden.has(t.id)) return false;
    if (t.withoutAsk && view.ask) return false;
    if (t.atRootOnly && pathname !== reg.path) return false;
    return view.allowed(t);
  });
}

/** Whether a link tab is selected for this path. The path must match whole. */
export function hrefSelected(tab: PhoneTab, pathname: string | null): boolean {
  return !!tab.href && pathname === tab.href;
}
