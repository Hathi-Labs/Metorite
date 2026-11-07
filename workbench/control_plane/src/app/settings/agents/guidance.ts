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
 * then null: the gateway still decides, so a wait never shows a refusal.
 */
export function writeRefusal(a: {
  projectId: string | null;
  canManageOrg: boolean;
  node?: NodeFacts;
}): string | null {
  if (a.projectId === null) {
    return a.canManageOrg
      ? null
      : "Only an organization admin can change the guidance of the whole organization.";
  }
  if (!a.node) return null;
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

/** The words of a failed save. The gateway's own reason wins where it sent one. */
export function saveFailure(status: number, detail: unknown): string {
  const said = typeof detail === "string" && detail.trim() ? detail.trim() : "";
  if (isTurnedOff(status, detail)) {
    return "Agent guidance is not turned on for this organization, so the text was not saved.";
  }
  if (status === 403) return said || "You cannot change the guidance of this scope.";
  if (status === 404) return "This project is gone, or you cannot see it now. The text was not saved.";
  if (status === 409) return said || "The guidance could not be saved. Try again.";
  if (status === 422) {
    return `The guidance is longer than ${GUIDANCE_MAX.toLocaleString("en-US")} characters. Make it shorter, then save.`;
  }
  return "Could not save the guidance. The connection dropped or the server is busy. Try again.";
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
