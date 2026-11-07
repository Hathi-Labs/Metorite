/**
 * STU-G3a fence ("Build it" flow): create a draft package, then go to its IDE, then open the dock
 * with the brief. A failed create opens nothing and says what happened.
 *
 * Mutation 1: call `go` or `openDock` before `create` finishes. "creates, then goes, then opens the dock" turns red.
 * Mutation 2: call `go` or `openDock` after a failed create. "opens nothing when the create fails" turns red.
 * Mutation 3: make `createDraftPackage` skip the POST. "posts a valid empty manifest" turns red.
 * Mutation 4: drop the page's `useAssistantFacts` or its package fact. "publishes the package facts" in build/page.test.ts turns red.
 */
import { describe, expect, it, vi } from "vitest";

import { ideFacts, PACKAGE_FACT } from "./ideFacts";
import {
  CREATE_FAILED, CREATE_LIMIT, buildHref, createDraftPackage, emptyManifest, nameFromBrief,
} from "./packageDraft";
import { appBrief } from "./packageLibrary";
import { StudioOff } from "./realDoor";
import { NOT_ON_LINE, startBuild } from "./startBuild";

const ID = "123e4567-e89b-12d3-a456-426614174000";

describe("startBuild", () => {
  it("creates, then goes, then opens the dock", async () => {
    const order: string[] = [];
    const r = await startBuild("track loans", {
      create: async (brief) => { order.push(`create:${brief}`); return { id: ID, name: "Track loans" }; },
      go: (href) => order.push(`go:${href}`),
      openDock: (draft) => order.push(`dock:${draft}`),
    });
    expect(r).toEqual({ ok: true, id: ID, href: `/studio/build?pkg=${ID}` });
    expect(order).toEqual([
      `create:${appBrief("track loans")}`, `go:/studio/build?pkg=${ID}`, `dock:${appBrief("track loans")}`,
    ]);
  });

  it("opens nothing when the create fails, and says what happened", async () => {
    const go = vi.fn();
    const openDock = vi.fn();
    const r = await startBuild("x", { create: async () => { throw new Error(CREATE_LIMIT); }, go, openDock });
    expect(r).toEqual({ ok: false, message: CREATE_LIMIT });
    expect(go).not.toHaveBeenCalled();
    expect(openDock).not.toHaveBeenCalled();
  });

  it("names the off switch in plain words", async () => {
    const r = await startBuild("x", { create: async () => { throw new StudioOff(); }, go: vi.fn(), openDock: vi.fn() });
    expect(r).toEqual({ ok: false, message: NOT_ON_LINE });
  });

  it("gives a sentence with the next step for a create that fails", () => {
    for (const line of [CREATE_FAILED, CREATE_LIMIT, NOT_ON_LINE]) expect(line).toMatch(/\. [A-Z]/);
  });
});

describe("createDraftPackage", () => {
  const reply = (status: number, body: unknown = {}) =>
    (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

  it("posts a valid empty manifest and returns the id", async () => {
    let seen: { url: string; init: RequestInit } | null = null;
    const f = (async (url: string, init: RequestInit) => {
      seen = { url, init };
      return new Response(JSON.stringify({ package: { id: ID, name: "Track loans" } }), { status: 201 });
    }) as unknown as typeof fetch;
    expect(await createDraftPackage("Build me an app: track loans", f)).toEqual({ id: ID, name: "Track loans" });
    expect(seen!.url).toBe("/api/studio/packages");
    expect(seen!.init.method).toBe("POST");
    const body = JSON.parse(String(seen!.init.body));
    expect(body).toMatchObject({ studio: 1, name: "Track loans", objects: [], actions: [], views: [], workflows: [] });
    expect(body.id).toMatch(/^[a-z][a-z0-9_-]{0,47}$/);
  });

  it("throws a sentence for each failure", async () => {
    await expect(createDraftPackage("x", reply(404))).rejects.toThrow("studio_off");
    await expect(createDraftPackage("x", reply(409))).rejects.toThrow(CREATE_LIMIT);
    await expect(createDraftPackage("x", reply(500))).rejects.toThrow(CREATE_FAILED);
    await expect(createDraftPackage("x", reply(201, {}))).rejects.toThrow(CREATE_FAILED);
    await expect(createDraftPackage("x", (async () => { throw new TypeError("net"); }) as unknown as typeof fetch)).rejects.toThrow(CREATE_FAILED);
  });
});

describe("the new manifest", () => {
  it("takes a name from the first words of the brief", () => {
    expect(nameFromBrief("Build me an app: a board that shows every printer service history today")).toBe("A board that shows every");
    expect(nameFromBrief(appBrief(""))).toBe("I want to build an");
    expect(nameFromBrief("   ")).toBe("New app");
  });
  it("makes a unique, valid id", () => {
    const a = emptyManifest("Build me an app: Loans", () => 0.1);
    const b = emptyManifest("Build me an app: Loans", () => 0.9);
    expect(a.id).not.toBe(b.id);
    expect(String(a.id)).toMatch(/^[a-z][a-z0-9_-]{0,47}$/);
    expect(String(emptyManifest("123 !!!").id)).toMatch(/^[a-z]/);
  });
  it("builds the href of the IDE", () => {
    expect(buildHref(ID)).toBe(`/studio/build?pkg=${ID}`);
    expect(buildHref(ID, "visit")).toBe(`/studio/build?pkg=${ID}&object=visit`);
  });
});

describe("ideFacts", () => {
  it("names the package id and the draft, as data", () => {
    const m = { studio: 1, id: "a", name: "A «x»", purpose: "p" };
    const f = ideFacts({ id: ID, name: "A «x»" }, m);
    expect(f.label).toBe("A x");
    expect(f.facts[0]).toBe(`${PACKAGE_FACT} ${ID}, and its name is «A x».`);
    expect(f.facts[1]).toContain("make no new package");
    expect(f.facts[2]).toContain(JSON.stringify(m).replace(/«/g, "\\u00ab").replace(/»/g, "\\u00bb"));
    expect(f.facts.every((x) => !x.includes("\n"))).toBe(true);
  });
  it("does not paste a draft that a chat cannot hold", () => {
    const f = ideFacts({ id: ID, name: "A" }, { purpose: "x".repeat(25_000) });
    expect(f.facts[2]).toMatch(/over the size/);
    expect(f.facts[2].length).toBeLessThan(300);
  });
  it("keeps a guillemet in the draft from ending the data fence", () => {
    const f = ideFacts({ id: ID, name: "A" }, { purpose: "a » b « c" });
    expect(f.facts[2]).not.toMatch(/[«»]/);
    expect(JSON.parse(f.facts[2].split("JSON: ")[1])).toEqual({ purpose: "a » b « c" });
  });
});
