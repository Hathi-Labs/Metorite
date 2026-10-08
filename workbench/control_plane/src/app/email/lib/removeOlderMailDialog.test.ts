// WS-17 EM-T6e: the dialog "Remove older mail from Metorite", drawn.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6e",
// done-whens A4, A8, A10 and A11, and the UI contract D5.
//
// R7 fences named here:
//   * `email-storage-dialog-name` (A4): two or more mailboxes draw the chip,
//     the address and the label. One mailbox reads "This mailbox".
//   * `email-storage-dialog-copy` (A10): the Metorite-only words, and a scan
//     of every word of the notice, the step and the dialog for a sentence that
//     says Metorite deletes mail in Outlook.
//   * `email-storage-dialog-busy` (A11): a 409 shows the detail of the gateway,
//     and the choice stays checked.
//   * `email-storage-dialog-ui` (D5): `Modal`, `Button`s, `Input type="date"`
//     with `max`, and no hand-rolled overlay.
//
// Review round 1 widens `email-storage-dialog-copy` and adds four fences:
//   * `email-storage-dialog-copy` now scans each word for "remove" or
//     "delete" with Outlook, Gmail or the provider, in either order.
//   * `email-storage-unconfirmed-view` (A13): the words after 3 minutes, as
//     drawn, claim no failure.
//   * `email-storage-follow-up-stops` (A13): the cleanup of `followUp` stops
//     the timer. No preview runs, and no state changes, after it.
//   * `email-storage-one-confirm` (A9): `sendRemoval` sends no POST for a
//     confirm that the reducer refuses.
//   * `email-storage-no-zero-flow` (A13): an answer of `{}` never ends as
//     "done", and never reads as "removed 0".
//
// vitest here runs in the node environment and reads `*.test.ts` only. A
// `Modal` portal draws nothing there, so the test draws the pure
// `RemoveOlderMailView` with `createElement` and `renderToStaticMarkup`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import {
  RemoveOlderMailView,
  followUp,
  previewText,
  sendRemoval,
  type RemovalCalls,
  type RemoveOlderMailViewProps,
  type RemovalAccount,
} from "../components/RemoveOlderMailDialog";
import { StorageNotice } from "../components/StorageNotice";
import { StorageStep } from "../components/StorageStep";
import {
  BYTES_PER_MB,
  FOLLOW_UP_LIMIT_MS,
  FOLLOW_UP_POLL_MS,
  STORAGE_COPY,
  initialRemovalState,
  providerUnchanged,
  removalFailure,
  removalReducer,
  type RemovalEvent,
  type RemovalState,
} from "./storage";
import type { OlderMailPreview } from "./types";

const MB = BYTES_PER_MB;
const EMAIL_APP = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(EMAIL_APP, rel), { encoding: "utf-8" }).replace(/\r\n/g, "\n");
const codeOnly = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
const noop = () => {};
const DISABLED = /(?:^|\s)disabled=""/;

const ACCOUNT: RemovalAccount = {
  id: "a",
  emailAddress: "vj@fracktal.in",
  displayLabel: "Fracktal",
  colorSlot: 1,
  provider: "microsoft",
};

const KEY = new Date(2026, 6, 6).toISOString();
const ECHO = "2026-07-05T18:30:00+00:00";
const run = (s: RemovalState, ...events: RemovalEvent[]) => events.reduce(removalReducer, s);
const picked = () => run(initialRemovalState(), { type: "pick", choice: { kind: "months", months: 3, before: KEY } });
const ready = (messages = 12_400) =>
  run(picked(), { type: "previewAnswered", key: KEY, answer: { before: ECHO, messages, bytes: 380 * MB } });
/** A removal that outlived the proxy, so the dialog follows it (D1). */
const following = () =>
  run(ready(), { type: "confirm", before: ECHO }, { type: "removeFailed", failure: { kind: "follow" }, now: 0 });
/** The follow-up after 3 minutes with no answer of 0 (A13). */
const unconfirmed = () => run(following(), { type: "followAnswered", answer: null, now: FOLLOW_UP_LIMIT_MS });

function view(over: Partial<RemoveOlderMailViewProps>): string {
  const props: RemoveOlderMailViewProps = {
    account: ACCOUNT,
    named: false,
    state: initialRemovalState(),
    dateMax: "2026-10-04",
    locale: "en-US",
    onPickMonths: noop,
    onPickDate: noop,
    onDateChange: noop,
    onConfirm: noop,
    onClose: noop,
    ...over,
  };
  return renderToStaticMarkup(createElement(RemoveOlderMailView, props));
}

function buttons(markup: string): Array<{ text: string; attrs: string }> {
  return [...markup.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map((m) => ({
    attrs: m[1],
    text: m[2].replace(/<[^>]*>/g, "").trim(),
  }));
}

const confirmButton = (markup: string) => buttons(markup).find((b) => b.text === STORAGE_COPY.confirm);

/** Text of the markup with each tag removed, so a sentence reads whole. */
const text = (markup: string) => markup.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");

describe("email-storage-dialog-copy (A10, D-EM-14)", () => {
  it("holds the Metorite-only sentences before any choice", () => {
    const out = text(view({}));
    expect(out).toContain("This removes mail from Metorite only.");
    expect(out).toContain("Your Outlook mailbox does not change.");
    expect(out).toContain(STORAGE_COPY.kept);
    expect(out).toContain(STORAGE_COPY.loadOlderNote);
  });

  it("names the provider that does not change", () => {
    expect(providerUnchanged("microsoft")).toBe("Your Outlook mailbox does not change.");
    expect(providerUnchanged(undefined)).toBe("Your Outlook mailbox does not change.");
    expect(providerUnchanged("gmail")).toBe("Your Gmail mailbox does not change.");
  });

  it("pins the words of the action, the title and the confirm", () => {
    expect(STORAGE_COPY.action).toBe("Remove older mail from Metorite");
    expect(STORAGE_COPY.dialogTitle).toBe("Remove older mail from Metorite");
    expect(STORAGE_COPY.confirm).toBe("Remove from Metorite");
    expect(STORAGE_COPY.metoriteOnly).toBe("This removes mail from Metorite only.");
  });

  it("no word of the notice, the step or the dialog says Metorite removes mail at the provider", () => {
    const states: RemovalState[] = [
      initialRemovalState(),
      picked(),
      ready(),
      ready(0),
      run(ready(), { type: "confirm", before: ECHO }),
      run(ready(), { type: "confirm", before: ECHO }, { type: "removeFailed", failure: removalFailure(Object.assign(new Error("x"), { status: 500 })), now: 0 }),
      following(),
      unconfirmed(),
      run(following(), { type: "followAnswered", answer: { messages: 0 }, now: 1 }),
      run(ready(), { type: "confirm", before: ECHO }, {
        type: "removed",
        result: { before: ECHO, removed: 12, storedBytes: 9 * MB, storageLimitBytes: 500 * MB },
      }),
    ];
    const markup: string[] = [];
    for (const provider of ["microsoft", "gmail", "imap"] as const) {
      const account = { ...ACCOUNT, provider };
      const full = { ...account, storedBytes: 512 * MB, storageLimitBytes: 500 * MB, importPhase: "limit", importReachedAt: null };
      for (const state of states) markup.push(view({ account, state }), view({ account, state, named: true }));
      markup.push(
        renderToStaticMarkup(createElement(StorageNotice, { account: full, named: true, onRemove: noop })),
        renderToStaticMarkup(createElement(StorageNotice, { account: { ...full, storedBytes: MB }, named: false, onRemove: noop })),
        renderToStaticMarkup(createElement(StorageStep, { account: full, named: true, onRemove: noop, onKeep: noop })),
      );
    }
    const words = [
      ...markup.map(text),
      ...markup.flatMap((m) => [...m.matchAll(/\b(?:aria-label|title)="([^"]*)"/g)].map((x) => x[1])),
      ...Object.values(STORAGE_COPY),
      ...(["microsoft", "gmail", "imap", undefined] as const).map(providerUnchanged),
    ];
    expect(words.join(" ")).toContain("Your Gmail mailbox does not change.");
    for (const w of words) expect(claimsProvider(w), w).toBe(false);
  });

  it("the scan can fail, and it passes the two Metorite-only sentences", () => {
    expect(claimsProvider("Metorite also removes this mail from your Outlook mailbox.")).toBe(true);
    expect(claimsProvider("Metorite deletes this mail in Gmail.")).toBe(true);
    expect(claimsProvider("Your Outlook mailbox loses the mail that Metorite removes.")).toBe(true);
    expect(claimsProvider("The provider deletes it too.")).toBe(true);
    // `[^.]*` stops at the period, so the two sentences stay apart.
    expect(claimsProvider("This removes mail from Metorite only. Your Outlook mailbox does not change.")).toBe(false);
    expect(claimsProvider("This removes mail from Metorite only. Your Gmail mailbox does not change.")).toBe(false);
  });
});

/**
 * True for a sentence that says mail is removed or deleted at the provider,
 * in either order. `[^.]*` keeps the match inside one sentence.
 */
function claimsProvider(words: string): boolean {
  const provider = "(?:outlook|gmail|provider|mail server)";
  return (
    new RegExp(`(?:remov|delet)\\w*[^.]*${provider}`, "i").test(words) ||
    new RegExp(`${provider}[^.]*(?:remov|delet)\\w*`, "i").test(words)
  );
}

describe("email-storage-unconfirmed-view (A13, review round 1)", () => {
  const FAILURE = /fail|error|could ?not|couldn't|did not|didn't|went wrong/i;

  it("pins the words, which claim no failure", () => {
    expect(STORAGE_COPY.unconfirmed).toBe(
      "Metorite cannot confirm the removal. Open this mailbox again later to see its storage.",
    );
    expect(STORAGE_COPY.unconfirmed).not.toMatch(FAILURE);
    expect(STORAGE_COPY.following).not.toMatch(FAILURE);
  });

  it("draws those words, and no failure, after 3 minutes", () => {
    const out = view({ state: unconfirmed() });
    expect(text(out).trim()).toBe(`${STORAGE_COPY.unconfirmed} ${STORAGE_COPY.close}`);
    for (const copy of [STORAGE_COPY.failed, STORAGE_COPY.previewFailed]) expect(text(out)).not.toContain(copy);
    expect(text(out)).not.toMatch(FAILURE);
    expect(out).not.toContain('role="alert"');
    expect(out).not.toContain("text-destructive");
  });

  it("the follow-up draws no failure either", () => {
    const out = view({ state: following() });
    expect(text(out).trim()).toBe(`${STORAGE_COPY.following} ${STORAGE_COPY.close}`);
    expect(text(out)).not.toMatch(FAILURE);
  });
});

describe("email-storage-dialog-name (A4, D3)", () => {
  it("two or more mailboxes: the chip and the address", () => {
    const out = view({ named: true });
    expect(out).toContain('aria-label="Mailbox Fracktal, vj@fracktal.in"');
    expect(out).toContain(">vj@fracktal.in<");
  });

  it("two or more mailboxes: the meter line names the label", () => {
    const done = run(ready(), { type: "confirm", before: ECHO }, {
      type: "removed",
      result: { before: ECHO, removed: 12_400, storedBytes: 132 * MB, storageLimitBytes: 500 * MB },
    });
    expect(text(view({ named: true, state: done }))).toContain("Fracktal now uses 132 MB of its 500 MB in Metorite.");
    expect(text(view({ named: false, state: done }))).toContain("This mailbox now uses 132 MB of its 500 MB in Metorite.");
    expect(text(view({ state: done }))).toContain("Metorite removed 12,400 messages.");
  });

  it("one mailbox: no chip", () => {
    expect(view({ named: false })).not.toContain("Mailbox Fracktal");
  });

  it("the notice names the label with two mailboxes, and 'This mailbox' with one (A4)", () => {
    const full = { ...ACCOUNT, storedBytes: 512 * MB, storageLimitBytes: 500 * MB, importPhase: "limit" };
    const two = renderToStaticMarkup(createElement(StorageNotice, { account: full, named: true, onRemove: noop, locale: "en-US" }));
    expect(two).toContain('aria-label="Mailbox Fracktal, vj@fracktal.in"');
    expect(text(two)).toContain("Fracktal uses 512 MB of its 500 MB in Metorite.");
    const one = renderToStaticMarkup(createElement(StorageNotice, { account: full, named: false, onRemove: noop, locale: "en-US" }));
    expect(one).not.toContain("Mailbox Fracktal");
    expect(text(one)).toContain("This mailbox uses 512 MB of its 500 MB in Metorite.");
  });
});

describe("the confirm on screen (A8)", () => {
  it("is disabled with no choice, while the preview runs, and at 0 messages", () => {
    for (const state of [initialRemovalState(), picked(), ready(0)]) {
      expect(confirmButton(view({ state }))?.attrs).toMatch(DISABLED);
    }
    expect(text(view({ state: picked() }))).toContain(STORAGE_COPY.counting);
    expect(text(view({ state: ready(0) }))).toContain(STORAGE_COPY.previewNone);
  });

  it("is enabled once the preview of the current choice answers, with the count", () => {
    const out = view({ state: ready() });
    expect(confirmButton(out)?.attrs).not.toMatch(DISABLED);
    expect(text(out)).toContain("12,400 messages, about 380 MB");
  });

  it("is disabled while the removal runs, and Cancel too", () => {
    const out = view({ state: run(ready(), { type: "confirm", before: ECHO }) });
    expect(confirmButton(out)?.attrs).toMatch(DISABLED);
    expect(buttons(out).find((b) => b.text === STORAGE_COPY.cancel)?.attrs).toMatch(DISABLED);
    expect(text(out)).toContain(STORAGE_COPY.removing);
  });

  it("the date choice needs a day in the past before it counts", () => {
    const dated = run(initialRemovalState(), { type: "pick", choice: { kind: "date", date: "", before: null } });
    expect(previewText(dated)).toBe(STORAGE_COPY.chooseDay);
    expect(previewText(initialRemovalState())).toBe(STORAGE_COPY.chooseFirst);
    const future = run(dated, { type: "pick", choice: { kind: "date", date: "2026-12-01", before: null } });
    expect(previewText(future)).toBe(STORAGE_COPY.dateInvalid);
    expect(confirmButton(view({ state: future }))?.attrs).toMatch(DISABLED);
  });
});

describe("email-storage-dialog-busy (A11)", () => {
  const BUSY = "A sync is running for this mailbox. Try again when it ends.";
  const err = Object.assign(new Error(BUSY), { status: 409 });

  it("shows the detail of the gateway as it is, and keeps the choice", () => {
    const state = run(ready(), { type: "confirm", before: ECHO }, {
      type: "removeFailed",
      failure: removalFailure(err),
      now: 0,
    });
    const out = view({ state });
    expect(out).toMatch(new RegExp(`role="alert"[^>]*>${BUSY.replace(/\./g, "\\.")}<`));
    const checked = buttons(out).filter((b) => b.attrs.includes('aria-checked="true"'));
    expect(checked.map((b) => b.text)).toEqual(["3 months"]);
    // The member can confirm again without a new pick.
    expect(confirmButton(out)?.attrs).not.toMatch(DISABLED);
  });
});

describe("email-storage-dialog-ui (D5)", () => {
  const src = codeOnly(read("components/RemoveOlderMailDialog.tsx"));

  it("is built on Modal, Button and Input, with no hand-rolled overlay", () => {
    expect(src).toContain('import Modal from "@/components/ui/Modal";');
    expect(src).toContain('import Button from "@/components/ui/Button";');
    expect(src).toContain('import Input from "@/components/ui/Input";');
    for (const f of ["components/RemoveOlderMailDialog.tsx", "components/StorageNotice.tsx", "components/StorageStep.tsx"]) {
      const code = codeOnly(read(f));
      expect(code, f).not.toMatch(/fixed inset-0|@base-ui\/react|ConfirmDialog|<button\b|<input\b/);
      expect(code, f).not.toMatch(/amber|yellow|emerald|#[0-9a-f]{3,6}\b/i);
    }
  });

  it("the date is Input type date with max today, and the months are a radio group of Buttons", () => {
    const out = view({ state: run(initialRemovalState(), { type: "pick", choice: { kind: "date", date: "", before: null } }) });
    expect(out).toMatch(/<input[^>]*type="date"[^>]*max="2026-10-04"/);
    expect(out).toContain('role="radiogroup"');
    const radios = buttons(out).filter((b) => b.attrs.includes('role="radio"'));
    expect(radios.map((b) => b.text)).toEqual(["1 month", "2 months", "3 months", "6 months", "A date"]);
  });

  it("names only icons that exist", () => {
    for (const f of ["components/RemoveOlderMailDialog.tsx", "components/StorageNotice.tsx", "components/StorageStep.tsx"]) {
      // Every quoted name inside a `name=` or `icon=` attribute, a ternary too.
      const attrs = [...read(f).matchAll(/\b(?:name|icon)=(\{[^}]*\}|"[^"]*")/g)].map((m) => m[1]);
      const names = attrs.flatMap((a) => [...a.matchAll(/"([A-Z][A-Za-z0-9]+)"/g)].map((m) => m[1]));
      expect(names.length, f).toBeGreaterThan(0);
      for (const n of names) expect(isKnownIcon(n), `${f}: ${n}`).toBe(true);
    }
  });
});

// ── Review round 1: the calls of the dialog, driven with spies ─────────────

type Spies = { [K in keyof RemovalCalls]: Mock<RemovalCalls[K]> };

/** Spies for each call. `remove` and `preview` answer at once by default. */
function spies(over: Partial<Spies> = {}): Spies {
  return {
    dispatch: vi.fn<RemovalCalls["dispatch"]>(),
    preview: vi.fn<RemovalCalls["preview"]>(async () => ({ before: ECHO, messages: 3, bytes: MB })),
    remove: vi.fn<RemovalCalls["remove"]>(async () => ({ before: ECHO, removed: 3, storedBytes: MB, storageLimitBytes: 500 * MB })),
    removed: vi.fn<RemovalCalls["removed"]>(),
    refresh: vi.fn<RemovalCalls["refresh"]>(async () => null),
    ...over,
  };
}

describe("email-storage-follow-up-stops (A13, review round 1)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reads the preview of the same before once, 5 seconds later", async () => {
    const calls = spies();
    followUp(ECHO, calls);
    await vi.advanceTimersByTimeAsync(FOLLOW_UP_POLL_MS - 1);
    expect(calls.preview).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(calls.preview.mock.calls).toEqual([[ECHO]]);
    expect(calls.dispatch).toHaveBeenCalledWith(expect.objectContaining({ type: "followAnswered", answer: { before: ECHO, messages: 3, bytes: MB } }));
    // One read for each call. The effect arms the next read when `polls` moves.
    await vi.advanceTimersByTimeAsync(FOLLOW_UP_LIMIT_MS);
    expect(calls.preview).toHaveBeenCalledTimes(1);
  });

  it("after the cleanup (a close or an unmount), no preview runs and no state changes", async () => {
    const calls = spies();
    const stop = followUp(ECHO, calls);
    stop();
    await vi.advanceTimersByTimeAsync(FOLLOW_UP_POLL_MS);
    await vi.advanceTimersByTimeAsync(FOLLOW_UP_LIMIT_MS);
    expect(calls.preview).not.toHaveBeenCalled();
    expect(calls.dispatch).not.toHaveBeenCalled();
    expect(calls.refresh).not.toHaveBeenCalled();
  });

  it("an answer that comes after the cleanup changes nothing", async () => {
    let answer: (p: OlderMailPreview) => void = () => {};
    const calls = spies({ preview: vi.fn<RemovalCalls["preview"]>(() => new Promise<OlderMailPreview>((r) => (answer = r))) });
    const stop = followUp(ECHO, calls);
    await vi.advanceTimersByTimeAsync(FOLLOW_UP_POLL_MS);
    expect(calls.preview).toHaveBeenCalledTimes(1);
    stop();
    answer({ before: ECHO, messages: 0, bytes: 0 });
    await vi.advanceTimersByTimeAsync(FOLLOW_UP_LIMIT_MS);
    expect(calls.dispatch).not.toHaveBeenCalled();
    expect(calls.refresh).not.toHaveBeenCalled();
  });

  it("at 0 messages, the re-read of the accounts still lands after the cleanup", async () => {
    // The answer moves the dialog to "done", and React then runs the cleanup.
    const account = { storedBytes: 132 * MB, storageLimitBytes: 500 * MB };
    let reread: (a: typeof account) => void = () => {};
    const calls = spies({
      preview: vi.fn<RemovalCalls["preview"]>(async () => ({ before: ECHO, messages: 0, bytes: 0 })),
      refresh: vi.fn<RemovalCalls["refresh"]>(() => new Promise<typeof account>((r) => (reread = r))),
    });
    const stop = followUp(ECHO, calls);
    await vi.advanceTimersByTimeAsync(FOLLOW_UP_POLL_MS);
    expect(calls.refresh).toHaveBeenCalledTimes(1);
    stop();
    reread(account);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.dispatch).toHaveBeenLastCalledWith({ type: "meterRead", account });
  });
});

describe("email-storage-one-confirm (A9, review round 1)", () => {
  const refused: Array<[string, () => RemovalState]> = [
    ["no choice", initialRemovalState],
    ["the preview runs", picked],
    ["0 messages", () => ready(0)],
    ["a failed count", () => run(picked(), { type: "previewAnswered", key: KEY, answer: null })],
    ["removing", () => run(ready(), { type: "confirm", before: ECHO })],
    ["following", following],
    ["unconfirmed", unconfirmed],
    ["done", () => run(following(), { type: "followAnswered", answer: { messages: 0 }, now: 1 })],
  ];

  it("a confirm that the reducer refuses sends no POST, and changes no state", () => {
    for (const [name, state] of refused) {
      const calls = spies();
      const sending = { current: false };
      expect(sendRemoval(state(), sending, calls), name).toBe(false);
      expect(calls.remove, name).not.toHaveBeenCalled();
      expect(calls.dispatch, name).not.toHaveBeenCalled();
      expect(sending.current, name).toBe(false);
    }
  });

  it("a confirm that the reducer takes sends one POST, with the before of the preview", async () => {
    const calls = spies();
    const sending = { current: false };
    expect(sendRemoval(ready(), sending, calls)).toBe(true);
    // A second click before the next render sends nothing.
    expect(sendRemoval(ready(), sending, calls)).toBe(false);
    expect(calls.remove.mock.calls).toEqual([[ECHO]]);
    expect(calls.dispatch.mock.calls[0]).toEqual([{ type: "confirm", before: ECHO }]);
    await Promise.resolve();
    await Promise.resolve();
    expect(calls.removed).toHaveBeenCalledTimes(1);
    expect(calls.dispatch).toHaveBeenLastCalledWith(expect.objectContaining({ type: "removed" }));
    expect(sending.current).toBe(false);
  });

  it("the dialog has one guard: the button and sendRemoval both ask acceptedConfirm", () => {
    const src = codeOnly(read("components/RemoveOlderMailDialog.tsx"));
    const sendFn = src.slice(src.indexOf("export function sendRemoval("), src.indexOf("export function followUp("));
    expect(sendFn).toContain("const before = acceptedConfirm(state);");
    expect(sendFn).not.toMatch(/confirmBefore\(|state\.phase/);
    expect(src).toContain("const sendable = acceptedConfirm(state) !== null;");
    expect(src).not.toMatch(/confirmBefore\(/);
  });
});

describe("email-storage-no-zero-flow (A13, review round 1)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  /** The reducer behind a dispatch spy, as `useReducer` holds it. */
  function held(start: RemovalState) {
    const box = { state: start };
    const dispatch = vi.fn<RemovalCalls["dispatch"]>((e: RemovalEvent) => {
      box.state = removalReducer(box.state, e);
    });
    return { box, dispatch };
  }

  it("a removal answered with {} is followed, never drawn as 'removed 0'", async () => {
    const api = await import("./api");
    const { box, dispatch } = held(ready());
    const calls = spies({ dispatch, remove: vi.fn<RemovalCalls["remove"]>((before) => api.removeOlderMail("a", before)) });
    expect(sendRemoval(box.state, { current: false }, calls)).toBe(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(box.state.phase).toBe("following");
    expect(box.state.removed).toBeNull();
    expect(calls.removed).not.toHaveBeenCalled();
    expect(text(view({ state: box.state }))).not.toMatch(/removed 0/i);
  });

  it("a follow-up answered with {} for 3 minutes ends unconfirmed, never done", async () => {
    const api = await import("./api");
    const { box, dispatch } = held(
      run(ready(), { type: "confirm", before: ECHO }, { type: "removeFailed", failure: { kind: "follow" }, now: Date.now() }),
    );
    const calls = spies({ dispatch, preview: vi.fn<RemovalCalls["preview"]>((before) => api.previewOlderMail("a", before)) });
    // The effect: one `followUp` for each value of `polls`, as React runs it.
    for (let i = 0; i < 100 && box.state.phase === "following"; i++) {
      const stop = followUp(ECHO, calls);
      await vi.advanceTimersByTimeAsync(FOLLOW_UP_POLL_MS);
      stop();
    }
    expect(box.state.phase).toBe("unconfirmed");
    expect(calls.refresh).not.toHaveBeenCalled();
    expect(dispatch.mock.calls.every(([e]) => e.type !== "followAnswered" || e.answer === null)).toBe(true);
  });
});
