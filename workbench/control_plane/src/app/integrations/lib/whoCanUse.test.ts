// ACC-2 (task 3988): the console rules of "Who can use", and the calls.
// Spec: integrations_console.md §3.9.2 to §3.9.5.
import { afterEach, describe, expect, it, vi } from "vitest";

import { getAccessGroups, getAccessPeople, getIntegrationAccess, setIntegrationAccess } from "./api";
import { row } from "./testRows";
import {
  LOAD_FAILED,
  clearCopy,
  emptyChoice,
  filterChoices,
  groupChoices,
  parseAccessList,
  personChoices,
  ruleBody,
  ruleSummary,
  rowRule,
  sameRule,
  saveLabel,
  serviceOfRow,
  toggleValue,
  unmatchedRules,
  withItem,
  type AccessItem,
  type AccessList,
} from "./whoCanUse";

const item = (over: Partial<AccessItem> = {}): AccessItem => ({
  service: "mcp-tally",
  kind: "mcp",
  mode: "everyone",
  groups: [],
  people: [],
  set_by: "",
  set_at: "",
  listed: true,
  readable: true,
  ...over,
});

const list = (services: AccessItem[], readable = true): AccessList => ({ services, readable });

const mcp = row({
  id: "mcp:tally",
  kind: "mcp",
  provider: "mcp",
  label: "Tally",
  owning_app: "integrations",
  detail: { name: "tally" },
});
const books = row({ id: "api:zoho_books:7", provider: "api:zoho_books", label: "Zoho Books" });

describe("which row may take a rule (§3.9.2)", () => {
  it("names an MCP server and an API connection of the organization", () => {
    expect(serviceOfRow(mcp)).toBe("mcp-tally");
    expect(serviceOfRow(books)).toBe("api-zoho_books");
  });

  it("gives a built-in connector, a mailbox, a number and a key no service", () => {
    for (const r of [
      row(),
      row({ provider: "github" }),
      row({ kind: "mailbox", provider: "gmail", scope: "member" }),
      row({ kind: "whatsapp", provider: "whatsapp", scope: "member" }),
      row({ id: "key:serpapi", kind: "api_key", provider: "serpapi" }),
      row({ provider: "api:" }),
    ]) {
      expect(serviceOfRow(r)).toBeNull();
    }
  });

  it("gives a member connection no service (§3.9.3)", () => {
    expect(serviceOfRow(row({ provider: "api:zoho_books", scope: "member" }))).toBeNull();
  });

  it("draws a control only for a service that the gateway lists", () => {
    const l = list([item(), item({ service: "api-zoho_books", kind: "api", listed: false })]);
    expect(rowRule(l, mcp)?.service).toBe("mcp-tally");
    expect(rowRule(l, books)).toBeNull();
    expect(rowRule(list([]), mcp)).toBeNull();
    expect(rowRule(l, row())).toBeNull();
  });

  it("draws nothing for an org off the flag, a refused viewer, or while it loads", () => {
    expect(rowRule(null, mcp)).toBeNull();
    expect(rowRule(undefined, mcp)).toBeNull();
    expect(unmatchedRules(null, [])).toEqual([]);
    expect(unmatchedRules(undefined, [])).toEqual([]);
  });
});

describe("a stored rule with no row", () => {
  const gone = item({ service: "mcp-old", kind: "", mode: "admins", listed: false });
  const key = item({ service: "serpapi", kind: "", mode: "chosen", people: ["a@x.test"], listed: false });
  const idle = item({ service: "api-zoho_books", kind: "api", mode: "admins" });

  it("shows each unlisted rule, and a listed rule that no row draws", () => {
    const l = list([item({ mode: "admins" }), gone, key, idle]);
    expect(unmatchedRules(l, [mcp]).map((i) => i.service)).toEqual(["mcp-old", "serpapi", "api-zoho_books"]);
  });

  it("leaves out a service with no rule, and a rule that its row shows", () => {
    const l = list([item({ mode: "admins" }), item({ service: "api-zoho_books", kind: "api" })]);
    expect(unmatchedRules(l, [mcp])).toEqual([]);
  });

  it("shows none while the whole value cannot be read", () => {
    expect(unmatchedRules(list([gone, idle], false), [])).toEqual([]);
  });

  it("says that a cleared rule cannot come back from this screen", () => {
    const copy = clearCopy(gone);
    expect(copy.title).toBe("Set mcp-old to Everyone?");
    expect(copy.body).toContain("cannot restore");
    expect(copy.confirmLabel).toBe("Set to Everyone");
  });
});

describe("the words of a rule", () => {
  it("says Everyone for no rule", () => {
    expect(ruleSummary(item())).toBe("Everyone");
  });

  it("says Admins only for an empty rule", () => {
    expect(ruleSummary(item({ mode: "admins" }))).toBe("Admins only");
    expect(ruleSummary(item({ mode: "chosen" }))).toBe("Admins only");
  });

  it("counts the teams and the people", () => {
    expect(ruleSummary(item({ mode: "chosen", groups: ["sales"] }))).toBe("1 team");
    expect(ruleSummary(item({ mode: "chosen", people: ["a@x.test", "b@x.test"] }))).toBe("2 people");
    expect(
      ruleSummary(item({ mode: "chosen", groups: ["sales", "ops"], people: ["a@x.test"] })),
    ).toBe("2 teams and 1 person");
  });

  it("says when a rule cannot be read", () => {
    expect(ruleSummary(item({ mode: "admins", readable: false }))).toBe(
      "Admins only (unreadable rule)",
    );
    expect(ruleSummary(item({ mode: "everyone" }), false)).toBe("Admins only");
  });
});

describe("the editor", () => {
  it("warns before a save of chosen with nothing picked", () => {
    expect(emptyChoice("chosen", [], [])).toBe(true);
    expect(emptyChoice("chosen", ["sales"], [])).toBe(false);
    expect(emptyChoice("admins", [], [])).toBe(false);
    expect(saveLabel("chosen", [], [])).toBe("Save as admins only");
    expect(saveLabel("everyone", [], [])).toBe("Save");
  });

  it("sends names with chosen only, because the gateway refuses them elsewhere", () => {
    expect(ruleBody("chosen", ["b", "a"], ["z@x.test", "a@x.test"])).toEqual({
      mode: "chosen",
      groups: ["a", "b"],
      people: ["a@x.test", "z@x.test"],
    });
    expect(ruleBody("everyone", ["a"], ["a@x.test"])).toEqual({ mode: "everyone", groups: [], people: [] });
    expect(ruleBody("admins", ["a"], ["a@x.test"])).toEqual({ mode: "admins", groups: [], people: [] });
  });

  it("knows a save that changes nothing", () => {
    const stored = item({ mode: "chosen", groups: ["ops", "sales"] });
    expect(sameRule(stored, ruleBody("chosen", ["sales", "ops"], []))).toBe(true);
    expect(sameRule(stored, ruleBody("chosen", ["sales"], []))).toBe(false);
    expect(sameRule(item({ mode: "admins" }), ruleBody("chosen", [], []))).toBe(true);
    expect(sameRule(item(), ruleBody("everyone", [], []))).toBe(true);
    expect(sameRule(item(), ruleBody("admins", [], []))).toBe(false);
    // A rule that cannot be read always takes a save, which replaces it.
    expect(sameRule(item({ mode: "admins", readable: false }), ruleBody("admins", [], []))).toBe(false);
  });

  it("adds and drops one pick", () => {
    expect(toggleValue(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleValue(["a", "b"], "a")).toEqual(["b"]);
  });

  it("keeps a picked team that left the list, and says so", () => {
    const out = groupChoices([{ slug: "sales", display_name: "Sales", member_count: 1 }], ["gone"], true);
    expect(out).toEqual([
      { value: "gone", label: "gone", hint: "This team does not exist now" },
      { value: "sales", label: "Sales", hint: "1 member" },
    ]);
    expect(groupChoices([], ["gone"], false)[0].hint).toBe("");
  });

  it("keeps a picked address that is not in the member list", () => {
    const out = personChoices(
      [{ email: "Priya@x.test", display_name: "Priya Rao", status: "invited" }, { email: "b@x.test" }],
      ["old@x.test", "priya@x.test"],
      true,
    );
    expect(out).toEqual([
      { value: "old@x.test", label: "old@x.test", hint: "Not in the member list" },
      { value: "priya@x.test", label: "Priya Rao", hint: "priya@x.test · Invited" },
      { value: "b@x.test", label: "b@x.test", hint: "" },
    ]);
  });

  it("searches by name and by address", () => {
    const out = personChoices([{ email: "pr@x.test", display_name: "Priya" }, { email: "ravi@x.test" }], [], true);
    expect(filterChoices(out, "priy").map((o) => o.value)).toEqual(["pr@x.test"]);
    expect(filterChoices(out, "RAVI@").map((o) => o.value)).toEqual(["ravi@x.test"]);
    expect(filterChoices(out, "  ")).toHaveLength(2);
    expect(filterChoices(out, "nobody")).toEqual([]);
  });

  it("puts a saved item in place of the old one", () => {
    const next = withItem(list([item(), item({ service: "mcp-b" })]), item({ mode: "admins" }));
    expect(next.services.map((i) => [i.service, i.mode])).toEqual([
      ["mcp-tally", "admins"],
      ["mcp-b", "everyone"],
    ]);
    expect(withItem(list([]), item()).services).toHaveLength(1);
  });
});

describe("the answer of the gateway", () => {
  it("drops a shape that it cannot draw, and reads a missing list as empty", () => {
    expect(parseAccessList({ rows: [] })).toEqual({ services: [], readable: true });
    expect(parseAccessList({ services: [{ service: "mcp-a" }, { mode: "admins" }, 7] }).services).toEqual([]);
    expect(parseAccessList({ services: [], readable: false }).readable).toBe(false);
  });

  it("keeps listed false with an empty kind", () => {
    const [i] = parseAccessList({
      services: [{ service: "serpapi", kind: "", mode: "admins", listed: false }],
    }).services;
    expect(i).toMatchObject({ service: "serpapi", kind: "", listed: false, readable: true });
  });
});

function reply(status: number, body: unknown) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("the calls", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads 404 (off the flag) and 403 (no right) as no control", async () => {
    reply(404, { detail: "Not Found" });
    await expect(getIntegrationAccess()).resolves.toBeNull();
    reply(403, { detail: "Forbidden: missing permission 'admin:access:manage'." });
    await expect(getIntegrationAccess()).resolves.toBeNull();
  });

  it("throws on another failure, with the server's words or a plain line", async () => {
    reply(502, { detail: "Gateway unreachable." });
    await expect(getIntegrationAccess()).rejects.toThrow("Gateway unreachable.");
    reply(500, {});
    await expect(getIntegrationAccess()).rejects.toThrow(LOAD_FAILED);
  });

  it("reads the list", async () => {
    const fetch = reply(200, { services: [item()], readable: true, source: "integration-rule" });
    await expect(getIntegrationAccess()).resolves.toEqual(list([item()]));
    expect(fetch).toHaveBeenCalledWith("/api/admin/integration-access", { cache: "no-store" });
  });

  it("sends the PUT of one service with the three keys only", async () => {
    const fetch = reply(200, item({ mode: "admins" }));
    await setIntegrationAccess("mcp-tally", ruleBody("admins", [], []));
    expect(fetch).toHaveBeenCalledWith("/api/admin/integration-access/mcp-tally", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "admins", groups: [], people: [] }),
    });
  });

  it.each([
    [422, "Not a member of this organization: ['old@x.test']."],
    [409, "A rule of who can use an integration names the team 'sales'. Change that rule first."],
    [403, "Forbidden: missing permission 'integrations:manage'."],
    [500, "The rule was not saved."],
  ])("keeps the words of a %i", async (status, detail) => {
    reply(status, { detail });
    await expect(setIntegrationAccess("mcp-tally", ruleBody("admins", [], []))).rejects.toMatchObject({
      message: detail,
      status,
    });
  });

  it("says what to do when a refusal has no words", async () => {
    reply(500, {});
    await expect(setIntegrationAccess("mcp-tally", ruleBody("admins", [], []))).rejects.toThrow(
      "Metorite could not save the rule. Try again.",
    );
  });

  it("reads the teams and the people from the admin routes", async () => {
    let fetch = reply(200, [{ slug: "sales" }]);
    await expect(getAccessGroups()).resolves.toEqual([{ slug: "sales" }]);
    expect(fetch).toHaveBeenCalledWith("/api/admin/groups", { cache: "no-store" });
    fetch = reply(200, [{ email: "a@x.test" }]);
    await expect(getAccessPeople()).resolves.toEqual([{ email: "a@x.test" }]);
    expect(fetch).toHaveBeenCalledWith("/api/admin/members", { cache: "no-store" });
    reply(200, { rows: [] });
    await expect(getAccessGroups()).rejects.toThrow("Could not read the teams.");
    reply(500, {});
    await expect(getAccessPeople()).rejects.toThrow("Could not read the people.");
  });
});
