/**
 * Client-side chat session management.
 *
 * localStorage is the synchronous source-of-truth for the sidebar list (instant reads).
 * Every mutating operation also fires an async Postgres sync in the background so history
 * survives browser cache clears and is accessible from any device.
 *
 * Message persistence: `getMessages` / `saveMessages` use the Postgres API as primary
 * store and write-through to localStorage for instant reads on mount.
 */

import { serializeReasoning, parseReasoning } from "@/lib/chatStream";

export interface ChatSession {
  id: string;
  name: string;
  agentName: string;
  createdAt: string;
  updatedAt: string;
  messageCount: number;
  /** Auto-derived from the first user message (M2.6). Falls back to `name`. */
  title?: string;
  /** Last ~120 chars of the most recent assistant turn, shown as a subtitle. */
  lastPreview?: string;
  /**
   * Room facts, server-sourced. A session shared with you appears in your list
   * because the gateway resolves membership — localStorage cannot know about a
   * room somebody else added you to, so these are only ever set by the merge
   * below and never invented locally.
   */
  visibility?: "private" | "people" | "org";
  /** False when someone else created this conversation and shared it with you. */
  isOwner?: boolean;
  /** >1 means somebody else is in here too. Drives the shared badge. */
  participantCount?: number;
}

// ---------------------------------------------------------------------------
// Chat cache namespaces (production bug 2026-10-05, PR #652)
// ---------------------------------------------------------------------------
// The session list used to live under one key for the whole browser. A rail
// restored the newest session of its agent from it, so a second member on one
// browser reopened the FIRST member's chat, from a different org. The gateway
// refused every send, which is correct, and the member saw only an error.
//
// Now EVERY per-member chat cache lives in one namespace per account, keyed by
// the scope `<email>|<orgId>`: the session list, each cached transcript
// (`msgs`, which also holds compaction summaries and browser-only replies),
// each send queue and the app builder's per-app ids. `chatKey` is the ONE key
// builder, and `railSessions.test.ts` fails on a raw `cc-` chat key built
// anywhere else. Design note: `projects_ai_chat.md`, "Chat cache namespaces
// and multi-account".
//
// - A switch of the bound scope deletes nothing. Switching back finds the old
//   namespace exactly as it was. A future account switcher only changes the
//   bound scope.
// - A sign-out clears the namespaces of that account only (`clearSignedOutAccount`).
// - The caches written before #652 have no owner. They are deleted, never
//   moved into a namespace (`purgeLegacyChatCaches`).
// - While no scope is bound, every read is empty and no local write occurs.
//   The server copy still syncs.

/** The prefix of every namespaced chat key. */
const NS_PREFIX = "cc-chat::";

/**
 * What a namespaced key holds. `unread` is the member's chats with a reply
 * they have not read, and `open` the heartbeat of the chats that show in a
 * visible tab (WS-51 S5, `lib/runSignals.ts`).
 */
export type ChatCacheKind = "sessions" | "msgs" | "queue" | "builder" | "unread" | "open";

/**
 * The window event that says the unread map changed in THIS tab. Another tab
 * hears the `storage` event instead. `lib/runSignals.ts` listens to both.
 */
export const UNREAD_EVENT = "cc-unread-change";

/**
 * Drop one chat from the unread map (WS-51 S5). A deleted or forgotten chat
 * must not keep a dot, or a count that nothing can clear. The map's entry
 * shape belongs to `lib/runSignals.ts`. This only removes a key.
 */
function dropUnread(id: string): void {
  const key = chatKey("unread");
  if (typeof window === "undefined" || !key) return;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return;
    const map = JSON.parse(raw) as Record<string, unknown>;
    if (!map || typeof map !== "object" || !(id in map)) return;
    delete map[id];
    localStorage.setItem(key, JSON.stringify(map));
    if (typeof window.dispatchEvent === "function") window.dispatchEvent(new Event(UNREAD_EVENT));
  } catch {
    /* storage off, or a map that does not parse: nothing to drop */
  }
}

/**
 * Points at the last member scope bound in this browser, so a sign-out that
 * the page did not see (an expiry, the middleware redirect) still knows whose
 * namespace to clear. It names an account and holds no chat content.
 */
const LAST_SCOPE_KEY = "cc-chat-last-scope";

/** Keys written before #652: one browser-wide list, and per-id caches. */
const LEGACY_LIST_KEY = "cc-chat-sessions";
const LEGACY_PREFIXES = ["cc-chat-sessions", "cc-msgs-", "cc-queue-", "cc-app-builder-session-"];

/** The member part of a scope for a browser with no email (dev with auth off). */
export const NO_EMAIL = "anonymous";

let boundScope: string | null = null;
/** The last org id seen for an email, so a failed org lookup keeps the scope. */
let lastIdentity: { email: string; orgId: string } | null = null;
/**
 * The last member scope seen in THIS page (fix round 2). Module state, so it
 * lives exactly as long as the page: a real sign-out is a full navigation,
 * and it resets. A deploy restart does not navigate, and it answers
 * `GET /api/auth/me` with a 200 that names nobody.
 */
let lastMemberScope: string | null = null;

/**
 * The scope of one member in one organization, or null when there is no
 * member to name. The email is case-folded, because the server folds it too.
 */
export function chatScope(
  email: string | null | undefined,
  organizationId: string | null | undefined,
): string | null {
  const who = (email ?? "").trim().toLowerCase();
  if (!who) return null;
  return `${who}|${(organizationId ?? "").trim()}`;
}

/**
 * THE key builder for every per-member chat cache. Null while no scope is
 * bound, and a caller then reads nothing and writes nothing.
 */
export function chatKey(
  kind: ChatCacheKind,
  id?: string,
  scope: string | null = boundScope,
): string | null {
  if (!scope) return null;
  return `${NS_PREFIX}${scope}::${kind}${id === undefined ? "" : `::${id}`}`;
}

/** The namespace prefix of one scope, or of every org of one email. */
function namespacePrefix(scope: string): string {
  return `${NS_PREFIX}${scope}::`;
}
function accountPrefix(email: string): string {
  return `${NS_PREFIX}${email}|`;
}

/**
 * The scope for one answer of `useAccess()`.
 *
 * - Loading: null. Nothing restores until the member is known.
 * - No email, after a member was seen in this page: that member's scope.
 *   A deploy restart answers 200 with nobody in it, and that is a blip, not a
 *   sign-out (fix round 2).
 * - No email on a STALE answer with no member seen: null.
 * - No email on an authoritative answer with no member seen: the `anonymous`
 *   scope (dev with auth off, or signed out).
 * - An email with no org id, after an answer that had one for the SAME email:
 *   the last org id. `GET /auth/me` answers `organization: {}` when its org
 *   query fails, and a scope that flipped would move every open rail.
 */
export function scopeFromAccess(a: {
  loading: boolean;
  stale: boolean;
  email: string | null | undefined;
  organizationId: string | null | undefined;
}): string | null {
  if (a.loading) return null;
  const email = (a.email ?? "").trim().toLowerCase();
  if (!email) {
    if (lastMemberScope) return lastMemberScope;
    return a.stale ? null : chatScope(NO_EMAIL, null);
  }
  let org = (a.organizationId ?? "").trim();
  if (!org && lastIdentity?.email === email) org = lastIdentity.orgId;
  if (org) lastIdentity = { email, orgId: org };
  const scope = chatScope(email, org);
  lastMemberScope = scope;
  return scope;
}

/**
 * Bind the scope every chat key is built in. Idempotent, and it touches no
 * storage, so a caller may run it during render. Returns true when the scope
 * changed. A change deletes nothing: the old namespace stays as it was.
 */
export function bindChatScope(scope: string | null): boolean {
  if (scope === boundScope) return false;
  boundScope = scope;
  return true;
}

/**
 * Tests only: end this "page". The module state goes and storage stays, as on
 * a full navigation.
 */
export function __newPageForTests(): void {
  boundScope = null;
  lastIdentity = null;
  lastMemberScope = null;
}

/** The scope that is bound now, or null. */
export function boundChatScope(): string | null {
  return boundScope;
}

/** The app builder's per-app key, in the bound namespace. Null while unbound. */
export function builderSessionKey(slug: string): string | null {
  return chatKey("builder", slug);
}

/** The builder session id remembered for an app, in the bound namespace. */
export function getBuilderSessionId(slug: string): string | null {
  const key = builderSessionKey(slug);
  if (typeof window === "undefined" || !key) return null;
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Remember the builder session id of an app, in the bound namespace. */
export function setBuilderSessionId(slug: string, id: string): void {
  const key = builderSessionKey(slug);
  if (typeof window === "undefined" || !key) return;
  try {
    localStorage.setItem(key, id);
  } catch {
    /* storage off: the next visit finds the session by its name */
  }
}

/** Every storage key that matches `test`. */
function keysWhere(test: (k: string) => boolean): string[] {
  const out: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && test(k)) out.push(k);
  }
  return out;
}

function removeKeys(keys: string[]): void {
  for (const k of keys) localStorage.removeItem(k);
}

/**
 * Delete the chat caches written before #652. They have no owner, so nobody
 * can say whose they are: they are never moved into a namespace.
 */
export function purgeLegacyChatCaches(): void {
  if (typeof window === "undefined") return;
  try {
    removeKeys(keysWhere((k) => k === LEGACY_LIST_KEY || LEGACY_PREFIXES.some((p) => k.startsWith(p))));
  } catch {
    /* storage unavailable: nothing is stored */
  }
}

/**
 * The storage work for a newly bound scope. Run it from an effect.
 *
 * It deletes the caches with no owner, and it records a MEMBER scope as the
 * last one. It deletes nothing of any namespace: a switch is not a sign-out.
 * It does nothing for the no-email scope while a member was seen in this page
 * (the deploy blip, fix round 2).
 */
export function tidyChatStorage(scope: string | null): void {
  if (!scope || typeof window === "undefined") return;
  const email = scope.slice(0, scope.indexOf("|"));
  if (email === NO_EMAIL && lastMemberScope) return;
  purgeLegacyChatCaches();
  if (email !== NO_EMAIL) {
    try { localStorage.setItem(LAST_SCOPE_KEY, scope); } catch { /* storage off */ }
  }
}

/**
 * A sign-out of the account of the last bound member scope: clear every
 * namespace of that EMAIL (all its orgs), and nothing of any other account.
 * The client cannot always tell which account ended (an expiry, the
 * middleware redirect), so it clears the last one it bound.
 */
export function clearSignedOutAccount(): void {
  if (typeof window === "undefined") return;
  try {
    const last = localStorage.getItem(LAST_SCOPE_KEY);
    if (last && last.includes("|")) {
      const prefix = accountPrefix(last.slice(0, last.indexOf("|")));
      removeKeys(keysWhere((k) => k.startsWith(prefix)));
    }
    localStorage.removeItem(LAST_SCOPE_KEY);
  } catch {
    /* storage unavailable: nothing is stored */
  }
  boundScope = null;
  lastIdentity = null;
  lastMemberScope = null;
}

/**
 * "Sign out of all accounts" (MT-1k slice A2): clear every namespace of each
 * of these emails, all their orgs, and the last-scope pointer.
 *
 * ⚠️ `clearSignedOutAccount` knows only the LAST bound account, so it would
 * leave the other accounts' chats behind on a shared computer. The switcher
 * names every account it held, and this clears them all.
 */
export function clearAccountNamespaces(emails: readonly string[]): void {
  if (typeof window === "undefined") return;
  try {
    // Both spellings: a token keeps the case the provider sent, and the scope
    // takes the email the gateway answered with.
    for (const email of new Set(emails.flatMap((e) => [e, e.toLowerCase()]))) {
      const prefix = accountPrefix(email);
      removeKeys(keysWhere((k) => k.startsWith(prefix)));
    }
    localStorage.removeItem(LAST_SCOPE_KEY);
  } catch {
    /* storage unavailable: nothing is stored */
  }
  boundScope = null;
  lastIdentity = null;
  lastMemberScope = null;
}

/**
 * True when the browser signed out, by any path: a sign-out button, an
 * expired session, the middleware redirect, the NextAuth sign-out page.
 *
 * Two answers must agree. NextAuth's own session says nobody is signed in,
 * AND an authoritative access answer names nobody. A deploy restart fails
 * only the second (the NextAuth session lives in this app, not in the
 * gateway), so it never clears a namespace. Dev with auth off names a member
 * in its access answer, so it never clears either.
 */
/** How long the sign-out confirm waits before it gives up (and clears nothing). */
export const SIGN_OUT_CONFIRM_MS = 5_000;

/**
 * Ask NextAuth once more, with no cache, whether anybody is signed in
 * (PR #652 round 3). True ONLY when the answer is a 2xx whose session names
 * no user. A network failure, a non-2xx, a body that does not parse or a
 * timeout is "not confirmed", and a caller then clears nothing.
 *
 * Why: NextAuth's client turns a FAILED session fetch into "unauthenticated"
 * and keeps it for the life of the page, and `/api/auth/me` answers 200 with
 * nobody while the gateway restarts. A deploy that restarts both close
 * together looks exactly like a sign-out. Clearing on that would wipe a
 * member who is still signed in.
 */
export async function confirmSignedOut(
  fetchImpl: typeof fetch = fetch,
  timeoutMs: number = SIGN_OUT_CONFIRM_MS,
): Promise<boolean> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  // A race as well as the abort: a timeout answers "not confirmed" even when
  // the request never settles.
  const timedOut = new Promise<false>((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve(false);
    }, timeoutMs);
  });
  const asked = (async (): Promise<boolean> => {
    const res = await fetchImpl("/api/auth/session", {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) return false;
    const body = (await res.json()) as { user?: unknown } | null;
    return !body || !body.user;
  })().catch(() => false);
  try {
    return await Promise.race([asked, timedOut]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export function isSignedOut(a: {
  sessionStatus: "loading" | "authenticated" | "unauthenticated";
  accessLoading: boolean;
  stale: boolean;
  email: string | null | undefined;
}): boolean {
  return (
    a.sessionStatus === "unauthenticated" &&
    !a.accessLoading &&
    !a.stale &&
    !(a.email ?? "").trim()
  );
}

/**
 * Tests and tooling only: forget every namespaced chat cache in this browser,
 * for every account, and the module state.
 */
export function forgetChatSessions(): void {
  boundScope = null;
  lastIdentity = null;
  lastMemberScope = null;
  if (typeof window === "undefined") return;
  try {
    removeKeys(keysWhere((k) => k.startsWith(NS_PREFIX) || k === LAST_SCOPE_KEY));
  } catch {
    /* storage unavailable */
  }
}

/** True when the namespace of `scope` holds any key. */
export function hasNamespace(scope: string): boolean {
  if (typeof window === "undefined") return false;
  const prefix = namespacePrefix(scope);
  return keysWhere((k) => k.startsWith(prefix)).length > 0;
}

/**
 * Sentinels a session's agentName can carry when its real agent hasn't been
 * resolved — chiefly "unknown", which /chat/active-sessions returns for a
 * Redis-active thread with no chat_session row yet. Such a value must NEVER be
 * dispatched to /agent/run/stream verbatim (it 422s "Unknown agent 'unknown'").
 * The gateway now recovers the real agent from the run trace on dispatch; the
 * client mirrors that intent by treating these as "unresolved → show picker".
 */
const UNRESOLVED_AGENT_SENTINELS = new Set(["", "unknown", "undefined", "null", "none"]);

/** True when a session's agentName is a placeholder, not a real agent. */
export function isUnresolvedAgent(agentName: string | null | undefined): boolean {
  return UNRESOLVED_AGENT_SENTINELS.has((agentName ?? "").trim().toLowerCase());
}

/** Write to localStorage without letting a QuotaExceededError crash the caller
 *  (the session list / message cache can fill the quota on heavy users). */
function safeSetItem(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* quota exceeded or storage unavailable — Postgres remains the durable store */
  }
}

function readList(key: string | null): ChatSession[] {
  if (typeof window === "undefined" || !key) return [];
  try {
    const parsed = JSON.parse(localStorage.getItem(key) ?? "[]") as unknown;
    return Array.isArray(parsed) ? (parsed as ChatSession[]) : [];
  } catch (_e) {
    return [];
  }
}

/** Write the bound member's list. No scope bound, no local write. */
function writeList(sessions: ChatSession[], key: string | null = chatKey("sessions")): void {
  if (!key) return;
  safeSetItem(key, JSON.stringify(sessions));
}

/** The signed-in member's list in this org. Empty while no scope is bound. */
export function getSessions(): ChatSession[] {
  return readList(chatKey("sessions"));
}

export function upsertSession(session: ChatSession): void {
  const sessions = getSessions();
  const idx = sessions.findIndex((s) => s.id === session.id);
  if (idx >= 0) {
    sessions[idx] = session;
  } else {
    sessions.unshift(session);
  }
  writeList(sessions);
  // Background sync to Postgres — never blocks the UI.
  _syncSessionToDb(session).catch(() => {});
}

export function deleteSession(id: string): void {
  const sessions = getSessions().filter((s) => s.id !== id);
  writeList(sessions);
  // Also remove the persisted messages for this session.
  deleteMessages(id);
  dropUnread(id);
  // Background delete from Postgres.
  fetch(`/api/chat/sessions/${id}`, { method: "DELETE" }).catch(() => {});
}

/**
 * Drop a session from THIS browser only. For an id the server refused: it is
 * not this member's to delete, so no DELETE goes to the server.
 *
 * ⚠️ The send queue (`cc-queue-<id>`) STAYS. It holds words the member typed
 * and has not sent, and a refused chat is no reason to lose them. The rail
 * carries them into the composer of the new chat (`carriedText`).
 */
export function forgetSession(id: string): void {
  writeList(getSessions().filter((s) => s.id !== id));
  deleteMessages(id);
  dropUnread(id);
}

/**
 * True when the server refused a session, not a turn. A 404 means "no such
 * conversation for you". A 403 counts only with the room rule's own words,
 * because a viewer's 403 ("cannot send") is a real answer about a real room.
 */
export function isSessionRefusal(status: number, body: string): boolean {
  if (status === 404) return true;
  return status === 403 && isSessionRefusalText(body);
}

/** The words of the refusal, for a stream that carries it as an error frame. */
export function isSessionRefusalText(text: string): boolean {
  return /not a participant of this conversation/i.test(text);
}

/**
 * Ask the server whether this member may read a session. GET /room answers
 * 404 for a room the caller cannot read (another org, or private and not in
 * it), and 200 for a room the caller is in or for an id with no row yet. Any
 * other answer, or no answer, is "unknown", and a caller must not act on it.
 */
export async function probeSession(
  id: string,
  fetchImpl: typeof fetch = fetch,
): Promise<"ok" | "refused" | "unknown"> {
  try {
    const res = await fetchImpl(`/api/chat/sessions/${encodeURIComponent(id)}/room`, {
      cache: "no-store",
    });
    if (res.ok) return "ok";
    if (res.status === 404) return "refused";
    if (res.status === 403) {
      const body = await res.text().catch(() => "");
      return isSessionRefusal(403, body) ? "refused" : "unknown";
    }
    return "unknown";
  } catch {
    return "unknown";
  }
}

export function createSession(agentName = "orchestrator"): ChatSession {
  // Never mint a session on a placeholder agent — that's the poisoned-row bug
  // (chat_session.agent_name='unknown' → dispatch 422). A caller passing a
  // sentinel means the agent wasn't resolved upstream; surface it loudly and
  // fall back to the orchestrator rather than persist an undispatchable name.
  if (isUnresolvedAgent(agentName)) {
    console.warn(
      `createSession called with unresolved agent ${JSON.stringify(agentName)} — ` +
      `falling back to "orchestrator". Resolve the agent before creating the session.`,
    );
    agentName = "orchestrator";
  }
  const now = new Date().toISOString();
  return {
    id: crypto.randomUUID(),
    name: new Date().toLocaleString("en-IN", {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    }),
    agentName,
    createdAt: now,
    updatedAt: now,
    messageCount: 0,
  };
}

export function touchSession(id: string, messageCount?: number): void {
  const sessions = getSessions();
  const s = sessions.find((x) => x.id === id);
  if (!s) return;
  s.updatedAt = new Date().toISOString();
  if (messageCount !== undefined) s.messageCount = messageCount;
  writeList(sessions);
}

/** Truncate text at a word boundary, appending an ellipsis when cut. */
function truncateAtWord(text: string, max: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const slice = clean.slice(0, max);
  const lastSpace = slice.lastIndexOf(" ");
  return (lastSpace > max * 0.6 ? slice.slice(0, lastSpace) : slice).trimEnd() + "\u2026";
}

/**
 * Enrich a session with an auto-title (from the first user message) and a
 * last-turn preview. No-op for fields left undefined. Title is only set once,
 * so manual edits / the first message win and later turns don't overwrite it.
 */
export function enrichSession(
  id: string,
  info: { firstUserMessage?: string; lastPreview?: string; messageCount?: number },
): void {
  const sessions = getSessions();
  const s = sessions.find((x) => x.id === id);
  if (!s) return;
  if (info.firstUserMessage && !s.title) {
    s.title = truncateAtWord(info.firstUserMessage, 60);
  }
  if (info.lastPreview !== undefined) {
    s.lastPreview = truncateAtWord(info.lastPreview, 120);
  }
  if (info.messageCount !== undefined) s.messageCount = info.messageCount;
  s.updatedAt = new Date().toISOString();
  writeList(sessions);
  // Sync enriched metadata to Postgres in background.
  _patchSessionInDb(id, {
    title: s.title,
    lastPreview: s.lastPreview,
    messageCount: s.messageCount,
  }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Postgres API helpers (all async, all fire-and-forget safe)
// ---------------------------------------------------------------------------

/** Push a full session record to Postgres (create or update). */
async function _syncSessionToDb(s: ChatSession): Promise<void> {
  await fetch("/api/chat/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: s.id,
      agent_name: s.agentName,
      title: s.title ?? null,
      last_preview: s.lastPreview ?? null,
      message_count: s.messageCount,
    }),
  });
}

/** Partially update session metadata in Postgres. */
async function _patchSessionInDb(
  id: string,
  patch: { title?: string; lastPreview?: string; messageCount?: number },
): Promise<void> {
  await fetch(`/api/chat/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: patch.title ?? null,
      last_preview: patch.lastPreview ?? null,
      message_count: patch.messageCount ?? null,
    }),
  });
}

/**
 * Fetch sessions from Postgres and merge into localStorage.
 * Called once on app mount by chat/page.tsx to restore sessions on a fresh browser.
 * localStorage wins for ordering (user-local reordering), Postgres wins for content.
 */
export async function fetchAndMergeSessionsFromDb(): Promise<ChatSession[]> {
  // The key is taken BEFORE the await. If the member changes while the request
  // is out, the answer lands in the list of the member who asked, never in the
  // list of the member who is bound when it returns.
  const key = chatKey("sessions");
  if (!key) return [];
  try {
    const res = await fetch("/api/chat/sessions", { signal: AbortSignal.timeout(5_000) });
    if (!res.ok) return readList(key);
    const remote = (await res.json()) as Array<{
      id: string;
      agentName: string;
      title?: string;
      lastPreview?: string;
      messageCount: number;
      createdAt: string;
      updatedAt: string;
      visibility?: ChatSession["visibility"];
      isOwner?: boolean;
      participantCount?: number;
    }>;

    const local = readList(key);
    const localIds = new Set(local.map((s) => s.id));
    // Membership is the server's answer, so it is refreshed on every merge
    // rather than only for sessions the browser has never seen. A room you
    // were added to this morning must gain its badge without you clearing
    // localStorage.
    const byId = new Map(remote.map((r) => [r.id, r]));
    for (const s of local) {
      const r = byId.get(s.id);
      if (!r) continue;
      s.visibility = r.visibility;
      s.isOwner = r.isOwner;
      s.participantCount = r.participantCount;
    }

    // Add any sessions from Postgres that aren't in localStorage.
    for (const r of remote) {
      if (!localIds.has(r.id)) {
        // Fall back to a formatted date (matching createSession) — NEVER the
        // raw UUID — so restored-from-DB sessions with no title don't show an
        // unreadable id in the sidebar.
        const fallbackName = (() => {
          const d = new Date(r.createdAt);
          return isNaN(d.getTime())
            ? "Conversation"
            : d.toLocaleString("en-IN", {
                day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
              });
        })();
        local.push({
          id: r.id,
          name: r.title ?? fallbackName,
          agentName: r.agentName,
          createdAt: r.createdAt,
          updatedAt: r.updatedAt,
          messageCount: r.messageCount,
          title: r.title,
          lastPreview: r.lastPreview,
          visibility: r.visibility,
          isOwner: r.isOwner,
          participantCount: r.participantCount,
        });
      }
    }

    // Sort by updatedAt descending.
    local.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    writeList(local, key);
    return local;
  } catch (_e) {
    return readList(key);
  }
}

// ---------------------------------------------------------------------------
// Message persistence — localStorage write-through cache + Postgres primary store
//
// localStorage:  instant reads on mount (no loading flash)
// Postgres:      durable store, survives browser cache clears / new devices
//
// Ephemeral fields (streaming, isThinkingActive) are never saved.
// ---------------------------------------------------------------------------

/** Minimal persisted shape — no streaming/ephemeral fields. */
export interface PersistedMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  /** True if the assistant was still streaming when saved (recovery flag). */
  streaming?: boolean;
  toolEvents?: unknown[];
  progressLines?: string[];
  reasoningBlocks?: string[];
  agentState?: Record<string, unknown>;
  customEvents?: { name: string; value: unknown }[];
  /** Agent's structured todo list (VS Code Todos panel parity). */
  todos?: { id: string; title: string; status: string }[];
  /** Real assistant-message segments (Phase 3b) — restored from
   *  agent_state.segments so segment-native rendering survives a reload. */
  segments?: { id: string; text: string }[];
  /** Authorship (see ChatMessage in chatStore.ts). `authorKind` is who to show
   *  as the speaker; `role` only says which side of the conversation it is. */
  authorEmail?: string;
  authorKind?: "human" | "agent" | "system";
  /** Server-side redaction — content is a notice, not the turn's real text. */
  redacted?: boolean;
  redactedCaps?: string[];
}

/** Maximum messages kept per session to avoid storage bloat. */
const MAX_MESSAGES_PER_SESSION = 200;

// ── Send-queue persistence ────────────────────────────────────────────────
// Queued/steered messages live in an in-memory ref while the component is
// mounted, but that ref is lost on a page refresh or when the user switches
// to another agent's session (the queue belongs to a specific session).
// Persist it per-session, in the member's namespace, so a queued message
// survives both and no other account ever reads it.

function strings(xs: unknown): string[] {
  return Array.isArray(xs) ? xs.filter((x): x is string => typeof x === "string") : [];
}

/** Read the persisted send-queue of a session, in the bound namespace. */
export function getQueue(sessionId: string): string[] {
  const key = chatKey("queue", sessionId);
  if (typeof window === "undefined" || !key) return [];
  try {
    const raw = localStorage.getItem(key);
    return raw ? strings(JSON.parse(raw)) : [];
  } catch (_e) {
    return [];
  }
}

/** Persist (or clear, when empty) the send-queue of a session. */
export function saveQueue(sessionId: string, queue: string[]): void {
  const key = chatKey("queue", sessionId);
  if (typeof window === "undefined" || !key) return;
  try {
    if (queue.length === 0) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(queue));
  } catch (_e) {
    // Storage quota exceeded — best-effort only.
  }
}

/** Read from the localStorage cache (synchronous, instant), in the bound namespace. */
export function getMessages(sessionId: string): PersistedMessage[] {
  const key = chatKey("msgs", sessionId);
  if (typeof window === "undefined" || !key) return [];
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as PersistedMessage[]) : [];
  } catch (_e) {
    return [];
  }
}

/**
 * Write a session's transcript to the local cache, in the bound namespace.
 * No server write. For a caller that must save synchronously (page unload).
 */
export function cacheMessages(sessionId: string, messages: unknown[]): void {
  const key = chatKey("msgs", sessionId);
  if (typeof window === "undefined" || !key) return;
  try {
    localStorage.setItem(key, JSON.stringify(messages));
  } catch (_e) {
    /* quota exceeded */
  }
}

/**
 * Save messages to localStorage immediately, then POST to Postgres in background.
 * Streaming messages (even with empty content) are preserved so the recovery
 * effect can detect an interrupted stream on page reload.  __ERROR__ system
 * messages are always skipped.
 */
export function saveMessages(sessionId: string, messages: PersistedMessage[]): void {
  if (typeof window === "undefined") return;
  const settled = messages
    .filter((m) =>
      m.role === "user" ||
      m.content.trim().length > 0 ||
      // Preserve streaming assistant messages even if empty — the recovery
      // effect needs them to detect an interrupted stream on reload.
      (m.role === "assistant" && m.streaming)
    )
    .filter((m) => !(m.role === "system" && m.content.startsWith("__ERROR__")))
    .slice(-MAX_MESSAGES_PER_SESSION);

  // 1. Write-through cache (sync, instant), in the bound namespace.
  cacheMessages(sessionId, settled);

  // 2. Persist to Postgres (async, background)
  if (settled.length === 0) return;
  // The server-side stream route (translateAndPersistStream in
  // app/api/agent/chat/route.ts) is the AUTHORITATIVE writer for an assistant
  // message while it streams — it accumulates the full SSE stream and re-upserts
  // the SAME row (by id) every ~3s and at completion.  If the client also POSTs
  // its partial / possibly-stale snapshot of a still-streaming assistant row,
  // the two last-writer-wins upserts race and a late client write can TRUNCATE
  // the server's final content.  Exclude still-streaming assistant rows from the
  // DB write; the client persists them once settled (streaming=false), which is
  // idempotent with the server's final.  (localStorage above still keeps the
  // streaming rows for refresh-recovery detection.)
  // NOTE: litellm mode has NO server-side persister, so a litellm reply is
  // durable only in localStorage.  Since WS-27bm S14 (projects_ai_chat.md §20,
  // D-PM-39) this path no longer writes a litellm reply to the server either:
  // only the server creates an agent row, so the gateway declines the insert
  // and names the id in `unchanged`.  A compaction summary (a system row) is
  // declined the same way.  Both stay browser-only until a server writer
  // exists.  Don't "fix" that by re-adding streaming-row writes — it
  // reintroduces the truncation race on the copilot/executor path (which IS
  // persisted server-side during the stream).
  const forDb = settled.filter((m) => !(m.role === "assistant" && m.streaming));
  if (forDb.length === 0) return;
  const payload = forDb.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    timestamp: m.timestamp,
    tool_events: m.toolEvents ?? [],
    progress_lines: m.progressLines ?? [],
    // Stored as JSON (serializeReasoning) so a block containing a "---" line
    // can't be torn apart on restore — see chatStream.parseReasoning.
    reasoning: serializeReasoning(m.reasoningBlocks),
    // Persist the structured todo list + real message segments (Phase 3b)
    // inside agent_state so the Todos panel and segment-native rendering
    // survive a refresh (no dedicated DB columns needed).
    agent_state: ((): Record<string, unknown> | null => {
      const st: Record<string, unknown> = { ...(m.agentState ?? {}) };
      if (m.todos && m.todos.length > 0) st.todos = m.todos;
      if (m.segments && m.segments.length > 0) st.segments = m.segments;
      return Object.keys(st).length > 0 ? st : null;
    })(),
    custom_events: m.customEvents ?? [],
    // Authorship is asserted ONLY for agent turns.  The server overrides the
    // author of a human turn with the authenticated caller, so a client that
    // sent `author_kind: "human"` would either be ignored or be trying to
    // speak as someone else — never send it.  An agent turn is different: a
    // browser persisting a run it merely watched is the only party that knows
    // WHICH agent spoke, so that attribution has to travel with the row.
    ...(m.authorKind === "agent"
      ? { author_kind: "agent", author_email: m.authorEmail ?? null }
      : {}),
  }));
  void queueSave(sessionId, JSON.stringify(payload));
}

/**
 * The waits before each retry of a failed save: three retries, each wait
 * twice the last (WS-27bm S15, and the storm of 2026-10-09).
 */
export const SAVE_RETRY_DELAYS_MS: readonly number[] = [1_000, 2_000, 4_000];

/**
 * How long a session sends no save after its retries all failed. The save
 * that waits is sent once when the pause ends.
 */
export const SAVE_PAUSE_MS = 60_000;

/**
 * POST one batch of messages, and retry a 5xx a bounded number of times
 * (WS-27bm S15 fix round 1, projects_ai_chat.md §21.12).
 *
 * The session upsert and the first save leave the browser at the same time. If
 * the save reaches the gateway first, the chat_message foreign key refuses it
 * and the answer is a 5xx. A retry lets the session row land first. A 4xx is an
 * answer, so it is not retried. Each wait is in `delaysMs`, so the retries are
 * bounded and the waits grow. The call never throws.
 */
export async function postMessagesWithRetry(
  sessionId: string,
  body: string,
  delaysMs: readonly number[] = SAVE_RETRY_DELAYS_MS,
): Promise<number | null> {
  const send = () =>
    fetch(`/api/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
  let last: number | null = null;
  for (let attempt = 0; attempt <= delaysMs.length; attempt += 1) {
    if (attempt > 0) {
      await new Promise((resolve) => setTimeout(resolve, delaysMs[attempt - 1]));
    }
    try {
      last = (await send()).status;
      if (last < 500) return last;
    } catch {
      // A network failure is retried the same way as a 5xx.
      last = null;
    }
  }
  return last;
}

/** One session's save line: one request at a time, and the latest body next. */
interface SaveLine {
  /** The body the server last stored. The same body is not sent again. */
  saved?: string;
  /**
   * The body the server last refused with a 4xx, for example a 403 to a room
   * member who may not send. The same body is not sent again either.
   */
  refused?: string;
  /** True while a save, or its retries, is on the way. */
  busy: boolean;
  /** The newest body that came while busy or paused. Older ones are dropped. */
  next?: string;
  /** No save leaves before this time (epoch ms). */
  pausedUntil: number;
}

const _saveLines = new Map<string, SaveLine>();

/** Forget every save line. For tests. */
export function resetSaveLines(): void {
  _saveLines.clear();
}

/**
 * Send *body* for *sessionId* through its save line (the storm of 2026-10-09).
 *
 * `AgentChat` calls `saveMessages` on EVERY change of its message list, and a
 * streaming turn changes it about twelve times a second. When each save
 * answered 500, the browser sent about 700 POSTs in one minute. The line
 * stops that:
 *
 * - a body equal to the one the server stored, or to the one it refused
 *   with a 4xx, is not sent;
 * - one request is on the way at a time, and a body that comes meanwhile
 *   replaces any body that waits, so a burst sends at most one more;
 * - when every retry fails, the line pauses for `SAVE_PAUSE_MS`, and then
 *   sends only the newest body. The local cache keeps every turn meanwhile.
 *
 * Fence: `src/lib/sessions.test.ts` ("the save line").
 */
export async function queueSave(
  sessionId: string,
  body: string,
  delaysMs: readonly number[] = SAVE_RETRY_DELAYS_MS,
  pauseMs: number = SAVE_PAUSE_MS,
): Promise<void> {
  let line = _saveLines.get(sessionId);
  if (!line) {
    line = { busy: false, pausedUntil: 0 };
    _saveLines.set(sessionId, line);
  }
  if (body === line.saved || body === line.refused) return;
  if (line.busy) {
    line.next = body;
    return;
  }
  line.busy = true;
  let current: string | undefined = body;
  try {
    while (current !== undefined) {
      const wait = line.pausedUntil - Date.now();
      if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
      // A newer body that came during the pause replaces this one.
      if (line.next !== undefined) {
        current = line.next;
        line.next = undefined;
      }
      if (current === line.saved || current === line.refused) break;
      const status = await postMessagesWithRetry(sessionId, current, delaysMs);
      if (status !== null && status < 300) {
        line.saved = current;
      } else if (status === null || status >= 500) {
        line.pausedUntil = Date.now() + pauseMs;
      } else {
        // A 4xx is an answer: do not pause, and do not resend this body.
        line.refused = current;
      }
      current = line.next;
      line.next = undefined;
      if (current === line.saved || current === line.refused) current = undefined;
    }
  } finally {
    line.busy = false;
  }
}

/**
 * Load messages from Postgres and update the localStorage cache.
 * Returns the fetched list, or falls back to the localStorage cache on error.
 *
 * Pass `opts` to lazy-load a window of history instead of the full session:
 *   - `limit`:  return only the most recent N messages (windowed restore).
 *   - `before`: a `timestamp` cursor — only messages older than it (used to
 *               page backwards on scroll-up).
 *
 * When paginating (limit or before set), the localStorage cache is left
 * untouched — the caller merges the window into the visible list and the
 * component's own save effect persists it.  Only an unpaginated full fetch
 * rewrites the cache authoritatively.
 */
export async function fetchMessagesFromDb(
  sessionId: string,
  opts?: { limit?: number; before?: number },
): Promise<PersistedMessage[]> {
  const paginated = !!(opts?.limit || opts?.before);
  try {
    const qs = new URLSearchParams();
    if (opts?.limit) qs.set("limit", String(opts.limit));
    if (opts?.before) qs.set("before", String(opts.before));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    const res = await fetch(`/api/chat/sessions/${sessionId}/messages${suffix}`, {
      signal: AbortSignal.timeout(8_000),
    });
    if (!res.ok) return paginated ? [] : getMessages(sessionId);
    const remote = (await res.json()) as Array<{
      id: string;
      role: "user" | "assistant" | "system";
      content: string;
      timestamp: number;
      toolEvents?: unknown[];
      progressLines?: string[];
      reasoning?: string;
      agentState?: Record<string, unknown>;
      customEvents?: { name: string; value: unknown }[];
      todos?: { id: string; title: string; status: string }[];
      authorEmail?: string | null;
      authorKind?: "human" | "agent" | "system" | null;
      redacted?: boolean;
      redactedCaps?: string[];
    }>;
    // Map Postgres `reasoning` field to localStorage `reasoningBlocks`.
    // Split on the block separator WITHOUT dropping empty segments so block
    // indices stay aligned with each tool's reasoningCutoff.
    const mapped = remote.map((r) => ({
      ...r,
      reasoningBlocks: parseReasoning(r.reasoning),
      reasoning: undefined,
      // Restore the todo list from agent_state (where it was persisted).
      todos: r.todos
        ?? (r.agentState?.todos as
          | { id: string; title: string; status: string }[]
          | undefined),
      // Restore real message segments (Phase 3b) so segment-native rendering
      // survives a full reload — the renderer prefers these over the folded
      // content when present.
      segments: r.agentState?.segments as
        | { id: string; text: string }[]
        | undefined,
      // Authorship.  `null` is how the server spells "pre-authorship row";
      // normalise it to undefined so the renderer's "no author known" branch
      // (which is the old, unattributed look) is a single check.
      authorEmail: r.authorEmail ?? undefined,
      authorKind: r.authorKind ?? undefined,
      redacted: r.redacted || undefined,
      redactedCaps: r.redactedCaps,
    }));
    // Update localStorage cache with authoritative Postgres data — only on a
    // full (unpaginated) fetch.  A windowed/paginated fetch must not shrink or
    // clobber the cache.
    //
    // An EMPTY server answer never replaces a non-empty cache (WS-27bm S15,
    // projects_ai_chat.md §21). On production the server held no row, so every
    // full fetch answered [] and this line erased the member's local history.
    // An empty answer can also mean a save that has not landed yet. The cache
    // is the only copy then, so it stays, and the caller gets it back.
    if (!paginated && mapped.length === 0) {
      const cached = getMessages(sessionId);
      if (cached.length > 0) return cached;
    }
    if (!paginated) cacheMessages(sessionId, mapped);
    return mapped as unknown as PersistedMessage[];
  } catch (_e) {
    return paginated ? [] : getMessages(sessionId);
  }
}

export function deleteMessages(sessionId: string): void {
  const key = chatKey("msgs", sessionId);
  if (typeof window === "undefined" || !key) return;
  try { localStorage.removeItem(key); } catch { /* storage off */ }
  // Postgres messages are CASCADE-deleted when the session is deleted via the API.
}
