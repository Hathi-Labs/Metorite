// The "Get to know me" offer in the empty chat (WS-63 PZ-27).
//
// Mutations (R7): make `shouldOffer` ignore `started_at`, and the started
// case goes red. Send `draft` instead of `text` in `startGetToKnowMe`, and the
// Yes case goes red. Drop the POST from "No thanks", and the decline case goes red.
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

const openAssistant = vi.fn();
vi.mock("@/lib/shell/assistant", () => ({ openAssistant: (o: unknown) => openAssistant(o) }));

import { startInterview } from "@/app/settings/assistant/lib/profile";
import { GET_TO_KNOW_ME_MESSAGE, startGetToKnowMe } from "@/lib/shell/interviewMessage";
import { OFFER_HIDDEN_KEY, hiddenThisSession, shouldOffer } from "./InterviewOffer";

const fresh = { learning: true };

describe("shouldOffer", () => {
  it("offers to a member who has not started, in an empty orchestrator chat", () => {
    expect(shouldOffer(fresh, true, 0, true)).toBe(true);
    expect(shouldOffer({ learning: true, interview: { areas: {} } }, true, 0, true)).toBe(true);
  });
  it("does not offer once the interview started or was declined", () => {
    expect(shouldOffer({ learning: true, interview: { started_at: "2026-10-25T08:00:00Z", areas: {} } }, true, 0, true)).toBe(false);
  });
  it("does not offer when learning is off", () => {
    expect(shouldOffer({ learning: false }, true, 0, true)).toBe(false);
  });
  it("does not offer when the profile is off for the org", () => {
    expect(shouldOffer(fresh, false, 0, true)).toBe(false);
  });
  it("does not offer outside the orchestrator", () => {
    expect(shouldOffer(fresh, true, 0, false)).toBe(false);
  });
  it("does not offer in a shared room", () => {
    expect(shouldOffer(fresh, true, 0, true, true)).toBe(false);
    expect(shouldOffer(fresh, true, 0, true, false)).toBe(true);
  });
  it("does not offer once the chat has a message", () => {
    expect(shouldOffer(fresh, true, 1, true)).toBe(false);
  });
  it("does not offer before the profile has loaded", () => {
    expect(shouldOffer(undefined, true, 0, true)).toBe(false);
    expect(shouldOffer(null, true, 0, true)).toBe(false);
  });
});

describe("the three answers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("Yes sends the request as the member's turn in a fresh chat", () => {
    startGetToKnowMe();
    expect(openAssistant).toHaveBeenCalledWith({ fresh: true, text: GET_TO_KNOW_ME_MESSAGE });
    const sent = openAssistant.mock.calls.at(-1)?.[0] as Record<string, unknown>;
    expect(sent).not.toHaveProperty("draft");
  });

  it("No thanks posts /interview with no body", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ started_at: "2026-10-25T08:00:00Z" }), { status: 200 });
    }));
    await startInterview();
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/auth/me/assistant-profile/interview");
    expect(calls[0].init?.method).toBe("POST");
    expect(calls[0].init?.body).toBeUndefined();
  });

  it("Not now hides for the session only", () => {
    const mem = new Map<string, string>();
    const store = { getItem: (k: string) => mem.get(k) ?? null };
    expect(hiddenThisSession(store)).toBe(false);
    mem.set(OFFER_HIDDEN_KEY, "1");
    expect(hiddenThisSession(store)).toBe(true);
  });

  it("the component wires the X to sessionStorage and No thanks to startInterview", () => {
    const src = readFileSync(new URL("./InterviewOffer.tsx", import.meta.url), "utf-8");
    expect(src).toMatch(/aria-label="Not now"[\s\S]*?hideForSession\(\)/);
    expect(src).toMatch(/await startInterview\(\)/);
    expect(src).toMatch(/onClick=\{\(\) => startGetToKnowMe\(\)\}/);
    expect(src).not.toMatch(/localStorage/);
  });

  it("the card links to Settings → Assistant settings (PZ-28)", () => {
    const src = readFileSync(new URL("./InterviewOffer.tsx", import.meta.url), "utf-8");
    expect(src).toMatch(
      /<Link href="\/settings\/assistant"[^>]*>\s*Settings → Assistant settings\s*<\/Link>/,
    );
  });
});
