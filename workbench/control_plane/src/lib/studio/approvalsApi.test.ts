// The client of the approval routes (J2b-4b). Mutation: change a path or a verb
// in approvalsApi.ts and the matching test fails.
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";

import { approveVersion, listApprovals, refusalOf, rejectVersion } from "./approvalsApi";

afterEach(() => vi.unstubAllGlobals());

function answer(status: number, body: unknown) {
  const f = vi.fn(async (...args: [string, RequestInit?]) => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", f);
  return f;
}

describe("the approval client", () => {
  it("lists the pending versions", async () => {
    const f = answer(200, { state: "pending", approvals: [], truncated: false });
    const r = await listApprovals();
    expect(f.mock.calls[0][0]).toBe("/api/studio/approvals?state=pending");
    expect(r.ok && r.data.approvals).toEqual([]);
  });
  it("approves by POST, with no body", async () => {
    const f = answer(200, { package_id: "p", version: 2, review_status: "approved" });
    const r = await approveVersion("p", 2);
    expect(f.mock.calls[0][0]).toBe("/api/studio/packages/p/versions/2/approval/approve");
    expect(f.mock.calls[0][1]?.method).toBe("POST");
    expect(f.mock.calls[0][1]?.body).toBeUndefined();
    expect(r.ok).toBe(true);
  });
  it("rejects by POST, with no reason", async () => {
    const f = answer(200, { package_id: "p", version: 2, review_status: "rejected" });
    await rejectVersion("p", 2);
    expect(f.mock.calls[0][0]).toBe("/api/studio/packages/p/versions/2/approval/reject");
    expect(f.mock.calls[0][1]?.method).toBe("POST");
  });
  it("shows the server sentence of a 403 as it is", async () => {
    answer(403, { detail: "Only a holder of studio:publish reads the apps." });
    const r = await listApprovals();
    expect(r).toEqual({ ok: false, status: 403, detail: "Only a holder of studio:publish reads the apps." });
  });
  it("reads the sentence and the code of a 409", async () => {
    answer(409, { detail: { code: "review_conflict", message: "This version is approved already." } });
    const r = await approveVersion("p", 1);
    expect(r).toEqual({ ok: false, status: 409, detail: "This version is approved already.", code: "review_conflict" });
  });
  it("answers a value on a network miss", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("x"); }));
    expect(await listApprovals()).toEqual({ ok: false, status: 0, detail: "The server did not answer. Try again." });
  });
  it("falls back to a plain sentence", () => {
    expect(refusalOf(null, 500).detail).toBe("The request failed (500).");
  });
});

describe("the page source", () => {
  const src = readFileSync(new URL("../../app/studio/approvals/page.tsx", import.meta.url), "utf8");
  it("draws the manifest as text, never as HTML", () => {
    expect(src).not.toMatch(/dangerouslySetInnerHTML|MarkdownMessage|SandboxedHtml/);
  });
  it("asks through ConfirmDialog, and the buttons come from standing()", () => {
    expect(src).toContain("ConfirmDialog");
    expect(src).toMatch(/who\.act \? \(/);
  });
  it("draws the cut note from the model", () => {
    expect(src).toContain("raw.note");
  });
});
