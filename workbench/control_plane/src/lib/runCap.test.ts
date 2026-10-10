/**
 * The run cap in the chat (WS-51 D-3, owner decision 2026-10-10).
 *
 * The gateway refuses a member's sixth live run with 429 `too_many_runs`
 * (`orchestrator/run_cap.py`). The chat must then:
 *
 * - show ONE notice in the run-error idiom, with "Open activity";
 * - give the member's words back to the composer;
 * - hold nothing and retry nothing: a 429 is not "Metorite is updating";
 * - save no row for the refused turn.
 *
 * Fences (R7): the code and its words, the frame the route sends, the seam
 * that settles the turn (against the real chat store), and the wiring in
 * the hook and the chat, read from source as `chatRecovery.test.ts` does.
 * The gateway half is `tests/unit/test_run_cap.py`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ErrorCardView } from "@/components/ChatErrorCard";
import { editRefusalReason, EDIT_TOO_MANY_RUNS } from "./chatEdit";
import { isUpdateOutage } from "./chatRecovery";
import { getSessionState, setSessionState, type ChatMessage } from "./chatStore";
import { RUN_CAP_NOTICE_ID, mergeCappedText, settleFailedTurn } from "./chatTurnFailure";
import {
  RUN_ERROR_WORDS,
  codeForGatewayRefusal,
  noticeBody,
  parseStoredRunError,
  runCapFromRaw,
} from "./runErrors";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (p: string) => readFileSync(`${SRC}/${p}`, "utf8").replace(/\r\n/g, "\n");

/** The gateway's 429 body. */
const BODY = JSON.stringify({ error: "too_many_runs", limit: 5, running: ["t-1", "t-2", "t-3", "t-4", "t-5"] });
/** What `app/api/agent/chat/route.ts` sends the browser for it. */
const FRAME = `data: ${JSON.stringify({ type: "error", content: BODY, code: codeForGatewayRefusal(429, BODY) })}\n\n`;

describe("the run-cap code", () => {
  it("is named from the body, so a plain 429 stays 'the AI is busy'", () => {
    expect(codeForGatewayRefusal(429, BODY)).toBe("too_many_runs");
    expect(codeForGatewayRefusal(429, "Too many requests")).toBe("rate_limited");
    expect(codeForGatewayRefusal(429, JSON.stringify({ error: "ai_budget_exhausted" }))).toBe("rate_limited");
    expect(codeForGatewayRefusal(409, BODY)).toBe("run_in_progress");
  });

  it("reads the limit and the runs from the bare body and from the frame", () => {
    expect(runCapFromRaw(BODY)).toEqual({ limit: 5, running: ["t-1", "t-2", "t-3", "t-4", "t-5"] });
    expect(runCapFromRaw(FRAME.trim())?.limit).toBe(5);
    expect(runCapFromRaw("not json")).toBeNull();
    expect(runCapFromRaw(JSON.stringify({ error: "other" }))).toBeNull();
  });

  it("says the owner's words, as a notice with Open activity", () => {
    const words = RUN_ERROR_WORDS.too_many_runs;
    expect(words.tone).toBe("notice");
    expect(words.action).toBe("Open activity");
    expect(noticeBody(words, BODY)).toBe(
      "You have 5 assistants running. Wait for one to finish or stop one, then send again.",
    );
    // A body with no limit still reads as a sentence.
    expect(noticeBody(words, "")).toContain("You have too many assistants running.");
    const html = renderToStaticMarkup(createElement(ErrorCardView, {
      error: { code: "too_many_runs", ref: null, raw: BODY }, onRetry: () => {},
    }));
    expect(html).toContain("Too many assistants running");
    expect(html).toContain("You have 5 assistants running.");
    expect(html).toContain("Open activity");
    expect(html).toContain('role="status"');
    expect(html).not.toContain("Show full error");
  });

  it("names the edit refusal too", () => {
    expect(editRefusalReason(429, FRAME)).toBe(EDIT_TOO_MANY_RUNS);
  });
});

describe("a send the cap refused", () => {
  function turnInFlight(threadId: string, words: string) {
    const userMsgId = `u-${threadId}`;
    const assistantId = `a-${threadId}`;
    const user: ChatMessage = { id: userMsgId, role: "user", content: words, timestamp: 1, awaitingServer: true };
    const draft: ChatMessage = { id: assistantId, role: "assistant", content: "", timestamp: 2, streaming: true };
    setSessionState(threadId, (prev) => ({ ...prev, messages: [{ id: "old", role: "assistant", content: "Earlier answer", timestamp: 0 }, user, draft] }));
    return { userMsgId, assistantId };
  }

  it("leaves the thread, gives the words back, and draws one notice", () => {
    const tid = "cap-thread-1";
    const ids = turnInFlight(tid, "Plan the release");
    const back: string[] = [];
    const outcome = settleFailedTurn({
      threadId: tid, ...ids, content: "Plan the release", rawErr: FRAME, status: 429,
      onRunCapped: (t) => back.push(t),
    });
    expect(outcome).toBe("capped");
    expect(back).toEqual(["Plan the release"]);
    const msgs = getSessionState(tid).messages;
    expect(msgs.map((m) => m.id)).toEqual(["old", RUN_CAP_NOTICE_ID]);
    expect(getSessionState(tid).error).toBeNull();
    const notice = parseStoredRunError(msgs[1].content.slice("__ERROR__".length));
    expect(notice.code).toBe("too_many_runs");
    expect(runCapFromRaw(notice.raw)?.limit).toBe(5);
  });

  it("keeps ONE notice when the member is refused twice", () => {
    const tid = "cap-thread-2";
    for (const words of ["first", "second"]) {
      const ids = turnInFlight(tid, words);
      setSessionState(tid, (prev) => ({
        ...prev,
        messages: [...getSessionState(tid).messages.filter((m) => m.id !== "old" || words === "first")],
      }));
      settleFailedTurn({ threadId: tid, ...ids, content: words, rawErr: FRAME, status: 429 });
    }
    const notices = getSessionState(tid).messages.filter((m) => m.id === RUN_CAP_NOTICE_ID);
    expect(notices).toHaveLength(1);
  });

  it("a held send that the cap refuses later leaves the thread too, and was never saveable", () => {
    // Review of #821: a send held through an app update (#797) carries the
    // mark from the start, and the retry spreads the bubble, so the mark
    // travels to the send that the cap refuses.
    const tid = "cap-thread-held";
    const held: ChatMessage = {
      id: "u-held", role: "user", content: "Plan the release", timestamp: 1,
      pendingDelivery: true, awaitingServer: true,
    };
    const retried: ChatMessage = { ...held, pendingDelivery: false };
    expect(retried.awaitingServer).toBe(true);
    const draft: ChatMessage = { id: "a-held", role: "assistant", content: "", timestamp: 2, streaming: true };
    setSessionState(tid, (prev) => ({ ...prev, messages: [retried, draft] }));
    expect(settleFailedTurn({
      threadId: tid, userMsgId: "u-held", assistantId: "a-held", content: "Plan the release",
      rawErr: FRAME, status: 429,
    })).toBe("capped");
    expect(getSessionState(tid).messages.map((m) => m.id)).toEqual([RUN_CAP_NOTICE_ID]);
  });

  it("never loses the refused words when the composer has text", () => {
    expect(mergeCappedText("", "Plan the release")).toBe("Plan the release");
    expect(mergeCappedText("  ", "Plan the release")).toBe("Plan the release");
    expect(mergeCappedText("Also check the budget", "Plan the release")).toBe(
      "Also check the budget\n\nPlan the release",
    );
    expect(mergeCappedText("Also check the budget\n", "Plan the release")).toBe(
      "Also check the budget\n\nPlan the release",
    );
    // Words already in the composer are not added twice.
    expect(mergeCappedText("Plan the release", "Plan the release")).toBe("Plan the release");
    expect(mergeCappedText("draft", "  ")).toBe("draft");
  });

  it("is not an app update, so nothing is held", () => {
    expect(isUpdateOutage({ status: 429, gotResponse: true })).toBe(false);
  });

  it("a plain 429 still draws the error card and keeps the turn", () => {
    const tid = "cap-thread-3";
    const ids = turnInFlight(tid, "hi");
    const outcome = settleFailedTurn({
      threadId: tid, ...ids, content: "hi",
      rawErr: `data: ${JSON.stringify({ type: "error", content: "busy", code: "rate_limited" })}\n\n`, status: 429,
    });
    expect(outcome).toBe("error");
    expect(getSessionState(tid).messages.some((m) => m.id === ids.userMsgId)).toBe(true);
  });
});

describe("the wiring", () => {
  const hook = read("hooks/useAgentChat.ts");
  const chat = read("components/AgentChat.tsx");
  const card = read("components/ChatErrorCard.tsx");
  const route = read("app/api/agent/chat/route.ts");

  it("the route names the code from the gateway's body", () => {
    expect(route).toContain("codeForGatewayRefusal(streamRes.status, text)");
  });

  it("the hook hands the refusal to the one seam, and confirms every other turn", () => {
    expect(hook).toContain("onRunCapped: onRunCappedRef.current,");
    expect(hook).toContain('capped = outcome === "capped";');
    expect(hook).toContain("if (!capped && !heldAgain) confirmTurn();");
    // The new turn waits for the server on the gateway path only.
    expect(hook).toMatch(/modeRef\.current === "copilot" \? \{ awaitingServer: true \}/);
  });

  it("the hook never holds or re-sends a capped turn", () => {
    // The hold paths are taken only for an update outage (502, 503, 504, or
    // no answer), which a 429 never is. The cap path calls no sender.
    const seam = read("lib/chatTurnFailure.ts");
    const capBranch = seam.slice(seam.indexOf('if (view.code === "too_many_runs")'), seam.indexOf('return "capped";'));
    expect(capBranch).not.toMatch(/holdForUpdate|sendMessage|retry/i);
  });

  it("the chat saves no turn that waits on the server, and restores the words", () => {
    expect(chat).toContain("!m.awaitingServer,");
    expect(chat).toContain("onRunCapped: restoreCappedText,");
    expect(chat).toContain("setInput((prev) => mergeCappedText(prev, text));");
  });

  it("a held send keeps the mark until the server takes it (review of #821)", () => {
    // The bubble that an app update holds is marked on the gateway path.
    const holdBranch = hook.slice(hook.indexOf('if (plan === "hold") {'), hook.indexOf('if (plan === "requeue")'));
    expect(holdBranch).toMatch(/modeRef\.current === "copilot" \? \{ awaitingServer: true \}/);
    // A send that an outage holds again is not confirmed: no answer came.
    expect(hook).toContain("heldAgain = true;");
    expect(hook).toContain("if (!capped && !heldAgain) confirmTurn();");
    expect(hook).toContain(
      "if (res.status !== 429 && !isUpdateOutage({ status: res.status, gotResponse: true })) confirmTurn();",
    );
  });

  it("the notice's button opens the activity panel, never a retry", () => {
    expect(card).toContain('error.code === "too_many_runs" ? openActivity : onRetry');
  });
});
