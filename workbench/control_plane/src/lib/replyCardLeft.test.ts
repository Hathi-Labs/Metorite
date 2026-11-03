// A reply card whose draft left Drafts (owner report, 2026-11-03, task 1526).
//
// The card writes its reply to Drafts at once. The member can send or delete
// that draft in the Email app while the card waits. A Send on the card then
// mailed each person a second time. R7: these tests fail if the card stops
// reading the state of its draft, if the Email app stops raising the event,
// if a gone card keeps its Send or Save, or if the words go back to "Save to
// Drafts keeps it in your mailbox" on a card that already wrote a draft. The
// gateway refuses the act too (`tests/unit/test_reply_card_left_drafts.py`).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { announceDraftLeft, DRAFT_LEFT_EVENT } from "@/lib/draftLeftEvent";
import {
  DRAFT_LEFT_LINE,
  DRAFT_POLL_MS,
  readDraftState,
  replyFromEvent,
  replyHint,
  saveLabel,
  watchCardDraft,
  type DraftState,
  type DraftWatchHost,
} from "@/lib/replyCard";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf-8");

const DRAFT = "00000000-0000-4000-8000-000000000001";
const VALUE = {
  message_id: "m-1",
  mailbox: "Work · jane@acme.test",
  recipients: { reply: { to: ["kim@contoso.test"], cc: [] }, reply_all: { to: ["kim@contoso.test"], cc: [] } },
  body: "Hi Kim",
  draft_id: DRAFT,
};

/** A window and a document that the test drives. */
function host(visible = true): DraftWatchHost & { doc: EventTarget & { visibilityState: string } } {
  const doc = Object.assign(new EventTarget(), { visibilityState: visible ? "visible" : "hidden" });
  return { win: new EventTarget(), doc };
}

/** Let the pending promises of a check run. */
async function flush(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

describe("the state read of a card's draft", () => {
  const answer = (status: number, body: unknown) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

  it("asks the email BFF for the one draft, and only a clear false is gone", async () => {
    const get = answer(200, { draft_id: DRAFT, in_drafts: false });
    expect(await readDraftState(DRAFT, get)).toBe("gone");
    const [url] = (get as unknown as { mock: { calls: unknown[][] } }).mock.calls[0] as [string];
    expect(url).toBe(`/api/email/reply-card/draft-state?draft_id=${DRAFT}`);
    expect(await readDraftState(DRAFT, answer(200, { in_drafts: true }))).toBe("in");
  });

  it("a fault, a refusal or an odd answer keeps the card as it is", async () => {
    expect(await readDraftState(DRAFT, answer(503, { detail: "busy" }))).toBe("unknown");
    expect(await readDraftState(DRAFT, answer(404, { detail: "Draft not found" }))).toBe("unknown");
    expect(await readDraftState(DRAFT, answer(200, { in_drafts: "no" }))).toBe("unknown");
    const offline = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    expect(await readDraftState(DRAFT, offline)).toBe("unknown");
  });

  it("a card with no draft asks nothing", async () => {
    const get = answer(200, { in_drafts: false });
    expect(await readDraftState("", get)).toBe("unknown");
    expect(get).not.toHaveBeenCalled();
  });
});

describe("the watch of a card's draft", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("reads at once when the Email app says a draft left, and stops after gone", async () => {
    const h = host();
    const states: DraftState[] = ["gone"];
    const reads = vi.fn(async () => states.shift() ?? "in");
    const onGone = vi.fn();
    watchCardDraft(DRAFT, onGone, { read: reads, host: h });
    h.win.dispatchEvent(new CustomEvent(DRAFT_LEFT_EVENT, { detail: { id: DRAFT } }));
    await flush();
    expect(reads).toHaveBeenCalledWith(DRAFT);
    expect(onGone).toHaveBeenCalledTimes(1);
    // Stopped: no event, no focus and no tick reads again.
    h.win.dispatchEvent(new CustomEvent(DRAFT_LEFT_EVENT));
    h.win.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(DRAFT_POLL_MS * 3);
    expect(reads).toHaveBeenCalledTimes(1);
    expect(onGone).toHaveBeenCalledTimes(1);
  });

  it("reads on focus, when the page shows again, and each minute while it shows", async () => {
    const h = host();
    const reads = vi.fn(async (): Promise<DraftState> => "in");
    const stop = watchCardDraft(DRAFT, vi.fn(), { read: reads, host: h });
    h.win.dispatchEvent(new Event("focus"));
    await flush();
    expect(reads).toHaveBeenCalledTimes(1);
    h.doc.dispatchEvent(new Event("visibilitychange"));
    await flush();
    expect(reads).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(DRAFT_POLL_MS);
    expect(reads).toHaveBeenCalledTimes(3);
    stop();
    h.win.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(DRAFT_POLL_MS * 2);
    expect(reads).toHaveBeenCalledTimes(3);
  });

  it("a hidden page reads nothing", async () => {
    const h = host(false);
    const reads = vi.fn(async (): Promise<DraftState> => "gone");
    const onGone = vi.fn();
    watchCardDraft(DRAFT, onGone, { read: reads, host: h });
    h.doc.dispatchEvent(new Event("visibilitychange"));
    h.win.dispatchEvent(new CustomEvent(DRAFT_LEFT_EVENT));
    await vi.advanceTimersByTimeAsync(DRAFT_POLL_MS * 2);
    expect(reads).not.toHaveBeenCalled();
    expect(onGone).not.toHaveBeenCalled();
  });

  it("an event during a read makes one more read, so a send during a read is not lost", async () => {
    const h = host();
    let release: (s: DraftState) => void = () => {};
    const reads = vi
      .fn<(id: string) => Promise<DraftState>>()
      .mockImplementationOnce(() => new Promise<DraftState>((r) => (release = r)))
      .mockImplementationOnce(async () => "gone");
    const onGone = vi.fn();
    watchCardDraft(DRAFT, onGone, { read: reads, host: h });
    h.win.dispatchEvent(new Event("focus"));
    await flush();
    h.win.dispatchEvent(new CustomEvent(DRAFT_LEFT_EVENT));
    await flush();
    expect(reads).toHaveBeenCalledTimes(1);
    release("in");
    await flush();
    expect(reads).toHaveBeenCalledTimes(2);
    expect(onGone).toHaveBeenCalledTimes(1);
  });

  it("a card with no draft watches nothing", async () => {
    const h = host();
    const add = vi.spyOn(h.win, "addEventListener");
    const reads = vi.fn(async (): Promise<DraftState> => "gone");
    watchCardDraft("", vi.fn(), { read: reads, host: h });
    await vi.advanceTimersByTimeAsync(DRAFT_POLL_MS * 2);
    expect(add).not.toHaveBeenCalled();
    expect(reads).not.toHaveBeenCalled();
  });
});

describe("the Email app raises the one event", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("announceDraftLeft dispatches DRAFT_LEFT_EVENT with the id", () => {
    const win = new EventTarget();
    vi.stubGlobal("window", win);
    const seen: unknown[] = [];
    win.addEventListener(DRAFT_LEFT_EVENT, (e) => seen.push((e as CustomEvent).detail));
    announceDraftLeft(DRAFT);
    expect(seen).toEqual([{ id: DRAFT }]);
  });

  it("a render with no window raises nothing and throws nothing", () => {
    expect(() => announceDraftLeft(DRAFT)).not.toThrow();
  });

  it("sendDraft and deleteEmail announce after the request answers", () => {
    const api = read("app/email/lib/api.ts");
    const send = api.slice(api.indexOf("export async function sendDraft("));
    const sendBody = send.slice(0, send.indexOf("\n}\n"));
    expect(sendBody).toMatch(/await gatewayFetch[\s\S]*announceDraftLeft\(draftId\);\s*return out;/);
    const del = api.slice(api.indexOf("export async function deleteEmail("));
    const delBody = del.slice(0, del.indexOf("\n}\n"));
    expect(delBody).toMatch(/await gatewayFetch[\s\S]*announceDraftLeft\(id\);/);
  });

  it("the event name is written once, in draftLeftEvent.ts", () => {
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) walk(path);
        else if (/\.(ts|tsx)$/.test(name) && !/\.test\.ts$/.test(name)) {
          if (readFileSync(path, "utf-8").includes(`"${DRAFT_LEFT_EVENT}"`)) hits.push(path);
        }
      }
    };
    walk(SRC);
    expect(hits.map((p) => p.replace(/\\/g, "/").split("/src/")[1])).toEqual(["lib/draftLeftEvent.ts"]);
  });
});

describe("the words of a card that wrote a draft", () => {
  const form = replyFromEvent(VALUE)!;
  const none = { ...form, draftId: "" };

  it("say that the reply is already in Drafts, and Save reads Save draft", () => {
    expect(replyHint(form)).toBe(
      "This reply is in the Drafts of Work · jane@acme.test. Send sends it now. " +
        "Save keeps your edits in Drafts. Discard deletes it from Drafts.",
    );
    expect(replyHint({ ...form, mailbox: "" })).toMatch(/^This reply is in your Drafts\. /);
    expect(saveLabel(form)).toBe("Save draft");
  });

  it("a card that wrote no draft keeps the words of before", () => {
    expect(replyHint(none)).toBe(
      "Send replies now from Work · jane@acme.test. Save to Drafts keeps it in your mailbox and sends nothing. Discard drops it.",
    );
    expect(saveLabel(none)).toBe("Save to Drafts");
  });
});

describe("the card draws the gone state", () => {
  const card = read("components/ReplyCardForm.tsx");

  it("watches its draft while it can still be answered", () => {
    expect(card).toMatch(/disabled \? undefined : watchCardDraft\(draftId, \(\) => setGone\(true\)\)/);
  });

  it("stops Send and Save, and shows the one line", () => {
    expect(card).toMatch(/const ok = bodyCanGo\(body\) && picked\.to\.length > 0 && !gone;/);
    expect(card).toMatch(/if \(!armed \|\| gone\) return;/);
    expect(card).toMatch(/\{gone && \([\s\S]*?\{DRAFT_LEFT_LINE\}/);
    expect(card).toContain("{saveLabel(form)}");
    // Fix round 1, item 3: a rule can replace the draft too, so no send is claimed.
    expect(DRAFT_LEFT_LINE).toBe(
      "This reply is no longer in Drafts. It was sent, deleted or replaced outside this card, so this card cannot send it.",
    );
  });

  it("a gone card only closes: it posts no trash of a draft that is not there", () => {
    expect(card).toMatch(/if \(gone\) \{\s*onDiscard\(\);\s*return;\s*\}/);
    expect(card).toContain('{gone ? "Close" : "Discard"}');
  });
});
