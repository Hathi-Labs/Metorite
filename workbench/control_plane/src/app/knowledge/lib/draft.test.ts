/**
 * The edit draft of one page (WS-61 KB-2a fix round 1). Fence for `draft.ts`.
 *
 * P2: a save diffs the draft against the SNAPSHOT taken when the edit began,
 * never against the live page. A newer version on the server makes the edit
 * stale, and the page must not overwrite it in silence.
 * P1: a dirty draft lives in session storage, by account and page, so a view
 * that remounts gets it back.
 */
import { describe, expect, it } from "vitest";

import {
  clearDraft,
  draftKey,
  draftPatch,
  isDirty,
  isStale,
  readDraft,
  snapshotOf,
  writeDraft,
} from "./draft";

const page = {
  id: "p1",
  title: "Refunds",
  body_md: "Old body",
  versions: [{ version: 3 }, { version: 2 }, { version: 1 }],
};

class MemoryStore {
  map = new Map<string, string>();
  getItem(k: string) {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string) {
    this.map.set(k, v);
  }
  removeItem(k: string) {
    this.map.delete(k);
  }
}

describe("the snapshot of an edit", () => {
  it("keeps the page, its newest version, its title and its body", () => {
    expect(snapshotOf(page)).toEqual({ pageId: "p1", version: 3, title: "Refunds", body_md: "Old body" });
  });

  it("reads a page with no versions as version 0", () => {
    expect(snapshotOf({ ...page, versions: [] }).version).toBe(0);
  });
});

describe("isDirty", () => {
  const snap = snapshotOf(page);
  it("is false for the snapshot itself", () => {
    expect(isDirty(snap, { title: "Refunds", body_md: "Old body" })).toBe(false);
  });
  it("is true for a changed body or title, an emptied title included", () => {
    expect(isDirty(snap, { title: "Refunds", body_md: "New" })).toBe(true);
    expect(isDirty(snap, { title: "", body_md: "Old body" })).toBe(true);
  });
});

describe("draftPatch diffs against the snapshot, not the live page", () => {
  it("sends only the body the member changed", () => {
    const snap = snapshotOf(page);
    // Another member changed the title meanwhile. The live page says "Returns".
    // The member did not touch the title, so the patch must not send it back.
    expect(draftPatch(snap, { title: "Refunds", body_md: "Mine" })).toEqual({ body_md: "Mine" });
  });

  it("sends nothing when the draft is the snapshot", () => {
    expect(draftPatch(snapshotOf(page), { title: "Refunds", body_md: "Old body" })).toBeNull();
  });
});

describe("isStale", () => {
  const snap = snapshotOf(page);
  it("is false while the newest version is the one the edit began on", () => {
    expect(isStale(snap, page)).toBe(false);
  });
  it("is true when someone saved a newer version", () => {
    expect(isStale(snap, { versions: [{ version: 4 }, ...page.versions] })).toBe(true);
  });
});

describe("the stored draft", () => {
  const scope = "asha@example.com|org1";
  const snap = snapshotOf(page);
  const draft = { title: "Refunds", body_md: "Typed" };

  it("is keyed by the account and the page", () => {
    expect(draftKey(scope, "p1")).toBe("kb-draft:asha@example.com|org1:p1");
  });

  it("round-trips, and clears", () => {
    const store = new MemoryStore();
    writeDraft(store, scope, snap, draft);
    expect(readDraft(store, scope, "p1")).toEqual({ snapshot: snap, draft });
    clearDraft(store, scope, "p1");
    expect(readDraft(store, scope, "p1")).toBeNull();
  });

  it("is not read by another account or for another page", () => {
    const store = new MemoryStore();
    writeDraft(store, scope, snap, draft);
    expect(readDraft(store, "priya@example.com|org1", "p1")).toBeNull();
    expect(readDraft(store, scope, "p2")).toBeNull();
  });

  it("reads a damaged entry as nothing", () => {
    const store = new MemoryStore();
    store.setItem(draftKey(scope, "p1"), "{not json");
    expect(readDraft(store, scope, "p1")).toBeNull();
    store.setItem(draftKey(scope, "p1"), JSON.stringify({ snapshot: { pageId: "p9" }, draft }));
    expect(readDraft(store, scope, "p1")).toBeNull();
  });
});
