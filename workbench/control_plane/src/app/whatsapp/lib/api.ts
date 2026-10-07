// Thin fetch wrappers over the /api/whatsapp proxy. Every call returns a safe
// default on failure so the read-only UI degrades to an empty state rather than
// throwing (the gateway may not be running in a pure-frontend dev session).

import type {
  WaAccount,
  WaBridgeSession,
  WaCall,
  WaCallDiagnostics,
  WaCallList,
  WaCallTimeline,
  WaCategory,
  WaChat,
  WaChatContext,
  WaConnectionInfo,
  WaEmbeddedResult,
  WaHistorySyncResult,
  WaLabel,
  WaMessage,
  WaPulse,
  WaRulePreview,
  WaSavedReply,
  WaStreams,
  WaTemplate,
  WaVerifyResult,
} from "./types";

async function getJSON<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(`/api/whatsapp/${path}`, { cache: "no-store" });
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

async function sendJSON<T>(
  path: string,
  method: "POST" | "PATCH",
  body: unknown
): Promise<{ ok: boolean; data: T | null; error?: string }> {
  try {
    const res = await fetch(`/api/whatsapp/${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = (await res.json().catch(() => null)) as T | null;
    if (!res.ok) {
      const detail =
        (data as { detail?: string } | null)?.detail ?? `HTTP ${res.status}`;
      return { ok: false, data: null, error: detail };
    }
    return { ok: true, data };
  } catch {
    return { ok: false, data: null, error: "network error" };
  }
}

async function deleteJSON(
  path: string
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(`/api/whatsapp/${path}`, { method: "DELETE" });
    if (!res.ok) {
      const data = (await res.json().catch(() => null)) as {
        detail?: string;
      } | null;
      return { ok: false, error: data?.detail ?? `HTTP ${res.status}` };
    }
    return { ok: true };
  } catch {
    return { ok: false, error: "network error" };
  }
}

function postJSON<T>(path: string, body: unknown) {
  return sendJSON<T>(path, "POST", body);
}

export function fetchAccounts(): Promise<WaAccount[]> {
  return getJSON<WaAccount[]>("accounts", []);
}

/** The user's default account (is_default), else the first, else null. Shared so
 *  every WhatsApp screen scopes to the SAME number the inbox shows — settings
 *  pages previously used a bare accounts[0], which could diverge. */
export function pickDefaultAccount<T extends { id: string; is_default?: boolean }>(
  accounts: T[]
): T | null {
  if (accounts.length === 0) return null;
  return accounts.find((a) => a.is_default) ?? accounts[0];
}

/** Disconnect (remove) a connected number. Its chats stay stored, but no new
 *  messages sync until it's reconnected. */
export function disconnectAccount(id: string) {
  return deleteJSON(`accounts/${id}`);
}

/** Start the coexistence history import again (WS-20 WA-C3 P4). The server
 *  answers 409 after Meta's 24-hour window, and 400 when the import is off. */
export function startHistoryImport(id: string) {
  return postJSON<WaHistorySyncResult>(
    `accounts/${encodeURIComponent(id)}/history-sync`,
    {}
  );
}

// ── Connect wizard (W11) ─────────────────────────────────────────────────────

export function fetchConnectionInfo(): Promise<WaConnectionInfo> {
  return getJSON<WaConnectionInfo>("connection/info", {
    webhook_url: "",
    webhook_path: "/whatsapp/webhook",
    verify_token: "",
    base_configured: false,
    embedded_signup: false,
    fb_app_id: "",
    es_config_id: "",
    graph_version: "v21.0",
  });
}

// Complete Embedded Signup: exchange the FB.login code for a connected account
// (W12). `waba_id` is required. A coexistence connect (WS-20 WA-C2) sends no
// `phone_number_id`, and the backend reads the number from the WABA.
export function embeddedSignup(input: {
  code: string;
  waba_id: string;
  phone_number_id?: string;
  onboarding: "cloud" | "coexistence";
  display_name?: string;
}) {
  return postJSON<WaEmbeddedResult>("connect/embedded", input);
}

// Live-tests credentials against Meta. The route returns 200 with {ok,...} even
// on a credential failure, so read `.data.ok`, not the HTTP status.
export function verifyConnection(input: {
  phone_number_id: string;
  access_token: string;
}) {
  return postJSON<WaVerifyResult>("accounts/verify", input);
}

export function createAccount(input: {
  phone_number: string;
  phone_number_id: string;
  waba_id?: string | null;
  display_name?: string;
  webhook_verify_token?: string | null;
  credentials: { access_token: string };
}) {
  return postJSON<WaAccount>("accounts", input);
}

// ── Personal number via whatsmeow QR bridge (W15) ─────────────────────────────

// Begin QR pairing for a personal number: the gateway creates a 'pairing'
// account and asks the local bridge for a QR to render.
export function startBridgeSession() {
  return postJSON<WaBridgeSession>("bridge/connect", {});
}

// Poll pairing status + the current QR for a pairing account.
export function fetchBridgeStatus(accountId: string): Promise<WaBridgeSession> {
  return getJSON<WaBridgeSession>(
    `bridge/status?account_id=${encodeURIComponent(accountId)}`,
    { account_id: accountId, qr: null, status: "unknown", bridge_reachable: false }
  );
}

export function fetchStreams(accountId?: string): Promise<WaStreams> {
  const q = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  return getJSON<WaStreams>(`streams${q}`, {
    needs_reply: 0,
    waiting: 0,
    groups: 0,
    all: 0,
    snoozed: 0,
  });
}

// `stream` is the triage stream ("needs_reply" | … ); pass a `label` id to
// instead scope the list to chats carrying a native WhatsApp label (W16).
export function fetchChats(
  stream: string,
  accountId?: string,
  label?: string
): Promise<WaChat[]> {
  const params = new URLSearchParams();
  if (stream) params.set("stream", stream);
  if (accountId) params.set("account_id", accountId);
  if (label) params.set("label", label);
  const qs = params.toString();
  return getJSON<WaChat[]>(`chats${qs ? `?${qs}` : ""}`, []);
}

export function fetchMessages(chatId: string): Promise<WaMessage[]> {
  return getJSON<WaMessage[]>(`chats/${chatId}/messages`, []);
}

export function fetchContext(chatId: string): Promise<WaChatContext | null> {
  return getJSON<WaChatContext | null>(`chats/${chatId}/context`, null);
}

export function fetchTemplates(accountId: string): Promise<WaTemplate[]> {
  return getJSON<WaTemplate[]>(
    `templates?account_id=${encodeURIComponent(accountId)}&approved_only=true`,
    []
  );
}

export function sendText(chatId: string, text: string) {
  return postJSON<{ wa_message_id: string; send_regime: string }>(
    `chats/${chatId}/send`,
    { text }
  );
}

export function sendTemplate(
  chatId: string,
  templateName: string,
  language: string
) {
  return postJSON<{ wa_message_id: string; send_regime: string }>(
    `chats/${chatId}/send`,
    { template_name: templateName, template_language: language }
  );
}

export function captureTask(messageId: string) {
  return postJSON<{ item_id: string; title: string; created: boolean }>(
    "capture-task",
    { message_id: messageId }
  );
}

// Transcribe a voice note on demand and fold it into triage (W4.3).
export function transcribeMessage(messageId: string) {
  return postJSON<{ message_id: string; transcript_text: string }>(
    `messages/${messageId}/transcribe`,
    {}
  );
}

export function generateDraft(chatId: string) {
  return postJSON<{ chat_id: string; draft_text: string; language: string }>(
    `chats/${chatId}/draft`,
    {}
  );
}

// Draft a gentle nudge to chase a commitment the other party owes us (W4.2).
export function draftNudge(commitmentId: string) {
  return postJSON<{
    commitment_id: string;
    chat_id: string;
    nudge_text: string;
    language: string;
  }>(`commitments/${commitmentId}/nudge`, {});
}

// Snooze a chat out of the queue until `until` (ISO-8601); it resurfaces on its
// own, or immediately if they send a new message (W6).
export function snoozeChat(chatId: string, until: string) {
  return postJSON<{ chat_id: string; snoozed_until: string | null }>(
    `chats/${chatId}/snooze`,
    { until }
  );
}

export function unsnoozeChat(chatId: string) {
  return postJSON<{ chat_id: string; snoozed_until: string | null }>(
    `chats/${chatId}/unsnooze`,
    {}
  );
}

export function fetchCategories(accountId: string): Promise<WaCategory[]> {
  return getJSON<WaCategory[]>(
    `categories?account_id=${encodeURIComponent(accountId)}`,
    []
  );
}

// Native WhatsApp labels/lists synced from the founder's number (W16).
export function fetchLabels(accountId?: string): Promise<WaLabel[]> {
  const q = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  return getJSON<WaLabel[]>(`labels${q}`, []);
}

export function bootstrapCategories(accountId: string) {
  return postJSON<WaCategory[]>(
    `accounts/${accountId}/categories/bootstrap`,
    {}
  );
}

export function updateCategory(
  categoryId: string,
  patch: Partial<
    Pick<
      WaCategory,
      "notify_policy" | "auto_reply_policy" | "draft_policy" | "escalate_after_mins"
    >
  >
) {
  return sendJSON<WaCategory>(`categories/${categoryId}`, "PATCH", patch);
}

export function fetchRulesPreview(accountId: string): Promise<WaRulePreview> {
  return getJSON<WaRulePreview>(
    `rules/preview?account_id=${encodeURIComponent(accountId)}`,
    { items: [], summary: {} }
  );
}

export function fetchSavedReplies(accountId: string): Promise<WaSavedReply[]> {
  return getJSON<WaSavedReply[]>(
    `saved-replies?account_id=${encodeURIComponent(accountId)}`,
    []
  );
}

export function createSavedReply(input: {
  account_id: string;
  title: string;
  body: string;
  shortcut?: string | null;
}) {
  return postJSON<WaSavedReply>("saved-replies", input);
}

export function deleteSavedReply(id: string) {
  return deleteJSON(`saved-replies/${id}`);
}

// ── voice calls (personal/bridge numbers only) ───────────────────────────────

export function fetchCalls(accountId: string): Promise<WaCallList> {
  return getJSON<WaCallList>(
    `calls?account_id=${encodeURIComponent(accountId)}`,
    { calls: [], bridge_reachable: false }
  );
}

export function fetchCallDiagnostics(accountId: string): Promise<WaCallDiagnostics> {
  return getJSON<WaCallDiagnostics>(
    `calls/diagnostics?account_id=${encodeURIComponent(accountId)}`,
    {
      account_id: accountId,
      session_exists: false,
      logged_in: false,
      connected: false,
      caller_ready: false,
      active_calls: 0,
      verdict: "Couldn't reach the gateway to check calling readiness.",
      bridge_reachable: false,
    }
  );
}

/** Place a 1:1 call (`to`) or a group call (`groupId`, or 2+ `targets`). */
export function placeCall(input: {
  accountId: string;
  to?: string;
  groupId?: string;
  targets?: string[];
}) {
  return sendJSON<WaCall>("calls", "POST", {
    account_id: input.accountId,
    to: input.to ?? "",
    group_id: input.groupId ?? "",
    targets: input.targets ?? [],
  });
}

export function fetchCallEvents(
  accountId: string,
  callId: string
): Promise<WaCallTimeline> {
  return getJSON<WaCallTimeline>(
    `calls/${encodeURIComponent(callId)}/events?account_id=${encodeURIComponent(accountId)}`,
    { call: null, events: [], bridge_reachable: false }
  );
}

export function callAction(
  action: "hangup" | "answer" | "reject",
  accountId: string,
  callId: string
) {
  return sendJSON<WaCall>(`calls/${action}`, "POST", {
    account_id: accountId,
    call_id: callId,
  });
}

export function fetchPulse(accountId: string, days = 7): Promise<WaPulse> {
  return getJSON<WaPulse>(
    `pulse?account_id=${encodeURIComponent(accountId)}&days=${days}`,
    {
      window_days: days,
      inbound: 0,
      outbound: 0,
      active_chats: 0,
      response: { replied: 0, median_minutes: null, p90_minutes: null },
      waiting_longest: [],
      by_intent: [],
      busiest: [],
    }
  );
}
