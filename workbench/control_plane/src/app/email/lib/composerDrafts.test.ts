// The reading pane after a reply and a send (owner report, 2026-10-14).
//
// Part 1, one reply in one box. The inline reply autosaves to a provider
// draft, and the thread refetch brings that draft back. The thread drew it as
// a DraftCard above the open composer, so the AI draft showed twice. The
// pane now keeps the ids of its drafts in React state, and
// `threadWithoutComposerDrafts` drops them while the reply is open.
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
  NO_COMPOSER_DRAFTS, newReplyDrafts, ownDraft, retireDrafts, threadWithoutComposerDrafts,
  type ComposerDrafts,
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
function drawn(drafts: ComposerDrafts, replyOpen: boolean): string {
  const messages = threadWithoutComposerDrafts(THREAD, drafts, replyOpen, isDraftEmail);
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
    expect(threadWithoutComposerDrafts(THREAD, hold, true, isDraftEmail)).toHaveLength(3);
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
        "threadWithoutComposerDrafts(thread, composerDrafts, replyMode !== null, isDraftEmail)",
      );
      expect(detail).toContain("shownThread && shownThread.length > 1 ? (");
      expect(detail).toContain("messages={shownThread}");
      expect(detail).not.toContain("messages={thread}");
    });

    it("a send and a discard retire the drafts, and a new reply starts empty", () => {
      expect(detail).toContain("setComposerDrafts((d) => retireDrafts(d));");
      expect(detail).toContain("setComposerDrafts((d) => retireDrafts(d, ids));");
      expect(detail).toContain("setComposerDrafts(newReplyDrafts);");
      expect(detail).toContain("setComposerDrafts(NO_COMPOSER_DRAFTS);");
    });
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
