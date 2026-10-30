// The Aroh app on the Operator Console — pure rules and readers only.
//
// CP-APP (Metorite task 1105, `customer_console.md` §6 "CP-APP"). The
// operator gives an organization the Aroh app with N seats. Members take a
// seat when they sign in to Aroh, and Aroh's AI use is charged to the
// organization's own credits. Two surfaces read these rules:
//
//   * the "Aroh" tab of a customer page — the switch, the seat count, who
//     holds a seat, and what Aroh spent (Console `GET /orgs/apps`);
//   * the `/aroh` page — "Connect Aroh": the hub's URL and operator token,
//     and Connect, Rotate and Disconnect (Console `/apps/connection*`).
//
// ⚠️ Pure functions only, so `aroh.test.ts` covers them with no renderer
// (DESIGN.md §7 rule 5). The JSX draws what these return.

import type { Tone } from "./tone";

export const AROH = "aroh";

export type ArohSeats = {
  purchased: number;
  assigned: number;
  available: number;
  oversubscribed: boolean;
};

export type ArohHolder = {
  email: string;
  display_name: string | null;
  assigned_at: string | null;
  membership_status: string | null;
};

export type ArohUsageMember = {
  member: string;
  calls: number;
  credits: string;
  cost_usd: string;
};

export type ArohUsage = {
  window_days: number;
  calls: number;
  credits: string;
  cost_usd: string;
  members: ArohUsageMember[];
};

export type ArohApp = {
  app: string;
  name: string;
  enabled: boolean;
  configured: boolean;
  deployment: { label: string; base_url: string } | null;
  seats: ArohSeats;
  holders: ArohHolder[];
  usage: ArohUsage;
  updated_at: string | null;
  updated_by: string | null;
};

/** The Aroh entry of a `GET /orgs/apps` body, or null when it is absent or
 *  not the shape this page understands (a Console that predates CP-APP). */
export function readArohApp(body: unknown): ArohApp | null {
  if (!body || typeof body !== "object") return null;
  const list = (body as { apps?: unknown }).apps;
  if (!Array.isArray(list)) return null;
  const row = list.find(
    (a) => a && typeof a === "object" && (a as { app?: unknown }).app === AROH,
  ) as Partial<ArohApp> | undefined;
  if (!row || typeof row.enabled !== "boolean" || !row.seats || !row.usage) return null;
  return {
    app: AROH,
    name: typeof row.name === "string" ? row.name : "Aroh",
    enabled: row.enabled,
    configured: Boolean(row.configured),
    deployment: row.deployment ?? null,
    seats: row.seats,
    holders: Array.isArray(row.holders) ? row.holders : [],
    usage: {
      ...row.usage,
      members: Array.isArray(row.usage.members) ? row.usage.members : [],
    },
    updated_at: row.updated_at ?? null,
    updated_by: row.updated_by ?? null,
  };
}

/** "1 of 2 seats in use", the line the panel leads with. */
export function seatLine(seats: ArohSeats): string {
  if (seats.purchased === 0 && seats.assigned === 0) return "No Aroh seats";
  const noun = seats.purchased === 1 ? "seat" : "seats";
  return `${seats.assigned} of ${seats.purchased} ${noun} in use`;
}

/** The state chip of the Aroh switch for one organization. */
export function arohState(app: ArohApp): { label: string; tone: Tone } {
  if (!app.configured) return { label: "Not given", tone: "neutral" };
  if (!app.enabled) return { label: "Off", tone: "warn" };
  if (app.seats.purchased > 0 && app.seats.available === 0) {
    return { label: "On · all seats in use", tone: "warn" };
  }
  return { label: "On", tone: "ok" };
}

/** Why the save button refuses, or null when it may save. The Console is the
 *  authority, and this only stops a request the Console would refuse. */
export function seatCountProblem(
  raw: string,
  assigned: number,
  reason: string,
): string | null {
  const t = raw.trim();
  const n = Number(t);
  if (t === "" || !Number.isInteger(n) || n < 0) return "Type a whole number of seats.";
  if (n < assigned) return `${assigned} seats are in use. Release a seat first.`;
  if (reason.trim().length < 3) return "Say why. It goes in the audit log.";
  return null;
}

// ── "Connect Aroh" ─────────────────────────────────────────────────────────

export type HubResult = {
  outcome?: string;
  hub_status?: number | null;
  ok?: boolean;
  kind?: string;
  last4?: string;
  configured?: boolean;
  error?: string;
  key_prefix?: string;
  revoked_prefix?: string | null;
};

export type ArohConnection = {
  app: string;
  base_url: string | null;
  default_base_url: string | null;
  token_set: boolean;
  token_last4: string | null;
  connected: boolean;
  connected_at: string | null;
  key_prefix: string | null;
  key_capabilities: string[] | null;
  capabilities: string[];
  deployment: { label: string; base_url: string } | null;
  last_result: HubResult | null;
  hub?: HubResult;
  updated_at: string | null;
  updated_by: string | null;
};

/** A `GET /apps/connection` body, or null when it is not one. */
export function readConnection(body: unknown): ArohConnection | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Partial<ArohConnection>;
  if (b.app !== AROH || typeof b.token_set !== "boolean") return null;
  return {
    app: AROH,
    base_url: b.base_url ?? null,
    default_base_url: b.default_base_url ?? null,
    token_set: b.token_set,
    token_last4: b.token_last4 ?? null,
    connected: Boolean(b.connected),
    connected_at: b.connected_at ?? null,
    key_prefix: b.key_prefix ?? null,
    key_capabilities: Array.isArray(b.key_capabilities) ? b.key_capabilities : null,
    capabilities: Array.isArray(b.capabilities) ? b.capabilities : [],
    deployment: b.deployment ?? null,
    last_result: b.last_result ?? null,
    hub: b.hub,
    updated_at: b.updated_at ?? null,
    updated_by: b.updated_by ?? null,
  };
}

/** Where the connection stands, as one chip. */
export function connectionState(c: ArohConnection): { label: string; tone: Tone } {
  if (c.connected) {
    if (c.hub && c.hub.hub_status !== 200) return { label: "Connected · hub not answering", tone: "danger" };
    if (c.hub && c.hub.configured === false) return { label: "Connected · hub holds no key", tone: "danger" };
    return { label: "Connected", tone: "ok" };
  }
  if (!c.token_set) return { label: "Not set up", tone: "neutral" };
  return { label: "Not connected", tone: "warn" };
}

/** The connect button's word: Connect the first time, Rotate after. */
export function connectWord(c: ArohConnection): "Connect Aroh" | "Rotate key" {
  return c.connected ? "Rotate key" : "Connect Aroh";
}

/** Why Connect, Rotate or Disconnect refuses, or null when it may run. The
 *  typed name is the owner's confirmation: each act mints or revokes a real
 *  credential. */
export function connectProblem(
  c: ArohConnection,
  typed: string,
  act: "connect" | "disconnect",
): string | null {
  if (act === "connect" && !c.token_set) return "Paste Aroh's operator token and save it first.";
  if (act === "disconnect" && !c.connected) return "Aroh is not connected.";
  if (typed.trim().toLowerCase() !== AROH) return `Type "${AROH}" to confirm. This mints or revokes a real key.`;
  return null;
}

/** One line for the last act's result. Never holds a token: the Console
 *  sends public fields only. */
export function resultLine(r: HubResult | null | undefined): string | null {
  if (!r) return null;
  const parts: string[] = [];
  if (r.outcome) parts.push(r.outcome);
  if (r.hub_status !== undefined) parts.push(r.hub_status === null ? "hub: no answer" : `hub: ${r.hub_status}`);
  if (r.kind) parts.push(`kind ${r.kind}`);
  if (r.last4) parts.push(`key …${r.last4}`);
  if (r.error) parts.push(r.error);
  return parts.length ? parts.join(" · ") : null;
}
