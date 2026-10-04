// WS-17 EM-T6e: the storage notice and "Remove older mail from Metorite",
// the pure half (`lib/storage.ts`).
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6e", the
// decisions D1 to D7 and the done-whens A1 to A15.
//
// R7 fences named here:
//   * `email-storage-limit` (A1): the rule of the limit, the mirror of
//     `email_ingestion.storage.at_limit`.
//   * `email-storage-copy` (A2, A3, D4): the words of the notice, the gap
//     line, the MB format and the preview line.
//   * `email-storage-confirm` (A8, A9): the confirm waits for the preview of
//     the CURRENT choice, and it sends the `before` of that preview.
//   * `email-storage-follow-up` (A11, A13): a 409 keeps the choice, and a
//     502, a 504 or a network error starts the follow-up of D1, which never
//     reports a failure.
//   * `email-storage-meter-store` (A14): the store carries the meter of the
//     answer, and the page reads the accounts again after it.
//   * `email-storage-kept` (D6): "Keep it as it is" stores ids only, and a
//     refused store never throws.
//
// vitest here runs in the node environment and reads `*.test.ts` only, so a
// component is drawn with `createElement` and `renderToStaticMarkup`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StorageNotice } from "../components/StorageNotice";
import { StorageStep } from "../components/StorageStep";
import { useEmailStore } from "./emailStore";
import {
  BYTES_PER_MB,
  FOLLOW_UP_LIMIT_MS,
  FOLLOW_UP_POLL_MS,
  STORAGE_COPY,
  STORAGE_KEPT_KEY,
  atStorageLimit,
  beforeOfDate,
  confirmBefore,
  dateInputValue,
  formatMb,
  initialRemovalState,
  keepNewestBefore,
  keepStorage,
  previewLine,
  readStorageKept,
  removalFailure,
  removalReducer,
  storageMark,
  storageNotice,
  storageNoticeKind,
  withRemovalMeter,
  type RemovalChoice,
  type RemovalEvent,
  type RemovalState,
} from "./storage";
import type { EmailAccount, RemoveOlderResult } from "./types";

const MB = BYTES_PER_MB;
const LIMIT = 500 * MB;
// 15:30 local time, so no time zone moves the day.
const NOW = new Date(2026, 9, 4, 15, 30, 0);

const EMAIL_APP = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(EMAIL_APP, rel), { encoding: "utf-8" }).replace(/\r\n/g, "\n");
const codeOnly = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

/** A mailbox whose first import stopped at the limit (EM-T6c). */
const FULL: Pick<
  EmailAccount,
  "id" | "emailAddress" | "displayLabel" | "colorSlot" | "storedBytes" | "storageLimitBytes" | "importPhase" | "importReachedAt"
> = {
  id: "a",
  emailAddress: "vj@fracktal.in",
  displayLabel: "Fracktal",
  colorSlot: 1,
  storedBytes: 512 * MB,
  storageLimitBytes: LIMIT,
  importPhase: "limit",
  importReachedAt: new Date(2026, 8, 14, 12, 0, 0).toISOString(),
};

const run = (s: RemovalState, ...events: RemovalEvent[]) => events.reduce(removalReducer, s);

describe("email-storage-limit (A1)", () => {
  it("is false for a null or absent meter, and for an absent limit", () => {
    expect(atStorageLimit({ storedBytes: null, storageLimitBytes: LIMIT })).toBe(false);
    expect(atStorageLimit({ storageLimitBytes: LIMIT })).toBe(false);
    expect(atStorageLimit({ storedBytes: 900 * MB })).toBe(false);
    expect(atStorageLimit({})).toBe(false);
  });

  it("is false under the limit, and true at it and over it", () => {
    expect(atStorageLimit({ storedBytes: LIMIT - 1, storageLimitBytes: LIMIT })).toBe(false);
    expect(atStorageLimit({ storedBytes: LIMIT, storageLimitBytes: LIMIT })).toBe(true);
    expect(atStorageLimit({ storedBytes: LIMIT + 1, storageLimitBytes: LIMIT })).toBe(true);
  });

  it("gives the switcher mark only at the limit", () => {
    expect(storageMark(FULL)).toBe("At the storage limit");
    expect(storageMark({ ...FULL, storedBytes: 10 * MB })).toBeNull();
    expect(storageMark({ ...FULL, storedBytes: null })).toBeNull();
  });
});

describe("email-storage-copy (A2, A3, D4)", () => {
  it("reads the notice of 512 MB of 500 MB, word for word (A2)", () => {
    const v = storageNotice(FULL, { name: null, now: NOW, locale: "en-US" });
    expect(v).toEqual({
      kind: "limit",
      text: "This mailbox uses 512 MB of its 500 MB in Metorite. Metorite stopped importing older mail.",
      action: "Remove older mail from Metorite",
    });
  });

  it("names the label in place of 'This mailbox' for two or more mailboxes (D3)", () => {
    const v = storageNotice(FULL, { name: "Fracktal", now: NOW, locale: "en-US" });
    expect(v?.text).toBe("Fracktal uses 512 MB of its 500 MB in Metorite. Metorite stopped importing older mail.");
  });

  it("draws no notice under the limit in the phase done (A3)", () => {
    const done = { ...FULL, storedBytes: 120 * MB, importPhase: "done" };
    expect(storageNoticeKind(done)).toBeNull();
    expect(storageNotice(done, { name: null, now: NOW })).toBeNull();
    const html = renderToStaticMarkup(createElement(StorageNotice, { account: done, named: false, onRemove: () => {} }));
    expect(html).toBe("");
  });

  it("draws the gap line with no removal action in the phase limit under the limit (A3, D2)", () => {
    const gap = { ...FULL, storedBytes: 300 * MB };
    expect(storageNoticeKind(gap)).toBe("gap");
    expect(storageNotice(gap, { name: null, now: NOW })).toEqual({
      kind: "gap",
      text: "Metorite imported this mailbox back to 14 Sep. It stopped there at the storage limit.",
    });
    const html = renderToStaticMarkup(createElement(StorageNotice, { account: gap, named: false, onRemove: () => {} }));
    expect(html).toContain("Metorite imported this mailbox back to 14 Sep.");
    expect(html).not.toContain("<button");
    expect(html).not.toContain(STORAGE_COPY.action);
    // No date to name: the line names none.
    expect(storageNotice({ ...gap, importReachedAt: null }, { name: "Fracktal", now: NOW })?.text).toBe(
      "Metorite stopped the import of Fracktal at the storage limit.",
    );
  });

  it("draws nothing when the gateway sends no limit, in any phase", () => {
    const old = { ...FULL, storageLimitBytes: undefined };
    expect(storageNoticeKind(old)).toBeNull();
    expect(storageNotice(old, { name: null, now: NOW })).toBeNull();
  });

  it("draws the limit notice with the warning tokens and the action (D5)", () => {
    const html = renderToStaticMarkup(
      createElement(StorageNotice, { account: FULL, named: false, onRemove: () => {}, locale: "en-US" }),
    );
    expect(html).toContain("This mailbox uses 512 MB of its 500 MB in Metorite.");
    expect(html).toMatch(/<button[^>]*>[\s\S]*?Remove older mail from Metorite[\s\S]*?<\/button>/);
    const section = html.match(/<section\b[^>]*class="([^"]*)"/)?.[1] ?? "";
    expect(section.split(/\s+/)).toEqual(expect.arrayContaining(["border-warning/30", "bg-warning/10"]));
    expect(html).not.toMatch(/amber|yellow/);
  });

  it("formats MB with 1 MB = 1,048,576 bytes", () => {
    expect(BYTES_PER_MB).toBe(1_048_576);
    expect(formatMb(512 * MB, "en-US")).toBe("512 MB");
    expect(formatMb(380.4 * MB, "en-US")).toBe("380 MB");
    expect(formatMb(1500 * MB, "en-US")).toBe("1,500 MB");
    expect(formatMb(1000)).toBe("less than 1 MB");
    expect(formatMb(0)).toBe("0 MB");
    expect(formatMb(Number.NaN)).toBe("0 MB");
  });

  it("reads the preview as '12,400 messages, about 380 MB' (D4)", () => {
    expect(previewLine({ messages: 12_400, bytes: 380 * MB }, "en-US")).toBe("12,400 messages, about 380 MB");
    expect(previewLine({ messages: 1, bytes: 2_000 }, "en-US")).toBe("1 message, less than 1 MB");
    expect(previewLine({ messages: 0, bytes: 0 })).toBe(STORAGE_COPY.previewNone);
  });
});

describe("the choices of the dialog: local midnight, never a bare date (D4)", () => {
  it("keeps the newest N months as N times 30 days, to local midnight", () => {
    expect(keepNewestBefore(1, NOW)).toBe(new Date(2026, 8, 4).toISOString());
    expect(keepNewestBefore(6, NOW)).toBe(new Date(2026, 3, 7).toISOString());
    for (const m of [1, 2, 3, 6]) {
      const before = keepNewestBefore(m, NOW);
      expect(before).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
      const local = new Date(before);
      expect([local.getHours(), local.getMinutes(), local.getSeconds()]).toEqual([0, 0, 0]);
    }
  });

  it("takes a picked day to the instant of its local midnight", () => {
    expect(beforeOfDate("2026-09-14", NOW)).toBe(new Date(2026, 8, 14).toISOString());
    // Today starts in the past, so it is a valid cutoff.
    expect(beforeOfDate("2026-10-04", NOW)).toBe(new Date(2026, 9, 4).toISOString());
  });

  it("refuses a day that is not real, not a date, or not in the past", () => {
    expect(beforeOfDate("2026-02-31", NOW)).toBeNull();
    expect(beforeOfDate("14/09/2026", NOW)).toBeNull();
    expect(beforeOfDate("", NOW)).toBeNull();
    expect(beforeOfDate("2026-10-05", NOW)).toBeNull();
  });

  it("gives today as the max of the date field", () => {
    expect(dateInputValue(NOW)).toBe("2026-10-04");
    expect(dateInputValue(new Date(2026, 0, 5, 9))).toBe("2026-01-05");
  });
});

const KEY_1 = new Date(2026, 8, 4).toISOString();
const KEY_3 = new Date(2026, 6, 6).toISOString();
const ONE: RemovalChoice = { kind: "months", months: 1, before: KEY_1 };
const THREE: RemovalChoice = { kind: "months", months: 3, before: KEY_3 };
// The gateway answers `before` as Python ISO text, which differs from the key.
const ECHO_3 = "2026-07-05T18:30:00+00:00";

describe("email-storage-confirm (A8, A9)", () => {
  it("stays disabled until the preview of the current choice answers", () => {
    let s = run(initialRemovalState(), { type: "pick", choice: ONE });
    expect(s.preview).toEqual({ state: "loading", key: KEY_1 });
    expect(confirmBefore(s.preview, s.choice?.before ?? null)).toBeNull();
    s = run(s, { type: "previewAnswered", key: KEY_1, answer: { before: "x", messages: 40, bytes: MB } });
    expect(confirmBefore(s.preview, s.choice?.before ?? null)).toBe("x");
  });

  it("a late answer for an earlier choice does not enable it (A8)", () => {
    let s = run(initialRemovalState(), { type: "pick", choice: ONE }, { type: "pick", choice: THREE });
    s = run(s, { type: "previewAnswered", key: KEY_1, answer: { before: "x", messages: 99, bytes: MB } });
    expect(s.preview).toEqual({ state: "loading", key: KEY_3 });
    expect(confirmBefore(s.preview, s.choice?.before ?? null)).toBeNull();
    // A ready preview of another key never enables the current choice.
    expect(
      confirmBefore({ state: "ready", key: KEY_1, answer: { before: "x", messages: 9, bytes: 1 } }, KEY_3),
    ).toBeNull();
  });

  it("stays disabled at 0 messages, and after a failed count", () => {
    const zero = run(
      initialRemovalState(),
      { type: "pick", choice: THREE },
      { type: "previewAnswered", key: KEY_3, answer: { before: ECHO_3, messages: 0, bytes: 0 } },
    );
    expect(confirmBefore(zero.preview, KEY_3)).toBeNull();
    const failed = run(
      initialRemovalState(),
      { type: "pick", choice: THREE },
      { type: "previewAnswered", key: KEY_3, answer: null },
    );
    expect(failed.preview.state).toBe("failed");
    expect(confirmBefore(failed.preview, KEY_3)).toBeNull();
  });

  it("sends the before of the preview that answered, not the key it asked with (A9)", () => {
    const s = run(
      initialRemovalState(),
      { type: "pick", choice: THREE },
      { type: "previewAnswered", key: KEY_3, answer: { before: ECHO_3, messages: 12_400, bytes: 380 * MB } },
    );
    expect(confirmBefore(s.preview, KEY_3)).toBe(ECHO_3);
    const removing = run(s, { type: "confirm", before: ECHO_3 });
    expect(removing.phase).toBe("removing");
    expect(removing.before).toBe(ECHO_3);
    // A confirm for any other value is not a confirm of this preview.
    expect(run(s, { type: "confirm", before: KEY_3 }).phase).toBe("choose");
  });

  it("the dialog sends exactly that value, for the id of the mailbox it names", () => {
    const src = codeOnly(read("components/RemoveOlderMailDialog.tsx"));
    expect(src).toMatch(
      /const before = confirmBefore\(state\.preview, state\.choice\?\.before \?\? null\);[\s\S]*?removeOlderMail\(id, before\)/,
    );
    expect(src).toContain("const id = account.id;");
    expect(src).toContain("previewOlderMail(id, before)");
    expect(src).not.toMatch(/selectedAccountId|poolHome/);
  });
});

/** A dialog that confirmed THREE and waits for the removal. */
function removingState(): RemovalState {
  return run(
    initialRemovalState(),
    { type: "pick", choice: THREE },
    { type: "previewAnswered", key: KEY_3, answer: { before: ECHO_3, messages: 12, bytes: MB } },
    { type: "confirm", before: ECHO_3 },
  );
}

function httpError(status: number | undefined, message: string): Error & { status?: number } {
  const e = new Error(message) as Error & { status?: number };
  if (status !== undefined) e.status = status;
  return e;
}

describe("email-storage-follow-up (A11, A13, D1)", () => {
  const BUSY = "A sync is running for this mailbox. Try again when it ends.";

  it("reads a 409 as busy, with the detail of the gateway as it is (A11)", () => {
    expect(removalFailure(httpError(409, BUSY))).toEqual({ kind: "busy", detail: BUSY });
  });

  it("a 409 keeps the choice and its preview, so the member can confirm again (A11)", () => {
    const before = removingState();
    const s = run(before, { type: "removeFailed", failure: removalFailure(httpError(409, BUSY)), now: 0 });
    expect(s.phase).toBe("choose");
    expect(s.message).toBe(BUSY);
    expect(s.choice).toBe(before.choice);
    expect(s.preview).toBe(before.preview);
    expect(confirmBefore(s.preview, s.choice?.before ?? null)).toBe(ECHO_3);
  });

  it("reads a 502, a 504 and a network error as a removal to follow (A13)", () => {
    expect(removalFailure(httpError(502, "Gateway error 502"))).toEqual({ kind: "follow" });
    expect(removalFailure(httpError(504, "Gateway error 504"))).toEqual({ kind: "follow" });
    expect(removalFailure(new TypeError("Failed to fetch"))).toEqual({ kind: "follow" });
  });

  it("reads any other answer as a failure that claims no count", () => {
    expect(removalFailure(httpError(400, "'before' must be a date in the past."))).toEqual({
      kind: "failed",
      detail: "'before' must be a date in the past.",
    });
    expect(removalFailure(httpError(500, "Gateway error 500"))).toEqual({ kind: "failed", detail: STORAGE_COPY.failed });
    expect(removalFailure(httpError(503, "Service Unavailable"))).toEqual({ kind: "failed", detail: STORAGE_COPY.failed });
  });

  it("follows the removal through the preview, and confirms at 0 messages (A13)", () => {
    expect(FOLLOW_UP_POLL_MS).toBe(5_000);
    expect(FOLLOW_UP_LIMIT_MS).toBe(180_000);
    let s = run(removingState(), { type: "removeFailed", failure: { kind: "follow" }, now: 1_000 });
    expect(s.phase).toBe("following");
    expect(s.before).toBe(ECHO_3);
    expect(s.message).toBeNull();
    s = run(s, { type: "followAnswered", answer: { messages: 7 }, now: 6_000 });
    expect([s.phase, s.polls]).toEqual(["following", 1]);
    // A failed read proves nothing either way.
    s = run(s, { type: "followAnswered", answer: null, now: 11_000 });
    expect([s.phase, s.polls, s.message]).toEqual(["following", 2, null]);
    s = run(s, { type: "followAnswered", answer: { messages: 0 }, now: 16_000 });
    expect(s.phase).toBe("done");
    expect(s.removed).toBeNull();
    expect(s.meter).toBeNull();
    // Then the page reads the accounts again, and the dialog shows the meter.
    s = run(s, { type: "meterRead", account: { storedBytes: 132 * MB, storageLimitBytes: LIMIT } });
    expect(s.meter).toEqual({ stored: 132 * MB, limit: LIMIT });
  });

  it("says it cannot confirm after 3 minutes, and never reports a failure (A13)", () => {
    let s = run(removingState(), { type: "removeFailed", failure: { kind: "follow" }, now: 0 });
    s = run(s, { type: "followAnswered", answer: { messages: 3 }, now: FOLLOW_UP_LIMIT_MS - 1 });
    expect(s.phase).toBe("following");
    s = run(s, { type: "followAnswered", answer: null, now: FOLLOW_UP_LIMIT_MS });
    expect(s.phase).toBe("unconfirmed");
    expect(s.message).toBeNull();
    // 0 messages at the last read still confirms.
    const late = run(
      run(removingState(), { type: "removeFailed", failure: { kind: "follow" }, now: 0 }),
      { type: "followAnswered", answer: { messages: 0 }, now: FOLLOW_UP_LIMIT_MS + 10 },
    );
    expect(late.phase).toBe("done");
    expect(STORAGE_COPY.unconfirmed).not.toMatch(/fail|error|could not remove/i);
    expect(STORAGE_COPY.following).not.toMatch(/fail|error/i);
  });

  it("the dialog polls the preview with the same before every 5 seconds", () => {
    const src = codeOnly(read("components/RemoveOlderMailDialog.tsx"));
    expect(src).toContain('const followBefore = state.phase === "following" ? state.before : null;');
    expect(src).toMatch(/setTimeout\(\(\) => \{\s*previewOlderMail\(id, followBefore\)/);
    expect(src).toContain("}, FOLLOW_UP_POLL_MS);");
    expect(src).toContain("[id, followBefore, state.polls]");
    // The meter alone is never the proof (D1).
    expect(src).not.toMatch(/listEmailAccounts|storedBytes\s*[<>]/);
  });

  it("a removal runs from the click, never from an effect that React can run twice", () => {
    const src = codeOnly(read("components/RemoveOlderMailDialog.tsx"));
    expect(src.match(/removeOlderMail\(/g)).toHaveLength(1);
    const confirmFn = src.slice(src.indexOf("const confirm = () => {"), src.indexOf("const followBefore"));
    expect(confirmFn.length).toBeGreaterThan(100);
    expect(confirmFn).toContain("removeOlderMail(id, before)");
    expect(confirmFn).toContain("sending.current = true;");
  });
});

describe("the api of the two routes", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stub(status: number, body: unknown) {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("encodes the before of the preview, so a + of an echo is not a space", async () => {
    const f = stub(200, { before: ECHO_3, messages: 12, bytes: 34 });
    const api = await import("./api");
    expect(await api.previewOlderMail("a1", ECHO_3)).toEqual({ before: ECHO_3, messages: 12, bytes: 34 });
    expect(f.mock.calls[0][0]).toBe("/api/email/accounts/a1/storage/older?before=2026-07-05T18%3A30%3A00%2B00%3A00");
  });

  it("posts the before of the removal and maps the answer", async () => {
    const f = stub(200, { before: ECHO_3, removed: 12, stored_bytes: 132 * MB, storage_limit_bytes: LIMIT });
    const api = await import("./api");
    expect(await api.removeOlderMail("a1", ECHO_3)).toEqual({
      before: ECHO_3,
      removed: 12,
      storedBytes: 132 * MB,
      storageLimitBytes: LIMIT,
    });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/email/accounts/a1/storage/remove-older");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ before: ECHO_3 });
  });

  it("keeps the status and the detail of a refusal, for the dialog to read", async () => {
    stub(409, { detail: "A sync is running for this mailbox. Try again when it ends." });
    const api = await import("./api");
    const err = await api.removeOlderMail("a1", ECHO_3).catch((e: unknown) => e);
    expect(removalFailure(err)).toEqual({
      kind: "busy",
      detail: "A sync is running for this mailbox. Try again when it ends.",
    });
  });

  it("maps the meter of GET /email/accounts, and leaves an old gateway without one", async () => {
    stub(200, [
      { id: "a1", provider: "microsoft", email_address: "a@x.test", stored_bytes: null, storage_limit_bytes: LIMIT },
      { id: "a2", provider: "microsoft", email_address: "b@x.test" },
    ]);
    const api = await import("./api");
    const [one, two] = await api.listEmailAccounts();
    expect([one.storedBytes, one.storageLimitBytes]).toEqual([null, LIMIT]);
    expect([two.storedBytes, two.storageLimitBytes]).toEqual([undefined, undefined]);
    expect(storageNoticeKind({ ...two, importPhase: "limit" })).toBeNull();
  });
});

describe("email-storage-meter-store (A14)", () => {
  const result: RemoveOlderResult = { before: ECHO_3, removed: 12, storedBytes: 132 * MB, storageLimitBytes: LIMIT };

  it("the store carries the stored_bytes of the answer", () => {
    const a = { id: "a", emailAddress: "a@x.test", storedBytes: 512 * MB, storageLimitBytes: LIMIT } as EmailAccount;
    const b = { id: "b", emailAddress: "b@x.test", storedBytes: 9 * MB, storageLimitBytes: LIMIT } as EmailAccount;
    useEmailStore.setState({ accounts: [a, b] });
    useEmailStore.getState().replaceAccount(withRemovalMeter(a, result));
    const [after, other] = useEmailStore.getState().accounts;
    expect(after.storedBytes).toBe(132 * MB);
    expect(after.storageLimitBytes).toBe(LIMIT);
    expect(atStorageLimit(after)).toBe(false);
    expect(other).toBe(b);
  });

  it("the page writes the answer first, then reads the accounts again", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toMatch(
      /onRemoved=\{\(id, result\) => \{\s*const current = useEmailStore\.getState\(\)\.accounts\.find\(\(a\) => a\.id === id\);\s*if \(current\) replaceAccount\(withRemovalMeter\(current, result\)\);\s*void refreshAccounts\(\);/,
    );
    expect(page).toContain("onRefresh={async (id) => (await refreshAccounts())?.find((a) => a.id === id) ?? null}");
  });
});

describe("email-storage-kept (D6)", () => {
  function memory(): Storage {
    const m = new Map<string, string>();
    return {
      getItem: (k: string) => m.get(k) ?? null,
      setItem: (k: string, v: string) => void m.set(k, v),
      removeItem: (k: string) => void m.delete(k),
      clear: () => m.clear(),
      key: () => null,
      length: 0,
    } as Storage;
  }

  it("stores the ids of the kept mailboxes, and nothing else", () => {
    const store = memory();
    expect(readStorageKept(store)).toEqual([]);
    expect(keepStorage("a", store)).toEqual(["a"]);
    expect(keepStorage("b", store)).toEqual(["a", "b"]);
    expect(keepStorage("a", store)).toEqual(["b", "a"]);
    expect(JSON.parse(store.getItem(STORAGE_KEPT_KEY) ?? "")).toEqual(["b", "a"]);
  });

  it("reads text that is not a list of ids as none", () => {
    const store = memory();
    store.setItem(STORAGE_KEPT_KEY, "{not json");
    expect(readStorageKept(store)).toEqual([]);
    store.setItem(STORAGE_KEPT_KEY, JSON.stringify(["a", 3, "", null, "b"]));
    expect(readStorageKept(store)).toEqual(["a", "b"]);
  });

  it("never throws for a store that refuses, or for no store", () => {
    const refusing = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    expect(readStorageKept(refusing)).toEqual([]);
    expect(keepStorage("a", refusing)).toEqual(["a"]);
    expect(readStorageKept(null)).toEqual([]);
    expect(keepStorage("a", null)).toEqual(["a"]);
  });

  it("holds at most 50 ids", () => {
    const store = memory();
    for (let i = 0; i < 60; i++) keepStorage(`m${i}`, store);
    const kept = readStorageKept(store);
    expect(kept).toHaveLength(50);
    expect(kept[49]).toBe("m59");
  });
});

describe("the storage step (D6)", () => {
  it("offers the dialog and 'Keep it as it is', and names the mailbox for two or more", () => {
    const html = renderToStaticMarkup(
      createElement(StorageStep, { account: FULL, named: true, onRemove: () => {}, onKeep: () => {}, locale: "en-US" }),
    );
    expect(html).toContain(STORAGE_COPY.stepTitle);
    expect(html).toContain("Fracktal uses 512 MB of its 500 MB in Metorite.");
    expect(html).toContain('aria-label="Mailbox Fracktal, vj@fracktal.in"');
    expect(html).toMatch(/<button[^>]*>[\s\S]*?Remove older mail from Metorite[\s\S]*?<\/button>/);
    expect(html).toMatch(/<button[^>]*>[\s\S]*?Keep it as it is[\s\S]*?<\/button>/);
  });

  it("draws nothing under the limit", () => {
    const under = { ...FULL, storedBytes: 10 * MB };
    expect(
      renderToStaticMarkup(createElement(StorageStep, { account: under, named: false, onRemove: () => {}, onKeep: () => {} })),
    ).toBe("");
  });

  it("the notice, the storage step and the rules step never share a key (visual review)", () => {
    // After "Keep it as it is", the notice and the rules step draw for ONE
    // mailbox, side by side. React warned of a duplicate key in the rig.
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain("key={`storage-notice-${storageAccount.id}`}");
    expect(page).toContain("key={`storage-step-${selectedAccount.id}`}");
    expect(page).not.toMatch(/<Storage(?:Notice|Step)\s+key=\{(?:storageAccount|selectedAccount)\.id\}/);
  });

  it("the page keeps the id on 'Keep it as it is', and draws the step before the rules step", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain("onKeep={() => setStorageKept(keepStorage(selectedAccount.id))}");
    expect(page).toContain("useState<string[]>(() => readStorageKept())");
    const step = page.indexOf("<StorageStep");
    expect(step).toBeGreaterThan(page.indexOf("{importPanels.map("));
    expect(step).toBeLessThan(page.indexOf("<OnboardingRulesStep"));
  });
});
