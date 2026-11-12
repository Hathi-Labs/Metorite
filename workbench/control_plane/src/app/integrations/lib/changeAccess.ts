// ── Change access: the decisions of the panel (WS-55 EA-17c-2, D-EA-13) ────
//
// Spec: `project-docs/specs/external_agent_access.md` §10.8 and §15
// EA-17c-2. `components/AgentAccessPanel.tsx` only draws what this module
// decides, so `AgentsView.test.ts` runs every rule in the node-env vitest.
//
//     PATCH /api/agent-access/connections/<id>   (gateway: EA-17c-1)
//
// ⚠️ An empty `pinned_projects` means EVERY project. So `buildChange` returns
// `[]` for the pins only in "All my projects". After "Only the ones I pick"
// it returns a problem and no body, also when the member drops the last pin.

import {
  NO_SELECTION,
  cleanSelection,
  grantSummary,
  personalSpillover,
  projectLists,
  projectsProblem,
  selectedCount,
  summaryText,
  type ProjectMode,
  type ProjectTree,
  type Selection,
  type SummaryPart,
} from "@/lib/accessTree";

import { AgentsError, agentHost, type AgentConnection } from "./agents";

export const OFFLINE_ACCESS = "offline_access";

/** The order of the switches. A scope the server adds later joins at the end. */
const SCOPE_ORDER = ["projects:read", "projects:write", "context:read"];

// ── The three switch states (spec §10.8) ───────────────────────────────────

export type ScopeState =
  /** On or off: the ceiling holds the scope, or the connection holds it now. */
  | "toggle"
  /** Off and disabled: the agent asked, the member cleared it, no live token carries it. */
  | "reconnect"
  /** Off and disabled: the agent never asked for it. */
  | "not-asked";

/**
 * The state of one scope switch.
 *
 * `ceiling_scopes` holds what a new turn-on may use. A scope that the
 * connection holds now stays switchable, because the server lets a held
 * scope go off and come back (the ceiling binds a turn-on only).
 */
export function scopeState(row: AgentConnection, scope: string): ScopeState {
  if ((row.ceiling_scopes ?? []).includes(scope) || row.scopes.includes(scope)) return "toggle";
  if ((row.requested_scopes ?? []).includes(scope)) return "reconnect";
  return "not-asked";
}

/** The line under a disabled switch, or null for a switch that works. */
export function scopeNote(row: AgentConnection, scope: string): string | null {
  const state = scopeState(row, scope);
  const host = agentHost(row);
  if (state === "reconnect") return `Reconnect ${host} to turn this on.`;
  if (state === "not-asked") return `${host} did not ask for this. It must reconnect to get it.`;
  return null;
}

/**
 * The scopes that get a switch: those the server knows (`known`, the keys of
 * `SCOPE_COPY`), in a fixed order. `offline_access` has no switch.
 */
export function switchScopes(row: AgentConnection, known: readonly string[]): string[] {
  const all = new Set<string>([
    ...row.scopes,
    ...(row.requested_scopes ?? []),
    ...(row.ceiling_scopes ?? []),
  ]);
  const shown = [...all].filter((s) => s !== OFFLINE_ACCESS && known.includes(s));
  const rank = (s: string) => {
    const i = SCOPE_ORDER.indexOf(s);
    return i < 0 ? SCOPE_ORDER.length : i;
  };
  return shown.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/**
 * Turn one scope on or off. "Read your projects and tasks" is needed for the
 * rest, so turning it off clears the others, and a turn-on of another scope
 * turns it on when the row allows that (the consent page works the same way).
 * A disabled scope never changes.
 */
export function toggleDraftScope(
  row: AgentConnection,
  chosen: ReadonlySet<string>,
  scope: string,
): Set<string> {
  const next = new Set(chosen);
  if (scopeState(row, scope) !== "toggle") return next;
  if (next.has(scope)) {
    next.delete(scope);
    if (scope === "projects:read") next.clear();
    return next;
  }
  next.add(scope);
  if (scopeState(row, "projects:read") === "toggle") next.add("projects:read");
  return next;
}

// ── The chips on a row ─────────────────────────────────────────────────────

export interface GrantChips {
  scopes: string[];
  projects: string;
  personal: boolean;
}

/** The grant of a row as chips: scopes, "N projects" or "All projects", and "Personal". */
export function grantChips(
  row: Pick<AgentConnection, "scopes" | "pinned_projects" | "include_personal">,
  words: (scopes: readonly string[]) => string[],
): GrantChips {
  const n = row.pinned_projects.length;
  return {
    scopes: words(row.scopes),
    projects: n === 0 ? "All projects" : `${n} ${n === 1 ? "project" : "projects"}`,
    personal: row.include_personal,
  };
}

// ── The draft ──────────────────────────────────────────────────────────────

export interface Draft {
  scopes: ReadonlySet<string>;
  mode: ProjectMode;
  selection: Selection;
  includePersonal: boolean;
}

export interface Marks {
  /** The picked projects that the member still sees. */
  selection: Selection;
  /** Stored marks that the list does not show, and that the member does not see any more. */
  stale: number;
  /** Stored marks to keep as they are: the list was cut, so the page cannot tell. */
  carried: Selection;
}

/**
 * Split the stored marks of a row. A mark in the tree is a pick. A mark of
 * the member's own personal tree is the personal switch, so it is neither.
 * Any other mark is stale (the list is whole) or carried (the list is cut).
 */
export function splitMarks(row: AgentConnection, tree: ProjectTree): Marks {
  const personal = new Set(tree.personalIds);
  const split = (ids: readonly string[]) => {
    const inTree = ids.filter((id) => tree.byId[id]);
    const other = ids.filter((id) => !tree.byId[id] && !personal.has(id));
    return { inTree, other };
  };
  const pins = split(row.pinned_projects);
  const out = split(row.left_out_projects ?? []);
  const cleaned = cleanSelection(tree, { pins: pins.inTree, leftOut: out.inTree });
  const strays = pins.other.length + out.other.length;
  return tree.truncated
    ? { selection: cleaned, stale: 0, carried: { pins: pins.other, leftOut: out.other } }
    : { selection: cleaned, stale: strays, carried: NO_SELECTION };
}

/**
 * The draft that the panel opens with. Without a list yet, a pinned row
 * opens in "pick" with no ticks, and `buildChange` refuses to save it.
 */
export function initialDraft(row: AgentConnection, tree: ProjectTree | null, known: readonly string[]): Draft {
  const pinned = row.pinned_projects.length > 0;
  const leftOut = (row.left_out_projects ?? []).length > 0;
  // A row with left-out marks and no pin reaches every project less those
  // subtrees. "All my projects" cannot say that, so it opens as a pick of
  // every root, and the left-out marks stay.
  const mode: ProjectMode = pinned || leftOut ? "pick" : "all";
  let selection: Selection = NO_SELECTION;
  if (tree && mode === "pick") {
    selection = splitMarks(row, tree).selection;
    if (!pinned) {
      selection = {
        pins: tree.nodes.filter((n) => n.parent === null).map((n) => n.id),
        leftOut: selection.leftOut,
      };
    }
  }
  return {
    scopes: new Set(row.scopes.filter((s) => s !== OFFLINE_ACCESS && known.includes(s))),
    mode,
    selection,
    includePersonal: row.include_personal,
  };
}

// ── The body ───────────────────────────────────────────────────────────────

export interface ChangeBody {
  scopes: string[];
  pinned_projects: string[];
  left_out_projects: string[];
  include_personal: boolean;
}

export type Built =
  | { ok: true; body: ChangeBody; personalPinned: number }
  | { ok: false; problem: string };

export const NO_SCOPE_PROBLEM = "Choose one permission or more.";

/**
 * The body of the PATCH, or the reason it cannot go.
 *
 * ⚠️ The pins are empty only when the mode is "all". After a pick, an empty
 * list never leaves: `projectsProblem` refuses it, and so does the last check.
 */
export function buildChange(
  row: AgentConnection,
  draft: Draft,
  tree: ProjectTree | null,
  known: readonly string[],
): Built {
  const scopes = switchScopes(row, known).filter((s) => draft.scopes.has(s));
  if (scopes.length === 0) return { ok: false, problem: NO_SCOPE_PROBLEM };
  const offline = row.scopes.includes(OFFLINE_ACCESS) ? [OFFLINE_ACCESS] : [];

  if (draft.mode === "all") {
    return {
      ok: true,
      personalPinned: 0,
      body: {
        scopes: [...scopes, ...offline],
        pinned_projects: [],
        left_out_projects: [],
        include_personal: draft.includePersonal,
      },
    };
  }

  const cleaned = tree ? cleanSelection(tree, draft.selection) : NO_SELECTION;
  const personalRoots = draft.includePersonal && tree ? tree.personalRoots : [];
  const spillover = tree && personalRoots.length > 0 ? personalSpillover(tree, cleaned) : [];
  const carried = tree && tree.truncated ? splitMarks(row, tree).carried : NO_SELECTION;
  const problem = projectsProblem(
    "pick",
    tree,
    cleaned,
    personalRoots.length + carried.pins.length,
    spillover.length + carried.leftOut.length,
  );
  if (problem) return { ok: false, problem };
  const lists = projectLists("pick", cleaned, personalRoots, spillover);
  const pins = [...lists.pins, ...carried.pins];
  if (pins.length === 0) return { ok: false, problem: "Pick one project or more, or choose All my projects." };
  return {
    ok: true,
    personalPinned: personalRoots.length,
    body: {
      scopes: [...scopes, ...offline],
      pinned_projects: pins,
      left_out_projects: [...lists.leftOut, ...carried.leftOut],
      include_personal: draft.includePersonal,
    },
  };
}

/** True when the body asks for what the row has now (Save stays off then). */
export function sameAsRow(row: AgentConnection, body: ChangeBody): boolean {
  const same = (a: readonly string[], b: readonly string[]) =>
    a.length === b.length && a.every((x) => b.includes(x));
  return (
    same([...new Set(body.scopes)], [...new Set(row.scopes)]) &&
    same([...new Set(body.pinned_projects)], [...new Set(row.pinned_projects)]) &&
    same([...new Set(body.left_out_projects)], [...new Set(row.left_out_projects ?? [])]) &&
    body.include_personal === row.include_personal
  );
}

/** What the agent can do after the save, as the consent page says it. */
export function changeSummary(
  row: AgentConnection,
  draft: Draft,
  tree: ProjectTree | null,
  personalPinned: number,
): SummaryPart[] {
  return grantSummary({
    who: agentHost(row),
    scopes: [...draft.scopes],
    mode: draft.mode,
    picked: tree ? selectedCount(tree, draft.selection) : 0,
    includePersonal: draft.includePersonal,
    personalPinned,
    offline: row.scopes.includes(OFFLINE_ACCESS),
  });
}

export { summaryText };

/** The words of a stale-mark warning, or null for none. */
export function staleNotice(stale: number): string | null {
  if (stale === 0) return null;
  const marks = stale === 1 ? "1 project that is marked for this agent is" : `${stale} projects that are marked for this agent are`;
  return `${marks} not in your list any more. You may have lost access to it, or it is gone. Save removes the mark.`;
}

// ── The request ────────────────────────────────────────────────────────────

/** Why a save failed, in words that say what to do (DESIGN_SYSTEM §10). */
export function saveFailure(status: number, error: string | null): string {
  if (status === 404) return "This agent is not connected any more. Reload the page.";
  if (status === 422 && error === "scope_above_ceiling") {
    return "The agent has no token for a permission you turned on. Reconnect the agent to turn it on.";
  }
  if (status === 422 && error === "project_not_seen") {
    return "You no longer see a project you picked. Pick again, then save.";
  }
  if (status === 422) return "Metorite did not accept this change. Check the permissions and projects, then save again.";
  if (status === 403) return "Metorite refused the change. Reload the page and try again.";
  if (status === 411 || status === 413) return "The change did not reach Metorite. Reload the page and try again.";
  return `The change was not saved (${status}). Try again.`;
}

/**
 * Save the grant of one connection. The body goes as ONE STRING, never a
 * stream: the BFF needs a declared `Content-Length` and answers 411 without it.
 * Returns the new row as the gateway stores it.
 */
export async function saveChange(id: string, body: ChangeBody): Promise<AgentConnection> {
  const payload = JSON.stringify(body);
  const res = await fetch(`/api/agent-access/connections/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: payload,
    cache: "no-store",
  });
  if (!res.ok) {
    const json = (await res.json().catch(() => ({}))) as { detail?: { error?: string }; error?: string };
    throw new AgentsError(saveFailure(res.status, json.detail?.error ?? json.error ?? null), res.status);
  }
  return (await res.json()) as AgentConnection;
}
