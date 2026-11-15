/**
 * The client of the MCP tool routes (WS-STU B1b-4). One thin layer over
 * `/api/studio/*`, the proxy of the gateway `/studio` routes.
 *
 *   GET  /api/studio/actions
 *   POST /api/studio/mcp/{server}/refresh
 *   POST /api/studio/mcp/{server}/tools/{tool}/approve   body {"expected_hash"}
 *
 * It sends no identity and no org. The proxy and the gateway take those from
 * the session. A server key and a tool name pass the grammar before a URL is
 * built, and `encodeURIComponent` runs on each. Every call answers a value
 * and never throws. The detail is the server's own sentence, unchanged.
 */
import { approvePath, mcpTools, refreshPath, validHash, type McpTool } from "./mcpToolsModel";

export type McpReply<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; detail: string; code?: string };

/** The server's sentence: `detail` as a string, or `detail.message` with its `code`. */
export function refusalOf(body: unknown, status: number): { detail: string; code?: string } {
  const b = body as { detail?: unknown; error?: unknown } | null;
  const raw = b?.detail ?? b?.error;
  if (typeof raw === "string" && raw) return { detail: raw };
  if (raw && typeof raw === "object") {
    const o = raw as { message?: unknown; code?: unknown };
    if (typeof o.message === "string" && o.message) {
      return { detail: o.message, code: typeof o.code === "string" ? o.code : undefined };
    }
  }
  return { detail: `The request failed (${status}).` };
}

async function send<T>(request: () => Promise<Response>): Promise<McpReply<T>> {
  let res: Response;
  try {
    res = await request();
  } catch {
    return { ok: false, status: 0, detail: "The server did not answer. Try again." };
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) return { ok: false, status: res.status, ...refusalOf(body, res.status) };
  return { ok: true, status: res.status, data: body as T };
}

const BAD_NAME = { ok: false, status: 0, detail: "This name cannot be sent." } as const;

export interface McpCatalog {
  tools: McpTool[];
  stale: boolean;
}

/** The MCP rows of the catalog. `stale` is true unless the answer says `false`. */
export async function listMcpTools(): Promise<McpReply<McpCatalog>> {
  const r = await send<{ actions?: unknown; stale?: unknown }>(() =>
    fetch("/api/studio/actions", { cache: "no-store" }),
  );
  if (!r.ok) return r;
  return { ok: true, status: r.status, data: { tools: mcpTools(r.data?.actions), stale: r.data?.stale !== false } };
}

export interface RefreshAnswer {
  tools: number;
  truncated: boolean;
  items: Array<{ tool: string; text_hash: string; changed: boolean }>;
}

export function refreshServer(server: string): Promise<McpReply<RefreshAnswer>> {
  const path = refreshPath(server);
  if (!path) return Promise.resolve(BAD_NAME);
  return send(() => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" } }));
}

export interface ApproveAnswer {
  server: string;
  tool: string;
  changed: boolean;
  class: string;
}

/** Approve ONE tool. *shownHash* is the hash the row showed. The browser never computes one. */
export function approveTool(server: string, tool: string, shownHash: string): Promise<McpReply<ApproveAnswer>> {
  const path = approvePath(server, tool);
  if (!path || !validHash(shownHash)) return Promise.resolve(BAD_NAME);
  return send(() =>
    fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_hash: shownHash }),
    }),
  );
}
