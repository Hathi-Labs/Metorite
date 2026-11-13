// HRD-6 (task 2985, `chat_run_continuity.md` §4.11): the line of a slow tool.
//
// R7 fences named here:
//   * `progress-line-live`: the live loop's `progress` event puts ONE line on
//     the message, and a new line replaces the old one.
//   * `progress-line-goes`: the line goes when its step ends, when a step of a
//     hand-off that it names ends, when answer text comes, and at the run end.
//     The end of ANOTHER step leaves it while its own step runs.
//   * `progress-line-draws`: `ThinkingContainer` draws the line under its
//     header while the turn works, with the trail closed, in one status
//     region that is mounted before the line comes, and draws nothing once
//     the turn is over.
//   * `progress-line-attach`: the attach loop replays the run and then
//     follows it live, so it applies each line and each clear in order. A
//     line with its clear ends with no line. A line with no clear yet shows.
//   * `progress-line-not-saved`: no loop keeps the line in `customEvents`, the
//     BFF save drops it, and `withCustomEvent` never keeps one, so no save
//     posts it.
//   * `progress-line-clear`: an empty line takes away the line of the step it
//     names (that step ended, or it shows a card), and only that one.

import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  PROGRESS_EVENT,
  applyStreamEvent,
  applySubAgentEvent,
  withCustomEvent,
  withProgressNotice,
  type StreamFold,
} from "@/lib/chatStream";
import type { ChatMessage } from "@/lib/chatStore";
import ThinkingContainer from "@/components/ThinkingContainer";

const LINE = "This is a large read. It can take a few minutes to get all the data. Please wait.";
const here = path.dirname(fileURLToPath(import.meta.url));

function msg(): ChatMessage {
  return { id: "a1", role: "assistant", content: "", timestamp: 0, streaming: true, isThinkingActive: true };
}

function run(m: ChatMessage, events: Record<string, unknown>[]): ChatMessage {
  const fold: StreamFold = { foldedAnswerIdx: -1 };
  return events.reduce((acc, e) => applyStreamEvent(acc, e, fold), m);
}

describe("progress-line-live", () => {
  it("keeps one line with the step it names", () => {
    const m = run(msg(), [{ type: "tool_start", id: "c1", name: "crm_aggregate", args: {} }]);
    const shown = withProgressNotice(m, { text: `  ${LINE}\n`, tool_call_id: "c1" });
    expect(shown.progressNotice).toEqual({ text: LINE, toolId: "c1" });
    const next = withProgressNotice(shown, { text: "Adding up 31 totals." });
    expect(next.progressNotice).toEqual({ text: "Adding up 31 totals." });
  });

  it("ignores an empty line with no line shown, and a value that is not an object", () => {
    const m = msg();
    expect(withProgressNotice(m, { text: "   " })).toBe(m);
    expect(withProgressNotice(m, null)).toBe(m);
    expect(withProgressNotice(m, "words")).toBe(m);
  });

  it("cuts a long line to one short line", () => {
    const shown = withProgressNotice(msg(), { text: "x".repeat(900) });
    expect(shown.progressNotice?.text.length).toBe(300);
  });
});

describe("progress-line-goes", () => {
  const started = (): ChatMessage =>
    withProgressNotice(
      run(msg(), [
        { type: "tool_start", id: "c1", name: "crm_aggregate", args: {} },
        { type: "tool_start", id: "c2", name: "find_contact", args: {} },
      ]),
      { text: LINE, tool_call_id: "c1" },
    );

  it("goes when its own step ends", () => {
    const m = run(started(), [{ type: "tool_end", id: "c1", success: true, result: "31" }]);
    expect(m.progressNotice).toBeUndefined();
  });

  it("stays while its own step runs and another step ends", () => {
    const m = run(started(), [{ type: "tool_end", id: "c2", success: true, result: "ok" }]);
    expect(m.progressNotice?.toolId).toBe("c1");
  });

  it("goes when answer text comes, and not for a blank delta", () => {
    const blank = run(started(), [{ type: "delta", content: "\n " }]);
    expect(blank.progressNotice).toBeDefined();
    const text = run(started(), [{ type: "delta", content: "You have 31 deals." }]);
    expect(text.progressNotice).toBeUndefined();
  });

  it("goes at the end of the run", () => {
    const m = run(started(), [{ type: "done" }]);
    expect(m.progressNotice).toBeUndefined();
  });

  it("goes when the hand-off step that it names ends", () => {
    let m = run(msg(), [{ type: "tool_start", id: "d1", name: "call_agent", args: {} }]);
    m = applySubAgentEvent(m, { type: "sub_agent_tool_start", agentName: "crm", id: "s1", name: "crm_aggregate" });
    m = withProgressNotice(m, { text: LINE, tool_call_id: "s1" });
    expect(m.progressNotice?.toolId).toBe("s1");
    m = applySubAgentEvent(m, { type: "sub_agent_tool_end", agentName: "crm", id: "s1", success: true, result: "31" });
    expect(m.progressNotice).toBeUndefined();
  });

  it("a line with no step goes when no step runs", () => {
    let m = run(msg(), [{ type: "tool_start", id: "c1", name: "crm_aggregate", args: {} }]);
    m = withProgressNotice(m, { text: LINE });
    m = run(m, [{ type: "tool_end", id: "c1", success: true, result: "31" }]);
    expect(m.progressNotice).toBeUndefined();
  });
});

describe("progress-line-draws", () => {
  const steps = [{ id: "c1", name: "crm_aggregate", args: {}, status: "running" as const, startedAt: 1 }];

  it("draws under the header while the turn works, with the trail closed", () => {
    const html = renderToStaticMarkup(
      createElement(ThinkingContainer, { toolEvents: steps, progressLines: [], isActive: true, notice: LINE }),
    );
    expect(html).toContain("data-trail-notice");
    expect(html).toContain('role="status"');
    expect(html).toContain(LINE);
    // The trail body is closed: the line is not inside it.
    expect(html).not.toContain("data-trail-body");
    expect(html.indexOf("data-trail-head")).toBeLessThan(html.indexOf("data-trail-notice"));
  });

  it("draws nothing once the turn is over, or with no line", () => {
    const over = renderToStaticMarkup(
      createElement(ThinkingContainer, { toolEvents: steps, progressLines: [], isActive: false, notice: LINE }),
    );
    expect(over).not.toContain("data-trail-notice");
    const none = renderToStaticMarkup(
      createElement(ThinkingContainer, { toolEvents: steps, progressLines: [], isActive: true }),
    );
    expect(none).not.toContain("data-trail-notice");
  });

  it("mounts the status region before the line comes, so a reader announces it", () => {
    const none = renderToStaticMarkup(
      createElement(ThinkingContainer, { toolEvents: steps, progressLines: [], isActive: true }),
    );
    expect(none).toMatch(/<div role="status" aria-live="polite" data-trail-status=""><\/div>/);
    const shown = renderToStaticMarkup(
      createElement(ThinkingContainer, { toolEvents: steps, progressLines: [], isActive: true, notice: LINE }),
    );
    const region = shown.slice(shown.indexOf('data-trail-status=""'));
    expect(region.indexOf("data-trail-notice")).toBeGreaterThan(0);
  });

  it("uses the design system's ink, not a colour of its own", () => {
    const src = readFileSync(path.join(here, "../components/ThinkingContainer.tsx"), "utf8");
    const block = src.slice(src.indexOf("data-trail-notice"), src.indexOf("Pre-content working indicator"));
    expect(block).toContain("text-muted-foreground");
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(/);
  });
});

describe("progress-line-not-saved", () => {
  it("no loop keeps the line in customEvents, and the BFF save drops it", () => {
    const hook = readFileSync(path.join(here, "../hooks/useAgentChat.ts"), "utf8");
    expect(hook).toContain("evtName !== PROGRESS_EVENT) {");
    expect(PROGRESS_EVENT).toBe("progress");
    const route = readFileSync(path.join(here, "../app/api/agent/chat/route.ts"), "utf8");
    expect(route).toContain('cev.name !== "progress"');
  });
});

/** What the attach loop of `useAgentChat` does with a replayed run: the
 *  stream events go through `applyStreamEvent`, and a `progress` event goes
 *  through `withProgressNotice`, in the order of the stream. */
function attach(m: ChatMessage, events: Record<string, unknown>[]): ChatMessage {
  const fold: StreamFold = { foldedAnswerIdx: -1 };
  return events.reduce((acc, e) => (
    e.type === "custom" && e.name === PROGRESS_EVENT
      ? withProgressNotice(acc, e.value)
      : applyStreamEvent(acc, e, fold)
  ), m);
}

describe("progress-line-attach", () => {
  const start = { type: "tool_start", id: "c1", name: "request_confirmation_write", args: {} };
  const line = { type: "custom", name: PROGRESS_EVENT, value: { text: LINE, tool_call_id: "c1" } };
  const clear = { type: "custom", name: PROGRESS_EVENT, value: { text: "", tool_call_id: "c1" } };

  it("a replayed line and its clear end with no line (a card, or the step ended)", () => {
    const m = attach(msg(), [start, line, clear]);
    expect(m.progressNotice).toBeUndefined();
    expect(m.customEvents ?? []).toEqual([]);
  });

  it("a replayed line with no clear yet shows: the step still runs", () => {
    const m = attach(msg(), [start, line]);
    expect(m.progressNotice).toEqual({ text: LINE, toolId: "c1" });
  });

  it("a later line that comes live after the replay shows too", () => {
    const later = { type: "custom", name: PROGRESS_EVENT, value: { text: LINE, tool_call_id: "c2" } };
    const m = attach(msg(), [start, line, clear, { type: "tool_start", id: "c2", name: "crm_aggregate", args: {} }, later]);
    expect(m.progressNotice?.toolId).toBe("c2");
  });

  it("the attach loop of the hook applies the line, and keeps it out of customEvents", () => {
    const hook = readFileSync(path.join(here, "../hooks/useAgentChat.ts"), "utf8");
    // Both loops: the live loop through `upd`, the attach loop through `updLast`.
    expect(hook).toContain("upd((m) => withProgressNotice(m, evt.value ?? evt.data));");
    expect(hook).toContain("updLast((m) => withProgressNotice(m, evt.value ?? evt.data));");
  });
});

describe("progress-line-clear", () => {
  it("an empty line of the step takes its line away", () => {
    const shown = withProgressNotice(msg(), { text: LINE, tool_call_id: "c1" });
    const cleared = withProgressNotice(shown, { text: "", tool_call_id: "c1" });
    expect(cleared.progressNotice).toBeUndefined();
  });

  it("an empty line of another step leaves the line", () => {
    const shown = withProgressNotice(msg(), { text: LINE, tool_call_id: "c2" });
    expect(withProgressNotice(shown, { text: "", tool_call_id: "c1" })).toBe(shown);
  });

  it("the hand-off case: the clear comes before the hand-off ends", () => {
    let m = run(msg(), [{ type: "tool_start", id: "d1", name: "call_agent", args: {} }]);
    m = applySubAgentEvent(m, { type: "sub_agent_tool_start", agentName: "crm", id: "s1", name: "crm_aggregate" });
    m = withProgressNotice(m, { text: LINE, tool_call_id: "s1" });
    // The delegated run is a batch run: its step ends report only after the
    // whole hand-off. The clear event arrives at once.
    m = withProgressNotice(m, { text: "", tool_call_id: "s1" });
    expect(m.progressNotice).toBeUndefined();
    expect(m.toolEvents?.[0].status).toBe("running");
  });
});

describe("progress-line-not-saved (custom events)", () => {
  it("withCustomEvent never keeps the line or its clear", () => {
    const m = msg();
    expect(withCustomEvent(m, PROGRESS_EVENT, { text: LINE })).toBe(m);
    expect(withCustomEvent(m, PROGRESS_EVENT, { text: "" })).toBe(m);
    expect(withCustomEvent(m, "navigate", { path: "/crm" }).customEvents).toHaveLength(1);
  });

  it("both loops of the hook leave the line out of customEvents", () => {
    const hook = readFileSync(path.join(here, "../hooks/useAgentChat.ts"), "utf8");
    const keeps = hook.match(/evtName !== GUIDE_EVENT && evtName !== PROGRESS_EVENT\) \{/g) ?? [];
    expect(keeps).toHaveLength(2);
  });
});

