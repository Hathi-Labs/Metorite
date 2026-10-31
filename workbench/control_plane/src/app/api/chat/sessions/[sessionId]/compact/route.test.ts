// `/api/chat/sessions/[id]/compact` keeps every message when no summary
// comes back (task 1128).
//
// A blank summary passed the old `?? "(Summary unavailable)"` check, because
// `""` is not null. The route then dropped the old messages with no summary,
// so the agent lost them. A failed call dropped them too. Now both answer 502
// with `compacted: false` and the messages as sent.
//
// Mutations: put back the `??` fallback, and the blank test fails. Drop the
// messages in the catch again, and the failure test fails. Remove `thinking`
// from the body, and the first test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const seam = vi.hoisted(() => ({
  bodies: [] as Record<string, unknown>[],
  reply: (): Response => new Response("{}", { status: 200 }),
}));

vi.mock("@/lib/gateway", () => ({
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayFetch: async (_url: string, init?: RequestInit) => {
    seam.bodies.push(JSON.parse(String(init?.body ?? "{}")));
    return seam.reply();
  },
}));

const MESSAGES = Array.from({ length: 10 }, (_, i) => ({
  id: `m${i}`,
  role: i % 2 ? "assistant" : "user",
  content: `turn ${i}`,
  timestamp: i,
}));

const answer = (content: string | null) =>
  new Response(JSON.stringify({ choices: [{ message: { content } }] }), { status: 200 });

const compact = async () => {
  const { POST } = await import("./route");
  const req = new NextRequest("http://app.test/api/chat/sessions/s1/compact", {
    method: "POST",
    body: JSON.stringify({ messages: MESSAGES, keepLast: 6 }),
  });
  return POST(req, { params: Promise.resolve({ sessionId: "s1" }) });
};

describe("/api/chat/sessions/[id]/compact", () => {
  beforeEach(() => {
    seam.bodies.length = 0;
  });

  it("compacts with a real summary, and asks for no thinking", async () => {
    seam.reply = () => answer("- Acme deal at stage 3");
    const res = await compact();
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.compacted).toBe(true);
    expect(data.removedCount).toBe(4);
    expect(data.messages).toHaveLength(7);
    expect(data.messages[0].content).toContain("Acme deal at stage 3");
    expect(seam.bodies[0].thinking).toEqual({ type: "disabled" });
  });

  it.each([[""], ["   "], [null]])("keeps every message on a blank summary (%j)", async (content) => {
    seam.reply = () => answer(content);
    const res = await compact();
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data).toMatchObject({ compacted: false, removedCount: 0, error: "summary_empty" });
    expect(data.messages).toEqual(MESSAGES);
  });

  it("keeps every message when the summary call fails", async () => {
    seam.reply = () => new Response("upstream", { status: 503 });
    const res = await compact();
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data).toMatchObject({ compacted: false, removedCount: 0, error: "summary_failed" });
    expect(data.messages).toEqual(MESSAGES);
  });
});
