/**
 * The command bar's tier 0: what it can show with no server call
 * (`navigation_shell.md` §6, NS-1).
 *
 * Two groups come from here:
 *
 *   • **Do** — jobs, such as "New task" or "Write an email". A job is a link
 *     that opens its form (`?do=<id>`, `doJob.ts`), so it works from any page.
 *   • **Go to** — EVERY app this member holds, from `visibleSections`, the
 *     same input the sidebar reads. It is NOT the sidebar's list. The sidebar
 *     shows only the apps a member pins, so the bar is the one door to an
 *     app the member did not pin (owner, 2026-10-11). Never build this group
 *     from the pins, from `shellSidebar` or from `pinnedPanes`. A preview
 *     pane, or a pane the member lacks, is not in `visibleSections`, so the
 *     bar never offers it.
 *
 * Everything here is pure, so the ranking rules are assertions, not clicks.
 * Fence: `registry.test.ts`.
 *
 * ⚠️ §6.7 rule 5, plain words: every item carries a `kind` the bar prints, such
 * as "Open" or "Start". A member never sees an id or a route.
 */
import type { NavPane, NavSection } from "@/lib/nav";
import { shellNavOn } from "@/lib/shell/shellNav";

/** The flag for the shell bar and its command bar. Off, nothing changes. */
export function shellBarOn(): boolean {
  if (process.env.NEXT_PUBLIC_SHELL_BAR === "1") return true;
  // ⚠️ Development and test builds ONLY: a browser may turn the bar on for
  // itself (`localStorage["cc-shell-bar"] = "1"`), so the browser suite runs
  // the bar both on and off in one dev server. Next inlines NODE_ENV, so a
  // production build drops this branch, and production has no such door.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-shell-bar") === "1";
    } catch {
      return false;
    }
  }
  return false;
}

/** Open the command bar, optionally with words in it (`detail.query`). */
export const OPEN_COMMAND_BAR = "shell:open-command-bar";
/** Put words into the page's own filter (§6.7 rule 3, "Show all in …"). */
export const FILL_PAGE_FILTER = "shell:fill-page-filter";

/**
 * The word a page's own list box uses (§6.7 rule 2): "Filter" while the shell
 * bar is on, because "Search" is the command bar's word. "Search" while it is
 * off, so nothing changes until the flag does.
 */
export function filterWord(): "Filter" | "Search" {
  return shellBarOn() ? "Filter" : "Search";
}

export interface ShellJob {
  id: string;
  label: string;
  /** Other words a member might type for this job. */
  words: readonly string[];
  /** The pane that owns it. The job shows only if the member holds that pane. */
  app: string;
  /** Where it opens. `?do=` names the job for the app (`doJob.ts`). */
  href: string;
  icon: string;
}

/**
 * The jobs, one or two per live app. NS-2 moves each into its app's
 * manifest (§5.1). Until then they live here, ONE list, so the bar and the
 * apps cannot disagree about what a job is called.
 */
export const JOBS: readonly ShellJob[] = [
  {
    id: "capture",
    label: "New task",
    words: ["task", "todo", "to-do", "capture", "add", "remind", "reminder", "note"],
    app: "/tasks",
    href: "/tasks?do=capture",
    icon: "CirclePlus",
  },
  {
    id: "compose",
    label: "Write an email",
    words: ["email", "mail", "compose", "send", "message", "write"],
    app: "/email",
    href: "/email?do=compose",
    icon: "PenSquare",
  },
  {
    id: "plan-day",
    label: "Plan my day",
    words: ["calendar", "schedule", "plan", "day", "timebox", "today", "agenda"],
    app: "/calendar",
    href: "/calendar",
    icon: "CalendarClock",
  },
  {
    // A root node in Projects is a SPACE (the tree grammar of migration 193),
    // so the label names what opens: the "New space" row in the tree.
    id: "new-project",
    label: "New space",
    words: ["space", "project", "workspace", "department", "board"],
    app: "/projects",
    href: "/projects?do=new-project",
    icon: "FolderPlus",
  },
  {
    id: "new-chat",
    label: "New chat",
    words: ["chat", "conversation", "assistant", "ai", "talk", "thread"],
    app: "/chat",
    href: "/chat?do=new-chat",
    icon: "MessageSquarePlus",
  },
  {
    // Admin only, as its pane is. The bar shows a job only when the member
    // holds its pane, and `visibleSections` holds Organisation for an admin.
    id: "invite",
    label: "Invite a member",
    // ⚠️ Not "members" or "seat". Those name the roster, so an admin who types
    // them and presses Enter must open Organisation, not the invite form.
    words: ["invite", "join", "onboard", "welcome", "newcomer"],
    app: "/settings/organization",
    href: "/settings/organization?do=invite",
    icon: "UserPlus",
  },
  {
    id: "find-person",
    label: "Find a colleague",
    words: ["people", "person", "colleague", "directory", "who", "team", "contact"],
    app: "/people",
    href: "/people",
    icon: "Users",
  },
  {
    id: "edit-profile",
    label: "Update my profile",
    words: ["profile", "skills", "cv", "resume", "hours", "me"],
    app: "/people/me",
    href: "/people/me",
    icon: "UserPen",
  },
  {
    id: "appearance",
    label: "Change how Metorite looks",
    words: ["appearance", "theme", "dark", "light", "colour", "color", "density", "accent", "sidebar"],
    app: "/settings/appearance",
    href: "/settings/appearance",
    icon: "Palette",
  },
  // Two link jobs: neither app has a form to open, so the job opens the app
  // where the work waits (owner decision, 2026-10-09). ⚠️ Not "chat" or
  // "message": New chat and Write an email own those words.
  {
    id: "whatsapp-reply",
    label: "Reply on WhatsApp",
    words: ["whatsapp", "wa", "reply", "customer", "customers"],
    app: "/whatsapp",
    href: "/whatsapp",
    icon: "MessageCircle",
  },
  {
    id: "review-approvals",
    label: "Review pending approvals",
    words: ["approve", "approval", "approvals", "pending", "review", "outgoing"],
    app: "/approvals",
    href: "/approvals",
    icon: "ShieldCheck",
  },
];

export type ItemGroup = "do" | "go";

export interface BarItem {
  key: string;
  group: ItemGroup;
  label: string;
  /** The plain line under the label: what it does, or where it opens. */
  hint: string;
  icon: string;
  href: string;
  /** The pane it belongs to, for the context boost. */
  app: string;
  words: readonly string[];
}

/** A Center's landing page. D49 keeps the route, and nothing links to it. */
const CENTER_ROUTE = "/centers/";

/**
 * Every pane this member holds, from ALL of `visibleSections`, never from the
 * pins. The bar offers each one as "Open <App>".
 *
 * A Center's landing page is left out. It is in `visibleSections` only with
 * the preview flag on, and D49 says nothing links to a Center. A setting
 * (My Profile, Appearance) stays: the account menu opens it as a page, and
 * two jobs belong to it. Fence: `registry.test.ts`, "every app the member
 * holds, with nothing pinned".
 */
export function heldPanes(sections: readonly NavSection[]): NavPane[] {
  return sections.flatMap((s) => s.items).filter((p) => !p.href.startsWith(CENTER_ROUTE));
}

/**
 * Every tier-0 item for this member: a job only when its app is held, and
 * every held app as "Go to".
 */
export function buildItems(
  panes: readonly NavPane[],
  /**
   * Print the manifest's purpose (NS-2). Behind `NEXT_PUBLIC_SHELL_NAV`,
   * because this bar is ON in production and a new line here reaches every
   * organization at once (verifier and review of NS-2, 2026-10-09).
   */
  purpose: boolean = shellNavOn(),
): BarItem[] {
  const held = new Map(panes.map((p) => [p.href, p]));
  const jobs: BarItem[] = JOBS.filter((j) => held.has(j.app)).map((j) => ({
    key: `do:${j.id}`,
    group: "do",
    label: j.label,
    hint: `Start · ${held.get(j.app)!.label}`,
    icon: j.icon,
    href: j.href,
    app: j.app,
    words: j.words,
  }));
  const apps: BarItem[] = panes.map((p) => ({
    key: `go:${p.href}`,
    group: "go",
    label: `Open ${p.label}`,
    // The manifest's purpose, in job words (§5.1), with the shell nav on.
    // `note` was written for operators ("Action Broker · outward writes
    // awaiting review").
    hint: purpose ? (p.blurb ?? p.note) : p.note,
    icon: p.icon,
    href: p.href,
    app: p.href,
    words: [p.label, ...`${purpose ? (p.blurb ?? "") : ""} ${p.note}`.split(/[^A-Za-z]+/)].filter(Boolean),
  }));
  return [...jobs, ...apps];
}

/** The app the member is in: the held pane with the longest matching href. */
export function contextPane(pathname: string, panes: readonly NavPane[]): NavPane | null {
  let best: NavPane | null = null;
  for (const p of panes) {
    const hit = pathname === p.href || pathname.startsWith(`${p.href}/`);
    if (hit && (!best || p.href.length > best.href.length)) best = p;
  }
  return best;
}

const norm = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}\s-]/gu, " ").trim();
const tokens = (s: string) => norm(s).split(/\s+/).filter(Boolean);

/**
 * Words that carry no meaning here. A member types the way they speak, so
 * "remind me to call" must not need "me" to match anything.
 */
const FILLER = new Set(["a", "an", "the", "my", "me", "to", "for", "of", "in", "on", "please", "i", "want", "need"]);
/** Verbs that say "do something", so they fit any job. */
const DO_VERBS = new Set(["new", "create", "add", "start", "make", "begin"]);
/** Verbs that say "take me there", so they fit any app. */
const GO_VERBS = new Set(["open", "go", "show", "goto", "view", "see"]);

/**
 * How well one item answers the words, or 0 for no match.
 *
 * People type sentences, not keywords: "new email to priya". So a word may
 * miss, as "priya" does, and the item still matches, when three things hold:
 *   • at least HALF the words land, on the label or on the item's words;
 *   • at least one that lands is a real content word, not only a verb such
 *     as "new" or "open";
 *   • each word that misses costs rank.
 * So "new email to priya" finds "Write an email", and "new holiday" or
 * "email holiday party budget" finds nothing. A sentence the registry cannot
 * read is the Ask row's to answer (§6.2).
 */
export function score(item: BarItem, query: string): number {
  const q = tokens(query).filter((t) => !FILLER.has(t));
  if (q.length === 0) return 0;
  const label = norm(item.label);
  const labelWords = tokens(item.label);
  const words = item.words.map(norm);
  let total = 0;
  let matched = 0;
  let content = 0;
  for (const t of q) {
    let best = 0;
    // ⚠️ A verb is never content, even where a label holds it: "new holiday"
    // must not find "New task" through the word "new".
    if (DO_VERBS.has(t) || GO_VERBS.has(t)) {
      const fits = (item.group === "do" && DO_VERBS.has(t)) || (item.group === "go" && GO_VERBS.has(t));
      if (fits) {
        matched += 1;
        total += 6;
      }
      continue;
    }
    if (labelWords.some((w) => w === t)) best = 40;
    else if (labelWords.some((w) => w.startsWith(t))) best = 30;
    else if (words.some((w) => w === t)) best = 25;
    else if (words.some((w) => w.startsWith(t))) best = 18;
    else if (t.length >= 3 && label.includes(t)) best = 10;
    if (best === 0) continue;
    matched += 1;
    if (best >= 18) content += 1;
    total += best;
  }
  // A query of verbs alone ("open", "new") lists everything of its kind.
  const onlyVerbs = q.every((t) => DO_VERBS.has(t) || GO_VERBS.has(t));
  if (onlyVerbs ? matched === 0 : content === 0 || matched * 2 < q.length) return 0;
  total -= 15 * (q.length - matched);
  if (label.startsWith(norm(query))) total += 30;
  return Math.max(total, 1);
}

export interface RankInput {
  items: readonly BarItem[];
  query: string;
  /** The app the member is in, when the "in Email" token is on. */
  context: string | null;
  /** Item keys the member opened, newest first. */
  recent: readonly string[];
  /**
   * Job ids in the member's preset order (NS-7, §8.1), for the empty bar
   * only. A job the preset does not name follows the named ones.
   */
  order?: readonly string[];
}

/**
 * The items to show, best first. Jobs rank above apps at an equal score,
 * because the bar is for doing (§6.2 puts Do first).
 *
 * With no words, the bar shows the member's recent items, then the jobs of
 * the app they are in, then the other jobs in the preset's order (`order`).
 */
export function rank({ items, query, context, recent, order = [] }: RankInput): BarItem[] {
  const recency = (k: string) => {
    const i = recent.indexOf(k);
    return i < 0 ? 0 : Math.max(1, 8 - i);
  };
  if (!query.trim()) {
    const recents = recent.map((k) => items.find((i) => i.key === k)).filter((i): i is BarItem => !!i);
    // A job the preset names ranks by its place there. One it does not name
    // keeps the list's own order, after them.
    const place = (i: BarItem) => {
      const at = order.indexOf(i.key.replace(/^do:/, ""));
      return at < 0 ? order.length : at;
    };
    const jobs = items
      .filter((i) => i.group === "do" && !recent.includes(i.key))
      .sort((a, b) => Number(b.app === context) - Number(a.app === context) || place(a) - place(b));
    return [...recents, ...jobs].slice(0, 8);
  }
  return items
    .map((item) => {
      const s = score(item, query);
      if (s === 0) return null;
      const boost = (item.app === context ? 15 : 0) + recency(item.key) * 2 + (item.group === "do" ? 1 : 0);
      return { item, s: s + boost };
    })
    .filter((x): x is { item: BarItem; s: number } => x !== null)
    .sort((a, b) => b.s - a.s)
    .map((x) => x.item);
}

/** Recent items per member, in this browser (§6.4 rule 5, before NS-2's column). */
export function recentKey(email: string | null): string {
  return `cc-shell-recent::${(email ?? "anon").toLowerCase()}`;
}

export function readRecent(email: string | null): string[] {
  try {
    const raw = localStorage.getItem(recentKey(email));
    const list = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(list) ? list.filter((x): x is string => typeof x === "string").slice(0, 8) : [];
  } catch {
    return [];
  }
}

export function rememberRecent(email: string | null, key: string): void {
  try {
    const next = [key, ...readRecent(email).filter((k) => k !== key)].slice(0, 8);
    localStorage.setItem(recentKey(email), JSON.stringify(next));
  } catch {
    /* storage off: the bar still works, it just forgets */
  }
}
