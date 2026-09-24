import {
  Email, EmailAccount,
  AnalyticsOverview, SenderStat, NewsletterStatus, UnsubscribeResult,
  AutomationRule, RuleTestResult, ExecutedRule, AssistantSettings,
  RecentTestResult, ColdSender, KnowledgeEntry,
  LearnedPattern, RunMessageResult, LearnedRulePattern, LabelInfo,
  RuleGuidance, MessageTimeline,
  VoiceProfile, VoiceProfilePreview, VoiceProfileBuildStatus,
  ContactCard, SenderStatus,
} from "./types";

// No NEXT_PUBLIC_GATEWAY_URL here on purpose: every call from this module goes
// through the Next BFF at /api/**, which is the only path that carries the
// caller's identity to the gateway. A browser-side gateway URL only ever
// produced unauthenticated requests.

async function gatewayFetch<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  // `path` already includes the gateway's `/email` router prefix (e.g.
  // "/email/accounts"), and the Next proxy lives at `/api/email/[...path]`.
  // Prefix with `/api` (NOT `/api/email`) so we hit `/api/email/accounts`,
  // not the doubled `/api/email/email/accounts` (which 404s).
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Gateway error ${res.status}`) as Error & {
      status?: number;
    };
    err.status = res.status;
    throw err;
  }

  // Many endpoints (DELETE, some PATCH) return 204 / an empty body — calling
  // res.json() on those throws "Unexpected end of JSON input". Parse only when
  // there's actually a body.
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ── Snake → CamelCase mappers ────────────────────────────────────────────

function asProvider(v: unknown): "gmail" | "microsoft" | "imap" {
  const s = String(v ?? "");
  if (s === "gmail" || s === "microsoft" || s === "imap") return s;
  return "imap";
}

/** Map a backend EmailAccount (snake_case) to frontend EmailAccount (camelCase). */
function mapAccount(raw: Record<string, unknown>): EmailAccount {
  return {
    id: String(raw.id ?? ""),
    provider: asProvider(raw.provider),
    emailAddress: String(raw.email_address ?? ""),
    label: String(raw.label ?? ""),
    avatar: (String(raw.email_address ?? "?").charAt(0) || "?").toUpperCase(),
    color: String(raw.avatar_color ?? "#6366f1"),
    unreadCount: Number(raw.unread_count ?? 0),
    syncEnabled: Boolean(raw.sync_enabled ?? true),
    lastSyncedAt: raw.last_synced_at ? String(raw.last_synced_at) : undefined,
    syncStatus: raw.sync_status ? String(raw.sync_status) : undefined,
    syncError: raw.sync_error ? String(raw.sync_error) : undefined,
    isDefault: Boolean(raw.is_default ?? false),
  };
}

/** Map a backend Email message (snake_case) to frontend Email (camelCase). */
function mapEmail(raw: Record<string, unknown>): Email {
  const fromRaw = (raw.from_address ?? raw.from ?? {}) as Record<string, unknown>;
  return {
    id: String(raw.id ?? ""),
    providerMessageId: String(raw.provider_message_id ?? ""),
    threadId: raw.thread_id ? String(raw.thread_id) : undefined,
    threadCount: Number(raw.thread_count ?? raw.threadCount ?? 1),
    accountId: String(raw.account_id ?? ""),
    from: {
      name: String(fromRaw.name ?? fromRaw.Name ?? ""),
      email: String(fromRaw.email ?? fromRaw.Email ?? ""),
    },
    to: ((raw.to_addresses ?? raw.to ?? []) as Array<Record<string, unknown>>).map(
      (a) => ({ name: String(a.name ?? a.Name ?? ""), email: String(a.email ?? a.Email ?? "") })
    ),
    cc: ((raw.cc_addresses ?? raw.cc ?? []) as Array<Record<string, unknown>>).map(
      (a) => ({ name: String(a.name ?? ""), email: String(a.email ?? "") })
    ),
    bcc: ((raw.bcc_addresses ?? raw.bcc ?? []) as Array<Record<string, unknown>>).map(
      (a) => ({ name: String(a.name ?? ""), email: String(a.email ?? "") })
    ),
    subject: String(raw.subject ?? ""),
    bodyText: String(raw.body_text ?? raw.bodyText ?? ""),
    bodyHtml: (raw.body_html as string) ?? (raw.bodyHtml as string) ?? undefined,
    bodyTruncated: Boolean(raw.body_truncated ?? raw.bodyTruncated ?? false),
    snippet: String(raw.snippet ?? ""),
    hasAttachments: Boolean(raw.has_attachments ?? raw.hasAttachments ?? false),
    attachments: ((raw.attachments as Array<Record<string, unknown>>) ?? []).map(
      (a) => ({
        id: String(a.id ?? ""),
        filename: String(a.filename ?? ""),
        mimeType: String(a.mime_type ?? a.mimeType ?? "application/octet-stream"),
        sizeBytes: Number(a.size_bytes ?? a.sizeBytes ?? 0),
        downloadUrl: (a.download_url as string) ?? (a.downloadUrl as string) ?? undefined,
      })
    ),
    isRead: Boolean(raw.is_read ?? raw.isRead ?? false),
    isStarred: Boolean(raw.is_starred ?? raw.isStarred ?? false),
    isFlagged: Boolean(raw.is_flagged ?? raw.isFlagged ?? false),
    importance: ((): "high" | "normal" | "low" => {
      const v = String(raw.importance ?? "normal").toLowerCase();
      return v === "high" || v === "low" ? v : "normal";
    })(),
    labels: (raw.labels as string[]) ?? [],
    categories: Array.isArray(raw.categories)
      ? (raw.categories as string[]) // real provider categories (Outlook) — keep verbatim
      : ((raw.labels as string[]) ?? []).filter(
          // Falling back to Gmail labels: hide UPPERCASE system labels.
          (l) => !/^[A-Z_]+$/.test(l)
        ),
    folder: String(raw.folder ?? "INBOX"),
    receivedAt: raw.received_at ? String(raw.received_at) : new Date().toISOString(),
    syncedAt: raw.synced_at ? String(raw.synced_at) : new Date().toISOString(),
    snoozedUntil: raw.snoozed_until != null ? String(raw.snoozed_until) : null,
    // Present only on /email/search results.
    rank: raw.rank != null ? Number(raw.rank) : undefined,
    highlight: raw.highlight != null ? String(raw.highlight) : undefined,
  };
}

// ── Email Accounts ───────────────────────────────────────────────────────

/** Capture an email into the My Tasks inbox (AI drafts the task title +
 *  context server-side; idempotent per email — re-capturing returns the
 *  existing open item). Goes through the tasks proxy, not /api/email. */
export async function captureEmailToTask(
  accountId: string,
  emailId: string
): Promise<{
  title: string;
  created: boolean;
  disposition: string;
  assigneeName: string | null;
  dueAt: string | null;
}> {
  const res = await gatewayFetch<Record<string, unknown>>(
    "/tasks/capture/from-email",
    {
      method: "POST",
      body: JSON.stringify({ account_id: accountId, email_id: emailId }),
    }
  );
  const item = (res.item ?? {}) as Record<string, unknown>;
  return {
    title: String(item.title ?? ""),
    created: Boolean(res.created),
    disposition: String(res.disposition ?? "INBOX"),
    assigneeName: res.assignee_name ? String(res.assignee_name) : null,
    dueAt: res.due_at ? String(res.due_at) : null,
  };
}

// ── Email → Task capture popup (preview → enhance → create) ────────────────
// The editable draft the popup renders. All fields are strings so the popup
// hands the same shape straight back on confirm (dates stay ISO strings, the
// backend parses them). `disposition` is the GTD routing bucket.
export interface TaskCaptureDraft {
  title: string;
  notes: string;
  disposition: string;
  nextAction: string;
  assigneeName: string;
  dueAt: string;
  deferUntil: string;
  context: string;
  /** Full-clarify fields the AI fills for an actionable (NEXT/CALENDAR) capture
   *  so a task filed outside the inbox lands complete. */
  energy: string;
  timeEstimateMins: number | null;
  subtasks: string[];
}

export interface SimilarTask {
  id: string;
  title: string;
  disposition: string;
  reason: "same-thread" | "similar-title";
  score: number;
}

export interface AlreadyCapturedTask {
  id: string;
  title: string;
  disposition: string;
}

export interface TaskCapturePreview {
  alreadyCaptured: AlreadyCapturedTask | null;
  draft: TaskCaptureDraft;
  similar: SimilarTask[];
  fromName: string;
  subject: string;
}

function mapDraft(raw: Record<string, unknown>): TaskCaptureDraft {
  return {
    title: String(raw.title ?? ""),
    notes: String(raw.notes ?? ""),
    disposition: String(raw.disposition ?? "INBOX"),
    nextAction: String(raw.next_action ?? ""),
    assigneeName: String(raw.assignee_name ?? ""),
    dueAt: String(raw.due_at ?? ""),
    deferUntil: String(raw.defer_until ?? ""),
    context: String(raw.context ?? ""),
    energy: String(raw.energy ?? ""),
    timeEstimateMins:
      raw.time_estimate_mins == null ? null : Number(raw.time_estimate_mins),
    subtasks: Array.isArray(raw.subtasks)
      ? raw.subtasks.map((s) => String(s))
      : [],
  };
}

function draftToWire(d: TaskCaptureDraft): Record<string, unknown> {
  return {
    title: d.title,
    notes: d.notes,
    disposition: d.disposition,
    next_action: d.nextAction,
    assignee_name: d.assigneeName,
    due_at: d.dueAt,
    defer_until: d.deferUntil,
    context: d.context,
    energy: d.energy,
    time_estimate_mins: d.timeEstimateMins,
    subtasks: d.subtasks,
  };
}

/** Step 1 — open the popup: subject-derived default title + "you may already
 *  have this" (same-thread / fuzzy-title) list. No LLM, no write. */
export async function previewEmailCapture(
  accountId: string,
  emailId: string
): Promise<TaskCapturePreview> {
  const res = await gatewayFetch<Record<string, unknown>>(
    "/tasks/capture/from-email/preview",
    {
      method: "POST",
      body: JSON.stringify({ account_id: accountId, email_id: emailId }),
    }
  );
  const ac = res.already_captured as Record<string, unknown> | null;
  return {
    alreadyCaptured: ac
      ? {
          id: String(ac.id ?? ""),
          title: String(ac.title ?? ""),
          disposition: String(ac.disposition ?? "INBOX"),
        }
      : null,
    draft: mapDraft((res.draft ?? {}) as Record<string, unknown>),
    similar: ((res.similar ?? []) as Record<string, unknown>[]).map((s) => ({
      id: String(s.id ?? ""),
      title: String(s.title ?? ""),
      disposition: String(s.disposition ?? "INBOX"),
      reason: (s.reason === "same-thread" ? "same-thread" : "similar-title"),
      score: Number(s.score ?? 0),
    })),
    fromName: String(res.from_name ?? ""),
    subject: String(res.subject ?? ""),
  };
}

/** Step 2 — "Enhance with AI": the LLM reads the whole email + thread and
 *  returns a routed draft. Still no write. */
export async function enhanceEmailCapture(
  accountId: string,
  emailId: string
): Promise<{ draft: TaskCaptureDraft; usedLlm: boolean; assigneeResolved: string | null }> {
  const res = await gatewayFetch<Record<string, unknown>>(
    "/tasks/capture/from-email/enhance",
    {
      method: "POST",
      body: JSON.stringify({ account_id: accountId, email_id: emailId }),
    }
  );
  return {
    draft: mapDraft((res.draft ?? {}) as Record<string, unknown>),
    usedLlm: Boolean(res.used_llm),
    assigneeResolved: res.assignee_resolved ? String(res.assignee_resolved) : null,
  };
}

/** Step 3 — confirm: write the (possibly edited) task. Idempotent per email. */
export async function createEmailCapture(
  accountId: string,
  emailId: string,
  draft: TaskCaptureDraft
): Promise<{
  title: string;
  created: boolean;
  disposition: string;
  assigneeName: string | null;
  dueAt: string | null;
}> {
  const res = await gatewayFetch<Record<string, unknown>>(
    "/tasks/capture/from-email/create",
    {
      method: "POST",
      body: JSON.stringify({
        account_id: accountId,
        email_id: emailId,
        draft: draftToWire(draft),
      }),
    }
  );
  const item = (res.item ?? {}) as Record<string, unknown>;
  return {
    title: String(item.title ?? ""),
    created: Boolean(res.created),
    disposition: String(res.disposition ?? "INBOX"),
    assigneeName: res.assignee_name ? String(res.assignee_name) : null,
    dueAt: res.due_at ? String(res.due_at) : null,
  };
}

// ── Commitment capture (a reply I SENT that promises a future action) ─────────
// Keyed on the reply's (thread_id, body, subject) rather than an email_id,
// because the just-sent message usually isn't mirrored locally yet. `detect`
// runs the commitment gate on send; `create` writes the confirmed task. Both
// feed the SAME TaskCaptureModal UI as an inbound capture.

export interface CommitmentDetection {
  isCommitment: boolean;
  draft: TaskCaptureDraft | null;
  similar: SimilarTask[];
  alreadyCaptured: AlreadyCapturedTask | null;
}

/** Detect on send: does the reply I just sent commit me to a task? Returns a
 *  routed draft for the popup when it does; isCommitment=false → no popup. */
export async function detectReplyCommitment(params: {
  accountId: string;
  threadId: string;
  body: string;
  subject?: string;
  replyToMessageId?: string | null;
  /** On send this stays false (fast: reply body only). "Enhance with AI" sets it
   *  true so the LLM reviews the whole conversation for a richer draft. */
  includeThread?: boolean;
}): Promise<CommitmentDetection> {
  const res = await gatewayFetch<Record<string, unknown>>(
    "/tasks/capture/from-reply/detect",
    {
      method: "POST",
      body: JSON.stringify({
        account_id: params.accountId,
        thread_id: params.threadId,
        body: params.body,
        subject: params.subject ?? "",
        reply_to_message_id: params.replyToMessageId ?? null,
        include_thread: params.includeThread ?? false,
      }),
    }
  );
  const ac = res.already_captured as Record<string, unknown> | null;
  return {
    isCommitment: Boolean(res.is_commitment),
    draft: res.draft
      ? mapDraft(res.draft as Record<string, unknown>)
      : null,
    similar: ((res.similar ?? []) as Record<string, unknown>[]).map((s) => ({
      id: String(s.id ?? ""),
      title: String(s.title ?? ""),
      disposition: String(s.disposition ?? "INBOX"),
      reason: s.reason === "same-thread" ? "same-thread" : "similar-title",
      score: Number(s.score ?? 0),
    })),
    alreadyCaptured: ac
      ? {
          id: String(ac.id ?? ""),
          title: String(ac.title ?? ""),
          disposition: String(ac.disposition ?? "INBOX"),
        }
      : null,
  };
}

/** Confirm the commitment popup: write the (edited) task, link it to the thread,
 *  and tag the thread "Task". Idempotent per thread. */
export async function createReplyCommitment(params: {
  accountId: string;
  threadId: string;
  subject?: string;
  body?: string;
  replyToMessageId?: string | null;
  draft: TaskCaptureDraft;
}): Promise<{
  title: string;
  created: boolean;
  disposition: string;
  assigneeName: string | null;
  dueAt: string | null;
}> {
  const res = await gatewayFetch<Record<string, unknown>>(
    "/tasks/capture/from-reply/create",
    {
      method: "POST",
      body: JSON.stringify({
        account_id: params.accountId,
        thread_id: params.threadId,
        subject: params.subject ?? "",
        body: params.body ?? "",
        reply_to_message_id: params.replyToMessageId ?? null,
        draft: draftToWire(params.draft),
      }),
    }
  );
  const item = (res.item ?? {}) as Record<string, unknown>;
  return {
    title: String(item.title ?? ""),
    created: Boolean(res.created),
    disposition: String(res.disposition ?? "INBOX"),
    assigneeName: res.assignee_name ? String(res.assignee_name) : null,
    dueAt: res.due_at ? String(res.due_at) : null,
  };
}

export async function listEmailAccounts(): Promise<EmailAccount[]> {
  const raw = await gatewayFetch<Record<string, unknown>[]>("/email/accounts");
  return (raw ?? []).map(mapAccount);
}

export interface CreateEmailAccountParams {
  provider: "imap" | "gmail" | "microsoft";
  emailAddress: string;
  label?: string;
  credentials: Record<string, unknown>;
}

export async function createEmailAccount(
  params: CreateEmailAccountParams
): Promise<EmailAccount> {
  const body: Record<string, unknown> = {
    provider: params.provider,
    email_address: params.emailAddress,
    label: params.label ?? "",
    credentials: params.credentials,
  };
  const raw = await gatewayFetch<Record<string, unknown>>(
    "/email/accounts",
    { method: "POST", body: JSON.stringify(body) }
  );
  return mapAccount(raw);
}

export async function deleteEmailAccount(id: string): Promise<void> {
  await gatewayFetch(`/email/accounts/${id}`, { method: "DELETE" });
}

/** Make this account the user's default mailbox (the inbox the UI opens on). */
export async function setDefaultEmailAccount(id: string): Promise<EmailAccount> {
  const raw = await gatewayFetch<Record<string, unknown>>(
    `/email/accounts/${id}/default`,
    { method: "POST" }
  );
  return mapAccount(raw);
}

export async function updateEmailAccount(
  id: string,
  updates: Partial<Pick<EmailAccount, "label" | "syncEnabled">>
): Promise<EmailAccount> {
  // Map camelCase → snake_case for the backend PATCH
  const body: Record<string, unknown> = {};
  if (updates.label !== undefined) body.label = updates.label;
  if (updates.syncEnabled !== undefined) body.sync_enabled = updates.syncEnabled;
  const raw = await gatewayFetch<Record<string, unknown>>(
    `/email/accounts/${id}`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
  return mapAccount(raw);
}

// ── Folders ──────────────────────────────────────────────────────────────

export interface EmailFolderRaw {
  provider_folder_id: string;
  name: string;
  type: string; // 'system' | 'user'
  message_count: number;
  unread_count: number;
}

export async function listEmailFolders(
  accountId: string
): Promise<EmailFolderRaw[]> {
  return gatewayFetch<EmailFolderRaw[]>(
    `/email/accounts/${accountId}/folders`
  );
}

/** Create (or reuse) a folder/label on the account; returns the folder. */
export async function createEmailFolder(
  accountId: string,
  name: string
): Promise<EmailFolderRaw> {
  return gatewayFetch<EmailFolderRaw>(
    `/email/accounts/${accountId}/folders`,
    { method: "POST", body: JSON.stringify({ name }) }
  );
}

// ── Emails ───────────────────────────────────────────────────────────────

export interface ListEmailsParams {
  accountId?: string;
  folder?: string;
  /** Filter to messages carrying this label/category. */
  label?: string;
  /** Filter to messages whose SENDER has this category (email_senders) —
   *  the always-on categorizer that also powers the Email Cleaner. */
  senderCategory?: string;
  query?: string;
  /** Collapse to one row per conversation (default true for the mailbox browse —
   *  the human list reads at thread level). Pass false for a per-message list. */
  collapse?: boolean;
  page?: number;
  pageSize?: number;
}

export interface ListEmailsResponse {
  emails: Email[];
  total: number;
  page: number;
  pageSize: number;
}

export async function listEmails(
  params: ListEmailsParams = {}
): Promise<ListEmailsResponse> {
  const searchParams = new URLSearchParams();
  if (params.accountId) searchParams.set("account_id", params.accountId);
  if (params.folder) searchParams.set("folder", params.folder);
  if (params.label) searchParams.set("label", params.label);
  if (params.senderCategory) searchParams.set("sender_category", params.senderCategory);
  if (params.query) searchParams.set("query", params.query);
  // Default to conversation-collapsed rows for the browse; callers that need a
  // per-message list (none in the app today) can pass collapse:false.
  if (params.collapse !== false) searchParams.set("collapse", "true");
  if (params.page) searchParams.set("page", String(params.page));
  if (params.pageSize) searchParams.set("page_size", String(params.pageSize));

  const raw = await gatewayFetch<{
    emails: Record<string, unknown>[];
    total: number;
    page: number;
    page_size: number;
  }>(`/email/messages?${searchParams.toString()}`);

  return {
    emails: (raw.emails ?? []).map(mapEmail),
    total: raw.total ?? 0,
    page: raw.page ?? 1,
    pageSize: raw.page_size ?? 50,
  };
}

/** What the current folder actually contains, so the chip row can offer only
 *  the filters with mail behind them. */
export interface MessageFacets {
  folder: string;
  total: number;
  unread: number;
  uncategorized: number;
  /** Lowercased rule label → count. */
  labels: Record<string, number>;
}

export async function getMessageFacets(
  accountId: string | null,
  folder: string
): Promise<MessageFacets> {
  const sp = new URLSearchParams({ folder });
  if (accountId) sp.set("account_id", accountId);
  return gatewayFetch(`/email/messages/facets?${sp}`);
}

export interface ContactSuggestion {
  email: string;
  name: string;
}

/** Recipient autocomplete: known correspondents matching `q` (address or
 *  display name) — people you've sent to, the learned contacts directory, and
 *  known senders, strongest first. Best-effort ([] on failure). */
export async function suggestContacts(
  accountId: string | null | undefined,
  q: string,
  limit = 8
): Promise<ContactSuggestion[]> {
  const sp = new URLSearchParams({ q, limit: String(limit) });
  if (accountId) sp.set("account_id", accountId);
  return gatewayFetch(`/email/contacts/suggest?${sp}`);
}

export interface SearchEmailsParams {
  /** Search text (websearch syntax). Optional: a filters-only search (tag pills,
   *  from/to, unread/…) with no typed text is a first-class query. */
  q?: string;
  /** Omit to search across ALL of the user's accounts (default). */
  accountId?: string;
  /** Scope: a folder key, "all" (everything but junk/trash), or "starred".
   *  Omit to search across every folder. */
  folder?: string;
  label?: string;
  /** Tag pills. A message must carry ALL of them (each pill narrows). Matched
   *  against user labels OR rule-engine categories ("Reply", "Newsletter"). */
  labels?: string[];
  /** Only mail carrying NONE of the rule-engine labels — the complement of the
   *  tag pills, and the same definition the Email Cleaner uses. */
  uncategorized?: boolean;
  /** `from:` pill — substring match on the sender's address or display name. */
  fromAddr?: string;
  /** `to:` pill — substring match on any To/Cc recipient. */
  toAddr?: string;
  senderCategory?: string;
  isRead?: boolean;
  isStarred?: boolean;
  hasAttachments?: boolean;
  receivedAfter?: string;
  receivedBefore?: string;
  /** Provider importance: "high" | "normal" | "low". */
  importance?: string;
  /** Ask for semantic (vector) re-ranking. The server honours it only when
   *  semantic search is enabled + embeddings exist; otherwise it returns pure
   *  lexical results and `hybrid: false`. */
  hybrid?: boolean;
  /** Skip message bodies. For list rows that only render subject + snippet —
   *  a page of HTML-heavy mail is megabytes otherwise. */
  light?: boolean;
  page?: number;
  pageSize?: number;
}

export interface SearchEmailsResult extends ListEmailsResponse {
  /** True when the server actually applied semantic re-ranking. */
  hybrid: boolean;
}

/** Ranked search over the user's email — the query surface behind the search bar.
 *  Distinct from listEmails (a plain folder list): results are relevance-ordered
 *  and each carries a highlighted `highlight` snippet. Accepts websearch syntax
 *  ("quoted phrase", OR, -exclude) plus the scope + pill filters the search bar
 *  composes. With no `q` it's a filters-only search, ordered newest-first. */
export async function searchEmails(
  params: SearchEmailsParams
): Promise<SearchEmailsResult> {
  const sp = new URLSearchParams();
  if (params.q?.trim()) sp.set("q", params.q.trim());
  if (params.accountId) sp.set("account_id", params.accountId);
  if (params.folder) sp.set("folder", params.folder);
  if (params.label) sp.set("label", params.label);
  // Repeated ?labels=…&labels=… — FastAPI reads it as a list.
  for (const t of params.labels ?? []) {
    if (t.trim()) sp.append("labels", t.trim());
  }
  if (params.fromAddr?.trim()) sp.set("from_addr", params.fromAddr.trim());
  if (params.toAddr?.trim()) sp.set("to_addr", params.toAddr.trim());
  if (params.senderCategory) sp.set("sender_category", params.senderCategory);
  if (params.isRead != null) sp.set("is_read", String(params.isRead));
  if (params.isStarred != null) sp.set("is_starred", String(params.isStarred));
  if (params.hasAttachments != null)
    sp.set("has_attachments", String(params.hasAttachments));
  if (params.uncategorized) sp.set("uncategorized", "true");
  if (params.receivedAfter) sp.set("received_after", params.receivedAfter);
  if (params.receivedBefore) sp.set("received_before", params.receivedBefore);
  if (params.importance) sp.set("importance", params.importance);
  if (params.hybrid) sp.set("hybrid", "true");
  if (params.light) sp.set("light", "true");
  if (params.page) sp.set("page", String(params.page));
  if (params.pageSize) sp.set("page_size", String(params.pageSize));

  const raw = await gatewayFetch<{
    emails: Record<string, unknown>[];
    total: number;
    page: number;
    page_size: number;
    hybrid?: boolean;
  }>(`/email/search?${sp.toString()}`);

  return {
    emails: (raw.emails ?? []).map(mapEmail),
    total: raw.total ?? 0,
    page: raw.page ?? 1,
    pageSize: raw.page_size ?? 50,
    hybrid: Boolean(raw.hybrid),
  };
}

export async function getEmail(id: string): Promise<Email> {
  const raw = await gatewayFetch<Record<string, unknown>>(`/email/messages/${id}`);
  return mapEmail(raw);
}

/** All messages in a conversation (across folders), oldest-first. */
export async function listThread(
  accountId: string | undefined,
  threadId: string
): Promise<Email[]> {
  const sp = new URLSearchParams();
  if (accountId) sp.set("account_id", accountId);
  sp.set("thread_id", threadId);
  sp.set("page_size", "100");
  const raw = await gatewayFetch<{ emails: Record<string, unknown>[] }>(
    `/email/messages?${sp.toString()}`
  );
  return (raw.emails ?? []).map(mapEmail);
}

export interface BackfillResult {
  synced: number;
  next_page_token: string | null;
  exhausted: boolean;
}

/** Page further back through the provider's history for a folder. */
export async function backfillFolder(
  accountId: string,
  folder: string,
  pageToken?: string | null
): Promise<BackfillResult> {
  return gatewayFetch<BackfillResult>(
    `/email/accounts/${accountId}/backfill`,
    {
      method: "POST",
      body: JSON.stringify({
        folder,
        page_token: pageToken ?? null,
        max_pages: 3,
      }),
    }
  );
}

// ── Full-body fetch (for truncated messages) ────────────────────────────

export interface FullBodyResponse {
  message_id: string;
  body_text: string;
  body_html: string | null;
  subject: string;
  from: string;
}

export async function fetchFullBody(id: string): Promise<FullBodyResponse> {
  return gatewayFetch<FullBodyResponse>(`/email/messages/${id}/full-body`);
}

export async function updateEmail(
  id: string,
  updates: Partial<Pick<Email, "isRead" | "isStarred" | "isFlagged" | "folder" | "labels">>
): Promise<Email> {
  // Map camelCase → snake_case for the backend PATCH
  const body: Record<string, unknown> = {};
  if (updates.isRead !== undefined) body.is_read = updates.isRead;
  if (updates.isStarred !== undefined) body.is_starred = updates.isStarred;
  if (updates.isFlagged !== undefined) body.is_flagged = updates.isFlagged;
  if (updates.folder !== undefined) body.folder = updates.folder;
  if (updates.labels !== undefined) body.labels = updates.labels;
  const raw = await gatewayFetch<Record<string, unknown>>(
    `/email/messages/${id}`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
  return mapEmail(raw);
}

/** User-applicable labels/categories (name + colour) for an account. */
export async function listLabels(accountId: string): Promise<LabelInfo[]> {
  const raw = await gatewayFetch<{ name: string; color: string | null }[]>(
    `/email/accounts/${accountId}/labels`
  );
  return raw.map((l) => ({ name: l.name, color: l.color ?? null }));
}

/** Set a label/category's colour on the provider (syncs to the real mailbox).
 *  `color` is a canonical preset token ('preset0'..'preset24'). */
export async function setLabelColor(
  accountId: string,
  name: string,
  color: string
): Promise<LabelInfo> {
  return gatewayFetch<LabelInfo>(`/email/accounts/${accountId}/labels`, {
    method: "PATCH",
    body: JSON.stringify({ name, color }),
  });
}

/** Add/remove labels (by name) on a message; syncs to the provider. */
export async function updateEmailLabels(
  id: string,
  add: string[],
  remove: string[]
): Promise<Email> {
  const body: Record<string, unknown> = {};
  if (add.length) body.add_labels = add;
  if (remove.length) body.remove_labels = remove;
  const raw = await gatewayFetch<Record<string, unknown>>(
    `/email/messages/${id}`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
  return mapEmail(raw);
}

export async function deleteEmail(id: string): Promise<void> {
  await gatewayFetch(`/email/messages/${id}`, { method: "DELETE" });
}

// ── Attachments ────────────────────────────────────────────────────────────

export function getAttachmentDownloadUrl(attachmentId: string): string {
  return `/api/email/attachments/${attachmentId}/download`;
}

// ── Send ─────────────────────────────────────────────────────────────────

export interface SendAttachment {
  filename: string;
  mimeType: string;
  /** base64-encoded file content (no data: prefix). */
  contentB64: string;
}

/** Attach a file from an agent workspace by path (no base64 round-trip) —
 *  e.g. an AI-generated artifact the email assistant (or a sub-agent) produced. */
export interface ArtifactAttachmentRef {
  path: string;
  name?: string;
  /** Source agent workspace (defaults to email-assistant on the backend). */
  agent?: string;
}

export interface SendEmailParams {
  accountId: string;
  to: string[];
  cc?: string[];
  bcc?: string[];
  subject: string;
  bodyText: string;
  bodyHtml?: string;
  replyToMessageId?: string;
  attachments?: SendAttachment[];
  /** Workspace artifacts to attach (resolved to bytes server-side). */
  artifacts?: ArtifactAttachmentRef[];
}

export async function sendEmail(params: SendEmailParams): Promise<{ id: string }> {
  // Map camelCase → snake_case for the backend
  const body: Record<string, unknown> = {
    account_id: params.accountId,
    to: params.to,
    subject: params.subject,
    body_text: params.bodyText,
  };
  if (params.cc) body.cc = params.cc;
  if (params.bcc) body.bcc = params.bcc;
  if (params.bodyHtml) body.body_html = params.bodyHtml;
  if (params.replyToMessageId) body.reply_to_message_id = params.replyToMessageId;
  if (params.attachments?.length) {
    body.attachments = params.attachments.map((a) => ({
      filename: a.filename,
      mime_type: a.mimeType,
      content_b64: a.contentB64,
    }));
  }
  if (params.artifacts?.length) {
    body.artifacts = params.artifacts.map((a) => ({
      path: a.path,
      name: a.name,
      agent: a.agent,
    }));
  }
  return gatewayFetch<{ id: string }>("/email/send", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Read a File into a base64 string (no data: prefix) for sendEmail attachments. */
export function fileToSendAttachment(file: File): Promise<SendAttachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve({
        filename: file.name,
        mimeType: file.type || "application/octet-stream",
        contentB64: comma >= 0 ? result.slice(comma + 1) : result,
      });
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

// ── Sync ─────────────────────────────────────────────────────────────────

export async function triggerSync(accountId: string): Promise<{ ok: boolean }> {
  return gatewayFetch<{ ok: boolean }>("/email/sync", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId }),
  });
}

/** Force a COMPLETE re-sync from the provider. With purge=true, deletes local
 *  messages first (for stale/corrupt local data) before re-fetching. */
export async function resyncAccount(
  accountId: string,
  purge = false,
): Promise<{ resynced: boolean; purged: boolean; messages_synced: number | null }> {
  return gatewayFetch(
    `/email/accounts/${accountId}/resync?purge=${purge ? "true" : "false"}`,
    { method: "POST" },
  );
}

// ── Drafts ───────────────────────────────────────────────────────────────

/** Save an explicit (user-edited) reply body to the provider Drafts folder. */
export async function saveDraftText(
  accountId: string,
  messageId: string,
  body: string,
): Promise<{ created: boolean; id?: string }> {
  return gatewayFetch("/email/drafts/save", {
    method: "POST",
    body: JSON.stringify({
      account_id: accountId,
      message_id: messageId,
      body,
    }),
  });
}

export interface SaveDraftParams {
  accountId: string;
  /** Local id of an existing draft to UPDATE in place (omit to create a new one). */
  draftId?: string;
  /** Local id of the message being replied to (creates a threaded reply draft). */
  replyToMessageId?: string;
  to?: string[];
  /** Carried ON the draft now, so a Cc'd reply saves as a draft instead of
   *  forcing a full send (the old three-way composer branch). */
  cc?: string[];
  bcc?: string[];
  subject?: string;
  body?: string;
  /** Files to attach to the draft (base64 uploads + workspace artifact refs).
   *  Pass only on the explicit pre-send save; the debounced auto-save omits them
   *  so the provider adds each attachment exactly once. */
  attachments?: SendAttachment[];
  artifacts?: ArtifactAttachmentRef[];
}

/**
 * Create or update a draft on the provider AND mirror it locally (the reverse-
 * sync write path behind auto-save). Returns the persisted message so the caller
 * can show it in Drafts / in-thread and keep editing the same draft.
 */
export async function saveDraft(params: SaveDraftParams): Promise<Email> {
  const raw = await gatewayFetch<Record<string, unknown>>("/email/drafts", {
    method: "PUT",
    body: JSON.stringify({
      account_id: params.accountId,
      draft_id: params.draftId ?? null,
      reply_to_message_id: params.replyToMessageId ?? null,
      to: params.to ?? [],
      cc: params.cc ?? [],
      bcc: params.bcc ?? [],
      subject: params.subject ?? "",
      body: params.body ?? "",
      attachments: params.attachments ?? [],
      artifacts: params.artifacts ?? [],
    }),
  });
  return mapEmail(raw);
}

/** Send an existing draft natively (Drafts → Sent, no duplicate). */
export async function sendDraft(
  accountId: string,
  draftId: string,
): Promise<{ sent: boolean }> {
  return gatewayFetch("/email/drafts/send", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, draft_id: draftId }),
  });
}

// NOTE: The email AI chat now runs through the shared chat pipeline
// (@/components/AgentChat → /api/agent/chat → the `email-assistant` agent), so
// the old bespoke streamAIChat / triggerQuickAction clients (which hit
// /api/email/ai/chat and /email/ai/quick-action) were removed. The backend
// endpoints still exist for any external callers.

// ── OAuth ────────────────────────────────────────────────────────────────

const WORKBENCH_URL =
  (typeof window !== "undefined" ? window.location.origin : "") ||
  process.env.NEXT_PUBLIC_WORKBENCH_URL ||
  "http://localhost:3001";

// Both helpers return a BFF path, never a gateway URL. `/email/oauth/…` on the
// gateway is gated (deliberately — the handler binds the mailbox to the caller),
// and a top-level browser navigation cannot carry the Bearer or X-User-Email
// that gate needs. api/email/oauth/[provider]/authorize authenticates the
// caller server-side and re-issues the provider's 302.

export function getOAuthUrl(provider: "gmail" | "microsoft"): string {
  return `/api/email/oauth/${provider}/authorize`;
}

export function getOAuthAuthorizeUrl(
  provider: "gmail" | "microsoft",
  redirectAfter?: string
): string {
  const params = new URLSearchParams();
  if (redirectAfter) params.set("redirect_after", redirectAfter);
  const qs = params.toString();
  return `/api/email/oauth/${provider}/authorize${qs ? `?${qs}` : ""}`;
}

export function getOAuthCallbackUrl(): string {
  return `${WORKBENCH_URL}/email/oauth/callback`;
}

// ══════════════════════════════════════════════════════════════════════════
// Email Automation — Analytics, Senders/Bulk, Newsletters, Assistant rules
// ══════════════════════════════════════════════════════════════════════════

// ── Analytics ──────────────────────────────────────────────────────────────

export async function getAnalyticsOverview(
  accountId?: string,
  days = 30
): Promise<AnalyticsOverview> {
  const sp = new URLSearchParams({ days: String(days) });
  if (accountId) sp.set("account_id", accountId);
  return gatewayFetch<AnalyticsOverview>(`/email/analytics/overview?${sp}`);
}

// ── Contact card ────────────────────────────────────────────────────────────

/** Everything the mail app knows about one person — address, signature-derived
 *  contact details, correspondence stats, and their last few messages.
 *
 *  Backed by mail already in the user's own accounts; there is no directory
 *  lookup and nothing is fetched from the provider, so this is fast enough to
 *  fire on a click. Pass `accountId` to scope the card to one mailbox.
 *
 *  Reading a card also files what it learned into the server-side contacts
 *  directory, so `name` is worth sending: for someone you only ever write TO
 *  there is no from_address to read a display name out of. */
export async function getContactCard(
  email: string,
  accountId?: string,
  limit = 3,
  name?: string
): Promise<ContactCard> {
  const sp = new URLSearchParams({ email, limit: String(limit) });
  if (accountId) sp.set("account_id", accountId);
  if (name?.trim()) sp.set("name", name.trim());
  const raw = await gatewayFetch<Record<string, unknown>>(
    `/email/contacts/card?${sp}`
  );
  const stats = (raw.stats ?? {}) as Record<string, unknown>;
  const details = (raw.details ?? {}) as Record<string, unknown>;
  const recent = (raw.recent ?? []) as Record<string, unknown>[];
  return {
    email: String(raw.email ?? email),
    name: raw.name ? String(raw.name) : null,
    domain: raw.domain ? String(raw.domain) : null,
    category: raw.category ? String(raw.category) : null,
    status: (String(raw.status ?? "UNHANDLED") as SenderStatus),
    stats: {
      received: Number(stats.received ?? 0),
      sent: Number(stats.sent ?? 0),
      unread: Number(stats.unread ?? 0),
      threads: Number(stats.threads ?? 0),
      firstSeen: stats.first_seen ? String(stats.first_seen) : null,
      lastSeen: stats.last_seen ? String(stats.last_seen) : null,
    },
    details: {
      phones: ((details.phones ?? []) as unknown[]).map(String),
      links: ((details.links ?? []) as unknown[]).map(String),
      title: details.title ? String(details.title) : null,
      organization: details.organization ? String(details.organization) : null,
      sourceMessageId: details.source_message_id
        ? String(details.source_message_id)
        : null,
    },
    recent: recent.map((m) => ({
      id: String(m.id ?? ""),
      threadId: m.thread_id ? String(m.thread_id) : null,
      accountId: String(m.account_id ?? ""),
      subject: String(m.subject ?? "(no subject)"),
      preview: String(m.preview ?? ""),
      receivedAt: m.received_at ? String(m.received_at) : null,
      isRead: Boolean(m.is_read),
      hasAttachments: Boolean(m.has_attachments),
      folder: String(m.folder ?? ""),
    })),
  };
}

// ── Senders + bulk actions ──────────────────────────────────────────────────

/** Senders across the WHOLE mailbox (trash/junk/drafts excluded) unless a
 *  `folder` is given. `total` is the distinct sender count in scope, so the
 *  caller can tell the user when the list is capped instead of implying it
 *  covers everything. */
export async function listSenders(
  accountId?: string,
  folder?: string,
  limit = 200,
  offset = 0,
  /** Also count mail already archived. Off by default: the cleaner is about
   *  what still needs handling, so an archived sender leaves the list. */
  includeArchived = false
): Promise<{ senders: SenderStat[]; total: number }> {
  const sp = new URLSearchParams({ limit: String(limit) });
  if (offset) sp.set("offset", String(offset));
  if (accountId) sp.set("account_id", accountId);
  if (folder) sp.set("folder", folder);
  if (includeArchived) sp.set("include_archived", "true");
  const res = await gatewayFetch<{ senders: SenderStat[]; total?: number }>(
    `/email/senders?${sp}`
  );
  const senders = res.senders ?? [];
  return { senders, total: res.total ?? senders.length };
}

export interface BulkActionParams {
  action: "archive" | "trash" | "read" | "unread" | "star" | "unstar";
  accountId?: string;
  messageIds?: string[];
  senderEmail?: string;
  folder?: string;
  olderThanDays?: number;
  onlyRead?: boolean;
}

export async function bulkAction(
  params: BulkActionParams
): Promise<{ affected: number }> {
  const body: Record<string, unknown> = { action: params.action };
  if (params.accountId) body.account_id = params.accountId;
  if (params.messageIds) body.message_ids = params.messageIds;
  if (params.senderEmail) body.sender_email = params.senderEmail;
  if (params.folder) body.folder = params.folder;
  if (params.olderThanDays) body.older_than_days = params.olderThanDays;
  if (params.onlyRead) body.only_read = params.onlyRead;
  return gatewayFetch<{ affected: number }>("/email/messages/bulk", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Newsletters (unsubscribe disposition) ───────────────────────────────────

export async function upsertNewsletter(params: {
  accountId: string;
  email: string;
  name?: string;
  status: NewsletterStatus;
  unsubscribeLink?: string | null;
}): Promise<{ ok: boolean; status: string; archived: number }> {
  return gatewayFetch("/email/newsletters", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      email: params.email,
      name: params.name,
      status: params.status,
      unsubscribe_link: params.unsubscribeLink ?? null,
    }),
  });
}

/** Actually unsubscribe from a sender: a real server-side RFC 8058 one-click
 *  POST (https) or unsubscribe email (mailto). When there's no usable link or
 *  the attempt fails, the sender is blocked (auto-archive + provider filter).
 *  Existing inbox mail is archived either way. */
export async function unsubscribeSender(params: {
  accountId: string;
  email: string;
  name?: string;
  unsubscribeLink?: string | null;
}): Promise<UnsubscribeResult> {
  return gatewayFetch("/email/unsubscribe", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      email: params.email,
      name: params.name,
      unsubscribe_link: params.unsubscribeLink ?? null,
    }),
  });
}

// ── Assistant: rules ────────────────────────────────────────────────────────

export async function listRules(accountId: string): Promise<AutomationRule[]> {
  const res = await gatewayFetch<{ rules: AutomationRule[] }>(
    `/email/rules?account_id=${encodeURIComponent(accountId)}`
  );
  return res.rules ?? [];
}

export async function createRule(rule: AutomationRule): Promise<AutomationRule> {
  return gatewayFetch<AutomationRule>("/email/rules", {
    method: "POST",
    body: JSON.stringify(rule),
  });
}

export async function updateRule(
  id: string,
  rule: AutomationRule
): Promise<AutomationRule> {
  return gatewayFetch<AutomationRule>(`/email/rules/${id}`, {
    method: "PATCH",
    body: JSON.stringify(rule),
  });
}

export async function deleteRule(id: string): Promise<void> {
  await gatewayFetch(`/email/rules/${id}`, { method: "DELETE" });
}

/** Everything ELSE that acts on the mailbox, for the Rules screen's
 *  "Also acting on your mail" section: cold-email blocker mode, the Cleaner's
 *  sender-disposition counts, and the provider-native inbox rules (read-only;
 *  `managed` marks the ones the Cleaner created as block filters). */
export interface RulePolicies {
  cold_email_blocker: string; // OFF | LABEL | ARCHIVE
  dispositions: Record<string, number>; // APPROVED | UNSUBSCRIBED | AUTO_ARCHIVED
  filters_active: number;
  provider_rules: {
    id: string;
    name: string;
    enabled: boolean;
    from_addresses: string[];
    summary: string[];
    managed: boolean;
  }[];
  /** False when the provider has no readable rules (no scope / MSA / IMAP). */
  provider_rules_supported: boolean;
}

export async function getRulePolicies(accountId: string): Promise<RulePolicies> {
  return gatewayFetch(
    `/email/rules/policies?account_id=${encodeURIComponent(accountId)}`
  );
}

// ── Draft attachments (email-assistant workspace) ───────────────────────────

export interface EmailArtifact {
  agent_name: string;
  path: string;
  name: string;
  size: number;
  mime_type: string;
  category: string;
  is_dir: boolean;
}

/** List files in the email-assistant workspace (the attachment picker source). */
export async function listEmailArtifacts(): Promise<EmailArtifact[]> {
  const res = await gatewayFetch<{ artifacts: EmailArtifact[] }>(
    "/agent/artifacts?agent=email-assistant",
  );
  return (res.artifacts ?? []).filter((a) => !a.is_dir);
}

/** Upload file(s) into the email-assistant agent-data folder; returns the
 *  stored files (path + name) to attach to a rule's draft action. */
export async function uploadEmailArtifacts(
  files: File[],
): Promise<{ path: string; name: string }[]> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  const res = await fetch(
    "/api/agent/artifacts/upload?agent=email-assistant&category=agent-data",
    { method: "POST", body: fd },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || body.detail || `Upload failed ${res.status}`);
  }
  const data = (await res.json()) as { path: string; name: string }[];
  return data.map((d) => ({ path: d.path, name: d.name }));
}

/** Create rule(s) from a plain-English description (inbox-zero's prompt flow).
 *  The text may describe several rules at once. */
export async function generateRules(
  accountId: string,
  prompt: string,
): Promise<{ created: AutomationRule[]; error?: string }> {
  return gatewayFetch("/email/rules/generate", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, prompt }),
  });
}

export async function installPresetRules(
  accountId: string
): Promise<{ installed: string[]; total_presets: number }> {
  return gatewayFetch(
    `/email/rules/install-presets?account_id=${encodeURIComponent(accountId)}`,
    { method: "POST" }
  );
}

/**
 * Delete ALL of an account's rules and reinstall the default inbox-zero set
 * fresh (provider-aware: Outlook files cleanup categories into folders, Gmail
 * labels them). Destructive — the UI guards it behind a confirmation prompt.
 */
export async function resetRules(
  accountId: string
): Promise<{ installed: string[]; total_presets: number; reset: boolean }> {
  return gatewayFetch(
    `/email/rules/reset?account_id=${encodeURIComponent(accountId)}`,
    { method: "POST" }
  );
}

export async function undoExecution(
  execId: string
): Promise<{ status: string; reversed: string[] }> {
  return gatewayFetch(`/email/rules/history/${execId}/undo`, {
    method: "POST",
  });
}

/** Persist a Fix correction as a learned classification pattern (inbox-zero
 *  parity) so the same sender is matched/skipped for that rule next time. */
export async function submitRuleFeedback(params: {
  accountId: string;
  sender: string;
  expected: string; // rule id | "none" | "new"
  matchedRuleIds?: string[];
  explanation?: string;
  messageId?: string;
  threadId?: string;
  /** Optional subject keyword to learn alongside (or instead of) the sender. */
  subjectKeyword?: string;
  /** What the correction should TEACH the classifier — goes into the prompt, so
   *  it changes how the model reasons about every sender, not just this one. */
  guidance?: string;
  /** Also pin this sender to the rule, skipping the classifier for them. OFF by
   *  default: a correction should improve the AI, not carve one sender out of
   *  its reach and leave the same misunderstanding everywhere else. */
  pinSender?: boolean;
}): Promise<{
  created: boolean;
  action?: string;
  learned?: unknown[];
  sender?: string;
  /** Set when the correction was a conversation-status fix (thread status set
   *  directly, since learned patterns are overridden for those rules). */
  status_correction?: { ok: boolean; status?: string; label?: string } | null;
  /** The label surgery done on the corrected message itself (H6): the wrong
   *  rules' labels stripped, and the corrected rule's label applied. */
  label_correction?: { removed: string[]; added: string[] } | null;
}> {
  return gatewayFetch("/email/rules/feedback", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      sender: params.sender,
      expected: params.expected,
      matched_rule_ids: params.matchedRuleIds ?? [],
      explanation: params.explanation ?? null,
      message_id: params.messageId ?? null,
      thread_id: params.threadId ?? null,
      subject_keyword: params.subjectKeyword || null,
      guidance: params.guidance || null,
      pin_sender: params.pinSender ?? false,
    }),
  });
}

/** Corrections that teach the classifier — the "improves the AI" half. */
export async function listRuleGuidance(
  accountId: string,
): Promise<RuleGuidance[]> {
  const res = await gatewayFetch<{ guidance: RuleGuidance[] }>(
    `/email/rules/guidance?account_id=${encodeURIComponent(accountId)}`,
  );
  return res.guidance ?? [];
}

export async function addRuleGuidance(params: {
  accountId: string;
  guidance: string;
  ruleId?: string | null;
}): Promise<{ ok: boolean }> {
  return gatewayFetch("/email/rules/guidance", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      guidance: params.guidance,
      rule_id: params.ruleId ?? null,
    }),
  });
}

export async function deleteRuleGuidance(
  id: string,
  accountId: string,
): Promise<void> {
  await gatewayFetch(
    `/email/rules/guidance/${encodeURIComponent(id)}` +
      `?account_id=${encodeURIComponent(accountId)}`,
    { method: "DELETE" },
  );
}

/** List learned classification patterns (sender → rule include/exclude). */
export async function listRulePatterns(
  accountId: string,
): Promise<LearnedRulePattern[]> {
  const res = await gatewayFetch<{ patterns: LearnedRulePattern[] }>(
    `/email/rules/patterns?account_id=${encodeURIComponent(accountId)}`,
  );
  return res.patterns ?? [];
}

export async function deleteRulePattern(id: string): Promise<void> {
  await gatewayFetch(`/email/rules/patterns/${id}`, { method: "DELETE" });
}

/** Approve or reject learned patterns — the gate the Email Cleaner reads.
 *
 *  Omit `patternIds` to act on everything still awaiting review. Rejecting keeps
 *  the row (with `rejected_at` set) rather than deleting it, so the auto-learner
 *  cannot re-infer the same pattern from the same sender an hour later. */
export async function reviewRulePatterns(params: {
  accountId: string;
  patternIds?: string[];
  approve: boolean;
}): Promise<{ updated: number; approved: boolean }> {
  return gatewayFetch("/email/rules/patterns/review", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      pattern_ids: params.patternIds ?? null,
      approve: params.approve,
    }),
  });
}

/** Process PAST inbox mail within a date range (inbox-zero "Process past
 *  emails"). Test = dry-run preview; Apply = execute. Results land in History. */
export async function processPastEmails(params: {
  accountId: string;
  startDate?: string; // YYYY-MM-DD, inclusive
  endDate?: string; // YYYY-MM-DD, inclusive
  isTest: boolean;
  includeRead?: boolean; // false = only process unread mail in the range
  /** Write drafts while backfilling. Defaults to FALSE — a backfill files old
   *  mail; drafting replies to months-old threads costs a model call each and
   *  is almost never wanted. Must be asked for explicitly. */
  draftReplies?: boolean;
  /** Skip mail the rules have already run over. Defaults to TRUE — the date
   *  picker is a range, not a cursor, so re-running or widening it otherwise
   *  re-covers everything already done at a classification call per message. */
  skipProcessed?: boolean;
}): Promise<{
  scheduled: boolean;
  count: number;
  dry_run: boolean;
  draft_replies?: boolean;
  skip_processed?: boolean;
  /** Excluded up front as already-processed. Distinguishes "already done" from
   *  "no emails found in that range" when `count` is 0. */
  already_processed?: number;
}> {
  return gatewayFetch("/email/rules/process-past", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      start_date: params.startDate ?? null,
      end_date: params.endDate ?? null,
      is_test: params.isTest,
      include_read: params.includeRead ?? true,
      draft_replies: params.draftReplies ?? false,
      skip_processed: params.skipProcessed ?? true,
    }),
  });
}

/** What a Process-past run over a range would cost, before running it. */
export type ProcessPastEstimate = {
  /** Everything in the range, whether or not the rules have seen it. */
  in_range: number;
  /** What this run would actually consider. */
  eligible: number;
  already_processed: number;
  /** Of `eligible`, mail "Clean older mail" fetched and deliberately kept away
   *  from the model. Still eligible here — a bounded, user-initiated run is the
   *  case the hold-back leaves room for — but worth saying out loud. */
  held_back: number;
  /** What the run will really send to the AI: `eligible` clamped to the row
   *  limit. Not the same number, which is the whole point of returning both. */
  will_process: number;
  /** True when `eligible` exceeds the limit, so the run covers only part of the
   *  range. Without this the tracker reports the truncated figure as the total
   *  and a partial run reads as a complete one. */
  capped: boolean;
  limit: number;
  max_span_days: number;
};

/** Re-apply rule runs the mail server refused.
 *
 *  A repair, not a re-run: it replays the rule already chosen, so it costs no
 *  model calls, cannot change a decision, and never drafts or sends. Rows that
 *  fail again stay FAILED — a message Outlook has since deleted is beyond
 *  repair and must not be quietly marked done. */
export async function retryFailedRuleActions(
  accountId: string,
  limit = 200
): Promise<{
  considered: number;
  repaired: number;
  still_failing: number;
  skipped_actions: string[];
}> {
  return gatewayFetch("/email/rules/history/retry-failed", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, limit }),
  });
}

/** Ask what a Process-past run would send to the AI, before starting it. */
export async function processPastEstimate(params: {
  accountId: string;
  startDate?: string;
  endDate?: string;
  includeRead?: boolean;
  skipProcessed?: boolean;
}): Promise<ProcessPastEstimate> {
  const q = new URLSearchParams({
    account_id: params.accountId,
    include_read: String(params.includeRead ?? true),
    skip_processed: String(params.skipProcessed ?? true),
  });
  if (params.startDate) q.set("start_date", params.startDate);
  if (params.endDate) q.set("end_date", params.endDate);
  return gatewayFetch<ProcessPastEstimate>(
    `/email/rules/process-past/estimate?${q.toString()}`
  );
}

/** Live progress for the most recent "Process past emails" run on an account.
 *  `idle` = nothing has run; `running` = job in flight; `done`/`error` = finished. */
export type ProcessPastStatus = {
  status: "idle" | "running" | "done" | "error";
  /** Which stage a running job is in: "downloading" the range from the provider,
   *  then "processing" (applying rules per email). */
  phase?: "downloading" | "processing";
  total?: number;
  processed?: number;
  applied?: number;
  skipped?: number;
  /** Excluded before the run as already-processed — NOT counted in `total`, and
   *  distinct from `skipped` (processed this run, matched no rule). */
  already_processed?: number;
  dry_run?: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
};

/** Poll the live progress of the background "Process past emails" job. */
export async function getProcessPastStatus(
  accountId: string
): Promise<ProcessPastStatus> {
  return gatewayFetch<ProcessPastStatus>(
    `/email/rules/process-past/status?account_id=${encodeURIComponent(accountId)}`
  );
}

/** On-demand "Find follow-ups" scan (inbox-zero parity): labels threads waiting
 *  too long for a reply and (if auto-draft is on) drafts nudges. */
export async function scanFollowUps(
  accountId: string
): Promise<{ configured: boolean; scanned: number; labeled: number; drafted: number }> {
  return gatewayFetch("/email/follow-ups/scan", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId }),
  });
}

export async function testRules(params: {
  accountId: string;
  emailId?: string;
  subject?: string;
  fromEmail?: string;
  body?: string;
}): Promise<RuleTestResult> {
  return gatewayFetch<RuleTestResult>("/email/rules/test", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      email_id: params.emailId,
      subject: params.subject,
      from_email: params.fromEmail,
      body: params.body,
    }),
  });
}

export async function getRulesHistory(
  accountId?: string,
  limit = 100
): Promise<ExecutedRule[]> {
  const sp = new URLSearchParams({ limit: String(limit) });
  if (accountId) sp.set("account_id", accountId);
  const res = await gatewayFetch<{ history: ExecutedRule[] }>(
    `/email/rules/history?${sp}`
  );
  return res.history ?? [];
}

/** Resolve a Reply-Zero thread from the dashboard. Default: mark done (labels
 *  collapse, captured tasks close). `done: false` reopens. `dismiss: true` is
 *  the honest third state — "never mind this thread": files it as FYI without
 *  claiming completion and WITHOUT closing captured tasks. */
export async function resolveThread(
  accountId: string,
  threadId: string,
  opts: { done?: boolean; dismiss?: boolean } = {}
): Promise<void> {
  await gatewayFetch(`/email/reply-zero/resolve`, {
    method: "POST",
    body: JSON.stringify({
      account_id: accountId,
      thread_id: threadId,
      done: opts.done ?? true,
      dismiss: opts.dismiss ?? false,
    }),
  });
}

/** Snooze (or, with until=null, un-snooze) a conversation. Applies to the whole
 *  thread; it reappears in the inbox on its own once the time passes. */
export async function snoozeEmail(
  messageId: string,
  until: string | null
): Promise<{ ok: boolean; snoozed_until: string | null }> {
  return gatewayFetch(
    `/email/messages/${encodeURIComponent(messageId)}/snooze`,
    { method: "POST", body: JSON.stringify({ until }) }
  );
}

/** The audit timeline for one message — everything the automation did to it. */
export async function getMessageTimeline(
  messageId: string
): Promise<MessageTimeline> {
  return gatewayFetch<MessageTimeline>(
    `/email/messages/${encodeURIComponent(messageId)}/timeline`
  );
}

export async function approveExecution(
  execId: string
): Promise<{ ok: boolean; status: string; actions: string[] }> {
  return gatewayFetch(`/email/rules/history/${execId}/approve`, {
    method: "POST",
  });
}

export async function rejectExecution(
  execId: string
): Promise<{ ok: boolean; status: string }> {
  return gatewayFetch(`/email/rules/history/${execId}/reject`, {
    method: "POST",
  });
}

export async function testRulesRecent(
  accountId: string,
  limit = 8
): Promise<RecentTestResult[]> {
  const res = await gatewayFetch<{ results: RecentTestResult[] }>(
    "/email/rules/test/recent",
    { method: "POST", body: JSON.stringify({ account_id: accountId, limit }) }
  );
  return res.results ?? [];
}

export async function runRules(params: {
  accountId: string;
  limit?: number;
  dryRun?: boolean;
}): Promise<{ scheduled: boolean; dry_run: boolean }> {
  return gatewayFetch("/email/rules/run", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      limit: params.limit ?? 20,
      dry_run: params.dryRun ?? true,
    }),
  });
}

/**
 * Run rules against a single message — the Test tab's per-row Test/Apply.
 * `isTest` true previews the match (nothing changes); false applies the matched
 * rule's actions and logs an APPLIED row to history.
 */
export async function runRuleOnMessage(params: {
  accountId: string;
  messageId: string;
  isTest: boolean;
}): Promise<RunMessageResult> {
  return gatewayFetch<RunMessageResult>("/email/rules/run-message", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      message_id: params.messageId,
      is_test: params.isTest,
    }),
  });
}

// ── Assistant: settings ─────────────────────────────────────────────────────

export async function getAssistantSettings(
  accountId: string
): Promise<AssistantSettings> {
  return gatewayFetch<AssistantSettings>(
    `/email/assistant/settings?account_id=${encodeURIComponent(accountId)}`
  );
}

export async function saveAssistantSettings(
  settings: AssistantSettings
): Promise<AssistantSettings> {
  return gatewayFetch<AssistantSettings>("/email/assistant/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export async function generateWritingStyle(
  accountId: string
): Promise<{ writing_style: string }> {
  return gatewayFetch<{ writing_style: string }>(
    `/email/assistant/writing-style/generate?account_id=${encodeURIComponent(accountId)}`,
    { method: "POST" }
  );
}

// ── Assistant: knowledge base ───────────────────────────────────────────────

export async function listKnowledge(
  accountId: string
): Promise<KnowledgeEntry[]> {
  const res = await gatewayFetch<{ entries: KnowledgeEntry[] }>(
    `/email/knowledge?account_id=${encodeURIComponent(accountId)}`
  );
  return res.entries ?? [];
}

export async function createKnowledge(
  entry: KnowledgeEntry
): Promise<KnowledgeEntry> {
  return gatewayFetch<KnowledgeEntry>("/email/knowledge", {
    method: "POST",
    body: JSON.stringify(entry),
  });
}

export async function updateKnowledge(
  id: string,
  entry: KnowledgeEntry
): Promise<KnowledgeEntry> {
  return gatewayFetch<KnowledgeEntry>(`/email/knowledge/${id}`, {
    method: "PATCH",
    body: JSON.stringify(entry),
  });
}

export async function deleteKnowledge(id: string): Promise<void> {
  await gatewayFetch(`/email/knowledge/${id}`, { method: "DELETE" });
}

/** Approve a suggested knowledge entry so it starts feeding drafts. */
export async function approveKnowledge(id: string): Promise<void> {
  await gatewayFetch(`/email/knowledge/${id}/approve`, { method: "POST" });
}

// ── Assistant: voice profile ────────────────────────────────────────────────

export async function getVoiceProfile(
  accountId: string
): Promise<VoiceProfile> {
  return gatewayFetch<VoiceProfile>(
    `/email/voice-profile?account_id=${encodeURIComponent(accountId)}`
  );
}

export async function previewVoiceProfile(params: {
  accountId: string;
  startDate?: string; // YYYY-MM-DD, inclusive
  endDate?: string; // YYYY-MM-DD, inclusive
  sources: string[]; // 'sent' and/or 'drafts'
}): Promise<VoiceProfilePreview> {
  const q = new URLSearchParams({
    account_id: params.accountId,
    sources: params.sources.join(","),
  });
  if (params.startDate) q.set("start_date", params.startDate);
  if (params.endDate) q.set("end_date", params.endDate);
  return gatewayFetch<VoiceProfilePreview>(
    `/email/voice-profile/preview?${q.toString()}`
  );
}

export async function buildVoiceProfile(params: {
  accountId: string;
  startDate?: string;
  endDate?: string;
  sources: string[];
  extractKnowledge: boolean;
}): Promise<{ scheduled: boolean }> {
  return gatewayFetch("/email/voice-profile/build", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      start_date: params.startDate,
      end_date: params.endDate,
      sources: params.sources,
      extract_knowledge: params.extractKnowledge,
    }),
  });
}

export async function getVoiceProfileStatus(
  accountId: string
): Promise<VoiceProfileBuildStatus> {
  return gatewayFetch<VoiceProfileBuildStatus>(
    `/email/voice-profile/status?account_id=${encodeURIComponent(accountId)}`
  );
}

export async function saveVoiceProfile(params: {
  accountId: string;
  enabled?: boolean;
  styleGuide?: string;
}): Promise<VoiceProfile> {
  return gatewayFetch<VoiceProfile>("/email/voice-profile", {
    method: "PUT",
    body: JSON.stringify({
      account_id: params.accountId,
      enabled: params.enabled,
      style_guide: params.styleGuide,
    }),
  });
}

export async function deleteVoiceProfile(accountId: string): Promise<void> {
  await gatewayFetch(
    `/email/voice-profile?account_id=${encodeURIComponent(accountId)}`,
    { method: "DELETE" }
  );
}

export async function sampleVoiceProfile(
  accountId: string,
  scenario: string
): Promise<{ sample: string; scenario: string }> {
  return gatewayFetch("/email/voice-profile/sample", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, scenario }),
  });
}

export async function listLearnedPatterns(
  accountId: string
): Promise<LearnedPattern[]> {
  const res = await gatewayFetch<{ patterns: LearnedPattern[] }>(
    `/email/learned-patterns?account_id=${encodeURIComponent(accountId)}`
  );
  return res.patterns ?? [];
}

export async function deleteLearnedPattern(id: string): Promise<void> {
  await gatewayFetch(`/email/learned-patterns/${id}`, { method: "DELETE" });
}

// ── Sender categorization ───────────────────────────────────────────────────

export async function categorizeSenders(
  accountId: string,
  limit = 60
): Promise<{ scheduled: boolean }> {
  return gatewayFetch("/email/senders/categorize", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, limit }),
  });
}

export async function getSenderCategories(
  accountId: string
): Promise<{ categories: string[]; counts: Record<string, number> }> {
  return gatewayFetch(
    `/email/senders/categories?account_id=${encodeURIComponent(accountId)}`
  );
}

// ── Uncategorized-inbox sweep (Email Cleaner) ───────────────────────────────

/** Result of projecting existing categorization onto uncategorized inbox mail.
 *  `no_evidence` is mail the sweep deliberately refused to guess at — it needs a
 *  real rules run, not a second classifier. */
export interface CleanupSweepResult {
  scanned: number;
  categorized: number;
  no_evidence: number;
  /** Rows the sweep decided but whose provider label write failed (e.g. Graph
   *  throttling) — counted so `categorized` and the decided total can't silently
   *  disagree. */
  failed?: number;
  by_category: Record<string, number>;
  by_reason: Record<string, number>;
  dry_run: boolean;
  /** The mailbox actually ran dry, rather than the run hitting its bound. */
  exhausted?: boolean;
  /** Preview only: it stopped at its sample bound, so these numbers describe
   *  part of the mailbox, not all of it. */
  sampled?: boolean;
  error?: string;
}

/** Preview the sweep. Bounded server-side to stay inside one request — the
 *  result carries `sampled: true` when it only saw part of the mailbox. */
export async function previewAutoCategorize(
  accountId: string,
  limit = 0
): Promise<CleanupSweepResult> {
  return gatewayFetch("/email/cleanup/auto-categorize", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, limit, dry_run: true }),
  });
}

/** Run the sweep for real over the WHOLE mailbox (`limit: 0`), in the
 *  background — poll `getCleanupStatus`. */
export async function runAutoCategorize(
  accountId: string,
  limit = 0
): Promise<{ scheduled: boolean }> {
  return gatewayFetch("/email/cleanup/auto-categorize", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, limit, dry_run: false }),
  });
}

export async function getCleanupStatus(
  accountId: string
): Promise<
  Partial<CleanupSweepResult> & {
    status: string;
    applied?: number;
    /** Which half of a backfill run is in flight. Absent for a plain sweep. */
    phase?: "downloading" | "cleaning" | "done" | "error";
    /** Messages newly fetched from the provider during the download phase. */
    synced?: number;
    /** History stamped rules-processed so it never reaches the model-driven
     *  rule run. This is what keeps a big backfill from costing anything. */
    held_back?: number;
  }
> {
  return gatewayFetch(
    `/email/cleanup/status?account_id=${encodeURIComponent(accountId)}`
  );
}

/** Fetch older mail from the provider, then categorize it with NO model calls.
 *
 *  The deterministic counterpart of "Process past emails". The Cleaner can only
 *  clean what has been synced, and the initial sync only ever reached back one
 *  year — so on a real mailbox most mail has never been seen locally. Runs in
 *  the background; poll `getCleanupStatus` for the two-phase progress.
 *
 *  `sinceDate` is YYYY-MM-DD; omit it to fetch the entire mailbox. */
export async function backfillAndClean(
  accountId: string,
  sinceDate?: string
): Promise<{ scheduled: boolean; since?: string | null; reason?: string }> {
  return gatewayFetch("/email/cleanup/backfill", {
    method: "POST",
    body: JSON.stringify({
      account_id: accountId,
      since_date: sinceDate ?? null,
    }),
  });
}

/** Re-read every label from the provider back into `categories`.
 *  The repair path when labels were lost locally — the truth still lives
 *  upstream, so this restores in seconds without a deep re-sync. */
export async function restoreProviderLabels(
  accountId: string
): Promise<{
  messages: number;
  labels: number;
  updated: number;
  error?: string;
}> {
  return gatewayFetch("/email/cleanup/restore-labels", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId }),
  });
}

export async function getUncategorizedOverview(
  accountId: string
): Promise<{
  uncategorized: number;
  /** Learned patterns the cleaner will NOT project until they are reviewed. */
  pending_patterns?: number;
  /** Uncategorized mail those pending patterns would reach if approved. */
  pending_pattern_reach?: number;
  top_senders: { email: string; name: string; count: number }[];
}> {
  return gatewayFetch(
    `/email/cleanup/uncategorized?account_id=${encodeURIComponent(accountId)}`
  );
}

// ── Cold-email blocker ──────────────────────────────────────────────────────

export async function listColdSenders(
  accountId: string
): Promise<ColdSender[]> {
  const res = await gatewayFetch<{ cold_senders: ColdSender[] }>(
    `/email/cold-senders?account_id=${encodeURIComponent(accountId)}`
  );
  return res.cold_senders ?? [];
}

export async function upsertColdSender(params: {
  accountId: string;
  fromEmail: string;
  status: "AI_LABELED_COLD" | "USER_REJECTED_COLD";
}): Promise<{ ok: boolean }> {
  return gatewayFetch("/email/cold-senders", {
    method: "POST",
    body: JSON.stringify({
      account_id: params.accountId,
      from_email: params.fromEmail,
      status: params.status,
    }),
  });
}

/**
 * Draft a context-aware reply using the orchestrating drafter (memory +
 * sales/task-manager agents). Optionally also saves it to the provider Drafts.
 */
export async function draftReplySmart(
  accountId: string,
  messageId: string,
  createDraft = false,
  followUp = false
): Promise<{ draft: string; created: boolean }> {
  return gatewayFetch("/email/draft-reply", {
    method: "POST",
    body: JSON.stringify({
      account_id: accountId,
      message_id: messageId,
      create_draft: createDraft,
      follow_up: followUp,
    }),
  });
}

/**
 * Draft or improve the body the user is composing. Pass ONLY the new text
 * (`body`) — the caller must strip the quoted trailing chain first so the AI
 * never rewrites the quote. Empty `body` ⇒ draft from scratch; non-empty ⇒
 * improve in place. For a reply/forward, pass `messageId` so the original thread
 * is loaded as context (never quoted back). Returns the drafted/improved body.
 */
export interface ComposeAssistArgs {
  accountId: string;
  body?: string;
  instruction?: string;
  mode?: "new" | "reply" | "forward";
  messageId?: string;
  to?: string[];
  subject?: string;
}

export async function composeAssist(
  args: ComposeAssistArgs
): Promise<{ draft: string; skipped?: string }> {
  return gatewayFetch("/email/compose-assist", {
    method: "POST",
    body: JSON.stringify({
      account_id: args.accountId,
      body: args.body ?? "",
      instruction: args.instruction ?? "",
      mode: args.mode ?? "new",
      message_id: args.messageId,
      to: args.to,
      subject: args.subject ?? "",
    }),
  });
}

/** One structured step event from the drafting backend. */
export interface ComposeActivityEvent {
  /** "stage" (context/memory/draft), "qualify", or "consult". */
  kind?: string;
  id?: string;
  status?: "start" | "done" | "failed";
  detail?: string;
  /** qualify: what kind of email this is, and how many consults it needs. */
  emailKind?: string;
  consults?: number;
  /** consult: the specialist asked, the question put to it, and its answer. */
  agent?: string;
  question?: string;
  answer?: string;
}

/** Progress callbacks for the streaming "Draft with AI" (all optional). */
export interface ComposeAssistStreamHandlers {
  /** Structured backend steps, for the composer's activity panel. */
  onActivity?: (event: ComposeActivityEvent) => void;
  /** Live model output. kind "content" is draft text (append to the body);
   *  "reasoning" is the model thinking out loud (show, don't insert). */
  onDelta?: (kind: "reasoning" | "content", text: string) => void;
}

/**
 * Streaming variant of {@link composeAssist}: same request, but progress and
 * draft tokens arrive live over SSE so the composer can narrate what the
 * backend is doing instead of holding a bare spinner. Resolves with the FINAL
 * cleaned draft (always prefer it over concatenated deltas). Falls back to the
 * non-streaming endpoint transparently if the stream can't be opened.
 */
export async function composeAssistStream(
  args: ComposeAssistArgs,
  handlers: ComposeAssistStreamHandlers = {}
): Promise<{ draft: string; skipped?: string }> {
  let res: Response;
  try {
    res = await fetch("/api/email/compose-assist/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account_id: args.accountId,
        body: args.body ?? "",
        instruction: args.instruction ?? "",
        mode: args.mode ?? "new",
        message_id: args.messageId,
        to: args.to,
        subject: args.subject ?? "",
      }),
    });
    if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
  } catch {
    // Older gateway / proxy without the stream route — degrade silently.
    return composeAssist(args);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done: { draft: string; skipped?: string } | null = null;
  let streamError: string | null = null;
  for (;;) {
    const { value, done: eof } = await reader.read();
    if (eof) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? ""; // keep the trailing partial line
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      let evt: ComposeActivityEvent & {
        type?: string;
        text?: string;
        draft?: string;
        skipped?: string;
        error?: string;
      };
      try {
        evt = JSON.parse(line.slice(6));
      } catch {
        continue;
      }
      if (evt.type === "activity") {
        const { type: _t, text: _x, draft: _d, ...event } = evt;
        handlers.onActivity?.(event);
      } else if (evt.type === "delta" && evt.text) {
        handlers.onDelta?.(
          evt.kind === "reasoning" ? "reasoning" : "content",
          evt.text
        );
      } else if (evt.type === "done") {
        done = { draft: evt.draft ?? "", skipped: evt.skipped };
      } else if (evt.type === "error") {
        streamError = evt.error || "draft failed";
      }
    }
  }
  if (done) return done;
  throw new Error(streamError ?? "draft stream ended without a result");
}

// ── Digest ──────────────────────────────────────────────────────────────────

export async function getDigest(
  accountId: string,
  period: "day" | "week" = "day"
): Promise<import("./types").DigestData> {
  return gatewayFetch(
    `/email/digest?account_id=${encodeURIComponent(accountId)}&period=${period}`
  );
}

export async function sendDigest(
  accountId: string,
  period: "day" | "week" = "day"
): Promise<{ sent: boolean; to: string }> {
  return gatewayFetch("/email/digest/send", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, period }),
  });
}
