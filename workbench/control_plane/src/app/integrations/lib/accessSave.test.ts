// ACC-2 (task 3988): the writes of "Who can use". The gateway PUT has no
// version check, so a save reads the stored rule first. These tests drive the
// real cache and a stubbed fetch.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll, peek, put, read } from "@/lib/dataCache";

import { ACCESS_KEY, clearAccessRule, keepSavedRule, saveAccessRule } from "./accessSave";
import { ruleBody, type AccessItem, type AccessList } from "./whoCanUse";

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

const other = (over: Partial<AccessItem> = {}) => item({ service: "mcp-linear", ...over });
const list = (...services: AccessItem[]): AccessList => ({ services, readable: true });
const cached = () => peek<AccessList>(ACCESS_KEY)?.data;
const ADMINS = ruleBody("admins", [], []);

interface Sent {
  method: string;
  url: string;
  body: unknown;
}

/** A gateway: `stored` answers the GET, and `onPut` runs before the PUT answers. */
function gateway(
  stored: AccessList,
  opts: { getStatus?: number; putStatus?: number; onPut?: () => void } = {},
): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    sent.push({ method, url, body });
    if (method === "PUT") {
      opts.onPut?.();
      if (opts.putStatus) return Response.json({ detail: "Not now." }, { status: opts.putStatus });
      const service = decodeURIComponent(url.split("/").pop()!);
      return Response.json(item({ service, ...(body as Partial<AccessItem>) }));
    }
    if (opts.getStatus) return Response.json({}, { status: opts.getStatus });
    return Response.json(stored);
  });
  return sent;
}

const methods = (sent: Sent[]) => sent.map((s) => s.method);

beforeEach(() => clearAll());
afterEach(() => vi.unstubAllGlobals());

describe("a save", () => {
  it("reads the stored rule, then writes, and draws the saved rule", async () => {
    put(ACCESS_KEY, list(item()));
    const sent = gateway(list(item()));
    const out = await saveAccessRule(item(), ADMINS);
    expect(out).toEqual({ kind: "saved", item: item({ mode: "admins" }) });
    expect(methods(sent)).toEqual(["GET", "PUT"]);
    expect(sent[1].body).toEqual(ADMINS);
    expect(cached()?.services).toEqual([item({ mode: "admins" })]);
  });

  it("writes nothing when the stored rule is not the one that the admin saw", async () => {
    // Another admin narrowed the rule. A save of "everyone" would widen it.
    put(ACCESS_KEY, list(item()));
    const now = item({ mode: "admins" });
    const sent = gateway(list(now));
    const out = await saveAccessRule(item(), ruleBody("everyone", [], []));
    expect(out).toEqual({ kind: "changed", current: now });
    expect(methods(sent)).toEqual(["GET"]);
    // The list on screen takes the news.
    expect(cached()?.services).toEqual([now]);
  });

  it("writes when only the order of the names or the stamp moved", async () => {
    const seen = item({ mode: "chosen", groups: ["a", "b"] });
    put(ACCESS_KEY, list(seen));
    const sent = gateway(list(item({ mode: "chosen", groups: ["b", "a"], set_at: "later" })));
    expect((await saveAccessRule(seen, ADMINS)).kind).toBe("saved");
    expect(methods(sent)).toEqual(["GET", "PUT"]);
  });

  it("replaces an unreadable rule, but not a rule that replaced it", async () => {
    const broken = item({ mode: "admins", readable: false });
    put(ACCESS_KEY, list(broken));
    gateway(list(broken));
    expect((await saveAccessRule(broken, ADMINS)).kind).toBe("saved");
    const sent = gateway(list(item({ mode: "admins" })));
    expect((await saveAccessRule(broken, ADMINS)).kind).toBe("changed");
    expect(methods(sent)).toEqual(["GET"]);
  });

  it("writes nothing when the service left the list, or the read answers 404 or 403", async () => {
    for (const sent of [gateway(list(other())), gateway(list(), { getStatus: 404 })]) {
      expect(await saveAccessRule(item(), ADMINS)).toEqual({ kind: "gone" });
      expect(methods(sent)).not.toContain("PUT");
    }
    const refused = gateway(list(), { getStatus: 403 });
    expect(await saveAccessRule(item(), ADMINS)).toEqual({ kind: "gone" });
    expect(methods(refused)).toEqual(["GET"]);
  });

  it("writes nothing when the read fails", async () => {
    const sent = gateway(list(item()), { getStatus: 502 });
    await expect(saveAccessRule(item(), ADMINS)).rejects.toThrow("Could not read");
    expect(methods(sent)).toEqual(["GET"]);
  });

  it("keeps the cache of a refused write", async () => {
    put(ACCESS_KEY, list(item()));
    gateway(list(item()), { putStatus: 409 });
    await expect(saveAccessRule(item(), ADMINS)).rejects.toThrow("Not now.");
    expect(cached()?.services).toEqual([item()]);
  });
});

describe("the list after a save", () => {
  it("keeps the news of another service that landed during the save", async () => {
    put(ACCESS_KEY, list(item(), other()));
    const news = other({ mode: "admins" });
    gateway(list(item(), other()), { onPut: () => put(ACCESS_KEY, list(item(), news)) });
    await saveAccessRule(item(), ADMINS);
    expect(cached()?.services).toEqual([item({ mode: "admins" }), news]);
  });

  it("does not let a read that started before the save replace it", async () => {
    put(ACCESS_KEY, list(item()));
    let land: (l: AccessList) => void = () => undefined;
    const old = read<AccessList>(ACCESS_KEY, () => new Promise((r) => (land = r)), { force: true });
    keepSavedRule(item({ mode: "admins" }));
    land(list(item()));
    await old;
    expect(cached()?.services).toEqual([item({ mode: "admins" })]);
  });

  it("reads again when another write dropped the cached list", async () => {
    const sent = gateway(list(item({ mode: "admins" })));
    keepSavedRule(item({ mode: "admins" }));
    await vi.waitFor(() => expect(cached()?.services).toEqual([item({ mode: "admins" })]));
    expect(methods(sent)).toEqual(["GET"]);
  });

  it("stays null for an org off the flag", () => {
    put(ACCESS_KEY, null);
    keepSavedRule(item());
    expect(cached()).toBeNull();
  });
});

describe("Set to Everyone", () => {
  const unlisted = item({ service: "mcp-old", kind: "", mode: "admins", listed: false });

  it("sends everyone when the rule is the one in the confirm", async () => {
    put(ACCESS_KEY, list(unlisted));
    const sent = gateway(list(unlisted));
    expect((await clearAccessRule(unlisted)).kind).toBe("saved");
    expect(methods(sent)).toEqual(["GET", "PUT"]);
    expect(sent[1].url).toContain("/mcp-old");
    expect(sent[1].body).toEqual({ mode: "everyone", groups: [], people: [] });
  });

  it("clears nothing when the rule changed after the confirm opened", async () => {
    const sent = gateway(list({ ...unlisted, mode: "chosen", people: ["a@x.test"] }));
    expect((await clearAccessRule(unlisted)).kind).toBe("changed");
    expect(methods(sent)).toEqual(["GET"]);
  });
});
