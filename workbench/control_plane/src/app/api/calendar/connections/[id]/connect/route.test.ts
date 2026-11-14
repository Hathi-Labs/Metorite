// WS-CAL CE-10c: Connect takes a provider word only, and passes on a safe https consent URL only.
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const GOOD = '{"url":"https://accounts.google.com/o/oauth2/v2/auth?state=s"}';
const seam = vi.hoisted(() => ({
  calls: [] as { path: string; init?: RequestInit }[],
  status: 200,
  body: "",
}));

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "dana@example.com" } }),
  isAuthEnabled: true,
  SESSION_SECRET: "calendar-test-secret-at-least-32-characters!",
}));

vi.mock("@/lib/gateway", () => ({
  proxyToGateway: async (path: string, init?: RequestInit) => {
    seam.calls.push({ path, init });
    return new Response(seam.body, { status: seam.status });
  },
}));

import { POST } from "./route";

const post = (id: string, sameSite = true, body?: string) =>
  POST(
    new NextRequest("http://localhost/api/calendar/connections/x/connect", {
      method: "POST",
      headers: sameSite ? { "sec-fetch-site": "same-origin" } : {},
      ...(body === undefined ? {} : { body }),
    }),
    { params: Promise.resolve({ id }) },
  );

beforeEach(() => {
  seam.calls.length = 0;
  seam.status = 200;
  seam.body = GOOD;
});

describe("POST /api/calendar/connections/[id]/connect", () => {
  it("forwards google and outlook, and returns the consent URL", async () => {
    for (const [word, host] of [["google", "accounts.google.com"], ["outlook", "login.microsoftonline.com"]]) {
      seam.body = JSON.stringify({ url: `https://${host}/auth?state=s` });
      const res = await post(word);
      expect(res.status).toBe(200);
      expect(((await res.json()) as { url: string }).url).toContain(`https://${host}/`);
    }
    expect(seam.calls.map((c) => c.path)).toEqual([
      "/tasks/calendar/connections/google/connect",
      "/tasks/calendar/connections/outlook/connect",
    ]);
  });

  it("refuses any other word, and sends nothing", async () => {
    for (const bad of ["microsoft", "GOOGLE", "..", "google/connect", "a?b", "", "3f1c2a9e-1b2d-4c3e-8f4a-5b6c7d8e9f01"]) {
      expect((await post(bad)).status, JSON.stringify(bad)).toBe(400);
    }
    expect(seam.calls).toEqual([]);
  });

  it("refuses a request from another site", async () => {
    expect((await post("google", false)).status).toBe(403);
    expect(seam.calls).toEqual([]);
  });

  it("never passes on a URL that is not a plain https URL", async () => {
    for (const url of [
      "http://accounts.google.com/a",
      "javascript:alert(1)",
      "https://u:p@accounts.google.com/a",
      "https://accounts.google.com.evil.test/a",
      "https://example.com/a",
      "https://login.microsoftonline.com/a",
      "",
      5,
    ]) {
      seam.body = JSON.stringify({ url });
      expect((await post("google")).status, String(url)).toBe(502);
    }
  });

  it("passes the Microsoft host for outlook only", async () => {
    seam.body = '{"url":"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?state=s"}';
    expect((await post("outlook")).status).toBe(200);
    expect((await post("google")).status).toBe(502);
  });

  it("passes a 404 through", async () => {
    seam.status = 404;
    seam.body = '{"detail":"Not Found"}';
    expect((await post("google")).status).toBe(404);
  });

  // CE-10d: the optional login hint.
  it("forwards a valid login_hint as the gateway body", async () => {
    const res = await post("google", true, '{"login_hint":"ada@example.com"}');
    expect(res.status).toBe(200);
    expect(seam.calls).toHaveLength(1);
    expect(seam.calls[0].path).toBe("/tasks/calendar/connections/google/connect");
    expect(JSON.parse(String(seam.calls[0].init?.body))).toEqual({ login_hint: "ada@example.com" });
  });

  it("sends no body for no body or an empty body", async () => {
    await post("google");
    await post("google", true, "");
    expect(seam.calls.map((c) => c.init?.body)).toEqual([undefined, undefined]);
  });

  it("refuses another key, a bad address and bad JSON, and sends nothing", async () => {
    const bad = [
      '{"login_hint":"ada@example.com","extra":1}',
      '{"other":"ada@example.com"}',
      '{"login_hint":"not an address"}',
      '{"login_hint":"a@b"}',
      `{"login_hint":"${"a".repeat(250)}@example.com"}`,
      '{"login_hint":5}',
      '{"login_hint":null}',
      "[]",
      '"x"',
      "{nope",
      "{}",
    ];
    for (const b of bad) expect((await post("google", true, b)).status, b).toBe(400);
    expect(seam.calls).toEqual([]);
  });

  it("passes a 409 and a 422 body through unchanged", async () => {
    for (const [status, detail] of [[409, "already_connected"], [422, "unknown_mailbox"]] as const) {
      seam.status = status;
      seam.body = JSON.stringify({ detail });
      const res = await post("google", true, '{"login_hint":"ada@example.com"}');
      expect(res.status).toBe(status);
      expect(await res.json()).toEqual({ detail });
    }
  });

  it("refuses a cross-site request that carries a hint", async () => {
    expect((await post("google", false, '{"login_hint":"ada@example.com"}')).status).toBe(403);
    expect(seam.calls).toEqual([]);
  });
});
