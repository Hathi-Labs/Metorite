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
 * Mutation 10: let `adopt` keep the old hash. "gives Mira the hash of the saved draft" turns red.
 */
import { describe, expect, it, vi } from "vitest";

import { NEWER_LINE, CLEAN, codeKey, codeValue, commitSave, countChanges, editLocked, isDirty, newerDraft, reduce, shownManifest, type EditState } from "./draftEditor";
import { SAVE_FAILED, SAVE_STALE, parseManifestText } from "./draftSave";
import { ideFacts } from "./ideFacts";
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

describe("Mira and the member together", () => {
  it("gives Mira the hash of the saved draft, so her next save names it", async () => {
    const fetchImpl = reply(200, { package: { name: "Y", manifest: m1, manifest_hash: H2 }, record_actions: { added: [], skipped: [] } });
    const r = await commitSave(ID, edited(), draft(), fetchImpl as unknown as typeof fetch);
    const facts = ideFacts({ id: ID, name: "Y" }, r.adopt?.manifest, r.adopt?.manifestHash ?? null);
    expect(facts.facts.join(" ")).toContain(`The based_on of this draft is ${H2}.`);
    expect(facts.facts.join(" ")).not.toContain(H1);
  });
});

// ── Fix round 1 ─────────────────────────────────────────────────────────────

describe("an edit while a save is in flight (P1-b)", () => {
  it("ignores an edit and Code text while saving, so the saved answer never drops one", () => {
    const saving = reduce(edited(), { type: "saving" });
    expect(reduce(saving, { type: "edit", manifest: { ...m1, purpose: "late" }, draft: draft() })).toBe(saving);
    expect(reduce(saving, { type: "code", text: "{}", draft: draft() })).toBe(saving);
  });

  it("takes edits again after the save settles", () => {
    const settled = reduce(reduce(edited(), { type: "saving" }), { type: "settled", notice: { kind: "stale", text: "x" } });
    expect(reduce(settled, { type: "edit", manifest: { ...m1, purpose: "again" }, draft: draft() }).working).toEqual({ ...m1, purpose: "again" });
  });

  it("locks the tabs while saving", () => {
    expect(editLocked(reduce(edited(), { type: "saving" }))).toBe(true);
    expect(editLocked(CLEAN)).toBe(false);
    expect(editLocked(reduce(CLEAN, { type: "code", text: "{ no", draft: draft() }))).toBe(true);
  });
});

describe("a Code edit and a newer draft (P1-c)", () => {
  const text0 = JSON.stringify(m0, null, 2);
  it("keeps the base of Code text that equals the stored draft, and drops that text when the draft moves", () => {
    const s = reduce(CLEAN, { type: "code", text: text0, draft: draft() });
    expect(s.base).toBe(H1);
    expect(codeValue(s, draft())).toBe(text0);
    expect(codeValue(s, draft({ ...m0, purpose: "Mira" }, H2))).toBeNull();
  });

  it("starts a new edit on the draft that the member sees, never on a stale base", () => {
    const stale = reduce(CLEAN, { type: "code", text: text0, draft: draft() });
    const moved = draft({ ...m0, purpose: "Mira" }, H2);
    const s = reduce(stale, { type: "code", text: JSON.stringify({ ...m0, purpose: "Mira", name: "Z" }), draft: moved });
    expect(s.base).toBe(H2);
    const e = reduce(stale, { type: "edit", manifest: { ...m1 }, draft: moved });
    expect(e.base).toBe(H2);
  });

  it("shows a newer draft for an open Code edit, and the save still names the first hash", async () => {
    const open = reduce(CLEAN, { type: "code", text: JSON.stringify(m1, null, 2), draft: draft() });
    const moved = draft({ ...m0, purpose: "Mira" }, H2);
    expect(newerDraft(open, moved)).toBe(true);
    const fetchImpl = reply(409, {});
    await commitSave(ID, open, moved, fetchImpl as unknown as typeof fetch);
    expect((fetchImpl.mock.calls[0] as unknown as [string])[0]).toContain(`based_on=${H1}`);
  });

  it("remounts the editor when the draft moves with no edit open, and keeps it while one is open (no undo into old text)", () => {
    const open = reduce(CLEAN, { type: "code", text: JSON.stringify(m1), draft: draft() });
    expect(codeKey(open, draft({ ...m0, purpose: "Mira" }, H2))).toBe(H1);
    expect(codeKey(CLEAN, draft())).toBe(H1);
    expect(codeKey(CLEAN, draft(m0, H2))).toBe(H2);
  });
});

describe("the number of changes (P2-a)", () => {
  it("counts the leaves that differ", () => {
    expect(countChanges(m0, m0)).toBe(0);
    expect(countChanges(m0, { ...m0, name: "Y" })).toBe(1);
    expect(countChanges({ a: [1, 2, 3] }, { a: [1, 9] })).toBe(2);
    expect(countChanges({ a: 1 }, { a: 1, b: { c: 1, d: 2 } })).toBe(1);
  });
});

describe("the words of a refusal (P3)", () => {
  it("keeps the server message on a 422 with no errors", async () => {
    const fetchImpl = reply(422, { detail: { message: "The body must be a JSON object.", errors: [] } });
    const r = await commitSave(ID, edited(), draft(), fetchImpl as unknown as typeof fetch);
    expect(r.action.type === "settled" && r.action.notice.text).toContain("The body must be a JSON object.");
  });

  it("says a save with no answer may have been saved, and that Reload shows the stored draft", () => {
    expect(SAVE_FAILED).toContain("may have been saved");
    expect(SAVE_FAILED).toContain("Reload");
    expect(SAVE_FAILED).not.toContain("Nothing was saved");
  });

  it("says that Reload drops the edit, in the stale line and in the newer-draft line", () => {
    expect(SAVE_STALE).toContain("Reload drops your edit");
    expect(NEWER_LINE).toContain("Reload drops your edit");
  });
});
