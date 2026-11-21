/**
 * Fence for `lib/chatRunFailed.ts` (AO-3b, task 3692): the "AI is taking a
 * breather" card survives a reload, and the model never sees the failed turn.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  RUN_FAILED_EVENT,
  failedMarker,
  failedMarkerOf,
  foldFailedTurns,
  isFailedTurn,
} from "@/lib/chatRunFailed";
import { checkpointIsEmpty } from "@/lib/assistantCheckpoint";
import { retryPlan } from "@/lib/chatRetry";
import { parseStoredRunError } from "@/lib/runErrors";

const marker = failedMarker("ai_unavailable", "ab12");
const u = (id: string, content = "hello") => ({ id, role: "user" as const, content, timestamp: 1, customEvents: [] });
const a = (id: string, content = "an answer") => ({ id, role: "assistant" as const, content, timestamp: 2, customEvents: [] });
const failed = (id: string) => ({ id, role: "assistant" as const, content: "", timestamp: 2, customEvents: [marker] });

describe("the saved failure marker", () => {
  it("carries the code and the ref", () => {
    expect(marker.name).toBe(RUN_FAILED_EVENT);
    expect(failedMarkerOf(failed("f"))).toEqual({ code: "ai_unavailable", ref: "ab12" });
  });
  it("ignores a code that is not saved", () => {
    const row = { role: "assistant" as const, customEvents: [failedMarker("timeout", null)] };
    expect(failedMarkerOf(row)).toBeNull();
    expect(isFailedTurn(row)).toBe(false);
  });
  it("names only an assistant row", () => {
    expect(isFailedTurn({ role: "user", customEvents: [marker] })).toBe(false);
    expect(isFailedTurn(failed("f"))).toBe(true);
  });
});

describe("foldFailedTurns", () => {
  it("draws the last failure row as the resting card, with the row's id", () => {
    const out = foldFailedTurns([u("u1"), failed("f1")]);
    expect(out.map((m) => m.id)).toEqual(["u1", "f1"]);
    expect(out[1].role).toBe("system");
    const err = parseStoredRunError(out[1].content.slice("__ERROR__".length));
    expect(err.code).toBe("ai_unavailable");
    expect(err.ref).toBe("ab12");
  });
  it("drops a failure row that a later turn followed", () => {
    const out = foldFailedTurns([u("u1"), failed("f1"), u("u2"), a("a2")]);
    expect(out.map((m) => m.id)).toEqual(["u1", "u2", "a2"]);
  });
  it("drops every marker in a window of older rows", () => {
    expect(foldFailedTurns([u("u1"), failed("f1")], false).map((m) => m.id)).toEqual(["u1"]);
  });
  it("leaves other rows alone", () => {
    const rows = [u("u1"), a("a1")];
    expect(foldFailedTurns(rows)).toEqual(rows);
  });
  it("keeps Try again working after a reload: one resend, one prompt dropped", () => {
    const out = foldFailedTurns([u("u1", "send the report"), failed("f1")]);
    expect(retryPlan(out, "f1")).toEqual({ resend: "send the report", drop: ["f1", "u1"] });
  });
});

describe("the saved row", () => {
  it("is no empty checkpoint: the route saves it", () => {
    expect(checkpointIsEmpty({ threadId: "t", content: "", customEvents: [marker] })).toBe(false);
  });
});

describe("the wiring", () => {
  const read = (p: string) => readFileSync(p, "utf-8");
  it("the chat route saves the marker on a RUN_ERROR", () => {
    const src = read("src/app/api/agent/chat/route.ts");
    expect(src).toContain("SAVED_FAILURE_CODES.has(ev.code)");
    expect(src).toContain("failedMarker(");
  });
  it("the history builder skips a failure row", () => {
    expect(read("src/hooks/useAgentChat.ts")).toContain("!isFailedTurn(m)");
  });
  it("both remote mappers fold the marker rows", () => {
    expect(read("src/hooks/useAgentChat.ts")).toContain("foldFailedTurns(remote");
    expect(read("src/lib/sessions.ts")).toContain("foldFailedTurns(remote.map");
  });
  it("the live card keeps the saved row's id", () => {
    expect(read("src/lib/chatTurnFailure.ts")).toContain("SAVED_FAILURE_CODES.has(view.code) ? t.assistantId");
  });
});
