// The model of /studio/approvals (J2b-4b). R7 mutations, each run once:
//  - `standing` returns act:true for the owner      -> "the owner sees no button" fails.
//  - `manifestText` drops the cut note              -> "a long manifest keeps the start and says so" fails.
//  - `manifestText` keeps the whole text            -> the same test fails.
//  - `clip` keeps control characters                -> "a control character becomes a space" fails.
import { describe, expect, it } from "vitest";

import {
  CUT_NOTE,
  MAX_MANIFEST_CHARS,
  NO_ACT_OWNER,
  NO_ACT_PUBLISHER,
  clip,
  confirmCopy,
  manifestText,
  parseSelection,
  pickRow,
  selectionHref,
  standing,
  summarizeManifest,
  type PendingApproval,
} from "./approvalsModel";

const ID = "0b7e4d2a-1c3f-4a5b-8c9d-0e1f2a3b4c5d";
const row = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  package_id: ID, name: "Orders", owner_email: "owen@x.io", version: 2, published_by: "pia@x.io",
  published_at: "2026-11-01T00:00:00Z", requested_by: "owen@x.io", requested_at: "2026-11-02T00:00:00Z",
  manifest: {}, ...over,
});

describe("the selection", () => {
  it("reads ?pkg and ?v", () => {
    expect(parseSelection(ID, "2")).toEqual({ pkg: ID, v: 2 });
    expect(selectionHref(ID, 2)).toBe(`/studio/approvals?pkg=${ID}&v=2`);
  });
  it("refuses anything that is not a uuid and a whole version", () => {
    expect(parseSelection("x", "2")).toBeNull();
    expect(parseSelection(ID, "0")).toBeNull();
    expect(parseSelection(ID, "1.5")).toBeNull();
    expect(parseSelection(ID, null)).toBeNull();
    expect(parseSelection(null, "1")).toBeNull();
  });
  it("picks the row by id and version", () => {
    const rows = [row(), row({ version: 3 })];
    expect(pickRow(rows, { pkg: ID, v: 3 })).toBe(rows[1]);
    expect(pickRow(rows, { pkg: ID, v: 9 })).toBeNull();
    expect(pickRow(rows, null)).toBeNull();
  });
});

describe("who may act", () => {
  it("the owner sees no button", () => {
    const s = standing(row(), "Owen@x.io");
    expect(s).toEqual({ act: false, reason: NO_ACT_OWNER });
  });
  it("the publisher sees no button", () => {
    expect(standing(row(), "pia@x.io")).toEqual({ act: false, reason: NO_ACT_PUBLISHER });
  });
  it("another member acts", () => {
    expect(standing(row(), "ada@x.io")).toEqual({ act: true });
  });
  it("an unknown viewer is not the owner, and the server still decides", () => {
    expect(standing(row({ owner_email: "" }), null)).toEqual({ act: true });
  });
});

describe("the summary", () => {
  it("lists objects, fields, actions, connections and pages as plain text", () => {
    const s = summarizeManifest({
      objects: [{ name: "order", label: "Orders", fields: [{ key: "total", type: "money" }] }],
      actions: [{ id: "order_close", title: "Close" }],
      connections: ["mcp:crm", { name: "gmail", as: "member" }],
      pages: [{ id: "home", title: "Home" }],
    });
    expect(s.objects[0]).toEqual({ name: "Orders", fields: [{ key: "total", type: "money" }], moreFields: 0 });
    expect(s.actions).toEqual([{ id: "order_close", title: "Close" }]);
    expect(s.connections).toEqual(["mcp:crm", "gmail"]);
    expect(s.pages).toEqual([{ id: "home", title: "Home" }]);
  });
  it("survives a manifest of the wrong shape", () => {
    expect(summarizeManifest(null).objects).toEqual([]);
    expect(summarizeManifest("x").pages).toEqual([]);
    expect(summarizeManifest({ objects: "no", actions: 4 }).actions).toEqual([]);
  });
  it("counts what the list cap hides", () => {
    const actions = Array.from({ length: 45 }, (_, i) => ({ id: `a${i}` }));
    const s = summarizeManifest({ actions });
    expect(s.actions).toHaveLength(40);
    expect(s.moreActions).toBe(5);
  });
  it("a control character becomes a space, and a long name is cut", () => {
    expect(clip("a\u0000b\nc")).toBe("a b c");
    expect(clip("x".repeat(200))).toHaveLength(120);
    expect(clip("x".repeat(200)).endsWith("...")).toBe(true);
  });
  it("a bidi override or a zero-width mark becomes a space, so text cannot reorder what follows", () => {
    expect(clip("a\u202eb\u2066c\u200bd")).toBe("a b c d");
  });
  it("keeps markup as text and does not read it", () => {
    const s = summarizeManifest({ actions: [{ id: "<img src=x onerror=1>", title: "**do this**" }] });
    expect(s.actions[0]).toEqual({ id: "<img src=x onerror=1>", title: "**do this**" });
  });
});

describe("the raw manifest", () => {
  it("a short manifest shows whole, with no note", () => {
    const t = manifestText({ studio: 1, id: "a" });
    expect(t.cut).toBe(false);
    expect(t.note).toBeNull();
    expect(JSON.parse(t.text)).toEqual({ studio: 1, id: "a" });
  });
  it("a long manifest keeps the start and says so", () => {
    const t = manifestText({ purpose: "p".repeat(MAX_MANIFEST_CHARS + 500) });
    expect(t.cut).toBe(true);
    expect(t.text).toHaveLength(MAX_MANIFEST_CHARS);
    expect(t.note).toBe(CUT_NOTE);
    expect(t.length).toBeGreaterThan(MAX_MANIFEST_CHARS);
  });
});

describe("the words of the dialogs", () => {
  it("name the app and the version, and each act", () => {
    expect(confirmCopy("approve", { name: "Orders", version: 2 }).confirmLabel).toBe("Approve");
    expect(confirmCopy("reject", { name: "Orders", version: 2 }).body).toContain("version 2");
  });
});
