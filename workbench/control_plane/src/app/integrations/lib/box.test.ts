// The pure rules of the box UI (WS-55 EA-19d). No DOM: the runner is node.
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BOX_OFF_MESSAGE,
  DAY_MS,
  EMPTY_BOX_FORM,
  NEW_ADDRESS_HINT,
  boxBody,
  boxScopeRows,
  daysProblem,
  expiryView,
  formProblem,
  isBox,
  lastUsedLabel,
  mintBoxToken,
  nameProblem,
  newAddress,
  revokeAllBoxTokens,
  showRevokeAll,
  revokedText,
  rotateBoxToken,
  toggleBoxScope,
} from "./box";

const NOW = Date.parse("2026-11-07T12:00:00Z");
const inDays = (d: number) => new Date(NOW + d * DAY_MS - 1000).toISOString();

afterEach(() => vi.unstubAllGlobals());

describe("the scope switches", () => {
  it("come from the consent copy, with no offline_access", () => {
    expect(boxScopeRows().map((r) => r.scope)).toEqual(["projects:read", "projects:write", "context:read"]);
    expect(boxScopeRows()[0].label).toBe("Read your projects and tasks");
  });

  it("start on projects read and write", () => {
    expect(EMPTY_BOX_FORM.scopes).toEqual(["projects:read", "projects:write"]);
    expect(EMPTY_BOX_FORM.days).toBe(90);
    expect(EMPTY_BOX_FORM.includePersonal).toBe(false);
  });

  it("turn read off and the others go, turn another on and read comes on", () => {
    expect(toggleBoxScope(["projects:read", "projects:write"], "projects:read")).toEqual([]);
    expect(toggleBoxScope([], "context:read")).toEqual(["projects:read", "context:read"]);
  });
});

describe("the form rules", () => {
  it("wants a name of 1 to 100 characters", () => {
    expect(nameProblem("")).not.toBeNull();
    expect(nameProblem("   ")).not.toBeNull();
    expect(nameProblem("a")).toBeNull();
    expect(nameProblem("a".repeat(100))).toBeNull();
    expect(nameProblem("a".repeat(101))).not.toBeNull();
  });

  it("wants days from 1 to 365", () => {
    for (const bad of [0, 366, 1.5, NaN]) expect(daysProblem(bad)).not.toBeNull();
    for (const good of [1, 90, 365]) expect(daysProblem(good)).toBeNull();
  });

  it("refuses a form with no read scope", () => {
    expect(formProblem({ ...EMPTY_BOX_FORM, name: "box", scopes: [] })).toBe("Choose a scope.");
    expect(formProblem({ ...EMPTY_BOX_FORM, name: "box" })).toBeNull();
  });

  it("sends only the fields of the act", () => {
    expect(boxBody({ ...EMPTY_BOX_FORM, name: "  home  " })).toEqual({
      name: "home",
      scopes: ["projects:read", "projects:write"],
      days: 90,
      include_personal: false,
    });
  });
});

describe("the row", () => {
  it("knows a box and names its last-use label", () => {
    expect(isBox({ credential: "box" })).toBe(true);
    expect(isBox({ credential: "oauth" })).toBe(false);
    expect(isBox({})).toBe(false);
    expect(lastUsedLabel({ credential: "box" })).toBe("Last used");
    expect(lastUsedLabel({ credential: "oauth" })).toBe("Last token refresh");
    expect(lastUsedLabel({})).toBe("Last token refresh");
  });

  it("colours the expiry: quiet, amber from 14 days, red from 3", () => {
    expect(expiryView(inDays(60), NOW)).toEqual({ color: null, text: "Expires in 60 days" });
    expect(expiryView(inDays(15), NOW).color).toBeNull();
    expect(expiryView(inDays(14), NOW)).toEqual({ color: "amber", text: "Expires in 14 days" });
    expect(expiryView(inDays(4), NOW).color).toBe("amber");
    expect(expiryView(inDays(3), NOW)).toEqual({ color: "red", text: "Expires in 3 days" });
    expect(expiryView(inDays(1), NOW)).toEqual({ color: "red", text: "Expires in 1 day" });
    expect(expiryView(new Date(NOW - 1).toISOString(), NOW)).toEqual({ color: "red", text: "Expired" });
    expect(expiryView("nonsense", NOW).text).toBe("");
  });

  it("marks a new address within 24 hours, for a box only", () => {
    const at = (ms: number) => new Date(NOW - ms).toISOString();
    const row = { credential: "box", last_seen_addr: "203.0.113.7", addr_changed_at: at(3600_000) };
    expect(newAddress(row, NOW)).toEqual({ addr: "203.0.113.7", hint: NEW_ADDRESS_HINT });
    expect(newAddress({ ...row, addr_changed_at: at(DAY_MS + 1000) }, NOW)).toBeNull();
    expect(newAddress({ ...row, addr_changed_at: null }, NOW)).toBeNull();
    expect(newAddress({ ...row, credential: "oauth" }, NOW)).toBeNull();
  });

  it("shows revoke all to an admin when the org holds a box", () => {
    expect(showRevokeAll(true, [{ credential: "box" } as never])).toBe(true);
    expect(showRevokeAll(false, [{ credential: "box" } as never])).toBe(false);
    expect(showRevokeAll(true, [{ credential: "oauth" } as never])).toBe(false);
    expect(showRevokeAll(true, null)).toBe(false);
  });

  it("says the revoke-all count", () => {
    expect(revokedText(0)).toBe("No box tokens to revoke");
    expect(revokedText(1)).toBe("1 box token revoked");
    expect(revokedText(4)).toBe("4 box tokens revoked");
  });
});

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const ISSUED = {
  token: "mbt_" + "A".repeat(43),
  connection_id: "c1",
  name: "home",
  scopes: ["projects:read"],
  expires_at: "2027-02-05T00:00:00Z",
};

describe("the requests", () => {
  it("mints with check first and the ticket second", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(reply(200, { ticket: "T1" }))
      .mockResolvedValueOnce(reply(200, ISSUED));
    vi.stubGlobal("fetch", fetchMock);
    const out = await mintBoxToken({ ...EMPTY_BOX_FORM, name: "home" });
    expect(out).toEqual({ ok: true, value: ISSUED });
    const [first, second] = fetchMock.mock.calls;
    expect(first[0]).toBe("/api/agent-access/box-tokens/check");
    expect(second[0]).toBe("/api/agent-access/box-tokens");
    expect(JSON.parse(second[1].body).ticket).toBe("T1");
    expect(JSON.parse(first[1].body).ticket).toBeUndefined();
    for (const [url] of fetchMock.mock.calls) expect(String(url)).not.toContain("mbt_");
  });

  it("answers off on a 404 of the check, and does not mint", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(reply(404, { error: "not_found" }));
    vi.stubGlobal("fetch", fetchMock);
    const out = await mintBoxToken({ ...EMPTY_BOX_FORM, name: "home" });
    expect(out).toEqual({ ok: false, off: true, message: BOX_OFF_MESSAGE });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("answers off on a 404 of the mint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(reply(200, { ticket: "T" })).mockResolvedValueOnce(reply(404, {})),
    );
    expect(await mintBoxToken({ ...EMPTY_BOX_FORM, name: "home" })).toMatchObject({ ok: false, off: true });
  });

  it("gives the words of another refusal, and survives a network error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(reply(422, { detail: { error: "no_scope", error_description: "Choose one scope or more." } })),
    );
    expect(await mintBoxToken({ ...EMPTY_BOX_FORM, name: "home" })).toEqual({
      ok: false,
      off: false,
      message: "Choose one scope or more.",
    });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    expect(await mintBoxToken({ ...EMPTY_BOX_FORM, name: "home" })).toMatchObject({ ok: false, off: false });
  });

  it("rotates with check, then the rotate route, with days and now", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(reply(200, { ticket: "T2", connection_id: "c1" }))
      .mockResolvedValueOnce(reply(200, ISSUED));
    vi.stubGlobal("fetch", fetchMock);
    const out = await rotateBoxToken("c1", { now: true });
    expect(out.ok).toBe(true);
    const [first, second] = fetchMock.mock.calls;
    expect(JSON.parse(first[1].body)).toEqual({ days: 90, now: true, connection_id: "c1" });
    expect(second[0]).toBe("/api/agent-access/box-tokens/c1/rotate");
    expect(JSON.parse(second[1].body)).toEqual({ days: 90, now: true, ticket: "T2" });
  });

  it("revokes all and reads the count, and a 403 is a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(reply(200, { revoked: 3 })));
    expect(await revokeAllBoxTokens()).toEqual({ ok: true, value: 3 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(reply(403, { error: "forbidden" })));
    expect(await revokeAllBoxTokens()).toMatchObject({ ok: false });
  });
});
