/**
 * "Draft with AI" on a forward names the people the forward goes to (task 1817).
 *
 * Owner report, 2026-11-06: the member forwarded a customer's email to the
 * team, and the draft wrote to the customer. The server needs the To and Cc
 * of the forward to write the note to them. A reply and a new mail send the
 * body they sent before.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { composeAssistBody, composeAssistStream } from "./api";
import { draftCc } from "./draftRecipients";

afterEach(() => vi.unstubAllGlobals());

describe("composeAssistBody", () => {
  it("sends the To and the Cc of a forward", () => {
    const sent = JSON.parse(
      composeAssistBody({
        accountId: "a1",
        mode: "forward",
        messageId: "m1",
        to: ["team@us.test"],
        cc: ["boss@us.test"],
      }),
    );
    expect(sent.to).toEqual(["team@us.test"]);
    expect(sent.cc).toEqual(["boss@us.test"]);
    expect(sent.mode).toBe("forward");
  });

  it("sends no cc key for a reply or a new mail", () => {
    const reply = JSON.parse(composeAssistBody({ accountId: "a1", mode: "reply", to: ["c@x.test"] }));
    expect("cc" in reply).toBe(false);
    expect(reply.to).toEqual(["c@x.test"]);
  });

  it("reaches the stream route with the forward's To and Cc", async () => {
    const bodies: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => {
        bodies.push(String(init.body));
        return new Response(`data: ${JSON.stringify({ type: "done", draft: "Hi team" })}\n\n`, { status: 200 });
      }),
    );
    await composeAssistStream({ accountId: "a1", mode: "forward", to: ["t@us.test"], cc: ["c@us.test"] });
    const sent = JSON.parse(bodies[0]);
    expect(sent.to).toEqual(["t@us.test"]);
    expect(sent.cc).toEqual(["c@us.test"]);
  });
});

describe("draftCc", () => {
  it("is the Cc list of a forward, trimmed and with no empty entry", () => {
    expect(draftCc("forward", "a@x.test, b@x.test ,, ")).toEqual(["a@x.test", "b@x.test"]);
    expect(draftCc("forward", "")).toEqual([]);
  });

  it("is nothing for a reply, so a reply sends the body it sent before", () => {
    expect(draftCc("reply", "a@x.test")).toBeUndefined();
    expect(draftCc(null, "a@x.test")).toBeUndefined();
  });
});

describe("the inline composer", () => {
  const detail = () =>
    readFileSync(fileURLToPath(new URL("../components/EmailDetail.tsx", import.meta.url)), "utf-8");
  /** The object that the forward and reply composer gives to `ai.run`. */
  const runArgs = () => {
    const m = /ai\.run\(\s*\{([^}]*mode: replyMode === "forward"[^}]*)\}/.exec(detail());
    expect(m).not.toBeNull();
    return m![1];
  };

  it("EmailDetail passes the Cc of a forward to ai.run", () => {
    expect(runArgs()).toMatch(/\bcc: ccArr\b/);
    expect(detail()).toMatch(/const ccArr = draftCc\(replyMode, replyCc\);/);
  });

  it("EmailDetail passes the To it reads at the click", () => {
    expect(runArgs()).toMatch(/\bto: toArr\b/);
    expect(detail()).toMatch(/const toArr = replyTo\.split\(","\)[^;]*;\s*\/\/ A forward goes to the people/);
  });
});
