// The /integrations client (IN-2) against a stubbed fetch.
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";

function stub(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("getConnections", () => {
  it("returns the rows", async () => {
    stub(200, { connections: [{ id: "a" }] });
    expect(await api.getConnections()).toEqual([{ id: "a" }]);
  });
  it("returns null on a 404: the read model is not built yet", async () => {
    stub(404, { detail: "Not Found" });
    expect(await api.getConnections()).toBeNull();
  });
  it("throws on a 403 with the server's words", async () => {
    stub(403, { detail: "Forbidden" });
    await expect(api.getConnections()).rejects.toMatchObject({ status: 403, message: "Forbidden" });
  });
});

describe("disconnect", () => {
  it("posts to the row's route", async () => {
    const fn = stub(200, { removed: true });
    await api.disconnect("a/b");
    expect(fn).toHaveBeenCalledWith("/api/integrations/connections/a%2Fb/disconnect", { method: "POST" });
  });
  it("keeps the 409 code and the setup link", async () => {
    stub(409, { detail: { code: "member_connection", setup_href: "/email" } });
    await expect(api.disconnect("m")).rejects.toMatchObject({
      status: 409,
      code: "member_connection",
      setupHref: "/email",
    });
  });
});

describe("testMcp", () => {
  it("sends no headers and no body: the server tests with the stored headers", async () => {
    const fn = stub(200, { ok: true, tools: 3, error: null });
    expect(await api.testMcp("brave search")).toEqual({ ok: true, tools: 3, error: null });
    expect(fn).toHaveBeenCalledWith("/api/integrations/mcp/brave%20search/test", { method: "POST" });
  });
});

describe("addMcp", () => {
  it("posts the body, and throws the guard's message on a refusal", async () => {
    const fn = stub(422, { detail: "Private addresses are refused." });
    await expect(api.addMcp({ name: "x" })).rejects.toMatchObject({
      status: 422,
      message: "Private addresses are refused.",
    });
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
