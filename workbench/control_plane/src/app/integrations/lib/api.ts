// ── The /integrations client ──────────────────────────────────────────────
//
// Every request goes through the BFF at /api/integrations/*. Errors keep their
// status and the server's own message, because the MCP guard explains its
// refusals (a private address, a non-https URL) and the form shows them.

import { parseBooksStatus, pickErrorText, startErrorText, type BooksStatus } from "./apiConnect";
import { refusalText, startErrorMessage, type Connection, type McpSwitches } from "./connections";
import {
  LOAD_FAILED,
  SAVE_FAILED,
  parseAccessItem,
  parseAccessList,
  type AccessGroup,
  type AccessItem,
  type AccessList,
  type AccessPerson,
  type RuleBody,
} from "./whoCanUse";

export class IntegrationsError extends Error {
  status: number;
  /** The machine code of an object `detail`, such as `member_connection`. */
  code?: string;
  setupHref?: string;
  constructor(message: string, status: number, code?: string, setupHref?: string) {
    super(message);
    this.name = "IntegrationsError";
    this.status = status;
    this.code = code;
    this.setupHref = setupHref;
  }
}

async function fail(res: Response): Promise<never> {
  const body = (await res.json().catch(() => ({}))) as { detail?: { code?: string; setup_href?: string } };
  const d = body?.detail && typeof body.detail === "object" ? body.detail : undefined;
  throw new IntegrationsError(refusalText(body, res.status), res.status, d?.code, d?.setup_href);
}

/**
 * The connection list. `null` means the read model is not built yet (404), so
 * the page shows the old views. Any other failure throws.
 */
export async function getConnections(): Promise<Connection[] | null> {
  const res = await fetch("/api/integrations/connections");
  if (res.status === 404) return null;
  if (!res.ok) return fail(res);
  const body = (await res.json().catch(() => ({}))) as { connections?: Connection[] };
  return Array.isArray(body.connections) ? body.connections : [];
}

export async function disconnect(id: string): Promise<void> {
  const res = await fetch(`/api/integrations/connections/${encodeURIComponent(id)}/disconnect`, {
    method: "POST",
  });
  if (!res.ok) return fail(res);
}

export interface McpTestResult {
  ok: boolean;
  tools: number | null;
  error: string | null;
}

/** The server tests with the headers it stored. The browser sends none. */
export async function testMcp(name: string): Promise<McpTestResult> {
  const res = await fetch(`/api/integrations/mcp/${encodeURIComponent(name)}/test`, {
    method: "POST",
  });
  if (!res.ok) return fail(res);
  return (await res.json()) as McpTestResult;
}

/**
 * Set the two switches of one MCP server: "always confirm" (H-300) and the
 * read-only key (MCP-C2). Both keys go in one PATCH, so the gateway checks
 * the pair that the admin sees, and never a half of it.
 *
 * It answers the pair that the gateway saved. The gateway answers each key
 * that the body named, so an answer that names neither key as a boolean
 * falls back to the pair that was sent.
 */
export async function setMcpSwitches(name: string, switches: McpSwitches): Promise<McpSwitches> {
  const res = await fetch(`/api/integrations/mcp/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      always_confirm: switches.always_confirm,
      read_only_key: switches.read_only_key,
    }),
  });
  if (!res.ok) return fail(res);
  const body = (await res.json().catch(() => ({}))) as Partial<Record<keyof McpSwitches, unknown>>;
  return {
    always_confirm:
      typeof body.always_confirm === "boolean" ? body.always_confirm : switches.always_confirm,
    read_only_key: typeof body.read_only_key === "boolean" ? body.read_only_key : switches.read_only_key,
  };
}

/**
 * CSY-7: save the description of one MCP server. The chat reads it. It
 * answers the text that the gateway stored (trimmed).
 */
export async function setMcpDescription(name: string, description: string): Promise<string> {
  const res = await fetch(`/api/integrations/mcp/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ description }),
  });
  if (!res.ok) return fail(res);
  const body = (await res.json().catch(() => ({}))) as { description?: unknown };
  return typeof body.description === "string" ? body.description : description.trim();
}

/**
 * WS-54 IN-8c: start the OAuth consent of one MCP server, and get the
 * provider's sign-in URL. The browser sends no body: the gateway signs the
 * state and builds the URL, and the BFF passes on an https URL only. The
 * caller sends the browser there.
 */
export async function startMcpOAuth(name: string): Promise<string> {
  const res = await fetch(`/api/integrations/mcp/${encodeURIComponent(name)}/oauth/start`, {
    method: "POST",
  });
  const body = (await res.json().catch(() => ({}))) as { url?: unknown; detail?: unknown };
  if (!res.ok) {
    const code = typeof body.detail === "string" ? body.detail : undefined;
    throw new IntegrationsError(startErrorMessage(code), res.status, code);
  }
  // The BFF checked it. A second check costs nothing.
  if (typeof body.url !== "string" || !body.url.startsWith("https://")) {
    throw new IntegrationsError(startErrorMessage("failed"), 502, "failed");
  }
  return body.url;
}

// ── Zoho Books, the first API connection (API-5) ──────────────────────────
//
// The gateway route answers 404 for an org that `API_CONNECTIONS_ORGS` does
// not hold, and 403 without `integrations:manage`. Both read as `null`: the
// console then offers the old "Add API" only.

/** The status of the org's Zoho Books connection, or `null` when it is not open to this member. */
export async function getBooksStatus(): Promise<BooksStatus | null> {
  const res = await fetch("/api/integrations/api/zoho_books", { cache: "no-store" });
  if (res.status === 404 || res.status === 403) return null;
  if (!res.ok) return fail(res);
  return parseBooksStatus(await res.json().catch(() => ({})));
}

/** The status with the choices of this admin's own pending pick. */
export async function getBooksPick(pick: string): Promise<BooksStatus | null> {
  const res = await fetch(`/api/integrations/api/zoho_books?pick=${encodeURIComponent(pick)}`, {
    cache: "no-store",
  });
  if (res.status === 404 || res.status === 403) return null;
  if (!res.ok) return fail(res);
  return parseBooksStatus(await res.json().catch(() => ({})));
}

/**
 * Start the Zoho Books consent. The gateway signs the state and builds the
 * URL, and the BFF passes on an https URL only. The caller sends the browser
 * there. A refusal carries its short code.
 */
export async function startBooks(): Promise<string> {
  const res = await fetch("/api/integrations/api/zoho_books/start", { method: "POST" });
  const body = (await res.json().catch(() => ({}))) as { url?: unknown; detail?: unknown };
  if (!res.ok) {
    const code = typeof body.detail === "string" ? body.detail : undefined;
    throw new IntegrationsError(startErrorText(code), res.status, code);
  }
  if (typeof body.url !== "string" || !body.url.startsWith("https://")) {
    throw new IntegrationsError(startErrorText("failed"), 502, "failed");
  }
  return body.url;
}

/** Finish a consent that found several organizations, with one of them. */
export async function pickBooks(pick: string, organizationId: string): Promise<BooksStatus> {
  const res = await fetch("/api/integrations/api/zoho_books/pick", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pick, zoho_organization_id: organizationId }),
  });
  const body: unknown = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const code = typeof detail === "string" ? detail : undefined;
    throw new IntegrationsError(pickErrorText(code), res.status, code);
  }
  return parseBooksStatus(body);
}

/**
 * P2 of the API-5 review: the admin closes the pick sheet, so the gateway
 * revokes the pending token and drops the entry. Best effort: it never throws,
 * and the sheet never waits for it. `keepalive` lets it finish after a reload.
 */
export async function cancelBooks(pick: string): Promise<void> {
  try {
    await fetch("/api/integrations/api/zoho_books/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pick }),
      keepalive: true,
    });
  } catch {
    // Redis drops the entry after 600 s anyway.
  }
}

/**
 * The same cancel, for a tab that closes (`pagehide`). A beacon is a POST.
 * The body is a plain string, so it goes as text/plain. Chrome refuses a Blob
 * of type application/json (crbug 490015). The relay reads the text.
 */
export function cancelBooksBeacon(pick: string): void {
  try {
    navigator.sendBeacon("/api/integrations/api/zoho_books/cancel", JSON.stringify({ pick }));
  } catch {
    // Best effort, as above.
  }
}

export async function addMcp(body: Record<string, unknown>): Promise<void> {
  const res = await fetch("/api/integrations/mcp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) return fail(res);
}

// ── Who can use an integration (ACC-2) ────────────────────────────────────
//
// The routes sit under `/admin`, so they go through the `/api/admin` relay.
// The GET answers 404 for an org that `INTEGRATION_ACCESS_ORGS` does not hold,
// and 403 without `integrations:manage` and `admin:access:manage`. Both read
// as `null`: the console then shows no control.

const ACCESS_URL = "/api/admin/integration-access";

/** The server's own words for a refusal, or `fallback`. Never a bare status. */
async function refusal(res: Response, fallback: string): Promise<IntegrationsError> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  const said = body?.detail ? refusalText(body, res.status) : fallback;
  return new IntegrationsError(said, res.status);
}

export async function getIntegrationAccess(): Promise<AccessList | null> {
  const res = await fetch(ACCESS_URL, { cache: "no-store" });
  if (res.status === 404 || res.status === 403) return null;
  if (!res.ok) throw await refusal(res, LOAD_FAILED);
  return parseAccessList(await res.json().catch(() => ({})));
}

/**
 * Set, change or clear the rule of one service. A refusal (422, 409, 403, 500)
 * carries the gateway's own words, so the admin knows what to correct.
 */
export async function setIntegrationAccess(service: string, rule: RuleBody): Promise<AccessItem> {
  const res = await fetch(`/api/admin/integration-access/${encodeURIComponent(service)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rule),
  });
  if (!res.ok) throw await refusal(res, SAVE_FAILED);
  const item = parseAccessItem(await res.json().catch(() => null));
  if (!item) {
    throw new IntegrationsError("Metorite could not read the saved rule. Open this page again.", 502);
  }
  return item;
}

/** The teams of the organization, for the picker. */
export async function getAccessGroups(): Promise<AccessGroup[]> {
  const res = await fetch("/api/admin/groups", { cache: "no-store" });
  if (!res.ok) throw await refusal(res, "Could not read the teams.");
  const rows: unknown = await res.json().catch(() => null);
  if (!Array.isArray(rows)) throw new IntegrationsError("Could not read the teams.", 502);
  return rows as AccessGroup[];
}

/** The members of the organization, for the picker. A removed member is not here. */
export async function getAccessPeople(): Promise<AccessPerson[]> {
  const res = await fetch("/api/admin/members", { cache: "no-store" });
  if (!res.ok) throw await refusal(res, "Could not read the people.");
  const rows: unknown = await res.json().catch(() => null);
  if (!Array.isArray(rows)) throw new IntegrationsError("Could not read the people.", 502);
  return rows as AccessPerson[];
}
