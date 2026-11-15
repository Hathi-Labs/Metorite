// CS-3a (task 3438, `chat_failure_modes.md` §6 row CS-3): the server sends
// the phase of a run as the CUSTOM event `run_phase` (`acb_skills.run_phase`).
// No client draws it yet, so nothing on this side may change.
//
// R7 fences named here:
//   * `run-phase-not-saved`: `withCustomEvent` never keeps a `run_phase`
//     event, and both loops of the hook leave it out of `customEvents`.
//   * `run-phase-not-forwarded`: the BFF route forwards no `run_phase` frame
//     and saves none. So a tab with an older bundle saves nothing, and its
//     run row keeps "Reading your message" (CS-1). A run that sent only
//     phases saves no row at all.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PROGRESS_EVENT, RUN_PHASE_EVENT, withCustomEvent } from "@/lib/chatStream";
import type { ChatMessage } from "@/lib/chatStore";

const here = path.dirname(fileURLToPath(import.meta.url));

const GATHERING = { type: "CUSTOM", name: "run_phase", value: { phase: "gathering" } };
const ASKING = { type: "CUSTOM", name: "run_phase", value: { phase: "asking" } };

function msg(): ChatMessage {
  return { id: "a1", role: "assistant", content: "", timestamp: 0, streaming: true, isThinkingActive: true };
}

describe("run-phase-not-saved", () => {
  it("withCustomEvent keeps no phase, and still keeps another event", () => {
    expect(RUN_PHASE_EVENT).toBe("run_phase");
    expect(RUN_PHASE_EVENT).not.toBe(PROGRESS_EVENT);
    const m = msg();
    expect(withCustomEvent(m, RUN_PHASE_EVENT, { phase: "gathering" })).toBe(m);
    expect(withCustomEvent(m, "artifact_created", { path: "x" }).customEvents).toHaveLength(1);
  });

  it("both loops of the hook leave the phase out of customEvents", () => {
    const hook = readFileSync(path.join(here, "../hooks/useAgentChat.ts"), "utf8");
    // The live loop (`upd`) and the attach loop (`updLast`): each guard of
    // `withCustomEvent` names the phase.
    const guards = hook.split(/\r?\n/).filter((l) => l.includes("!HITL_CONTROL_EVENTS.has(evtName)"));
    expect(guards).toHaveLength(2);
    for (const g of guards) expect(g).toContain("evtName !== RUN_PHASE_EVENT");
  });
});

/** Run the BFF route's reconnect path over *events*, as the gateway would
 *  replay them. Gives the frames the browser gets and each saved checkpoint. */
async function replay(events: Record<string, unknown>[]): Promise<{
  frames: Record<string, unknown>[];
  saved: Array<Array<Record<string, unknown>>>;
}> {
  const saved: Array<Array<Record<string, unknown>>> = [];
  vi.doMock("@/auth", () => ({
    auth: async () => ({ user: { email: "ana@a.test" } }),
    isAuthEnabled: true,
  }));
  vi.doMock("@/lib/gateway", () => ({
    GATEWAY_URL: "http://gw.test",
    requireIdentity: async () => ({ email: "ana@a.test", role: "employee" }),
    gatewayHeaders: async (extra?: Record<string, string>) => ({ ...(extra ?? {}) }),
    gatewayFetch: async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/reconnect")) {
        const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
        return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      if (url.includes("/messages")) {
        saved.push(JSON.parse(String(init?.body)) as Array<Record<string, unknown>>);
        return new Response("{}", { status: 200 });
      }
      return new Response("{}", { status: 404 });
    },
  }));
  const { POST } = await import("@/app/api/agent/chat/route");
  const req = new Request("http://app.test/api/agent/chat", {
    method: "POST",
    body: JSON.stringify({
      agentName: "orchestrator", message: "(reconnect)", threadId: "t1",
      assistantMessageId: "a1", reconnect: true,
    }),
  });
  const res = await POST(req as never);
  const text = await res.text();
  const frames = text
    .split("\n\n")
    .filter((s) => s.startsWith("data: "))
    .map((s) => JSON.parse(s.slice(6)) as Record<string, unknown>);
  return { frames, saved };
}

describe("run-phase-not-forwarded", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.doUnmock("@/auth");
    vi.doUnmock("@/lib/gateway");
  });

  it("forwards no phase and saves no phase, around a real answer", async () => {
    const { frames, saved } = await replay([
      { type: "RUN_STARTED", runId: "r1", threadId: "t1" },
      GATHERING,
      ASKING,
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta: "Hello." },
      { type: "RUN_FINISHED", runId: "r1", threadId: "t1" },
    ]);
    expect(JSON.stringify(frames)).not.toContain("run_phase");
    expect(frames.map((f) => f.type)).toEqual(["delta", "done"]);
    expect(saved.length).toBeGreaterThan(0);
    expect(JSON.stringify(saved)).not.toContain("run_phase");
    expect(JSON.stringify(saved)).toContain("Hello.");
  });

  it("a run that sent only phases forwards nothing and saves no row", async () => {
    const { frames, saved } = await replay([
      { type: "RUN_STARTED", runId: "r1", threadId: "t1" },
      GATHERING,
      ASKING,
      { type: "RUN_ERROR", message: "boom" },
    ]);
    expect(JSON.stringify(frames)).not.toContain("run_phase");
    expect(saved).toEqual([]);
  });

  it("still forwards and saves another custom event", async () => {
    const card = { type: "CUSTOM", name: "artifact_created", value: { path: "outputs/a.md" } };
    const { frames, saved } = await replay([
      { type: "RUN_STARTED", runId: "r1", threadId: "t1" },
      GATHERING,
      card,
      { type: "RUN_FINISHED", runId: "r1", threadId: "t1" },
    ]);
    expect(frames.filter((f) => f.type === "custom").map((f) => f.name)).toEqual(["artifact_created"]);
    expect(JSON.stringify(saved)).toContain("artifact_created");
    expect(JSON.stringify(saved)).not.toContain("run_phase");
  });
});
