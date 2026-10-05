/**
 * A chat id never follows the browser to another member (production bug,
 * 2026-10-05).
 *
 * A DEWiN member signed in on a browser that had held the owner's Fracktal
 * session. The Projects rail restored the owner's Projects chat, and every
 * send failed with "Agent run failed: You are not a participant of this
 * conversation." The gateway was right to refuse. The client was wrong to ask.
 *
 * Three rules, each fenced here:
 *   1. A stored list of member A or org X is never read for member B or org Y.
 *   2. A RESTORED id that the server refuses gives way to a new chat, with no
 *      error card, and the member's words go back to the composer.
 *   3. A chat the member opened on purpose keeps its error.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getSessionState, setSessionState } from "./chatStore";
import { settleFailedTurn } from "./chatTurnFailure";
import {
  NO_PICK,
  RECOVERED_NOTICE,
  carriedText,
  openDeliberately,
  recoverRefused,
  recoveredNotice,
  refusalHandler,
  restoreOrStart,
  restoreOrStartAfterMerge,
  type RailPick,
} from "./railSessions";
import {
  BUILDER_SESSION_KEY_PREFIX,
  CHAT_OWNER_KEY,
  LEGACY_SESSIONS_KEY,
  bindChatScope,
  chatScope,
  createSession,
  forgetChatSessions,
  forgetSession,
  getQueue,
  getSessions,
  isSessionRefusal,
  probeSession,
  saveQueue,
  scopeFromAccess,
  tidyChatStorage,
  upsertSession,
  type ChatSession,
} from "./sessions";

class MemoryStorage {
  private map = new Map<string, string>();
  get length(): number {
    return this.map.size;
  }
  key(i: number): string | null {
    return [...this.map.keys()][i] ?? null;
  }
  getItem(k: string): string | null {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.map.set(k, String(v));
  }
  removeItem(k: string): void {
    this.map.delete(k);
  }
  keys(): string[] {
    return [...this.map.keys()];
  }
}

const AGENT = "projects-assistant";
const OWNER = chatScope("vjvarada@fracktal.in", "org-fracktal") as string;
const MEMBER = chatScope("sharat@dewin.in", "org-dewin") as string;
/** The same person, in a second organization. */
const OWNER_ELSEWHERE = chatScope("vjvarada@fracktal.in", "org-dewin") as string;
/** A second member of the owner's org: the same org id, another email. */
const COLLEAGUE = chatScope("ops@fracktal.in", "org-fracktal") as string;

const REFUSED_BODY = JSON.stringify({ detail: "You are not a participant of this conversation." });

let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  // The store syncs every write to the server. Nothing here asserts on it.
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
});

afterEach(() => {
  forgetChatSessions();
  vi.unstubAllGlobals();
});

/** The owner's Projects chat, written the way the rail writes it. */
function ownerChat(): string {
  bindChatScope(OWNER);
  const s = createSession(AGENT);
  upsertSession(s);
  return s.id;
}

describe("rule 1: a stored chat id is the member's and the org's", () => {
  it("restores the owner's own chat for the owner", () => {
    // The control: without it, every case below could pass for nothing.
    const id = ownerChat();
    bindChatScope(OWNER);
    expect(restoreOrStart(AGENT)).toEqual({ activeId: id, restoredId: id });
  });

  it("does NOT restore member A's chat for member B in another org", () => {
    const ownersId = ownerChat();
    bindChatScope(MEMBER);
    expect(getSessions()).toEqual([]);
    const pick = restoreOrStart(AGENT);
    expect(pick.activeId).not.toBe(ownersId);
    // A fresh chat is opened on purpose, never restored.
    expect(pick.restoredId).toBeNull();
  });

  it("does NOT restore a chat of org X for the same member in org Y", () => {
    const ownersId = ownerChat();
    bindChatScope(OWNER_ELSEWHERE);
    expect(getSessions()).toEqual([]);
    expect(restoreOrStart(AGENT).activeId).not.toBe(ownersId);
  });

  it("does NOT restore member A's chat for member B in the SAME org", () => {
    // Fix round 1: a key of the org alone passes both cases above.
    const ownersId = ownerChat();
    bindChatScope(COLLEAGUE);
    expect(getSessions()).toEqual([]);
    expect(restoreOrStart(AGENT).activeId).not.toBe(ownersId);
  });

  it("never reads the old browser-wide list, and the tidy removes it", () => {
    const leaked = createSession(AGENT);
    storage.setItem(LEGACY_SESSIONS_KEY, JSON.stringify([leaked]));
    bindChatScope(MEMBER);
    expect(getSessions()).toEqual([]);
    tidyChatStorage(MEMBER);
    expect(storage.getItem(LEGACY_SESSIONS_KEY)).toBeNull();
  });

  it("reads and writes nothing while no member is bound", () => {
    ownerChat();
    bindChatScope(null);
    expect(getSessions()).toEqual([]);
    const before = storage.keys().length;
    upsertSession(createSession(AGENT));
    expect(storage.keys().length).toBe(before);
  });

  it("folds the email's case, because the server folds it too", () => {
    expect(chatScope("Sharat@DEWIN.in", "org-dewin")).toBe(MEMBER);
    expect(chatScope("", "org-dewin")).toBeNull();
  });

  it("forgets every stored chat id on sign-out, and keeps the transcripts", () => {
    const id = ownerChat();
    storage.setItem(`${BUILDER_SESSION_KEY_PREFIX}${OWNER}::crm`, id);
    storage.setItem(`${BUILDER_SESSION_KEY_PREFIX}crm`, id);
    storage.setItem(`cc-msgs-${id}`, "[]");
    forgetChatSessions();
    expect(storage.keys()).toEqual([`cc-msgs-${id}`]);
    bindChatScope(OWNER);
    expect(getSessions()).toEqual([]);
  });
});

describe("the scope of one access answer (fix round 1)", () => {
  const answer = (over: Partial<Parameters<typeof scopeFromAccess>[0]>) =>
    scopeFromAccess({ loading: false, stale: false, email: "", organizationId: null, ...over });

  it("is null while access loads", () => {
    expect(answer({ loading: true, email: "sharat@dewin.in", organizationId: "org-dewin" })).toBeNull();
  });

  it("is null when the first resolve failed and named nobody", () => {
    // A gateway restart in a deploy: loading ends, the answer is stale, and
    // there is no email. A shared `anonymous` list here was a P2 finding.
    expect(answer({ stale: true })).toBeNull();
  });

  it("is the no-email scope only for an authoritative answer with no email", () => {
    expect(answer({})).toBe("anonymous|");
  });

  it("keeps the last org id while the org lookup fails for the same member", () => {
    expect(answer({ email: "sharat@dewin.in", organizationId: "org-dewin" })).toBe(MEMBER);
    expect(answer({ email: "sharat@dewin.in", organizationId: undefined })).toBe(MEMBER);
  });

  it("never lends one member's org id to another member", () => {
    expect(answer({ email: "sharat@dewin.in", organizationId: "org-dewin" })).toBe(MEMBER);
    expect(answer({ email: "ops@fracktal.in", organizationId: undefined })).toBe("ops@fracktal.in|");
  });
});

describe("the tidy, bound to the identity (fix round 1)", () => {
  it("drops other members' ids when the member of the browser changes", () => {
    const ownersId = ownerChat();
    tidyChatStorage(OWNER);
    storage.setItem(`${BUILDER_SESSION_KEY_PREFIX}${OWNER}::crm`, ownersId);
    bindChatScope(MEMBER);
    upsertSession(createSession(AGENT));
    tidyChatStorage(MEMBER);
    const left = storage.keys();
    expect(left.some((k) => k.includes("vjvarada@fracktal.in"))).toBe(false);
    expect(left).toContain(`cc-chat-sessions::${MEMBER}`);
    expect(storage.getItem(CHAT_OWNER_KEY)).toBe("sharat@dewin.in");
  });

  it("keeps the lists while the same member stays, in either org", () => {
    ownerChat();
    tidyChatStorage(OWNER);
    bindChatScope(OWNER_ELSEWHERE);
    upsertSession(createSession(AGENT));
    tidyChatStorage(OWNER_ELSEWHERE);
    expect(storage.keys()).toContain(`cc-chat-sessions::${OWNER}`);
    expect(storage.keys()).toContain(`cc-chat-sessions::${OWNER_ELSEWHERE}`);
  });

  it("drops the member's ids when the browser signs out by any path", () => {
    // An expired session or the middleware redirect ends at /signin, where
    // the access answer names nobody and the scope is `anonymous|`.
    ownerChat();
    tidyChatStorage(OWNER);
    tidyChatStorage(scopeFromAccess({ loading: false, stale: false, email: "", organizationId: null }));
    expect(storage.keys().some((k) => k.startsWith("cc-chat-sessions::vjvarada"))).toBe(false);
  });
});

describe("a first visit waits for the server's list (fix round 1)", () => {
  const remote = (id: string): ChatSession => ({
    id, name: "x", agentName: AGENT, createdAt: "2026-10-01", updatedAt: "2026-10-01", messageCount: 3,
  });

  it("restores the server's chat instead of starting an empty one", async () => {
    bindChatScope(MEMBER);
    const merge = vi.fn(async () => { upsertSession(remote("server-chat")); return getSessions(); });
    const pick = await restoreOrStartAfterMerge(AGENT, merge, 1_000);
    expect(merge).toHaveBeenCalledTimes(1);
    expect(pick).toEqual({ activeId: "server-chat", restoredId: "server-chat" });
    expect(getSessions().map((s) => s.id)).toEqual(["server-chat"]);
  });

  it("does not wait when this browser already holds a chat", async () => {
    const id = ownerChat();
    const merge = vi.fn(async () => getSessions());
    expect(await restoreOrStartAfterMerge(AGENT, merge, 1_000)).toEqual({ activeId: id, restoredId: id });
    expect(merge).not.toHaveBeenCalled();
  });

  it("starts a chat after the wait when the server is slow", async () => {
    bindChatScope(MEMBER);
    const merge = () => new Promise<ChatSession[]>(() => {});
    const pick = await restoreOrStartAfterMerge(AGENT, merge, 20);
    expect(pick.restoredId).toBeNull();
    expect(getSessions()).toHaveLength(1);
  });
});

describe("unsent work survives a refused chat (fix round 1)", () => {
  it("keeps the queue of a forgotten chat, and carries it to the composer", () => {
    bindChatScope(MEMBER);
    const s = createSession(AGENT);
    upsertSession(s);
    saveQueue(s.id, ["and the second thing"]);
    forgetSession(s.id);
    expect(getQueue(s.id)).toEqual(["and the second thing"]);
    expect(carriedText(s.id, "What is stuck here?")).toBe("What is stuck here?\n\nand the second thing");
    expect(carriedText(s.id)).toBe("and the second thing");
    expect(carriedText("nothing-here")).toBeUndefined();
  });

  it("explains the new chat on load, and names the box only when it holds words", () => {
    expect(recoveredNotice(undefined)).toBe(RECOVERED_NOTICE);
    expect(recoveredNotice("hi")).toMatch(/Your message is in the box/);
    expect(RECOVERED_NOTICE).not.toMatch(/Your message/);
  });
});

describe("rule 2: a restored id the server refuses starts a fresh chat", () => {
  /** One turn in flight on `threadId`, the way useAgentChat appends it. */
  function turnInFlight(threadId: string): { userMsgId: string; assistantId: string } {
    setSessionState(threadId, (prev) => ({
      ...prev,
      messages: [
        { id: "u1", role: "user", content: "What is stuck here?", timestamp: 1 },
        { id: "a1", role: "assistant", content: "", timestamp: 2, streaming: true },
      ],
    }));
    return { userMsgId: "u1", assistantId: "a1" };
  }

  /** The rail's recover step, against a pick it holds. */
  function railRecover(holder: { pick: RailPick; texts: string[] }) {
    return (refusedId: string, text?: string): boolean => {
      const next = recoverRefused(holder.pick, refusedId, AGENT);
      if (!next) return false;
      holder.pick = next;
      if (text) holder.texts.push(text);
      return true;
    };
  }

  it("drops the turn, draws NO error card, and opens a new chat", () => {
    // The production case: the browser restored the owner's chat id for the
    // member (written before the list was keyed, so planted here directly).
    const ownersId = ownerChat();
    bindChatScope(MEMBER);
    upsertSession({ ...createSession(AGENT), id: ownersId });
    const holder = { pick: restoreOrStart(AGENT), texts: [] as string[] };
    expect(holder.pick).toEqual({ activeId: ownersId, restoredId: ownersId });

    const ids = turnInFlight(ownersId);
    const outcome = settleFailedTurn({
      threadId: ownersId,
      ...ids,
      content: "What is stuck here?",
      rawErr: REFUSED_BODY,
      status: 403,
      onSessionRefused: refusalHandler(holder.pick, railRecover(holder)),
    });

    expect(outcome).toBe("recovered");
    const state = getSessionState(ownersId);
    expect(state.error).toBeNull();
    expect(state.messages.some((m) => m.content.startsWith("__ERROR__"))).toBe(false);
    expect(state.messages).toEqual([]);
    // A new chat, opened on purpose, and the refused id is gone from the list.
    expect(holder.pick.activeId).not.toBe(ownersId);
    expect(holder.pick.restoredId).toBeNull();
    expect(getSessions().map((s) => s.id)).not.toContain(ownersId);
    // The member's words go back to the composer of the new chat.
    expect(holder.texts).toEqual(["What is stuck here?"]);
  });

  it("recovers from a 404 the same way", () => {
    bindChatScope(MEMBER);
    const stale = createSession(AGENT);
    upsertSession(stale);
    const holder = { pick: restoreOrStart(AGENT), texts: [] as string[] };
    const ids = turnInFlight(stale.id);
    expect(
      settleFailedTurn({
        threadId: stale.id, ...ids, content: "hi", rawErr: "{}", status: 404,
        onSessionRefused: refusalHandler(holder.pick, railRecover(holder)),
      }),
    ).toBe("recovered");
    expect(holder.pick.activeId).not.toBe(stale.id);
  });

  it("recovers from the refusal as an error frame inside the stream", () => {
    bindChatScope(MEMBER);
    const stale = createSession(AGENT);
    upsertSession(stale);
    const holder = { pick: restoreOrStart(AGENT), texts: [] as string[] };
    const ids = turnInFlight(stale.id);
    expect(
      settleFailedTurn({
        threadId: stale.id, ...ids, content: "hi",
        rawErr: "You are not a participant of this conversation.", status: null,
        onSessionRefused: refusalHandler(holder.pick, railRecover(holder)),
      }),
    ).toBe("recovered");
  });

  it("asks GET /room on load: 404 and the 403 rule are refusals, nothing else is", async () => {
    const answer = (status: number, body = "{}") =>
      vi.fn(async () => new Response(body, { status })) as unknown as typeof fetch;
    expect(await probeSession("s", answer(404))).toBe("refused");
    expect(await probeSession("s", answer(403, REFUSED_BODY))).toBe("refused");
    expect(await probeSession("s", answer(200))).toBe("ok");
    expect(await probeSession("s", answer(503))).toBe("unknown");
    expect(await probeSession("s", answer(403, '{"detail":"cannot send"}'))).toBe("unknown");
    const down = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    expect(await probeSession("s", down as unknown as typeof fetch)).toBe("unknown");
  });
});

describe("rule 3: a chat opened on purpose keeps its error", () => {
  function settle(pick: RailPick, threadId: string, rawErr: string, status: number) {
    setSessionState(threadId, (prev) => ({
      ...prev,
      messages: [
        { id: "u1", role: "user", content: "hello", timestamp: 1 },
        { id: "a1", role: "assistant", content: "", timestamp: 2, streaming: true },
      ],
    }));
    const recover = (id: string) => recoverRefused(pick, id, AGENT) !== null;
    return settleFailedTurn({
      threadId, userMsgId: "u1", assistantId: "a1", content: "hello", rawErr, status,
      onSessionRefused: refusalHandler(pick, recover),
    });
  }

  it("draws the error card for a refused chat the member picked from history", () => {
    bindChatScope(MEMBER);
    const picked = createSession(AGENT);
    upsertSession(picked);
    const pick = openDeliberately(picked.id);
    // The surface gives AgentChat no handler for it at all.
    expect(refusalHandler(pick, () => true)).toBeUndefined();
    expect(settle(pick, picked.id, REFUSED_BODY, 403)).toBe("error");
    const state = getSessionState(picked.id);
    expect(state.messages.some((m) => m.content.startsWith("__ERROR__"))).toBe(true);
    // And the chat is still in the list: nothing forgot it.
    expect(getSessions().map((s) => s.id)).toContain(picked.id);
  });

  it("draws the error card for a viewer's 403, even on a restored chat", () => {
    // "You are a viewer in this room, which cannot send" is a real answer
    // about a real room. It is not a refused session.
    bindChatScope(MEMBER);
    const s = createSession(AGENT);
    upsertSession(s);
    const pick = restoreOrStart(AGENT);
    const viewer = JSON.stringify({ detail: "You are a viewer in this room, which cannot send messages." });
    expect(isSessionRefusal(403, viewer)).toBe(false);
    expect(settle(pick, s.id, viewer, 403)).toBe("error");
  });

  it("draws the error card for a turn that failed for another reason", () => {
    bindChatScope(MEMBER);
    const s = createSession(AGENT);
    upsertSession(s);
    expect(settle(restoreOrStart(AGENT), s.id, "Gateway error: boom", 500)).toBe("error");
  });

  it("recoverRefused itself refuses a chat opened on purpose, and forgets nothing", () => {
    // The load probe calls recoverRefused with no handler in between, so the
    // rule must hold here too, not only in refusalHandler.
    bindChatScope(MEMBER);
    const picked = createSession(AGENT);
    upsertSession(picked);
    expect(recoverRefused(openDeliberately(picked.id), picked.id, AGENT)).toBeNull();
    expect(getSessions().map((s) => s.id)).toEqual([picked.id]);
  });

  it("does not recover a refused id that is no longer the open chat", () => {
    bindChatScope(MEMBER);
    const s = createSession(AGENT);
    upsertSession(s);
    const pick = restoreOrStart(AGENT);
    expect(recoverRefused(pick, "some-other-id", AGENT)).toBeNull();
    expect(recoverRefused(NO_PICK, "", AGENT)).toBeNull();
  });
});

describe("the wiring (source fence)", () => {
  const read = (rel: string) =>
    readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");

  it("every chat surface that restores a session hands AgentChat the refusal handler", () => {
    for (const rel of [
      "../app/projects/components/AssistantRail.tsx",
      "../app/tasks/components/AssistantRail.tsx",
      "../app/email/components/EmailAssistantChat.tsx",
      "../app/chat/page.tsx",
    ]) {
      expect(read(rel), rel).toMatch(/onSessionRefused=\{onSessionRefused\}/);
    }
  });

  it("AgentChat passes the handler on, and useAgentChat settles every failure through the seam", () => {
    expect(read("../components/AgentChat.tsx")).toMatch(/^\s+onSessionRefused,\n\s+\/\/ Load the FULL/m);
    const hook = read("../hooks/useAgentChat.ts");
    expect(hook).toContain("settleFailedTurn({");
    expect(hook).toContain("status: failedStatus,");
    expect(hook).not.toContain("__ERROR__${");
  });

  it("the scope comes from useAccess, the one identity source of the client", () => {
    const hook = read("../hooks/useChatSessions.ts");
    expect(hook).toContain('import { useAccess } from "@/components/AccessProvider";');
    expect(hook).toMatch(/bindChatScope\(scope\);/);
    // The scope rules live in scopeFromAccess, with the stale flag passed on.
    expect(hook).toMatch(/scopeFromAccess\(\{\s*loading,\s*stale,/);
  });

  it("the tidy runs in an effect, from AppShell on every page", () => {
    const hook = read("../hooks/useChatSessions.ts");
    expect(hook).toMatch(/useEffect\(\(\) => \{\s*tidyChatStorage\(scope\);\s*\}, \[scope\]\);/);
    expect(read("../components/AppShell.tsx")).toMatch(/^\s*useChatScope\(\);\r?$/m);
  });

  it("a recovery on load explains itself, as a recovery on send does", () => {
    const hook = read("../hooks/useChatSessions.ts");
    // A whole line each: the note is set on EVERY recovery, with no condition.
    expect(hook).toMatch(/^\s*setNoticeText\(recoveredNotice\(carried\)\);\r?$/m);
    expect(hook).toContain("const carried = carriedText(refusedId, pendingText);");
    expect(read("../app/chat/page.tsx")).toMatch(/^\s*setRecoveryNotice\(recoveredNotice\(carried\)\);\r?$/m);
  });

  it("only lib/sessions.ts names the session-list key", () => {
    for (const rel of [
      "../app/projects/components/AssistantRail.tsx",
      "../app/tasks/components/AssistantRail.tsx",
      "../app/email/components/EmailAssistantChat.tsx",
      "../app/chat/page.tsx",
      "../app/build/apps/[slug]/edit/page.tsx",
    ]) {
      const src = read(rel);
      expect(src, rel).not.toContain("cc-chat-sessions");
      expect(src, rel).not.toContain("cc-app-builder-session-");
    }
  });
});
