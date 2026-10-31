// `/api/chat/suggestions` asks for no thinking (task 1128).
//
// Three short lines are a structured job. A thinking model spends the cap of
// 150 tokens on thinking and returns no text, and the UI then shows only its
// static defaults. The Router drops `thinking` for a model that does not take
// it, so the body can always carry it.
//
// Mutation: remove `thinking` from the body, and the first test fails.
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

  it("asks for no thinking, and reads the three lines", async () => {
    seam.reply = () => answer('["Draft a note to Acme", "Which deals slip?", "Show stage 3"]');
    const res = await ask();
    expect(await res.json()).toEqual({
      suggestions: ["Draft a note to Acme", "Which deals slip?", "Show stage 3"],
    });
    expect(seam.bodies[0].thinking).toEqual({ type: "disabled" });
    expect(seam.bodies[0].max_tokens).toBe(150);
  });

  it("answers no suggestions on a blank reply", async () => {
    seam.reply = () => answer("");
    expect(await (await ask()).json()).toEqual({ suggestions: [] });
  });
});
