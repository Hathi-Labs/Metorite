// The ready apps client (STU-K1b). A 404 hides the section, a 409 opens the
// package that the member has, and the click sends the template id.
// Mutation: send a wrong id, or treat a 409 as an error, and a test fails.
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadTemplatesView, startFromTemplate } from "@/lib/studio/templatesApi";

afterEach(() => vi.unstubAllGlobals());

function answer(status: number, body: unknown) {
  const f = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", f);
  return f;
}

describe("the template list", () => {
  it("lists names and purposes on a 200", async () => {
    const t = { id: "field-visits", name: "Field visits", purpose: "Log visits." };
    answer(200, { templates: [t] });
    expect(await loadTemplatesView()).toEqual({ kind: "ok", templates: [t] });
  });
  it("hides on a 404", async () => {
    answer(404, { detail: "Not found" });
    expect(await loadTemplatesView()).toEqual({ kind: "off" });
  });
  it("hides on an empty list", async () => {
    answer(200, { templates: [] });
    expect(await loadTemplatesView()).toEqual({ kind: "off" });
  });
  it("hides on a network miss", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("x"); }));
    expect(await loadTemplatesView()).toEqual({ kind: "off" });
  });
});

describe("startFromTemplate", () => {
  it("posts the template id and opens the new package", async () => {
    const f = answer(201, { package: { id: "p1" } });
    const r = await startFromTemplate("field-visits");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/studio/packages/from-template");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ template: "field-visits" }));
    expect(r).toEqual({ ok: true, href: "/studio/package-preview?pkg=p1" });
  });
  it("opens the existing package on a 409 package_exists", async () => {
    answer(409, { detail: { code: "package_exists", package_id: "p9", message: "You have it." } });
    expect(await startFromTemplate("x")).toEqual({ ok: true, href: "/studio/package-preview?pkg=p9" });
  });
  it("shows the one sentence of the server on an error", async () => {
    answer(503, { detail: { code: "store_busy", message: "Studio is busy. Try again soon." } });
    expect(await startFromTemplate("x")).toEqual({ ok: false, message: "Studio is busy. Try again soon." });
  });
  it("shows the sentence of a 422", async () => {
    answer(422, { detail: "Unknown template." });
    expect(await startFromTemplate("x")).toEqual({ ok: false, message: "Unknown template." });
  });
});
