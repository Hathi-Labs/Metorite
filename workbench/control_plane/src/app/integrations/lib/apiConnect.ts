// ── API connections in the console (API-5) ────────────────────────────────
//
// Spec: project-docs/specs/api_connections.md §2.9 and §2.11, and
// integrations_console.md §3.7 (IN-9, IN-10, IN-11).
//
// "Add API" becomes "Connect an API". The first-party templates (T1) come
// first. They are the `api:` rows of the one connector list (IN-11). Zoho
// Books is the only one today. The gateway route `GET
// /integrations/api/zoho_books` is the switch: it answers 404 for an org that
// `API_CONNECTIONS_ORGS` does not hold. So the browser reads no public flag,
// and a dark org sees the old "Add API" only.
//
// ⚠️ "Connected" shows ONLY when the gateway says so. The gateway writes the
// row only after its test read worked, so this file never guesses a state.
// Nothing here holds a token: the status carries none.

import { connectorRows, type Connector } from "@/lib/connectors";

import type { Connection } from "./connections";

/** The provider of the Zoho Books connection (`connect.py`, `PROVIDER`). */
export const BOOKS_PROVIDER = "api:zoho_books";

/** The T1 API templates: the `api:` rows of the connector list, in file order. */
export function t1Apis(): readonly Connector[] {
  return connectorRows().filter((r) => r.id.startsWith("api:"));
}

// ── The status that the gateway sends ─────────────────────────────────────

/** The result card of IN-9, as `acb_common.connector_result_card` makes it. */
export interface ResultCard {
  provider: string;
  label: string;
  works_now: { unlocks: string; owning_app: string; apps: string[]; mira: string };
  templates: string[];
  build_offer: boolean;
}

export interface BooksChoice {
  id: string;
  name: string;
}

/** `StatusOut` of `routes/integrations_api_connect.py`. No token, no blob. */
export interface BooksStatus {
  connected: boolean;
  status: string | null;
  organization_id: string | null;
  connected_by: string | null;
  choices: BooksChoice[];
  card: ResultCard | null;
}

function text(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

function texts(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : [];
}

function parseCard(v: unknown): ResultCard | null {
  if (!v || typeof v !== "object") return null;
  const c = v as Record<string, unknown>;
  const w = (c.works_now && typeof c.works_now === "object" ? c.works_now : {}) as Record<string, unknown>;
  const label = text(c.label);
  if (!label) return null;
  return {
    provider: text(c.provider) ?? "",
    label,
    works_now: {
      unlocks: text(w.unlocks) ?? "",
      owning_app: text(w.owning_app) ?? "",
      apps: texts(w.apps),
      mira: text(w.mira) ?? "",
    },
    templates: texts(c.templates),
    build_offer: c.build_offer === true,
  };
}

/** A safe copy of the gateway's answer. A field of the wrong type reads as absent. */
export function parseBooksStatus(body: unknown): BooksStatus {
  const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  const choices = Array.isArray(b.choices)
    ? b.choices.flatMap((c) => {
        const o = (c && typeof c === "object" ? c : {}) as Record<string, unknown>;
        const id = text(o.id);
        return id && /^[0-9]{1,20}$/.test(id) ? [{ id, name: text(o.name) ?? id }] : [];
      })
    : [];
  return {
    connected: b.connected === true,
    status: text(b.status),
    organization_id: text(b.organization_id),
    connected_by: text(b.connected_by),
    choices,
    card: b.connected === true ? parseCard(b.card) : null,
  };
}

// ── The row of one T1 API ─────────────────────────────────────────────────

export type ApiRowState = "connected" | "needs_reconnect" | "not_connected";

/** The state of the row, from the gateway's answer only. */
export function apiRowState(status: BooksStatus | null): ApiRowState {
  if (status?.connected === true) return "connected";
  if (status?.status === "needs_reconnect" || status?.status === "needs_reset") return "needs_reconnect";
  return "not_connected";
}

/** The button of the row. */
export const API_CONNECT_LABEL: Record<ApiRowState, string> = {
  not_connected: "Connect",
  needs_reconnect: "Connect again",
  connected: "Reconnect",
};

/** The tile of the API keys view: "Connect an API" when a T1 API is open. */
export function addApiLabel(connectApis: boolean): string {
  return connectApis ? "Connect an API" : "Add API";
}

/** The line of the old custom path, under the T1 list. */
export const CUSTOM_API_LINE = "Another API? Add it with its keys. The chat cannot use a custom API yet.";

// ── The words of each failure ─────────────────────────────────────────────

/**
 * The text of `code` in `table`, from an OWN key only, else null. A code from
 * a URL such as `__proto__` must never read the prototype (review of #1023).
 */
function ownText(table: Record<string, string>, code: string | null | undefined): string | null {
  if (typeof code !== "string" || !Object.prototype.hasOwnProperty.call(table, code)) return null;
  return table[code];
}

const START_TEXT: Record<string, string> = {
  zoho_not_configured: "Metorite has no Zoho sign-in on this server. Ask your Metorite contact to set it up.",
  signing_unavailable: "Metorite cannot start a sign-in now. Try again in a few minutes.",
  pending_unavailable: "Metorite cannot start a sign-in now. Try again in a few minutes.",
  not_available: "Zoho Books is not on for your organization.",
};

/** A refused start: what is wrong, and what to do. */
export function startErrorText(code: string | null | undefined): string {
  return ownText(START_TEXT, code) ?? "Metorite cannot start the Zoho sign-in. Try again.";
}

const RETURN_TEXT: Record<string, string> = {
  access_denied: "You cancelled the sign-in at Zoho. Nothing was saved. Connect again when you are ready.",
  invalid_state: "The sign-in expired or came from another session. Nothing was saved. Connect again.",
  untrusted_host: "Zoho sent the sign-in from a server that Metorite does not trust. Nothing was saved.",
  zoho_not_configured: "Metorite has no Zoho sign-in on this server. Ask your Metorite contact to set it up.",
  pending_unavailable: "Metorite cannot keep the sign-in now. Nothing was saved. Try again in a few minutes.",
  no_books_organization: "Your Zoho account has no Zoho Books organization. Sign in with an account that uses Zoho Books.",
  context_unreadable: "Metorite cannot read your Zoho Books organizations. Nothing was saved. Try again.",
  test_read_failed: "Metorite cannot read invoices with this sign-in, so nothing was saved. Check your Zoho Books role and connect again.",
  no_code: "Zoho sent no sign-in code. Nothing was saved. Connect again.",
};

/** The toast after a failed return from Zoho. An unknown code still says what to do. */
export function connectErrorText(code: string | null | undefined): string {
  return ownText(RETURN_TEXT, code) ?? "The Zoho sign-in did not finish. Nothing was saved. Connect again.";
}

const PICK_TEXT: Record<string, string> = {
  invalid_pick: "This choice expired or belongs to another session. Connect Zoho Books again.",
  unknown_organization: "That organization is not in your Zoho account. Connect Zoho Books again.",
  test_read_failed: "Metorite cannot read invoices of this organization, so nothing was saved. Check your Zoho Books role, or pick another one.",
  untrusted_host: "Zoho sent the sign-in from a server that Metorite does not trust. Nothing was saved.",
  pending_unavailable: "Metorite cannot finish the connection now. Try again in a few minutes.",
};

/** The line under the pick sheet after a refused pick. */
export function pickErrorText(code: string | null | undefined): string {
  return ownText(PICK_TEXT, code) ?? "Metorite cannot save this choice. Try again.";
}

/** True when a refused pick ends the pick: the sheet then offers a new connect. */
export function pickIsOver(code: string | null | undefined): boolean {
  return code === "invalid_pick" || code === "unknown_organization" || code === "untrusted_host";
}

// ── The return from Zoho ──────────────────────────────────────────────────

export type ApiReturn =
  | { kind: "connected" }
  | { kind: "error"; code: string }
  | { kind: "pick"; pick: string }
  | null;

/** The keys of the return. The page removes them once it reads them. */
export const API_RETURN_KEYS = ["api_connected", "api_connect_error", "api_pick"] as const;

const PICK_HANDLE = /^[A-Za-z0-9_-]{16,64}$/;
const CODE = /^[a-z0-9_]{1,40}$/;

/**
 * What the address says after Zoho: the three shapes that the BFF callback
 * lets through. Anybody can write such a link, so the caller acts on it
 * only after the gateway status answers (the route is dark otherwise).
 */
export function apiReturn(params: { get(name: string): string | null }): ApiReturn {
  if (params.get("api_connected") === "zoho_books") return { kind: "connected" };
  const pick = params.get("api_pick");
  if (pick !== null && PICK_HANDLE.test(pick)) return { kind: "pick", pick };
  const code = params.get("api_connect_error");
  if (code !== null) return { kind: "error", code: CODE.test(code) ? code : "failed" };
  return null;
}

/** The address with no key of the return. */
export function withoutApiReturn(pathname: string, search: URLSearchParams): string {
  const rest = new URLSearchParams(search);
  for (const k of API_RETURN_KEYS) rest.delete(k);
  const qs = rest.toString();
  return qs ? `${pathname}?${qs}` : pathname;
}

// ── The result card (IN-9) ────────────────────────────────────────────────

export interface CardView {
  title: string;
  lines: string[];
  /** The one next step, a link. Never a modal, and never a build that starts by itself. */
  next: { label: string; href: string } | null;
}

/** Where the next step of a card goes: AI Studio. */
export const STUDIO_HREF = "/studio";

/**
 * The words of the card, in the order of the model: what works now, then a
 * template, then "Build an app". It says the same as `chat_line` in Python.
 */
export function resultCardView(card: ResultCard): CardView {
  const w = card.works_now;
  const lines = [w.unlocks];
  lines.push(
    w.apps.length > 0 ? `These use it now: ${w.apps.join(", ")}.` : "No app or chat tool uses it yet.",
  );
  lines.push(w.mira);
  let next: CardView["next"] = null;
  if (card.templates.length > 0) {
    next = { label: `Start from the template ${card.templates[0]}`, href: STUDIO_HREF };
  } else if (card.build_offer) {
    next = { label: `Build an app on ${card.label}`, href: STUDIO_HREF };
  }
  return { title: `${card.label} is connected.`, lines: lines.filter(Boolean), next };
}

// ── Used by, and the disconnect preview (IN-10) ───────────────────────────

export interface UsedBy {
  names: string[];
  line: string;
}

/**
 * What uses an API connection. Only the chat tools of API-4 will read it, and
 * they are not built yet, so the list is empty and the line says so. `null`
 * for a row that is not an API connection: the console shows no list there.
 */
export function usedBy(row: Pick<Connection, "provider">): UsedBy | null {
  if (!row.provider.startsWith("api:")) return null;
  return { names: [], line: "No app, workflow or chat tool uses this connection yet." };
}

/** The confirm words of the disconnect of an API connection. It asks once. */
export function apiDisconnectCopy(row: Pick<Connection, "provider" | "label">): {
  title: string;
  body: string;
  confirmLabel: string;
} {
  const uses = usedBy(row);
  const stops =
    uses && uses.names.length > 0
      ? `These stop: ${uses.names.join(", ")}.`
      : "Nothing uses this connection now, so nothing stops.";
  const at = row.provider.startsWith("api:zoho") ? "Zoho" : "the provider";
  return {
    title: `Disconnect ${row.label}?`,
    body:
      `Metorite revokes the sign-in at ${at} and deletes it. ${stops} ` +
      "To use it again, connect it again in Integrations.",
    confirmLabel: "Disconnect",
  };
}
