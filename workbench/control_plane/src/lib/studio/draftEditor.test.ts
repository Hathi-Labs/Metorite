/**
 * STU-G3c fence: the one save, the dirty state, and the guards of an edit.
 *
 * Mutation 1: send the PUT with no `based_on`, or with the hash of the NEW draft. "sends the hash the edit began from" turns red.
 * Mutation 2: treat a 409 as a failure that drops the edit. "keeps the edit on a 409" turns red.
 * Mutation 3: let a 422 write no Problems. "puts each 422 error at its path" turns red.
 * Mutation 4: let `commitSave` send text that does not parse. "refuses JSON that does not parse, with the line" turns red.
 * Mutation 5: let a newer draft replace the working copy. "keeps the working copy when the stored draft moves" turns red.
 * Mutation 6: let `base` move on each edit. "keeps the first hash as the base" turns red.
 * Mutation 7: drop the `fault` guard from the `edit` case. "locks the other tabs while the Code text is broken" turns red.
 * Mutation 8: keep the working copy after a save. "returns to clean after a save" turns red.
 * Mutation 9: call a manifest equal to the stored one an edit. "is no edit when the manifest is the stored one" turns red.
 */
import { describe, expect, it, vi } from "vitest";

import { CLEAN, commitSave, isDirty, newerDraft, reduce, shownManifest, type EditState } from "./draftEditor";
import { SAVE_FAILED, SAVE_STALE, parseManifestText } from "./draftSave";
import type { PackageDraft } from "./packageDraft";

const ID = "123e4567-e89b-12d3-a456-426614174000";
const H1 = "a".repeat(64);
const H2 = "b".repeat(64);
const m0 = { studio: 1, id: "x", name: "X", purpose: "p", objects: [] };
const m1 = { ...m0, name: "Y" };
const draft = (manifest: unknown = m0, hash: string | null = H1): PackageDraft => ({
  id: ID, name: "X", manifest, liveVersion: null, manifestHash: hash, objects: [],
});
const reply = (status: number, body: unknown) => vi.fn(async () => new Response(JSON.stringify(body), { status }));
const edited = (): EditState => reduce(CLEAN, { type: "edit", manifest: m1, draft: draft() });

describe("dirty state", () => {
  it("is no edit when the manifest is the stored one", () => {
    const s = reduce(CLEAN, { type: "edit", manifest: { ...m0 }, draft: draft() });
    expect(s).toEqual(CLEAN);
    expect(isDirty(s, draft())).toBe(false);
  });

  it("is dirty after an edit, and draws the working copy", () => {
    const s = edited();
    expect(isDirty(s, draft())).toBe(true);
    expect(shownManifest(s, draft())).toBe(m1);
    expect(shownManifest(CLEAN, draft())).toBe(m0);
  });

  it("keeps the first hash as the base", () => {
    let s = edited();
    expect(s.base).toBe(H1);
    s = reduce(s, { type: "edit", manifest: { ...m1, purpose: "q" }, draft: draft(m0, H2) });
    expect(s.base).toBe(H1);
  });

  it("keeps the working copy when the stored draft moves, and says a newer draft exists", () => {
    const s = edited();
    const moved = draft({ ...m0, purpose: "Mira wrote this" }, H2);
    expect(shownManifest(s, moved)).toBe(m1);
    expect(isDirty(s, moved)).toBe(true);
    expect(newerDraft(s, moved)).toBe(true);
    expect(newerDraft(s, draft())).toBe(false);
    expect(newerDraft(CLEAN, moved)).toBe(false);
  });

  it("returns to clean after a save and after a revert", () => {
    expect(reduce(edited(), { type: "saved", notice: { kind: "saved", text: "ok" } })).toEqual({ ...CLEAN, notice: { kind: "saved", text: "ok" } });
    expect(reduce(edited(), { type: "revert" })).toEqual(CLEAN);
  });

  it("locks the other tabs while the Code text is broken", () => {
    const broken = reduce(CLEAN, { type: "code", text: "{ nope", draft: draft() });
    expect(broken.fault?.line).toBe(1);
    expect(isDirty(broken, draft())).toBe(true);
    expect(reduce(broken, { type: "edit", manifest: m1, draft: draft() })).toBe(broken);
  });

  it("takes Code text that parses as the working copy, and keeps the text", () => {
    const text = JSON.stringify(m1, null, 2);
    const s = reduce(CLEAN, { type: "code", text, draft: draft() });
    expect(s.working).toEqual(m1);
    expect(s.code).toBe(text);
    expect(s.fault).toBeNull();
    // The stored text in other words is no edit, and the text stays so the cursor does not jump.
    const same = reduce(s, { type: "code", text: JSON.stringify(m0), draft: draft() });
    expect(isDirty(same, draft())).toBe(false);
    expect(same.code).toBe(JSON.stringify(m0));
  });
});

describe("the JSON of the Code tab", () => {
  it("names the line of a fault", () => {
    const r = parseManifestText('{\n  "a": 1,\n  "b": ,\n}');
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.fault.line).toBe(3);
      expect(r.fault.message).toContain("Line 3");
      expect(r.fault.message).toContain("Fix it");
    }
  });

  it("refuses a value that is not one object", () => {
    for (const text of ["[]", "3", "null", '"x"', ""]) expect(parseManifestText(text).ok).toBe(false);
    expect(parseManifestText('{"a":1}').ok).toBe(true);
  });
});

describe("the one save", () => {
  it("sends the hash the edit began from, as a PUT to the draft route", async () => {
    const fetchImpl = reply(200, { package: { name: "Y", manifest: m1, manifest_hash: H2, live_version: null }, record_actions: { added: [], skipped: [] } });
    // The stored draft moved on (hash H2), the edit began from H1.
    const r = await commitSave(ID, edited(), draft(m0, H2), fetchImpl as unknown as typeof fetch);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/studio/packages/${ID}/draft?based_on=${H1}`);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual(m1);
    expect(r.action.type).toBe("saved");
    expect(r.adopt?.manifestHash).toBe(H2);
    expect(r.adopt?.manifest).toEqual(m1);
  });

  it("names the record actions that Studio added", async () => {
    const fetchImpl = reply(200, { package: { manifest: m1, manifest_hash: H2 }, record_actions: { added: ["a_list", "a_get"], skipped: [] } });
    const r = await commitSave(ID, edited(), draft(), fetchImpl as unknown as typeof fetch);
    expect(r.action.type === "saved" && r.action.notice.text).toContain("2 record actions");
  });

  it("keeps the edit on a 409, and says that the draft changed and nothing was saved", async () => {
    const fetchImpl = reply(409, { detail: { code: "draft_changed" } });
    const s = edited();
    const r = await commitSave(ID, s, draft(), fetchImpl as unknown as typeof fetch);
    expect(r.adopt).toBeUndefined();
    expect(r.action).toEqual({ type: "settled", notice: { kind: "stale", text: SAVE_STALE } });
    expect(SAVE_STALE).toContain("Mira or another tab saved it");
    expect(SAVE_STALE).toContain("Nothing was saved");
    expect(SAVE_STALE).toContain("Reload");
    const after = reduce(s, r.action);
    expect(after.working).toBe(m1);
    expect(isDirty(after, draft())).toBe(true);
  });

  it("puts each 422 error at its path", async () => {
    const fetchImpl = reply(422, { detail: { message: "x", errors: [
      { path: "$.objects[0].fields[1].key", message: "Bad key." }, { path: "$.views[0]", message: "No object." },
    ] } });
    const r = await commitSave(ID, edited(), draft(), fetchImpl as unknown as typeof fetch);
    expect(r.action.type).toBe("settled");
    if (r.action.type === "settled") {
      expect(r.action.server).toEqual([
        { severity: "error", path: "$.objects[0].fields[1].key", message: "Bad key." },
        { severity: "error", path: "$.views[0]", message: "No object." },
      ]);
      expect(r.action.notice.text).toContain("2 problems are in the Problems panel");
    }
  });

  it("refuses JSON that does not parse, with the line, and sends nothing", async () => {
    const fetchImpl = vi.fn();
    const s = reduce(CLEAN, { type: "code", text: '{\n"a": }', draft: draft() });
    const r = await commitSave(ID, s, draft(), fetchImpl as unknown as typeof fetch);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(r.action.type === "settled" && r.action.notice.kind).toBe("json");
    expect(r.action.type === "settled" && r.action.notice.text).toContain("Line 2");
  });

  it("says nothing was saved when the network fails, and when the base is unknown", async () => {
    const down = vi.fn(async () => { throw new TypeError("x"); });
    const r = await commitSave(ID, edited(), draft(), down as unknown as typeof fetch);
    expect(r.action).toEqual({ type: "settled", notice: { kind: "failed", text: SAVE_FAILED } });
    const noBase = reduce(CLEAN, { type: "edit", manifest: m1, draft: draft(m0, null) });
    const fetchImpl = vi.fn();
    const r2 = await commitSave(ID, noBase, draft(m0, null), fetchImpl as unknown as typeof fetch);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(r2.action.type === "settled" && r2.action.notice.text).toContain("Nothing was saved");
  });

  it("says the app is gone on a 404", async () => {
    const r = await commitSave(ID, edited(), draft(), reply(404, {}) as unknown as typeof fetch);
    expect(r.action.type === "settled" && r.action.notice.text).toContain("not available");
  });
});
