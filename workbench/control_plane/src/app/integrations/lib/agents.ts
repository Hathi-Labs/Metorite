// ── The AI agents view, as pure decisions (WS-55 EA-4b, D-EA-9) ────────────
//
// Spec: `project-docs/specs/external_agent_access.md` §5.4, §15 EA-4b and
// §17.1. `components/AgentsView.tsx` only draws what this module decides, so
// `agents.test.ts` runs every rule in the node-env vitest.
//
// Four BFF routes, each a relay of one gateway route:
//
//     GET    /api/agent-access/connections            the member's own
//     DELETE /api/agent-access/connections/<id>       revoke one of them
//     GET    /api/integrations/agent-connections      every one of the org
//     DELETE /api/integrations/agent-connections/<id> revoke any of them
//
// The two org routes need `feature:integrations` and `integrations:manage`.
// The gateway checks that. The page only hides what the member cannot use.

import { SCOPE_COPY } from "@/app/connect/authorize/consent";

/** The one address of the MCP server (spec §5.5). */
export const MCP_URL = "https://mcp.metorite.com/mcp";

/** The Claude Code command, word for word (spec §17.1). */
export const CLAUDE_CODE_COMMAND = `claude mcp add --transport http metorite ${MCP_URL}`;

/** The cache keys of the two lists. `invalidate(AGENTS_KEY_PREFIX)` wakes both. */
export const AGENTS_KEY_PREFIX = "agents:";
export const MY_AGENTS_KEY = "agents:mine";
export const ORG_AGENTS_KEY = "agents:org";

// ── The clients (spec §17.1) ───────────────────────────────────────────────

export type ClientState = "tested" | "untested" | "unsupported";

export interface ClientStep {
  text: string;
  /** A command the member copies. */
  code?: string;
}

export interface AgentClient {
  id: string;
  label: string;
  state: ClientState;
  stateLabel: string;
  steps: ClientStep[];
}

/** The client tabs of the connect card. §17.1 of the spec is the table of record. */
export const AGENT_CLIENTS: AgentClient[] = [
  {
    id: "claude-code",
    label: "Claude Code",
    state: "tested",
    stateLabel: "Tested with 2.1.296",
    steps: [
      { text: "In a terminal, run this command:", code: CLAUDE_CODE_COMMAND },
      { text: "In Claude Code, run /mcp, choose metorite, then Authenticate." },
      { text: "Your browser opens a Metorite page. Check what the agent asks for, then approve." },
    ],
  },
  {
    id: "claude-ai",
    label: "claude.ai / Claude Desktop",
    state: "untested",
    stateLabel: "Not tested yet",
    steps: [
      { text: "Open Settings, then Connectors, then Add custom connector." },
      { text: "Paste the address above." },
      { text: "Approve on the Metorite page that opens." },
    ],
  },
  {
    id: "vscode",
    label: "VS Code",
    state: "untested",
    stateLabel: "Not tested yet",
    steps: [
      { text: "Add an HTTP MCP server with the address above." },
      { text: "VS Code may ask for a sign-in method that Metorite does not offer yet." },
    ],
  },
  {
    id: "cursor",
    label: "Cursor",
    state: "unsupported",
    stateLabel: "Not supported yet",
    steps: [{ text: "Cursor cannot connect to Metorite yet." }],
  },
];

/** What an agent can do and cannot do, as the card says it. */
export const AGENT_CAN = [
  "Read your projects and tasks",
  "Search your tasks",
  "Move a task to another status, but never to done or cancelled",
  "Link pull requests, commits and branches to a task",
];

export const AGENT_CANNOT = [
  "Close or cancel a task",
  "See anything that you cannot see",
  "Act after you revoke it",
];

// ── One connection ─────────────────────────────────────────────────────────

/** One live connection, as the gateway gives it. No token, no hash, no client id. */
export interface AgentConnection {
  id: string;
  member_email: string;
  client_name: string;
  client_origin: string;
  scopes: string[];
  pinned_projects: string[];
  include_personal: boolean;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
}

/**
 * The headline: the VERIFIED host the client document came from, as the
 * consent page names it (spec §10.5). The scheme goes, a port stays.
 */
export function agentHost(row: Pick<AgentConnection, "client_origin">): string {
  const origin = (row.client_origin || "").trim();
  const host = origin.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").replace(/\/+$/, "");
  return host || "Unknown agent";
}

/**
 * The name the agent gives itself, shown second and only as a claim. `null`
 * when it says nothing the host does not already say: approve stores the
 * host as the name when the document gives none.
 */
export function agentClaim(row: Pick<AgentConnection, "client_name" | "client_origin">): string | null {
  const name = (row.client_name || "").trim();
  if (!name) return null;
  const host = agentHost(row);
  if (name === host || name === row.client_origin) return null;
  return `It calls itself “${name}”`;
}

/** Each scope in plain words, in the words of the consent page. */
export function scopeWords(scopes: readonly string[]): string[] {
  return scopes.map((s) => SCOPE_COPY[s]?.label ?? s);
}

export function pinsText(pins: readonly string[]): string {
  if (pins.length === 0) return "Every project you can see";
  return pins.length === 1 ? "Pinned to 1 project" : `Pinned to ${pins.length} projects`;
}

export function personalText(include: boolean): string {
  return include ? "Included" : "Not included";
}

const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** A day, as "14 Oct 2026". An empty or bad value is "Never". */
export function dayText(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "Never" : DAY.format(new Date(t));
}

/** The rows that still show: every row but those the member just revoked. */
export function shownRows(
  rows: readonly AgentConnection[] | null | undefined,
  gone: ReadonlySet<string>,
): AgentConnection[] {
  return (rows ?? []).filter((r) => !gone.has(r.id));
}

/** The words of the revoke dialog. They must be true (ConfirmDialog's rule). */
export function revokeCopy(row: AgentConnection, admin: boolean): { title: string; body: string } {
  const who = admin ? `${row.member_email}'s agent` : "The agent";
  return {
    title: "Revoke this agent?",
    body:
      `${who} loses access at once. Its next call fails, and it cannot renew its sign-in. ` +
      "To use it again, connect it again.",
  };
}

// ── The four requests ──────────────────────────────────────────────────────

export class AgentsError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "AgentsError";
    this.status = status;
  }
}

async function readList(res: Response): Promise<AgentConnection[] | null> {
  // 404: the agent flag is off for this deployment. Not an error to show.
  if (res.status === 404) return null;
  if (!res.ok) throw new AgentsError(`The list did not load (${res.status}).`, res.status);
  const body = (await res.json().catch(() => ({}))) as { connections?: AgentConnection[] };
  return Array.isArray(body.connections) ? body.connections : [];
}

/** The member's own agents. `null` when agent connections are off. */
export async function listMine(): Promise<AgentConnection[] | null> {
  return readList(await fetch("/api/agent-access/connections", { cache: "no-store" }));
}

/** Every agent connection of the org. Needs `integrations:manage`. */
export async function listOrg(): Promise<AgentConnection[] | null> {
  return readList(await fetch("/api/integrations/agent-connections", { cache: "no-store" }));
}

/**
 * Revoke one connection. `"gone"` when the gateway no longer knows it (404):
 * somebody revoked it first, or it expired. The row goes either way.
 */
export async function revoke(id: string, admin: boolean): Promise<"revoked" | "gone"> {
  const base = admin ? "/api/integrations/agent-connections" : "/api/agent-access/connections";
  const res = await fetch(`${base}/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (res.status === 204 || res.ok) return "revoked";
  if (res.status === 404) return "gone";
  throw new AgentsError(`The agent was not revoked (${res.status}).`, res.status);
}

// ── The delete switch (WS-55 EA-6h, EA3-51) ───────────────────────────────
//
//     GET /api/auth/me/agent-access   the member's switch
//     PUT /api/auth/me/agent-access   set it, body {delete_enabled}
//
// One switch, default off. With it on, a connected agent may delete a task
// after the member says yes in the agent. The gateway checks it on each delete.

/** The cache key of the switch. It shares the prefix, so a revoke wakes it too. */
export const AGENT_ACCESS_KEY = "agents:access";

export interface AgentAccess {
  delete_enabled: boolean;
}

/** The words of the card. They must be true of what the gateway does. */
export const DELETE_SWITCH_COPY = {
  title: "Let my AI agents delete tasks",
  body:
    "A connected agent asks you first, in the agent, and names the task. " +
    "A delete cannot be undone. You choose which subtasks are deleted and which stay.",
  on: "Your agents can delete a task after you say yes.",
  off: "Your agents cannot delete tasks.",
  /** The error state of the card (DESIGN_SYSTEM §9): what, and what next. */
  loadError: "This setting did not load.",
  loadHint: "Try again. If it still does not load, reload the page. Until it loads, the switch stays as it was.",
  retry: "Try again",
} as const;

/** The line under the switch, for its state. */
export function deleteSwitchHint(on: boolean): string {
  return on ? DELETE_SWITCH_COPY.on : DELETE_SWITCH_COPY.off;
}

async function readAccess(res: Response, failed: string): Promise<AgentAccess> {
  if (!res.ok) throw new AgentsError(`${failed} (${res.status}).`, res.status);
  const body = (await res.json().catch(() => ({}))) as { delete_enabled?: unknown };
  // A body with no boolean is an error, never "off": the card must not draw
  // a state that the gateway did not give.
  if (typeof body.delete_enabled !== "boolean") throw new AgentsError(`${failed}.`, res.status);
  return { delete_enabled: body.delete_enabled };
}

/** The member's switch. `null` when agent connections are off (404). */
export async function readAgentAccess(): Promise<AgentAccess | null> {
  const res = await fetch("/api/auth/me/agent-access", { cache: "no-store" });
  if (res.status === 404) return null;
  return readAccess(res, "The setting did not load");
}

/** Set the switch. Returns what the gateway stored. */
export async function saveAgentAccess(on: boolean): Promise<AgentAccess> {
  const res = await fetch("/api/auth/me/agent-access", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ delete_enabled: on }),
  });
  return readAccess(res, "The setting was not saved");
}
