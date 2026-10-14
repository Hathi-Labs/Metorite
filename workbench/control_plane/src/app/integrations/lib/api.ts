// ── The /integrations client ──────────────────────────────────────────────
//
// Every request goes through the BFF at /api/integrations/*. Errors keep their
// status and the server's own message, because the MCP guard explains its
// refusals (a private address, a non-https URL) and the form shows them.

import { refusalText, startErrorMessage, type Connection } from "./connections";

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

/** H-300: turn the "always confirm" switch of one MCP server on or off. */
export async function setMcpAlwaysConfirm(name: string, value: boolean): Promise<void> {
  const res = await fetch(`/api/integrations/mcp/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ always_confirm: value }),
  });
  if (!res.ok) return fail(res);
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

export async function addMcp(body: Record<string, unknown>): Promise<void> {
  const res = await fetch("/api/integrations/mcp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) return fail(res);
}
