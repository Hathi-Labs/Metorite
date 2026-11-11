/**
 * Settings → Agents → Guidance, as pure decisions (WS-MCP SW-31b).
 *
 * Spec: `project-docs/specs/agent_coordination.md` SW-31. The gateway owns
 * the store and every rule (`gateway/routes/agent_guidance.py`, SW-31a). This
 * file only turns its answers into what the screen shows: the scope list, the
 * counter, the one-line summary of a version, the reason a member cannot
 * save, and the words of a failed save. The page draws them, and
 * `guidance.test.ts` holds them.
 *
 * ⚠️ The reason a member cannot save is a courtesy. The gateway still
 * decides each save, and a 403 from it shows in the same place.
 */
import type { ProjectTree } from "@/lib/accessTree";

/** The longest block of guidance. The gateway answers 422 above it (`BLOCK_MAX`). */
export const GUIDANCE_MAX = 8000;

/** The most versions one history read asks for. The gateway caps it at 200. */
export const HISTORY_LIMIT = 50;

/** The capability that writes the guidance of the whole organization. */
export const ORG_WRITE_CAPABILITY = "admin:settings:manage";

/** The value of the org level in the scope picker. A project id is a UUID. */
export const ORG_SCOPE = "org";

/** One sentence of help: how the guidance reaches an agent. */
export const HOW_IT_REACHES =
  "An agent that works a task reads the guidance of its project, its space and the whole organization. " +
  "The nearest block wins, and guidance never changes which tools an agent calls.";

export interface ScopeOption {
  /** `ORG_SCOPE` for the whole organization, else the project id. */
  value: string;
  /** The text of the option. */
  label: string;
  /** The indent of the option: 0 for the organization, 1 for a space. */
  depth: number;
  /** The project id, or null for the whole organization. */
  projectId: string | null;
  archived: boolean;
}

/** One saved version, as `GET /agent-guidance/history` sends it. */
export interface GuidanceVersion {
  id: string;
  project_id: string | null;
  version: number;
  body: string;
  written_by: string;
  written_at: string | null;
}

/** The read of one scope: its versions, or the router is off for this org. */
export type GuidanceRead = { kind: "off" } | { kind: "ok"; rows: GuidanceVersion[] };

/** What the page knows of one project node (`GET /projects/nodes/{id}`). */
export interface NodeFacts {
  archived_at?: string | null;
  status?: string | null;
  can_write_grants?: boolean;
}

/**
 * The scope picker: the whole organization first, then each space and
 * project of the tree, in tree order. An archived node stays in the list,
 * so its history can still be read.
 */
export function scopeOptions(tree: ProjectTree | null | undefined): ScopeOption[] {
  const out: ScopeOption[] = [
    { value: ORG_SCOPE, label: "Whole organization", depth: 0, projectId: null, archived: false },
  ];
  for (const node of tree?.nodes ?? []) {
    out.push({
      value: node.id,
      label: `${node.name}${node.archived ? " (archived)" : ""}`,
      depth: node.depth + 1,
      projectId: node.id,
      archived: node.archived,
    });
  }
  return out;
}

/** The project id of a picker value, or null for the whole organization. */
export function projectIdOf(value: string): string | null {
  return value === ORG_SCOPE ? null : value;
}

/** The counter under the editor. */
export function counter(body: string): { used: number; over: boolean; label: string } {
  const used = body.length;
  const over = used > GUIDANCE_MAX;
  const label = over
    ? `${(used - GUIDANCE_MAX).toLocaleString("en-US")} characters over the limit of ${GUIDANCE_MAX.toLocaleString("en-US")}`
    : `${used.toLocaleString("en-US")} of ${GUIDANCE_MAX.toLocaleString("en-US")} characters`;
  return { used, over, label };
}

/** The longest summary of a version, in characters. */
export const SUMMARY_MAX = 80;

/**
 * The first line of a version, for the history list. A heading mark at the
 * start goes, and a long line is cut. An empty body cleared the scope.
 */
export function versionSummary(body: string): string {
  const line = body.split(/\r?\n/).map((l) => l.trim()).find((l) => l.length > 0);
  if (!line) return "Cleared (no text)";
  const plain = line.replace(/^#{1,6}\s+/, "").replace(/^[-*]\s+/, "");
  return plain.length > SUMMARY_MAX ? `${plain.slice(0, SUMMARY_MAX - 1)}…` : plain;
}

/**
 * Why this member cannot save this scope, or null when the save may go.
 *
 * `node` is undefined while the facts of a project load, and the answer is
 * then null: the gateway still decides, so a wait never shows a refusal. A
 * read of the facts that FAILED is a refusal, so Save never stays on with
 * nothing checked.
 */
export function writeRefusal(a: {
  projectId: string | null;
  canManageOrg: boolean;
  node?: NodeFacts;
  nodeFailed?: boolean;
}): string | null {
  if (a.projectId === null) {
    return a.canManageOrg
      ? null
      : "Only an organization admin can change the guidance of the whole organization.";
  }
  if (!a.node) return a.nodeFailed ? "Could not check who can change this project. Try again." : null;
  if (a.node.archived_at || a.node.status === "archived") {
    return "This project is archived, so its guidance cannot change.";
  }
  if (a.node.can_write_grants === false) {
    return "Only the project lead or a Projects admin can change the guidance of this project.";
  }
  return null;
}

/** True when a 404 means the router is dark for this org, not a hidden project. */
export function isTurnedOff(status: number, detail: unknown): boolean {
  return status === 404 && detail === "Not Found";
}

/**
 * The words of a failed save. The gateway's own reason wins where it sent
 * one as text. `length` is the length of the text that the member sent.
 */
export function saveFailure(status: number, detail: unknown, length = 0): string {
  const said = typeof detail === "string" && detail.trim() ? detail.trim() : "";
  if (isTurnedOff(status, detail)) {
    return "Agent guidance is not turned on for this organization, so the text was not saved.";
  }
  if (status === 403) return said || "You cannot change the guidance of this scope.";
  if (status === 404) return "This project is gone, or you cannot see it now. The text was not saved.";
  if (status === 409) return said || "The guidance could not be saved. Try again.";
  if (status === 422) {
    if (said) return said;
    return length > GUIDANCE_MAX
      ? `The guidance is longer than ${GUIDANCE_MAX.toLocaleString("en-US")} characters. Make it shorter, then save.`
      : "The server did not accept this text. Remove any unusual characters, then save.";
  }
  return "Could not save the guidance. The connection dropped or the server is busy. Try again.";
}

/**
 * The answer of a history read, as the page uses it. A 404 with the detail
 * "Not Found" is the dark router, so it is the off state and not an error.
 * Each other failure throws the words that the page shows.
 */
export function historyReadOf(status: number, body: unknown): GuidanceRead {
  const b = (body ?? {}) as { rows?: unknown; detail?: unknown };
  if (status >= 200 && status < 300) {
    return { kind: "ok", rows: Array.isArray(b.rows) ? (b.rows as GuidanceVersion[]) : [] };
  }
  if (isTurnedOff(status, b.detail)) return { kind: "off" };
  if (status === 404) throw new Error("This project is gone, or you cannot see it now.");
  if (status === 403) throw new Error("You cannot open agent guidance. It needs access to Projects.");
  throw new Error("Could not load the guidance. The connection dropped or the server is busy.");
}

// ── The editor, as one reducer ──────────────────────────────────────────────

/** A note under the editor, for one scope only. */
export interface ScopeNote {
  scope: string;
  kind: "ok" | "error";
  text: string;
}

/**
 * The state of the editor. Each part names its scope, so a switch of scope
 * never shows the note or the "Saving…" of another scope.
 */
export interface EditorState {
  /** A draft for each scope, so a switch of scope keeps what the member typed. */
  drafts: Record<string, string>;
  /** The version open in the History, read-only. */
  open: { scope: string; id: string } | null;
  /** The scope whose save is in flight. */
  saving: string | null;
  note: ScopeNote | null;
  /**
   * The id of the version that the last save of a scope made. The draft stays
   * until the History head shows this id, so the editor never flashes the old
   * text between the save and the next read.
   */
  saved: Record<string, string>;
}

export const EMPTY_EDITOR: EditorState = { drafts: {}, open: null, saving: null, note: null, saved: {} };

export type EditorAction =
  | { type: "edit"; scope: string; text: string }
  | { type: "discard"; scope: string }
  | { type: "use"; scope: string; text: string }
  | { type: "open"; scope: string; id: string | null }
  | { type: "save-start"; scope: string }
  | { type: "save-done"; scope: string; id: string; version: number }
  | { type: "save-failed"; scope: string; text: string };

function without<T>(map: Record<string, T>, key: string): Record<string, T> {
  const rest = { ...map };
  delete rest[key];
  return rest;
}

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  const { scope } = action;
  const clearNote = state.note?.scope === scope ? null : state.note;
  switch (action.type) {
    case "edit":
      return { ...state, drafts: { ...state.drafts, [scope]: action.text }, saved: without(state.saved, scope), note: clearNote };
    case "discard":
      return { ...state, drafts: without(state.drafts, scope), saved: without(state.saved, scope), note: clearNote };
    case "use":
      // "Use this text" only copies the version into the editor and closes it.
      // It writes nothing. A save of it makes a new version.
      return {
        ...state,
        drafts: { ...state.drafts, [scope]: action.text },
        saved: without(state.saved, scope),
        open: null,
        note: clearNote,
      };
    case "open":
      return { ...state, open: action.id ? { scope, id: action.id } : null };
    case "save-start":
      return { ...state, saving: scope, note: clearNote };
    case "save-done":
      return {
        ...state,
        saving: state.saving === scope ? null : state.saving,
        saved: { ...state.saved, [scope]: action.id },
        note: { scope, kind: "ok", text: `Saved as version ${action.version}.` },
      };
    case "save-failed":
      // The draft stays, so a refusal never loses what the member typed.
      return {
        ...state,
        saving: state.saving === scope ? null : state.saving,
        note: { scope, kind: "error", text: action.text },
      };
  }
}

/**
 * The text in the editor for one scope, and whether it differs from the
 * newest version. A draft that a save made stops counting once the History
 * head shows the saved version.
 */
export function editorText(
  state: EditorState,
  scope: string,
  rows: readonly GuidanceVersion[],
): { text: string; dirty: boolean } {
  const latest = rows[0]?.body ?? "";
  const draft = state.drafts[scope];
  const settled = state.saved[scope] !== undefined && rows[0]?.id === state.saved[scope];
  const text = draft === undefined || settled ? latest : draft;
  return { text, dirty: text !== latest };
}

/**
 * When a version was written, as a date that a reader in India and a reader
 * in the US read as the same day: "7 Nov 2026, 14:05".
 */
export function writtenWhen(iso: string | null): string {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The cache key of one scope's history. */
export function historyKey(projectId: string | null): string {
  return projectId ? `agent-guidance/history?project_id=${projectId}` : "agent-guidance/history";
}
