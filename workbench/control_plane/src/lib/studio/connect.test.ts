// WS-STU I8a, Connect in place: the pure model of the lines of the IDE, and the one guard of the
// return path (`ai_studio.md` §8.1 and §14 I8a).
//
// R7: M1, return "connect" for a row with `canConnect: false`. "a member who may not connect sees
// one sentence and no button" goes red. M2, drop the `/studio/` check of `safeStudioReturn`. "the
// guard of return" goes red. M3, build the link from a map of app paths. "the link is the server's
// setup_href" goes red.
import { describe, expect, it, vi } from "vitest";

import {
  CONNECTIONS_READ_FAILED,
  connectHref,
  connectLines,
  getConnections,
  missingLine,
  parseConnections,
  safeStudioReturn,
  sheetCopy,
  unmetCount,
  type ConnectionRow,
} from "./connect";

const PKG = "0f8e2a4c-1b3d-4e5f-8a9b-0c1d2e3f4a5b";
const BACK = encodeURIComponent(`/studio/build?pkg=${PKG}`);

const row = (over: Partial<ConnectionRow> = {}): ConnectionRow => ({
  name: "zoho",
  label: "Zoho CRM",
  met: false,
  setupHref: "/crm",
  canConnect: true,
  ...over,
});

describe("the lines of the draft", () => {
  it("keeps the order of the draft, one line for each name", () => {
    const lines = connectLines([row(), row({ name: "mcp:tally", label: "tally" }), row({ name: "github" })], PKG);
    expect(lines.map((l) => l.row.name)).toEqual(["zoho", "mcp:tally", "github"]);
  });

  it("a met connection reads connected, with no button", () => {
    const [line] = connectLines([row({ met: true })], PKG);
    expect(line).toMatchObject({ kind: "met", text: "Zoho CRM is connected." });
  });

  it("an unmet connection has Connect, and the link is the server's setup_href with the way back", () => {
    const [line] = connectLines([row()], PKG);
    expect(line.kind).toBe("connect");
    expect(line).toMatchObject({ text: "This app needs Zoho CRM.", href: `/crm?return=${BACK}` });
  });

  it("a member who may not connect sees one sentence and no button", () => {
    const [line] = connectLines([row({ canConnect: false })], PKG);
    expect(line).toEqual({
      kind: "ask",
      row: row({ canConnect: false }),
      text: "An admin must connect Zoho CRM. Ask one, then come back.",
    });
    expect("href" in line).toBe(false);
  });

  it("an MCP server reads as the MCP server <name>", () => {
    const tally = row({ name: "mcp:tally", label: "tally", setupHref: "/integrations?add=mcp&name=tally" });
    expect(connectLines([tally], PKG)[0].text).toBe("This app needs the MCP server tally.");
    expect(connectLines([{ ...tally, met: true }], PKG)[0].text).toBe("The MCP server tally is connected.");
    expect(connectLines([{ ...tally, canConnect: false }], PKG)[0].text).toBe(
      "An admin must connect the MCP server tally. Ask one, then come back.",
    );
  });

  it("an admin and a member differ only by can_connect, which the gateway decides", () => {
    const admin = connectLines([row({ canConnect: true })], PKG)[0];
    const member = connectLines([row({ canConnect: false })], PKG)[0];
    expect([admin.kind, member.kind]).toEqual(["connect", "ask"]);
  });

  it("counts the names that are not met for the header", () => {
    expect(unmetCount([row(), row({ met: true })])).toBe(1);
    expect(missingLine([row({ met: true })])).toBeNull();
    expect(missingLine([row()])).toBe("1 connection missing");
    expect(missingLine([row(), row({ name: "github" })])).toBe("2 connections missing");
    expect(missingLine([])).toBeNull();
  });
});

describe("the link is the server's setup_href", () => {
  it("keeps the query of the owning app and adds the return", () => {
    expect(connectHref({ setupHref: "/calendar?settings=1" }, PKG)).toBe(
      `/calendar?settings=1&return=${BACK}`,
    );
    expect(connectHref({ setupHref: "/integrations?add=mcp&name=tally" }, PKG)).toBe(
      `/integrations?add=mcp&name=tally&return=${BACK}`,
    );
  });

  it("gives no link for a value that is not a path of this site", () => {
    for (const bad of [null, "", "crm", "//evil.example/crm", "/\\evil.example", "https://evil.example/crm",
      "javascript:alert(1)", "/\t/evil.example"]) {
      expect(connectHref({ setupHref: bad }, PKG)).toBeNull();
    }
  });

  it("an unmet line with no safe link still says what it needs, and the sheet holds no link", () => {
    const [line] = connectLines([row({ setupHref: null })], PKG);
    expect(line).toMatchObject({ kind: "connect", href: null });
  });
});

describe("the guard of return", () => {
  it("passes a path of this site under /studio/", () => {
    expect(safeStudioReturn(`/studio/build?pkg=${PKG}`)).toBe(`/studio/build?pkg=${PKG}`);
    expect(safeStudioReturn("/studio/build?pkg=x#data")).toBe("/studio/build?pkg=x#data");
    expect(safeStudioReturn("/studio/package-preview?pkg=x")).toBe("/studio/package-preview?pkg=x");
  });

  it("drops each other value", () => {
    for (const bad of [
      null, undefined, 42, "", "/", "/studio", "/studiox/build", "/admin", "/integrations",
      "studio/build", "//evil.example/studio/build", "/\\evil.example/studio/build",
      "https://evil.example/studio/build", "http://localhost/studio/build", "javascript:alert(1)",
      "/studio/../admin", "/studio/%2e%2e/admin", "/studio/%2E%2E/settings", "/\t/evil.example",
      "/studio/\nbuild",
    ]) {
      expect(safeStudioReturn(bad), String(bad)).toBeNull();
    }
  });
});

describe("the words of the sheet", () => {
  it("an MCP server opens Integrations with its name, and comes back", () => {
    const copy = sheetCopy(row({ name: "mcp:tally", label: "tally" }));
    expect(copy.title).toBe("Connect tally");
    expect(copy.action).toBe("Open Integrations");
    expect(copy.body).toContain("server name tally");
    expect(copy.body).toContain("brings you back to this app");
  });

  it("a provider opens the connect page of its app", () => {
    const copy = sheetCopy(row());
    expect(copy).toEqual({
      title: "Connect Zoho CRM",
      body: "Metorite opens the page where Zoho CRM connects. When you finish there, come back to this app.",
      action: "Open the connect page",
    });
  });
});

describe("the read", () => {
  it("parses the answer and trusts none of it", () => {
    const rows = parseConnections({
      connections: [
        { name: "zoho", label: "Zoho CRM", met: true, setup_href: "/crm", can_connect: false },
        { name: "mcp:tally", label: "", met: "yes", setup_href: "https://evil.example", can_connect: 1 },
        { label: "no name" },
        "zoho",
      ],
    });
    expect(rows).toEqual([
      { name: "zoho", label: "Zoho CRM", met: true, setupHref: "/crm", canConnect: false },
      { name: "mcp:tally", label: "mcp:tally", met: false, setupHref: null, canConnect: false },
    ]);
    expect(parseConnections(null)).toEqual([]);
    expect(parseConnections({ connections: "x" })).toEqual([]);
  });

  it("reads the one route, and a 404 (the gate off) gives no line", async () => {
    const ok = vi.fn(async () => new Response(JSON.stringify({ connections: [
      { name: "github", label: "GitHub", met: false, setup_href: "/integrations", can_connect: true },
    ] }), { status: 200 }));
    expect(await getConnections(PKG, ok as unknown as typeof fetch)).toHaveLength(1);
    expect(ok).toHaveBeenCalledWith(`/api/studio/packages/${PKG}/connections`);
    const off = vi.fn(async () => new Response("{}", { status: 404 }));
    expect(await getConnections(PKG, off as unknown as typeof fetch)).toEqual([]);
    const down = vi.fn(async () => new Response("{}", { status: 503 }));
    await expect(getConnections(PKG, down as unknown as typeof fetch)).rejects.toThrow(CONNECTIONS_READ_FAILED);
  });
});
