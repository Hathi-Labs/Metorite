// The member's "Follow up" setting (task 4002).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { fetchFollowUpsOn, followUpsOn, requestFollowUps } from "./followUpPref";

const reply = (status: number, body: unknown) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
const exchange = { userMessage: "hi", assistantMessage: "hello", agentName: "orchestrator" };

describe("followUpsOn", () => {
  it("is off with no stored value, and for each value but true", () => {
    for (const raw of [null, {}, { apps: {} }, { apps: { chat: { summaries: true } } },
      { apps: { chat: { follow_up_suggestions: false } } },
      { apps: { chat: { follow_up_suggestions: "true" } } }]) {
      expect(followUpsOn(raw)).toBe(false);
    }
  });

  it("is on only with a stored true", () => {
    expect(followUpsOn({ apps: { chat: { follow_up_suggestions: true } } })).toBe(true);
  });
});

describe("fetchFollowUpsOn", () => {
  it("reads the profile route", async () => {
    const f = reply(200, { apps: { chat: { follow_up_suggestions: true } } });
    expect(await fetchFollowUpsOn(f)).toBe(true);
    expect(vi.mocked(f).mock.calls[0][0]).toBe("/api/auth/me/assistant-profile");
  });

  it("throws on a fault, so no guess is cached", async () => {
    await expect(fetchFollowUpsOn(reply(503, { detail: "unavailable" }))).rejects.toThrow("503");
  });
});

describe("requestFollowUps", () => {
  it("sends no request while the setting is off", async () => {
    const f = reply(200, { suggestions: ["a"] });
    expect(await requestFollowUps(false, exchange, undefined, f)).toEqual([]);
    expect(f).not.toHaveBeenCalled();
  });

  it("asks the model when the setting is on", async () => {
    const f = reply(200, { suggestions: ["a", "b"] });
    expect(await requestFollowUps(true, exchange, undefined, f)).toEqual(["a", "b"]);
    const [url, init] = vi.mocked(f).mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/chat/suggestions");
    expect(JSON.parse(String(init.body))).toEqual(exchange);
  });

  it("answers none for a body with no list", async () => {
    expect(await requestFollowUps(true, exchange, undefined, reply(401, { error: "no" }))).toEqual([]);
  });
});

describe("AgentChat takes the setting", () => {
  const src = readFileSync(join(__dirname, "../components/AgentChat.tsx"), "utf-8");

  it("has no second door to the suggestions route", () => {
    expect(src).not.toContain('fetch("/api/chat/suggestions"');
    expect(src).toMatch(/requestFollowUps\(\s*followUps,/);
  });

  it("stops the effect before it marks the answer as asked", () => {
    expect(src).toMatch(
      /if \(!followUps \|\| isLoading\) return;[\s\S]{0,400}lastSuggestedForRef\.current = last\.id/,
    );
  });

  it("draws the Follow up segment only when the setting is on", () => {
    expect(src).toMatch(/\{followUps && !isLoading/);
    expect(src.match(/label="Follow up"/g)).toHaveLength(1);
  });
});
