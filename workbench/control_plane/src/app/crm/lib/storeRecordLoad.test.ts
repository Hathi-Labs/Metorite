import { beforeEach, describe, expect, it, vi } from "vitest";

// ── The read of the open record (ORC-1, D181) ─────────────────────────────
//
// A docked card lets the member click A and then B. The latest ask must win, or
// an edit goes to the record on screen and not to the one in the URL. A read
// that fails belongs to the card, because a banner on the page behind a full
// card stays out of sight.

vi.mock("./api", () => ({
  CrmError: class CrmError extends Error {},
  getRecord: vi.fn(),
  getTimeline: vi.fn(async () => ({ entries: [] })),
  listDealContacts: vi.fn(async () => ({ rows: [] })),
  listRecords: vi.fn(async () => ({ rows: [], total: 0 })),
}));

import * as api from "./api";
import { useCrmStore } from "./store";

const getRecord = vi.mocked(api.getRecord);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

describe("loadRecord", () => {
  beforeEach(() => {
    getRecord.mockReset();
    useCrmStore.setState({ record: null, recordFailed: false, error: null });
  });

  it("a slow answer for A does not draw over B", async () => {
    const a = deferred<Record<string, unknown>>();
    const b = deferred<Record<string, unknown>>();
    getRecord.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    const first = useCrmStore.getState().loadRecord("deals", "a");
    const second = useCrmStore.getState().loadRecord("deals", "b");
    b.resolve({ id: "b" });
    await second;
    a.resolve({ id: "a" });
    await first;
    expect(useCrmStore.getState().record).toEqual({ id: "b" });
  });

  it("a failed answer for A does not fail B", async () => {
    const a = deferred<Record<string, unknown>>();
    const b = deferred<Record<string, unknown>>();
    getRecord.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    const first = useCrmStore.getState().loadRecord("deals", "a");
    const second = useCrmStore.getState().loadRecord("deals", "b");
    b.resolve({ id: "b" });
    await second;
    a.reject(new Error("no"));
    await first;
    expect(useCrmStore.getState().recordFailed).toBe(false);
    expect(useCrmStore.getState().record).toEqual({ id: "b" });
  });

  it("a read that fails is the card's to say, not a banner behind it", async () => {
    getRecord.mockRejectedValueOnce(new Error("no"));
    await useCrmStore.getState().loadRecord("deals", "a");
    expect(useCrmStore.getState().recordFailed).toBe(true);
    expect(useCrmStore.getState().error).toBeNull();
  });

  it("a retry clears the failure while it reads again", async () => {
    getRecord.mockRejectedValueOnce(new Error("no"));
    await useCrmStore.getState().loadRecord("deals", "a");
    const again = deferred<Record<string, unknown>>();
    getRecord.mockReturnValueOnce(again.promise);
    const retry = useCrmStore.getState().loadRecord("deals", "a");
    expect(useCrmStore.getState().recordFailed).toBe(false);
    again.resolve({ id: "a" });
    await retry;
    expect(useCrmStore.getState().record).toEqual({ id: "a" });
  });

  it("a later read that fails keeps the record and the banner", async () => {
    getRecord.mockResolvedValueOnce({ id: "a" });
    vi.mocked(api.listDealContacts).mockRejectedValueOnce(new Error("no contacts"));
    await useCrmStore.getState().loadRecord("deals", "a");
    expect(useCrmStore.getState().record).toEqual({ id: "a" });
    expect(useCrmStore.getState().recordFailed).toBe(false);
    expect(useCrmStore.getState().error).not.toBeNull();
  });
});
