import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll, peek } from "@/lib/dataCache";
import { orgLayoutKey, parseOrgLayout, type OrgLayout } from "./orgLayout";
import { STARRABLE } from "./presets";
import {
  MAX_PINS,
  readOrgLayoutRaw,
  refreshShellDefault,
  resetRole,
  saveOrgLayout,
  setRole,
  toBody,
} from "./orgLayoutAdmin";

/**
 * PKG-17c-1 (`package_host.md` §9.1). The admin read keeps a fault apart from
 * an empty default. The PUT body fits the gateway model (`extra="forbid"`).
 */

const [A, B, C] = [...STARRABLE];
const reply = (status: number, body: unknown = {}) => () =>
  Promise.resolve(new Response(typeof body === "string" ? body : JSON.stringify(body), { status }));
const stub = (r: () => Promise<Response>) => {
  const f = vi.fn<(u: RequestInfo | URL, i?: RequestInit) => Promise<Response>>(() => r());
  vi.stubGlobal("fetch", f);
  return f;
};

beforeEach(() => clearAll());
afterEach(() => vi.unstubAllGlobals());

describe("readOrgLayoutRaw", () => {
  it("reads a 404 as off", async () => {
    stub(reply(404));
    expect(await readOrgLayoutRaw()).toEqual({ kind: "off" });
  });

  it.each([500, 502, 401, 403])("reads a %i as a fault, never as empty", async (status) => {
    stub(reply(status, { roles: {} }));
    expect(await readOrgLayoutRaw()).toEqual({ kind: "fault" });
  });

  it("reads a network error as a fault", async () => {
    stub(() => Promise.reject(new TypeError("fetch failed")));
    expect(await readOrgLayoutRaw()).toEqual({ kind: "fault" });
  });

  it.each(["not json", "[]", "null", "{}", '{"roles":null}'])("reads the body %s as a fault", async (body) => {
    stub(reply(200, body));
    expect(await readOrgLayoutRaw()).toEqual({ kind: "fault" });
  });

  it("reads an empty 200 as ok with empty true", async () => {
    stub(reply(200, { roles: {}, updatedBy: "", updatedAt: "" }));
    expect(await readOrgLayoutRaw()).toEqual({ kind: "ok", layout: { roles: {} }, empty: true });
  });

  it("reads a default as ok with empty false, and writes no cache", async () => {
    stub(reply(200, { roles: { member: { preset: null, pins: [A] } } }));
    const got = await readOrgLayoutRaw();
    expect(got).toEqual({ kind: "ok", layout: { roles: { member: { preset: null, pins: [A] } } }, empty: false });
    expect(peek(orgLayoutKey())).toBeUndefined();
  });
});

describe("toBody", () => {
  it("drops updatedBy and updatedAt", () => {
    const layout = { ...parseOrgLayout({ roles: { member: { pins: [A] } } }), updatedBy: "x", updatedAt: "y" };
    expect(Object.keys(toBody(layout as OrgLayout))).toEqual(["roles"]);
  });

  it("drops a pin outside STARRABLE and a repeat", () => {
    const layout: OrgLayout = { roles: { member: { preset: null, pins: [A, "/nope", A, B] } } };
    expect(toBody(layout).roles.member?.pins).toEqual([A, B]);
  });

  it("sends an untouched role back unchanged, and leaves out a role with no entry", () => {
    const layout: OrgLayout = { roles: { admin: { preset: "engineer", pins: [C] }, member: { preset: null, pins: [A] } } };
    const edited = setRole(layout, "member", { preset: null, pins: [B] });
    expect(toBody(edited).roles.admin).toEqual(layout.roles.admin);
    expect(toBody(edited).roles.member).toEqual({ preset: null, pins: [B] });
    expect(Object.keys(toBody(edited).roles).sort()).toEqual(["admin", "member"]);
  });

  it("caps the pins at the gateway's MAX_PINS", () => {
    expect(MAX_PINS).toBe(64);
    const many = Array.from({ length: MAX_PINS + 5 }, () => A);
    // One pane repeats, so the cap shows with the real count of panes too.
    const panes = [...STARRABLE];
    const layout: OrgLayout = { roles: { member: { preset: null, pins: [...many, ...panes] } } };
    expect((toBody(layout).roles.member?.pins ?? []).length).toBeLessThanOrEqual(MAX_PINS);
  });

  it("drops an unknown preset id", () => {
    const layout = { roles: { member: { preset: "ceo", pins: [A] } } } as unknown as OrgLayout;
    expect(toBody(layout).roles.member).toEqual({ preset: null, pins: [A] });
  });

  it("keeps an empty list of pins (it is a choice)", () => {
    expect(toBody({ roles: { member: { preset: null, pins: [] } } }).roles.member).toEqual({ preset: null, pins: [] });
  });
});

describe("setRole and resetRole", () => {
  it("sets a role and resets it", () => {
    const one = setRole({ roles: {} }, "guest", { preset: null, pins: [A] });
    expect(one.roles.guest?.pins).toEqual([A]);
    expect(resetRole(one, "guest").roles).toEqual({});
  });

  it("removes a role when the entry has nothing", () => {
    const one = setRole({ roles: { guest: { preset: null, pins: [A] } } }, "guest", { preset: null, pins: null });
    expect(one.roles).toEqual({});
  });
});

describe("saveOrgLayout", () => {
  it("sends the whole document with PUT and returns the saved layout", async () => {
    const f = stub(reply(200, { roles: { member: { preset: null, pins: [A] } }, updatedBy: "a@x.test", updatedAt: "t" }));
    const got = await saveOrgLayout({ roles: { member: { preset: null, pins: [A] } } });
    expect(got).toEqual({ kind: "saved", layout: { roles: { member: { preset: null, pins: [A] } } } });
    const init = f.mock.calls[0][1];
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ roles: { member: { preset: null, pins: [A] } } });
  });

  it("maps a 422 to an error with the reason", async () => {
    stub(reply(422, { detail: "unknown preset: ceo" }));
    expect(await saveOrgLayout({ roles: {} })).toEqual({ kind: "error", status: 422, reason: "unknown preset: ceo" });
  });

  it("maps a 404 to an error", async () => {
    stub(reply(404, { error: "layout_unavailable" }));
    const got = await saveOrgLayout({ roles: {} });
    expect(got.kind).toBe("error");
    expect(got.kind === "error" && got.status).toBe(404);
  });

  it("maps a network error to an error with status 0", async () => {
    stub(() => Promise.reject(new TypeError("fetch failed")));
    const got = await saveOrgLayout({ roles: {} });
    expect(got.kind === "error" && got.status).toBe(0);
  });
});

describe("refreshShellDefault", () => {
  it("puts the layout under the key of the shell", () => {
    const layout: OrgLayout = { roles: { member: { preset: null, pins: [A] } } };
    refreshShellDefault(layout);
    expect(peek(orgLayoutKey())?.data).toEqual(layout);
  });
});
