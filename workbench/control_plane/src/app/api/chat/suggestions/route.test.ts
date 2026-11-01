// `/api/chat/suggestions` reads three short lines, and a blank reply is none.
//
// The body sends no `thinking` (task 1128). `deepseek/deepseek-chat` does not
// think, and the gateway's local `/v1` path forwards named fields only, so the
// field would reach no model. If `SUGGESTION_MODEL` ever names a thinking
// tier, that path must forward `thinking` first (through `params_for_model`).
//
// Mutation: send a `thinking` field again, and the first test fails.
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

const ask = async () => {
  const { POST } = await import("./route");
  return POST(
    new NextRequest("http://app.test/api/chat/suggestions", {
      method: "POST",
      body: JSON.stringify({ userMessage: "Deals?", assistantMessage: "Acme is at stage 3." }),
    }),
  );
};

const answer = (content: string) =>
  new Response(JSON.stringify({ choices: [{ message: { content } }] }), { status: 200 });

describe("/api/chat/suggestions", () => {
  beforeEach(() => {
    seam.bodies.length = 0;
  });

  it("reads the three lines, and sends no thinking field", async () => {
    seam.reply = () => answer('["Draft a note to Acme", "Which deals slip?", "Show stage 3"]');
    const res = await ask();
    expect(await res.json()).toEqual({
      suggestions: ["Draft a note to Acme", "Which deals slip?", "Show stage 3"],
    });
    expect(seam.bodies[0]).not.toHaveProperty("thinking");
    expect(seam.bodies[0].max_tokens).toBe(150);
  });

  it("answers no suggestions on a blank reply", async () => {
    seam.reply = () => answer("");
    expect(await (await ask()).json()).toEqual({ suggestions: [] });
  });
});
