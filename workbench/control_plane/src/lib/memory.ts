import { gatewayFetch, headersActingAs } from "@/lib/gateway";

/**
 * Mem0 REST client — server-side utility used by /api/chat/memories.
 *
 * Priority:
 *   1. GATEWAY_BASE_URL → proxies to /memory/* on the FastAPI gateway
 *      (gateway owns auth + Postgres pgvector backend — recommended).
 *   2. MEM0_API_URL → legacy self-hosted Mem0 REST container.
 *   3. Neither set → returns empty / no-op gracefully.
 */

export interface Mem0Memory {
  id: string;
  memory: string;
  user_id?: string;
  created_at?: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
}

export interface Mem0Message {
  role: "user" | "assistant" | "system";
  content: string;
}

// ── Internal helpers ──────────────────────────────────────────────────────

/**
 * The internal token proves the request came from the platform. It does NOT
 * say who the platform is acting FOR — and the gateway grants a bearer-only
 * call full service access, because whoever holds that token could assert any
 * identity anyway (acb_auth/deps.py §1b).
 *
 * So every call here forwards the acting member too. Without it the memory
 * router's scope check has nobody to compare against and waves the request
 * through as a service principal — which is exactly how an unauthenticated
 * `/api/chat/memories?userId=<colleague>` read reached a colleague's private
 * memories before this was threaded through.
 *
 * `headersActingAs` throws on a blank email rather than dropping the header,
 * which is what the previous `if (actingEmail)` did — the same omission, one
 * layer down, and it would have reopened the hole the moment a caller passed
 * an empty string.
 */
function gatewayHeaders(actingEmail: string): Record<string, string> {
  return headersActingAs(actingEmail, { "Content-Type": "application/json" });
}

function legacyHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (process.env.MEM0_API_KEY) h["Authorization"] = `Token ${process.env.MEM0_API_KEY}`;
  return h;
}

const GATEWAY = () => process.env.GATEWAY_BASE_URL ?? "http://127.0.0.1:8000";
const LEGACY = () => process.env.MEM0_API_URL ?? "";

/**
 * Retrieve all stored memories for a user, most-recent first.
 */
export async function fetchMemories(
  userId: string,
  actingEmail: string,
): Promise<Mem0Memory[]> {
  // ── Gateway path (preferred) ──────────────────────────────────────────
  try {
    const res = await gatewayFetch(
      `${GATEWAY()}/memory/${encodeURIComponent(userId)}`,
      { headers: gatewayHeaders(actingEmail), next: { revalidate: 0 } }
    );
    if (res.ok) {
      const data = await res.json() as Mem0Memory[] | { results: Mem0Memory[]; memories?: Mem0Memory[] };
      if (Array.isArray(data)) return data;
      return (data as { results?: Mem0Memory[] }).results ?? [];
    }
  } catch (_e) {
    // fall through to legacy
  }

  // ── Legacy MEM0_API_URL path ──────────────────────────────────────────
  const baseUrl = LEGACY();
  if (!baseUrl) return [];
  try {
    const res = await fetch(
      `${baseUrl}/v1/memories?user_id=${encodeURIComponent(userId)}&limit=50`,
      { headers: legacyHeaders(), next: { revalidate: 60 } }
    );
    if (!res.ok) return [];
    const data = await res.json() as Mem0Memory[] | { memories: Mem0Memory[] };
    return Array.isArray(data) ? data : (data.memories ?? []);
  } catch (_e) {
    return [];
  }
}

/**
 * Search memories by semantic query.
 */
export async function searchMemories(
  userId: string,
  query: string,
  actingEmail: string,
): Promise<Mem0Memory[]> {
  // ── Gateway path ──────────────────────────────────────────────────────
  try {
    const res = await gatewayFetch(
      `${GATEWAY()}/memory/${encodeURIComponent(userId)}/search`,
      {
        method: "POST",
        headers: gatewayHeaders(actingEmail),
        body: JSON.stringify({ query, limit: 10 }),
      }
    );
    if (res.ok) {
      const data = await res.json() as Mem0Memory[] | { results: Mem0Memory[] };
      if (Array.isArray(data)) return data;
      return (data as { results?: Mem0Memory[] }).results ?? [];
    }
  } catch (_e) {
    // fall through
  }

  // ── Legacy path ───────────────────────────────────────────────────────
  const baseUrl = LEGACY();
  if (!baseUrl) return [];
  try {
    const res = await fetch(`${baseUrl}/v1/memories/search`, {
      method: "POST",
      headers: legacyHeaders(),
      body: JSON.stringify({ user_id: userId, query, limit: 10 }),
    });
    if (!res.ok) return [];
    const data = await res.json() as Mem0Memory[] | { memories: Mem0Memory[] };
    return Array.isArray(data) ? data : (data.memories ?? []);
  } catch (_e) {
    return [];
  }
}

/**
 * Save a conversation to Mem0 (fire-and-forget — non-critical path).
 */
export async function saveConversation(
  userId: string,
  messages: Mem0Message[],
  actingEmail: string,
): Promise<void> {
  if (!messages.length) return;

  // ── Gateway path ──────────────────────────────────────────────────────
  try {
    const res = await gatewayFetch(
      `${GATEWAY()}/memory/${encodeURIComponent(userId)}/add`,
      {
        method: "POST",
        headers: gatewayHeaders(actingEmail),
        body: JSON.stringify({ messages }),
      }
    );
    if (res.ok || res.status === 202) return;
  } catch (_e) {
    // fall through
  }

  // ── Legacy path ───────────────────────────────────────────────────────
  const baseUrl = LEGACY();
  if (!baseUrl) return;
  try {
    await fetch(`${baseUrl}/v1/memories`, {
      method: "POST",
      headers: legacyHeaders(),
      body: JSON.stringify({ user_id: userId, messages }),
    });
  } catch (_e) {
    /* graceful */
  }
}

/**
 * Delete a single memory by ID.
 */
export async function deleteMemory(
  userId: string,
  memoryId: string,
  actingEmail: string,
): Promise<void> {
  // ── Gateway path ──────────────────────────────────────────────────────
  try {
    const res = await gatewayFetch(
      `${GATEWAY()}/memory/${encodeURIComponent(userId)}/${encodeURIComponent(memoryId)}`,
      { method: "DELETE", headers: gatewayHeaders(actingEmail) }
    );
    if (res.ok || res.status === 204) return;
  } catch (_e) {
    // fall through
  }

  // ── Legacy path ───────────────────────────────────────────────────────
  const baseUrl = LEGACY();
  if (!baseUrl) return;
  try {
    await fetch(`${baseUrl}/v1/memories/${memoryId}`, {
      method: "DELETE",
      headers: legacyHeaders(),
    });
  } catch (_e) {
    /* graceful */
  }
}

/**
 * Check if either Mem0 or Graphiti is enabled on the gateway.
 */
export async function fetchMemoryStatus(
  userId: string,
  actingEmail: string,
): Promise<{
  mem0_enabled: boolean;
  graphiti_enabled: boolean;
  count?: number;
}> {
  try {
    const res = await gatewayFetch(
      `${GATEWAY()}/memory/${encodeURIComponent(userId)}/status`,
      { headers: gatewayHeaders(actingEmail), next: { revalidate: 30 } }
    );
    if (res.ok) return await res.json();
  } catch (_e) {
    /* graceful */
  }
  return { mem0_enabled: false, graphiti_enabled: false };
}

