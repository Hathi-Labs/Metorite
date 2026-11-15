// The client of /studio/mcp-tools (B1b-4). R7 mutations, each run once:
//  - `approveTool` sends a hash that the client made   -> "sends the hash the row showed" fails.
//  - `approveTool` skips the grammar                   -> "builds no URL for a bad name" fails.
import { afterEach, describe, expect, it, vi } from "vitest";

import { approveTool, listMcpTools, refreshServer, refusalOf } from "./mcpToolsApi";

const H = "b".repeat(64);
const reply = (status: number, body: unknown) =>
  vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));

afterEach(() => vi.unstubAllGlobals());

describe("approveTool", () => {
  it("posts the shown hash to the encoded path", async () => {
    const f = reply(200, { server: "docs", tool: "search", changed: false, class: "A" });
    vi.stubGlobal("fetch", f);
    const r = await approveTool("docs", "search", H);
    expect(r.ok).toBe(true);
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/studio/mcp/docs/tools/search/approve");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ expected_hash: H });
  });
  it("builds no URL for a bad name or a bad hash", async () => {
    const f = reply(200, {});
    vi.stubGlobal("fetch", f);
    expect((await approveTool("docs", "../x", H)).ok).toBe(false);
    expect((await approveTool("Docs", "x", H)).ok).toBe(false);
    expect((await approveTool("docs", "x", "ABC")).ok).toBe(false);
    expect(f).not.toHaveBeenCalled();
  });
  it("answers the 409 code and the server's own sentence", async () => {
    vi.stubGlobal("fetch", reply(409, { detail: { code: "mcp_tool_text_changed", message: "It changed." } }));
    expect(await approveTool("docs", "x", H)).toEqual({
      ok: false, status: 409, detail: "It changed.", code: "mcp_tool_text_changed",
    });
  });
  it("never throws when the network fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    expect((await approveTool("docs", "x", H)).ok).toBe(false);
  });
});

describe("refreshServer and listMcpTools", () => {
  it("posts the refresh to the encoded server", async () => {
    const f = reply(200, { tools: 2, truncated: false, items: [] });
    vi.stubGlobal("fetch", f);
    expect((await refreshServer("docs")).ok).toBe(true);
    expect(f.mock.calls[0][0]).toBe("/api/studio/mcp/docs/refresh");
    expect((await refreshServer("a/b")).ok).toBe(false);
    expect(f).toHaveBeenCalledTimes(1);
  });
  it("reads the catalog and keeps MCP rows", async () => {
    vi.stubGlobal("fetch", reply(200, {
      actions: [{ source: "mcp", server: "docs", mcp_tool: "x", class: "B", changed: true, text_hash: H }, { source: "door" }],
      stale: false,
    }));
    const r = await listMcpTools();
    expect(r.ok && r.data.tools.length).toBe(1);
    expect(r.ok && r.data.stale).toBe(false);
  });
  it("a missing stale reads as stale", async () => {
    vi.stubGlobal("fetch", reply(200, { actions: [] }));
    const r = await listMcpTools();
    expect(r.ok && r.data.stale).toBe(true);
  });
  it("refusalOf reads a string and an object", () => {
    expect(refusalOf({ detail: "No." }, 403).detail).toBe("No.");
    expect(refusalOf(null, 500).detail).toBe("The request failed (500).");
  });
});
