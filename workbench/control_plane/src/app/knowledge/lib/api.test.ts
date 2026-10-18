/**
 * The /knowledge client (WS-61 KB-2a and KB-3b). Fence for `api.ts`: the
 * revert payload, the BFF paths, the search request, and a refusal that
 * keeps its status.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  KnowledgeError,
  archivePage,
  createPage,
  getPage,
  listPages,
  listSpaces,
  moveBody,
  movePage,
  publishPage,
  revertBody,
  revertPage,
  searchKnowledge,
  updatePage,
} from "./api";

type Call = { url: string; init?: RequestInit };

function stubFetch(status: number, body: unknown): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the revert payload", () => {
  it("is the version number and nothing else", () => {
    expect(revertBody(1)).toEqual({ version: 1 });
    expect(revertBody(7)).toEqual({ version: 7 });
  });

  it("refuses a number the gateway would refuse (ge=1, an int)", () => {
    expect(() => revertBody(0)).toThrow(RangeError);
    expect(() => revertBody(1.5)).toThrow(RangeError);
  });

  it("posts it to the page's revert route through the BFF", async () => {
    const calls = stubFetch(200, { id: "p1", version: 4 });
    await revertPage("p1", 1);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/knowledge/pages/p1/revert");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ version: 1 });
  });
});

describe("the move payload (KB-2e)", () => {
  it("sends the place only, for a reorder", async () => {
    const calls = stubFetch(200, { id: "p1", position: 2 });
    await movePage("p1", { position: 2 });
    expect(calls[0].url).toBe("/api/knowledge/pages/p1/move");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ position: 2 });
  });

  it("sends a null parent for the root, and keeps it", async () => {
    const calls = stubFetch(200, { id: "p1", version: 3 });
    await movePage("p1", { parent_id: null, position: 0 });
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ parent_id: null, position: 0 });
  });

  it("refuses a place the gateway would refuse (ge=0, an int)", () => {
    expect(() => moveBody({ position: -1 })).toThrow(RangeError);
    expect(() => moveBody({ position: 0.5 })).toThrow(RangeError);
    expect(moveBody({ position: 0 })).toEqual({ position: 0 });
  });

  it("archives through the page's archive route", async () => {
    const calls = stubFetch(200, { id: "p1", state: "archived" });
    await archivePage("p1");
    expect(calls[0].url).toBe("/api/knowledge/pages/p1/archive");
    expect(calls[0].init?.method).toBe("POST");
  });
});

describe("the routes", () => {
  it("reads and writes through /api/knowledge, never the gateway", async () => {
    const calls = stubFetch(200, { spaces: [], pages: [] });
    await listSpaces();
    await listPages("s 1");
    await getPage("p1");
    await createPage({ title: "T" });
    await updatePage("p1", { body_md: "B" });
    await publishPage("p1");
    expect(calls.map((c) => `${c.init?.method ?? "GET"} ${c.url}`)).toEqual([
      "GET /api/knowledge/spaces",
      "GET /api/knowledge/pages?space=s%201",
      "GET /api/knowledge/pages/p1",
      "POST /api/knowledge/pages",
      "PATCH /api/knowledge/pages/p1",
      "POST /api/knowledge/pages/p1/publish",
    ]);
    expect(JSON.parse(String(calls[3].init?.body))).toEqual({ title: "T" });
    expect(JSON.parse(String(calls[4].init?.body))).toEqual({ body_md: "B" });
  });
});

describe("the search request", () => {
  it("encodes the words and asks for 20 hits, with no hybrid and no space", async () => {
    const calls = stubFetch(200, { query: "refund & 50% off", hits: [], degraded: true });
    const answer = await searchKnowledge("refund & 50% off");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/knowledge/search?q=refund%20%26%2050%25%20off&k=20");
    expect(calls[0].init?.method).toBeUndefined();
    expect(answer).toEqual({ query: "refund & 50% off", hits: [], degraded: true });
  });

  it("passes the signal through to fetch, so a newer keystroke cancels it", async () => {
    const calls = stubFetch(200, { query: "refund", hits: [], degraded: false });
    const ctrl = new AbortController();
    await searchKnowledge("refund", { signal: ctrl.signal });
    expect(calls[0].init?.signal).toBe(ctrl.signal);
  });
});

describe("a refusal", () => {
  it("keeps its status and the gateway's words", async () => {
    stubFetch(403, { detail: "Editing this page needs a write grant on its space." });
    const err = await updatePage("p1", { title: "x" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(KnowledgeError);
    expect((err as KnowledgeError).status).toBe(403);
    expect((err as KnowledgeError).message).toBe("Editing this page needs a write grant on its space.");
  });

  it("joins a validation failure into words, never [object Object]", async () => {
    stubFetch(422, { detail: [{ msg: "field required" }, { msg: "too long" }] });
    const err = (await createPage({ title: "" }).catch((e: unknown) => e)) as KnowledgeError;
    expect(err.status).toBe(422);
    expect(err.message).toBe("field required; too long");
  });
});
