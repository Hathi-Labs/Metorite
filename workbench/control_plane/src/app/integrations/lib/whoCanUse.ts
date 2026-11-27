// ── Who can use an integration: the console rules (ACC-2) ──────────────────
//
// Fences: `whoCanUse.test.ts`, `accessSave.test.ts` and
// `components/WhoCanUse.test.tsx`. `accessSave.ts` holds the writes.
//
// Spec: project-docs/specs/integrations_console.md §3.9.2 to §3.9.5.
//
// The gateway owns the rules (`/admin/integration-access`). This file holds
// what the console decides about them, as pure functions with tests: which row
// gets a control, which stored rule has no row, the words, and the PUT body.
//
// ⚠️ The gateway names the services that take a rule. The console never adds
// one. A built-in connector, a mailbox, a number and a stored key take no rule
// (a PUT answers 422), so they get no control.

import { mcpName, type Connection } from "./connections";

export type AccessMode = "everyone" | "chosen" | "admins";

/** One service of `GET /admin/integration-access`. */
export interface AccessItem {
  service: string;
  /** `mcp`, `api`, or "" for a stored rule of a service that left the list. */
  kind: string;
  mode: AccessMode;
  /** Group slugs, with no `group:` prefix. */
  groups: string[];
  /** Email addresses. */
  people: string[];
  set_by: string;
  set_at: string;
  /** False when the service left the list. Only "Everyone" clears its rule. */
  listed: boolean;
  /** False when the gateway cannot read the stored rule. It is then admins only. */
  readable: boolean;
}

export interface AccessList {
  services: AccessItem[];
  /** False when the whole stored value cannot be read: all is admins only. */
  readable: boolean;
}

export interface RuleBody {
  mode: AccessMode;
  groups: string[];
  people: string[];
}

const MODES: readonly string[] = ["everyone", "chosen", "admins"];

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/** One item of the answer, or null for a shape the console cannot draw. */
export function parseAccessItem(raw: unknown): AccessItem | null {
  const r = (raw ?? {}) as Record<string, unknown>;
  if (typeof r.service !== "string" || !r.service) return null;
  if (typeof r.mode !== "string" || !MODES.includes(r.mode)) return null;
  return {
    service: r.service,
    kind: typeof r.kind === "string" ? r.kind : "",
    mode: r.mode as AccessMode,
    groups: strings(r.groups),
    people: strings(r.people),
    set_by: typeof r.set_by === "string" ? r.set_by : "",
    set_at: typeof r.set_at === "string" ? r.set_at : "",
    listed: r.listed !== false,
    readable: r.readable !== false,
  };
}

/** The body of the GET. A body with no list reads as no service at all. */
export function parseAccessList(body: unknown): AccessList {
  const b = (body ?? {}) as { services?: unknown; readable?: unknown };
  const services = Array.isArray(b.services) ? b.services : [];
  return {
    services: services.map(parseAccessItem).filter((i): i is AccessItem => i !== null),
    readable: b.readable !== false,
  };
}

/**
 * The service name of a row that may take a rule (§3.9.2): `mcp-<name>` for an
 * MCP server, `api-<template>` for an API connection of the organization. Null
 * for each other row. A member connection takes no rule (§3.9.3).
 */
export function serviceOfRow(row: Connection): string | null {
  if (row.scope !== "org") return null;
  if (row.kind === "mcp") {
    const name = mcpName(row);
    return name ? `mcp-${name}` : null;
  }
  if (row.kind === "oauth" && row.provider.startsWith("api:") && row.provider.length > 4) {
    return `api-${row.provider.slice(4)}`;
  }
  return null;
}

/**
 * The rule that a row draws, or null for a row with no control. `list` is null
 * for an org off the flag and for a viewer with no right (404 and 403), and
 * undefined while it loads. Both draw nothing.
 */
export function rowRule(list: AccessList | null | undefined, row: Connection): AccessItem | null {
  const service = serviceOfRow(row);
  if (!list || !service) return null;
  return list.services.find((i) => i.service === service && i.listed) ?? null;
}

/**
 * The longest time that the list waits for the access rules, from the moment
 * the list arrives. After it, the list shows, whatever the rules read does.
 */
export const ACCESS_WAIT_MS = 2_000;

/**
 * True while a row can still grow: the first read of the rules has no answer
 * and no failure, and a row of the list may take a rule. The list then keeps
 * its skeleton, so no row moves under the pointer. An answer of `null` (404
 * and 403) and a failure both end the wait.
 */
export function waitsForRules(
  rows: readonly Connection[] | null | undefined,
  list: AccessList | null | undefined,
  error: string | null,
): boolean {
  return list === undefined && !error && (rows ?? []).some((r) => serviceOfRow(r) !== null);
}

/**
 * The stored rules that no row of the list shows: a removed MCP server, an old
 * connector key (`listed: false`), or an API connection that is not connected.
 * An admin must see them to change or clear them. Empty while the whole value
 * cannot be read, because the banner says it and no save can repair it.
 */
export function unmatchedRules(
  list: AccessList | null | undefined,
  rows: readonly Connection[],
): AccessItem[] {
  if (!list || !list.readable) return [];
  const shown = new Set(rows.map((r) => rowRule(list, r)?.service).filter(Boolean));
  return list.services.filter((i) => i.mode !== "everyone" && !shown.has(i.service));
}

// ── Words ─────────────────────────────────────────────────────────────────

export const UNREADABLE_ALL =
  "Metorite cannot read the stored access rules. So only members who manage integrations " +
  "can use each integration. This screen cannot repair it. Ask Metorite support.";

export const LOAD_FAILED = "Could not read who can use each integration.";

export const SAVE_FAILED = "Metorite could not save the rule. Try again.";

export const RULE_FACTS =
  "Members who manage integrations can always use it. A rule only limits access, and a change " +
  "applies from the next run.";

export const MODE_CHOICES: ReadonlyArray<{ value: AccessMode; label: string; description: string }> = [
  { value: "everyone", label: "Everyone", description: "Each member whose role allows integrations." },
  {
    value: "chosen",
    label: "Chosen teams and people",
    description: "Only the teams and people that you pick.",
  },
  { value: "admins", label: "Admins only", description: "Only members who manage integrations." },
];

export const EMPTY_CHOICE_WARNING =
  "You picked no team and no person. If you save now, only admins can use this integration.";

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The short answer of one rule, as the row shows it. */
export function ruleSummary(item: AccessItem, listReadable = true): string {
  if (!listReadable) return "Admins only";
  // Short, so the control shows it whole on a phone.
  if (!item.readable) return "Admins only (unreadable rule)";
  if (item.mode === "everyone") return "Everyone";
  const parts = [
    item.groups.length ? count(item.groups.length, "team", "teams") : "",
    item.people.length ? count(item.people.length, "person", "people") : "",
  ].filter(Boolean);
  if (item.mode === "admins" || parts.length === 0) return "Admins only";
  return parts.join(" and ");
}

// ── The editor ────────────────────────────────────────────────────────────

/** "Chosen" with nothing picked. The gateway stores it as admins only. */
export function emptyChoice(mode: AccessMode, groups: readonly string[], people: readonly string[]): boolean {
  return mode === "chosen" && groups.length === 0 && people.length === 0;
}

/** The PUT body. Only "chosen" takes names: the gateway refuses them elsewhere. */
export function ruleBody(
  mode: AccessMode,
  groups: readonly string[],
  people: readonly string[],
): RuleBody {
  const chosen = mode === "chosen";
  return {
    mode,
    groups: chosen ? [...groups].sort() : [],
    people: chosen ? [...people].sort() : [],
  };
}

/** One string for one rule. The order of the names does not count. */
function ruleKey(b: RuleBody): string {
  return JSON.stringify([b.mode, [...b.groups].sort(), [...b.people].sort()]);
}

/**
 * True when a save would change nothing. A rule that cannot be read always
 * counts as changed, because a save replaces it.
 */
export function sameRule(item: AccessItem, body: RuleBody): boolean {
  if (!item.readable) return false;
  // "Chosen" with nothing picked is the stored shape of "admins".
  const asStored: RuleBody = emptyChoice(body.mode, body.groups, body.people)
    ? ruleBody("admins", [], [])
    : body;
  return ruleKey(asStored) === ruleKey(ruleBody(item.mode, item.groups, item.people));
}

// ── A rule that changed under the editor ──────────────────────────────────
//
// The list is live: a focus read can bring the change of another admin while
// the editor is open. A save must never replace a rule that the admin did not
// see. So the editor keeps the rule that its choices started from.

/** True when two answers of the gateway hold the same stored rule. */
export function sameStored(a: AccessItem, b: AccessItem): boolean {
  return a.readable === b.readable && ruleKey(a) === ruleKey(b);
}

/** The choices of the editor, and the stored rule that they started from. */
export interface RuleDraft {
  /** The rule that the editor shows as stored. */
  base: AccessItem;
  mode: AccessMode;
  groups: string[];
  people: string[];
  /** True after a choice, or after a stopped save. Only a reload clears it. */
  touched: boolean;
}

export function draftOf(item: AccessItem): RuleDraft {
  return { base: item, mode: item.mode, groups: item.groups, people: item.people, touched: false };
}

/** A choice of the admin. An empty change marks a stopped save. */
export function editDraft(
  draft: RuleDraft,
  change: Partial<Pick<RuleDraft, "mode" | "groups" | "people">>,
): RuleDraft {
  return { ...draft, ...change, touched: true };
}

/**
 * The draft after the editor heard `now`. An untouched draft takes the new
 * rule. A touched draft stays, and `changedRule` then names the new rule.
 * The same object comes back when nothing moves.
 */
export function followRule(draft: RuleDraft, now: AccessItem): RuleDraft {
  return draft.touched || sameStored(draft.base, now) ? draft : draftOf(now);
}

/** The newer rule that stops a save until the admin reloads it, or null. */
export function changedRule(draft: RuleDraft, now: AccessItem): AccessItem | null {
  return sameStored(draft.base, now) ? null : now;
}

/** The words of the editor for a rule that changed under it. */
export function changedText(now: AccessItem): string {
  return (
    `This rule changed after you opened it. It is now: ${ruleSummary(now)}. ` +
    "Reload the rule before you save. A reload replaces your choices here."
  );
}

/** The words of the confirm that stopped on a changed rule. */
export const CLEAR_CHANGED =
  "This rule changed, so Metorite saved nothing. Cancel, then look at the rule again.";

export const RULE_GONE =
  "Metorite cannot find this rule now, so it saved nothing. Open this page again.";

export function saveLabel(mode: AccessMode, groups: readonly string[], people: readonly string[]): string {
  return emptyChoice(mode, groups, people) ? "Save as admins only" : "Save";
}

/** Add or drop one value. */
export function toggleValue(current: readonly string[], value: string): string[] {
  return current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
}

export interface AccessGroup {
  slug: string;
  display_name?: string;
  member_count?: number;
}

export interface AccessPerson {
  email: string;
  display_name?: string;
  status?: string;
}

export interface PickOption {
  value: string;
  label: string;
  hint: string;
}

/**
 * The teams of the picker. A picked slug that the list does not hold stays as
 * an option, so the admin can clear it. `loaded` false (the list is not read
 * yet, or failed) makes no claim about it.
 */
export function groupChoices(
  groups: readonly AccessGroup[],
  picked: readonly string[],
  loaded: boolean,
): PickOption[] {
  const out: PickOption[] = groups.map((g) => ({
    value: g.slug,
    label: g.display_name || g.slug,
    hint: typeof g.member_count === "number" ? count(g.member_count, "member", "members") : "",
  }));
  const known = new Set(out.map((o) => o.value));
  const rest = picked
    .filter((slug) => !known.has(slug))
    .map((slug) => ({ value: slug, label: slug, hint: loaded ? "This team does not exist now" : "" }));
  return [...rest, ...out];
}

/** The people of the picker, with the same rule for a picked address. */
export function personChoices(
  people: readonly AccessPerson[],
  picked: readonly string[],
  loaded: boolean,
): PickOption[] {
  const out: PickOption[] = people.map((p) => {
    const email = p.email.trim().toLowerCase();
    const name = (p.display_name ?? "").trim();
    return {
      value: email,
      label: name || email,
      hint: [name ? email : "", p.status === "invited" ? "Invited" : p.status === "suspended" ? "Suspended" : ""]
        .filter(Boolean)
        .join(" · "),
    };
  });
  const known = new Set(out.map((o) => o.value));
  const rest = picked
    .filter((email) => !known.has(email))
    .map((email) => ({ value: email, label: email, hint: loaded ? "Not in the member list" : "" }));
  return [...rest, ...out];
}

export function filterChoices(options: readonly PickOption[], query: string): PickOption[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...options];
  return options.filter((o) => `${o.label} ${o.value} ${o.hint}`.toLowerCase().includes(q));
}

// ── After a write ─────────────────────────────────────────────────────────

/** The list with the saved item in place of the old one. */
export function withItem(list: AccessList, item: AccessItem): AccessList {
  const known = list.services.some((i) => i.service === item.service);
  return {
    ...list,
    services: known
      ? list.services.map((i) => (i.service === item.service ? item : i))
      : [...list.services, item],
  };
}

export function savedTitle(label: string, item: AccessItem): string {
  return `Who can use ${label}: ${ruleSummary(item)}.`;
}

/** The words of the confirm that clears a rule with no row. */
export function clearCopy(item: AccessItem): { title: string; body: string; confirmLabel: string } {
  return {
    title: `Set ${item.service} to Everyone?`,
    body:
      "Metorite removes the stored rule. If this integration comes back, " +
      "each member whose role allows it can use it. You cannot restore this rule from this screen.",
    confirmLabel: "Set to Everyone",
  };
}
