/**
 * The pure half of the Apps section of `/studio` for Studio packages
 * (`ai_studio.md` §14 BETA-1, task 1809).
 *
 * `/studio` read only the App Workshop list, so a member who made and published
 * a package saw "No apps yet". This module turns the package list of the member
 * (`GET /api/studio/packages`) into cards, merges it with the App Workshop apps,
 * and counts the pills. It draws nothing. Fence: `packageLibrary.test.ts`.
 */
import { previewHref } from "./packagePreview";
import type { OrgPackage, PackageSummary } from "./realDoor";

export interface PackageCard {
  kind: "package";
  id: string;
  name: string;
  state: "live" | "draft";
  /** The app page of the package. */
  href: string;
  /** The last change, as the gateway wrote it. Null when it wrote none. */
  changedAt: string | null;
}

/** An App Workshop app, as far as the merge reads it. */
export interface WorkshopLike {
  live_version?: number | null;
  updated_at: string;
  owner_email: string;
  role?: string;
}

export type LibraryItem<A extends WorkshopLike> =
  | { kind: "workshop"; app: A; key: string; changedAt: string }
  | (PackageCard & { key: string });

/** A package is live when it holds a published version, and a draft when it does not. */
export function packageCard(p: PackageSummary): PackageCard {
  return {
    kind: "package",
    id: p.id,
    name: p.name.trim() || "Untitled app",
    state: p.live_version != null ? "live" : "draft",
    href: previewHref(p.id),
    changedAt: p.updated_at,
  };
}

const stamp = (iso: string | null): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : 0;
};

/** One list: the packages and the Workshop apps, the newest change first. */
export function mergeLibrary<A extends WorkshopLike & { slug: string }>(
  apps: readonly A[], packages: readonly PackageSummary[],
): LibraryItem<A>[] {
  const items: LibraryItem<A>[] = [
    ...packages.map((p) => ({ ...packageCard(p), key: `pkg:${p.id}` })),
    ...apps.map((app): LibraryItem<A> => ({ kind: "workshop", app, key: `app:${app.slug}`, changedAt: app.updated_at })),
  ];
  return items.sort((a, b) => stamp(b.changedAt) - stamp(a.changedAt));
}

export interface LibraryCounts {
  all: number;
  mine: number;
  team: number;
  drafts: number;
  templates: number;
}

/**
 * The pills. The list of packages is the member's own, so each package counts in
 * "Mine" and in "All", and a package with no live version counts in "Drafts".
 * A package is never Team or a Template.
 */
export function libraryCounts(
  apps: readonly (WorkshopLike & { visibility?: string; is_template?: boolean })[],
  packages: readonly PackageSummary[],
  myEmail: string,
): LibraryCounts {
  const mine = apps.filter((a) => a.role === "own" || a.owner_email.toLowerCase() === myEmail).length;
  return {
    all: apps.length + packages.length,
    mine: mine + packages.length,
    team: apps.filter((a) => a.visibility === "org").length,
    drafts: apps.filter((a) => !a.live_version).length + packages.filter((p) => p.live_version == null).length,
    templates: apps.filter((a) => a.is_template).length,
  };
}

export type LibraryFilter = "all" | "mine" | "team" | "drafts" | "templates" | "members";

/** Does a package show under this pill? The counts and the grid use the same rule. */
export function packageShows(filter: LibraryFilter, p: Pick<PackageSummary, "live_version">): boolean {
  if (filter === "all" || filter === "mine") return true;
  // "members" is the admin view. It draws its own grid, so no member package shows beside it.
  if (filter === "drafts") return p.live_version == null;
  return false;
}

/** "5m ago", "3h ago", "2d ago", then a short date. Null gives "no change yet". */
export function changedWords(iso: string | null, now: number = Date.now()): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return "No change yet";
  const mins = Math.floor((now - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** The brief that starts a chat. A person edits it and sends it, so it carries no typed mark. */
export function appBrief(description: string): string {
  const text = description.trim();
  return text ? `Build me an app: ${text}` : "I want to build an app. Ask me what it should track, then build it.";
}

/** The line of an object that has no list action. It says what is missing and the next step. */
export const NO_LIST_LINE =
  "This app has no list action for this object, so it cannot show its records. Ask Mira to add a list action.";

export function noListLine(label: string | null | undefined): string {
  const name = label?.trim();
  return name
    ? `The ${name} object has no list action, so it cannot show its records. Ask Mira to add a list action.`
    : NO_LIST_LINE;
}

/** The words when the package list did not load. */
export const PACKAGES_FAILED_LINE =
  "Your Studio apps did not load. Refresh the list to try again.";

// --- the admin view, "All members" (STU-J4) ---------------------------------

/** The words for a package that has no audit row. Each says what is empty. */
export const NO_RUNS_LINE = "No runs yet";
export const NO_ERRORS_LINE = "No errors";
export const USE_NOT_TRACKED_LINE = "Not tracked yet";
/** The label of the count. A "run" here is one action call through the door, not a workflow run. */
export const ACTION_CALLS_LABEL = "Action calls";

export interface OrgCard {
  id: string;
  name: string;
  /** The owner's address. The admin view names the owner of each package. */
  owner: string;
  mine: boolean;
  state: "live" | "draft";
  /** "v3", or "No live version" for a draft. */
  version: string;
  calls: string;
  /** Null when the package has no error. Then `errorLine` says so. */
  error: { action: string; outcome: "failed" | "refused"; when: string } | null;
  errorLine: string;
  use: string;
  changedAt: string | null;
}

/** "1,204 action calls", "1 action call", or the empty state. The cap shows as "10,000+". */
export function callsWords(count: number, capped: boolean): string {
  if (count <= 0) return NO_RUNS_LINE;
  const n = count.toLocaleString("en-US") + (capped ? "+" : "");
  return `${n} action ${count === 1 && !capped ? "call" : "calls"}`;
}

/** Only the action, the outcome and the time. The gateway sends no reason text, and this shows none. */
export function orgCard(p: OrgPackage, now: number = Date.now()): OrgCard {
  const e = p.last_error;
  const outcome = e?.outcome === "failed" || e?.outcome === "refused" ? e.outcome : null;
  const error: OrgCard["error"] = e && outcome
    ? { action: e.action?.trim() || "an action", outcome, when: changedWords(e.at, now) }
    : null;
  return {
    id: p.id,
    name: p.name.trim() || "Untitled app",
    owner: p.owner_email,
    mine: p.mine,
    state: p.live_version != null ? "live" : "draft",
    version: p.live_version != null ? `v${p.live_version}` : "No live version",
    calls: callsWords(p.action_calls, p.action_calls_capped),
    error,
    errorLine: error ? `${error.outcome === "failed" ? "Failed" : "Refused"}: ${error.action}, ${error.when}` : NO_ERRORS_LINE,
    use: USE_NOT_TRACKED_LINE,
    changedAt: p.updated_at,
  };
}

/** The pill of the admin view shows only when the gateway said `admin: true`. */
export function membersPill(admin: boolean, count: number): { id: "members"; label: string; count: number } | null {
  return admin ? { id: "members", label: "All members", count } : null;
}

/** The filter that the grid and the pills both use. "members" falls back to "all" when its pill is gone. */
export function effectiveFilter(chosen: LibraryFilter, hasMembersPill: boolean): LibraryFilter {
  return chosen === "members" && !hasMembersPill ? "all" : chosen;
}
