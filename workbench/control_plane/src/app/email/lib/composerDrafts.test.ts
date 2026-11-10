// The reading pane after a reply and a send (owner report, 2026-10-14).
//
// Part 1, one reply in one box. The inline reply autosaves to a provider
// draft, and the thread refetch brings that draft back. The thread drew it as
// a DraftCard above the open composer, so the AI draft showed twice. The
// pane now keeps the ids of its drafts in React state, and
// `threadWithoutComposerDrafts` drops them while the reply is open.
// Review round 1: a pop-out hands the draft to the full composer, so the
// thread hides it while that composer is open too (P1). A send that ends
// after the member opened another reply leaves that reply alone (P2).
//
// Part 2, no banner after a send. The pane syncs the mailbox after a send.
// A sync that runs already answers 409, and the store showed its detail as
// the red banner. That sync is now `quiet`, and a quiet 409 sets no error.
//
// R7 fences named here: `email-composer-owns-its-draft` (part 1) and
// `email-quiet-sync-busy` (part 2).
//
// vitest runs in the node environment, so it cannot mount EmailDetail. Each
// DECISION runs here, the thread renders through `renderToStaticMarkup`, and
// the WIRING in EmailDetail is a scan of its code with comments stripped.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConversationView, isDraftEmail } from "../components/ConversationView";
import {
  NO_COMPOSER_DRAFTS, draftsAfterSend, newReplyDrafts, ownDraft, retireDrafts,
  threadWithoutComposerDrafts,
  type ComposerDrafts, type DraftEditors,
} from "./draftAutosave";
import type { Email } from "./types";

const ROOT = join(__dirname, "..");
const read = (rel: string) =>
  readFileSync(join(ROOT, rel), { encoding: "utf-8" }).replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

const mail = (over: Partial<Email>): Email => ({
  id: "m",
  providerMessageId: "p",
  threadId: "t-1",
  accountId: "acc-1",
  from: { name: "Jibitesh", email: "jibitesh@example.test" },
  to: [{ name: "Me", email: "me@example.test" }],
  subject: "Quote",
  bodyText: "",
  bodyTruncated: false,
  snippet: "",
  hasAttachments: false,
  isRead: true,
  isStarred: false,
  isFlagged: false,
  importance: "normal",
  labels: [],
  categories: [],
  folder: "inbox",
  receivedAt: "2026-10-14T09:00:00Z",
  syncedAt: "2026-10-14T09:00:00Z",
  ...over,
});

const ORIGINAL = mail({ id: "orig", bodyText: "Please send the quote.", snippet: "Please send" });
// The draft that the inline reply saved, with the AI text in it.
const COMPOSER_DRAFT = mail({
  id: "draft-composer", folder: "drafts", subject: "Re: Quote",
  from: { name: "Me", email: "me@example.test" },
  to: [{ name: "Jibitesh", email: "jibitesh@example.test" }],
  bodyText: "COMPOSER-AI-DRAFT body text",
});
// A draft that the member saved earlier, or on another device.
const OTHER_DRAFT = mail({
  id: "draft-other", folder: "drafts", subject: "Re: Quote",
  from: { name: "Me", email: "me@example.test" },
  to: [{ name: "Jibitesh", email: "jibitesh@example.test" }],
  bodyText: "OTHER-DEVICE-DRAFT body text",
});
const THREAD = [ORIGINAL, COMPOSER_DRAFT, OTHER_DRAFT];

/** The thread as the pane draws it, rendered to markup. */
/** The inline reply is open, and the full composer is closed. */
const REPLY: DraftEditors = { replyOpen: true, composeOpen: false, composeDraftId: null };
/** Both editors are closed. */
const NONE: DraftEditors = { replyOpen: false, composeOpen: false, composeDraftId: null };

function drawn(drafts: ComposerDrafts, replyOpen: boolean | DraftEditors): string {
  const editors = typeof replyOpen === "boolean" ? (replyOpen ? REPLY : NONE) : replyOpen;
  const messages = threadWithoutComposerDrafts(THREAD, drafts, editors, isDraftEmail);
  return renderToStaticMarkup(createElement(ConversationView, { messages, openedId: "orig" }));
}

/** The record after the first autosave of the reply gave its draft. */
const saved = ownDraft(NO_COMPOSER_DRAFTS, COMPOSER_DRAFT.id);

describe("the inline reply owns its draft (email-composer-owns-its-draft)", () => {
  it("draws no DraftCard for the draft of the open reply", () => {
    const html = drawn(saved, true);
    expect(html).not.toContain("COMPOSER-AI-DRAFT");
    expect(html).toContain("Please send the quote.");
  });

  it("still draws an unrelated draft of the thread", () => {
    const html = drawn(saved, true);
    expect(html).toContain("OTHER-DEVICE-DRAFT");
    // One card for the other draft, and none for the draft of the reply.
    expect(html.match(/Sends into this conversation/g)).toHaveLength(1);
  });

  it("draws the draft again once the reply closes and keeps it", () => {
    const html = drawn(saved, false);
    expect(html).toContain("COMPOSER-AI-DRAFT");
    expect(html).toContain("OTHER-DEVICE-DRAFT");
  });

  it("a new reply gives the draft of the closed reply back to the thread", () => {
    const html = drawn(newReplyDrafts(saved), true);
    expect(html).toContain("COMPOSER-AI-DRAFT");
  });

  it("keeps a sent or a discarded draft hidden after the reply closes", () => {
    // The send deletes the row, and a refetch before that can still hold it.
    const html = drawn(retireDrafts(saved), false);
    expect(html).not.toContain("COMPOSER-AI-DRAFT");
    expect(html).toContain("OTHER-DEVICE-DRAFT");
  });

  it("a discard hides a draft that only the drain named", () => {
    const after = retireDrafts(NO_COMPOSER_DRAFTS, [COMPOSER_DRAFT.id]);
    expect(drawn(after, false)).not.toContain("COMPOSER-AI-DRAFT");
  });

  it("hides only drafts, never a message with the same id", () => {
    const hold = ownDraft(NO_COMPOSER_DRAFTS, ORIGINAL.id);
    expect(threadWithoutComposerDrafts(THREAD, hold, REPLY, isDraftEmail)).toHaveLength(3);
  });

  it("records each id once", () => {
    const twice = ownDraft(saved, COMPOSER_DRAFT.id);
    expect(twice).toBe(saved);
    expect(retireDrafts(twice, [COMPOSER_DRAFT.id]).gone).toEqual([COMPOSER_DRAFT.id]);
  });

  describe("the wiring in EmailDetail", () => {
    const detail = codeOnly(read("components/EmailDetail.tsx"));

    it("holds the drafts in state, not in a ref", () => {
      expect(detail).toContain(
        "const [composerDrafts, setComposerDrafts] = useState<ComposerDrafts>(NO_COMPOSER_DRAFTS);",
      );
    });

    it("records the draft in the same step that sets draftIdRef", () => {
      expect(detail).toMatch(
        /draftIdRef\.current = saved\.id;\s*replyHasFileRef\.current = saved\.hasAttachments;\s*lastSaveRef\.current = recordSave\([^;]*\);\s*setComposerDrafts\(\(d\) => ownDraft\(d, saved\.id\)\);/,
      );
    });

    it("draws the thread without the drafts of the open reply", () => {
      expect(detail).toContain(
        "{ replyOpen: replyMode !== null, composeOpen, composeDraftId },",
      );
      expect(detail).toContain("shownThread && shownThread.length > 1 ? (");
      expect(detail).toContain("messages={shownThread}");
      expect(detail).not.toContain("messages={thread}");
    });

    it("a send and a discard retire the drafts, and a new reply holds only the thread's draft", () => {
      expect(detail).toContain("setComposerDrafts((d) => retireDrafts(d));");
      expect(detail).toContain("setComposerDrafts((d) => retireDrafts(d, ids));");
      expect(detail).toContain("setComposerDrafts((d) => markGone(d, ids));");
      // A new reply holds no draft, or the one draft that it continues
      // (owner, 2026-10-22, `continueDraft.test.ts`).
      expect(detail).toContain(
        "setComposerDrafts((d) => (held ? ownDraft(newReplyDrafts(d), held.id) : newReplyDrafts(d)));",
      );
      expect(detail).toContain("setComposerDrafts(NO_COMPOSER_DRAFTS);");
    });
  });
});

describe("a pop-out keeps the draft out of the thread (review round 1, P1)", () => {
  // The full composer saved its own first draft: the pop-out came before the
  // first autosave, or the reply had a Bcc. The hand-over then held no id.
  const COMPOSE_OWN = mail({
    id: "draft-compose", folder: "drafts", subject: "Re: Quote",
    from: { name: "Me", email: "me@example.test" },
    bodyText: "COMPOSE-PANEL-DRAFT body text",
  });

  it("hides the draft that the full composer made when the hand-over held no id", () => {
    const thread = [ORIGINAL, COMPOSE_OWN, OTHER_DRAFT];
    const editors = { replyOpen: false, composeOpen: true, composeDraftId: COMPOSE_OWN.id };
    const shown = threadWithoutComposerDrafts(thread, NO_COMPOSER_DRAFTS, editors, isDraftEmail);
    expect(shown.map((m) => m.id)).toEqual([ORIGINAL.id, OTHER_DRAFT.id]);
  });

  it("hides the handed-over draft while the full composer is open", () => {
    // Before the hand-over settles, the composer names no draft yet.
    expect(drawn(saved, { replyOpen: false, composeOpen: true, composeDraftId: null }))
      .not.toContain("COMPOSER-AI-DRAFT");
    // After it settles, the composer names the same draft.
    const after = drawn(saved, { replyOpen: false, composeOpen: true, composeDraftId: COMPOSER_DRAFT.id });
    expect(after).not.toContain("COMPOSER-AI-DRAFT");
    expect(after).toContain("OTHER-DEVICE-DRAFT");
  });

  it("draws the draft again once the full composer closes and keeps it", () => {
    // A close clears the id in the store, and the reading pane shows the draft.
    expect(drawn(saved, NONE)).toContain("COMPOSER-AI-DRAFT");
    const thread = [ORIGINAL, COMPOSE_OWN];
    const closed = { replyOpen: false, composeOpen: false, composeDraftId: COMPOSE_OWN.id };
    expect(threadWithoutComposerDrafts(thread, NO_COMPOSER_DRAFTS, closed, isDraftEmail)).toHaveLength(2);
  });

  it("ComposePanel and the store keep the id of the composer draft", () => {
    const panel = codeOnly(read("components/ComposePanel.tsx"));
    expect(panel).toMatch(/draftIdRef\.current = draftId;\s*draftHasFileRef\.current = Boolean\(draftHasFile\);\s*setComposeDraftId\(draftId\);/);
    expect(panel).toMatch(/draftIdRef\.current = saved\.id;[\s\S]{0,200}setComposeDraftId\(saved\.id\);/);
    const store = codeOnly(read("lib/emailStore.ts"));
    expect(store).toContain("set({ composeOpen: false, composeDraftId: null, composeDefaults: null });");
    expect(store).toContain("set({ composeOpen: true, composeDraftId: null, composeDefaults: defaults || null });");
  });
});

describe("a send that ends after a new reply opened (review round 1, P2)", () => {
  const NEW_REPLY = "draft-new-reply";

  it("ends only the sent draft, and the new reply keeps its own", () => {
    // The new reply saved its draft while the send of the old reply ran.
    const during = ownDraft(newReplyDrafts(saved), NEW_REPLY);
    const after = draftsAfterSend(during, [COMPOSER_DRAFT.id], false);
    expect(after.own).toEqual([NEW_REPLY]);
    expect(after.gone).toEqual([COMPOSER_DRAFT.id]);
  });

  it("a send of the open reply ends each of its drafts", () => {
    const after = draftsAfterSend(saved, [COMPOSER_DRAFT.id], true);
    expect(after).toEqual({ own: [], gone: [COMPOSER_DRAFT.id] });
  });

  it("EmailDetail resets the reply only when the reply that sent is still open", () => {
    const detail = codeOnly(read("components/EmailDetail.tsx"));
    expect(detail).toMatch(
      /const live = replySessionRef\.current === session;\s*setComposerDrafts\(\(d\) => draftsAfterSend\(d, sentDraftId \? \[sentDraftId\] : \[\], live\)\);\s*if \(live\) resetReplySession\(\);\s*refreshThreadAfterSend\(sent\);/,
    );
    expect(detail).toMatch(/const live = replySessionRef\.current === session;\s*if \(live\) resetReplySession\(\);\s*await drained;/);
    expect(detail).toContain(
      "if (replySessionRef.current === session) setComposerDrafts((d) => ownDraft(d, saved.id));",
    );
  });
});

describe("a sync after a send shows no banner (email-quiet-sync-busy)", () => {
  const SYNC_BUSY =
    "A sync is already running for this mailbox. New mail appears when it finishes.";

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const answer = (status: number, detail: string) =>
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail }), { status })));

  it("a quiet sync answered 409 sets no error and leaves the mailbox idle", async () => {
    answer(409, SYNC_BUSY);
    const { useEmailStore } = await import("./emailStore");
    useEmailStore.setState({ syncStatus: {}, error: null } as never);
    await useEmailStore.getState().triggerSync("acc-1", { quiet: true });
    expect(useEmailStore.getState().error).toBeNull();
    expect(useEmailStore.getState().syncStatus["acc-1"]).toBe("idle");
  });

  it("a sync that the member starts still shows the 409", async () => {
    answer(409, SYNC_BUSY);
    const { useEmailStore } = await import("./emailStore");
    useEmailStore.setState({ syncStatus: {}, error: null } as never);
    await useEmailStore.getState().triggerSync("acc-1");
    expect(useEmailStore.getState().error).toBe(SYNC_BUSY);
  });

  it("a quiet sync that fails another way still shows and marks the mailbox", async () => {
    answer(500, "Sync failed: boom");
    const { useEmailStore } = await import("./emailStore");
    useEmailStore.setState({ syncStatus: {}, error: null } as never);
    await useEmailStore.getState().triggerSync("acc-1", { quiet: true });
    expect(useEmailStore.getState().error).toBe("Sync failed: boom");
    expect(useEmailStore.getState().syncStatus["acc-1"]).toBe("error");
  });

  it("each sync after a send in EmailDetail is quiet", () => {
    const detail = codeOnly(read("components/EmailDetail.tsx"));
    const calls = detail.match(/triggerSync\([^)]*\)/g) ?? [];
    // The sync of the mailbox of the mail, and of another sending mailbox.
    expect(calls).toEqual([
      "triggerSync(fromId, { quiet: true })",
      "triggerSync(acct, { quiet: true })",
    ]);
  });

  it("the Refresh of the member is not quiet", () => {
    const store = codeOnly(read("lib/emailStore.ts"));
    expect(store).toContain("pooledMailboxes(accounts).map((a) => get().triggerSync(a.id));");
    expect(store).toContain("get().triggerSync(selectedAccountId);");
  });
});
