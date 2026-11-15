// CS-1 (task 2898): the first words of a turn, in 2 seconds.
//
// Task 2875 measured the production gateway on 2026-11-11: the setup before the first model
// request takes 0.5 to 1.7 s, the first request takes 5.4 to 9.6 s before its first tool call,
// and the member sees no text until the end. The chat cannot make the model faster, so it must
// not wait for the model: the line shows from the moment of Send and names what Mira does.
//
// R7 fences named here:
//   * `first-line-in-2s`: on the scripted turn of task 2875 (the model is silent for 9.6 s), the
//     first visible working line shows within 2 seconds of Send. It is measured on the run row and
//     on the header of the thinking trail, with a clock over a script of events.
//   * `first-line-says-what`: that line is the fixed phrase `FIRST_STEP`, not a generic word.
//   * `first-line-gives-way`: the first real step (a tool, text) takes the line over, so it never
//     sits beside a newer step.
//   * `text-before-tool-shows`: text a model writes before a tool call shows at once and stays in
//     the answer, so a lead-in is not held for the end.
//   * `first-line-not-stale`: once answer text has started, the trail header never says that Mira
//     still reads the message. A run that this tab joins again (attach) never has the first line.
//   * `first-line-only-on-send`: only the placeholder that Send puts in the thread carries the flag.
//
// Mutation 1: put "Starting" back in `runView`. `first-line-says-what` turns red.
// Mutation 2: put "Thinking…" back as the header fallback. `first-line-in-2s` turns red.
// Mutation 3: draw the line only after the first event (a 9.6 s script). `first-line-in-2s` turns red.

import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { applyStreamEvent, type StreamFold } from "@/lib/chatStream";
import type { ChatMessage } from "@/lib/chatStore";
import ThinkingContainer from "@/components/ThinkingContainer";
import { FIRST_STEP, IDLE_RUN, reduceRun, runView, type RunEvent } from "@/lib/runStatus";
import { WRITING_STEP, stepOfMessage } from "@/lib/runStep";

/** The generic words that are not a line about what Mira does. */
const GENERIC = ["Starting", "Thinking…", "Thinking", "Working", "Loading…", ""];

/** One event of a scripted turn, at a time after Send. */
type Step = { at: number; event: Record<string, unknown> };

/** The turn of task 2875 that asked "what email should I get back to": the first tool call at 9.6 s. */
const SLOW_TURN: Step[] = [
  { at: 9600, event: { type: "tool_start", id: "t1", name: "find_priority", args: {} } },
  { at: 9900, event: { type: "tool_end", id: "t1", name: "find_priority", result: "{}" } },
  { at: 12400, event: { type: "delta", content: "Two emails need an answer today." } },
];

function placeholder(): ChatMessage {
  // The message that Send puts in the thread at once: `useAgentChat.ts` builds this shape.
  return { id: "a1", role: "assistant", content: "", timestamp: 0, streaming: true, isThinkingActive: true, firstLine: true, toolEvents: [], progressLines: [] };
}

/** What the member sees at `t` ms after Send: the run row label and the header of the trail. */
function seen(script: Step[], t: number): { row: string | null; header: string } {
  const fold: StreamFold = { foldedAnswerIdx: -1 };
  let m = placeholder();
  let run = reduceRun(IDLE_RUN, { type: "send", at: 0, messageId: "a1" } as RunEvent);
  for (const s of script) {
    if (s.at > t) break;
    m = applyStreamEvent(m, s.event, fold);
    run = reduceRun(run, { type: "event", at: s.at, step: stepOfMessage(m) } as RunEvent);
  }
  const html = renderToStaticMarkup(
    createElement(ThinkingContainer, {
      toolEvents: m.toolEvents ?? [], progressLines: m.progressLines ?? [], reasoningBlocks: m.reasoningBlocks, isActive: true,
      firstLine: m.firstLine,
      // The same test as `MarkdownMessage`: answer text in the content scratch or in a segment.
      answerStarted: !!m.content.trim() || (m.segments ?? []).some((s) => s.text.trim().length > 0),
    }),
  );
  // The header title is the first text of the button that heads the trail.
  const header = (html.match(/<button[^>]*>([\s\S]*?)<\/button>/)?.[1] ?? html).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return { row: runView(run, t)?.label ?? null, header };
}

/** The first time, in ms after Send, at which a non-generic working line shows on the row AND in the trail header. */
function firstVisibleLineMs(script: Step[], limit = 15000, stepMs = 100): number | null {
  for (let t = 0; t <= limit; t += stepMs) {
    const v = seen(script, t);
    const rowOk = v.row !== null && !GENERIC.includes(v.row);
    const headerOk = v.header.length > 0 && !GENERIC.includes(v.header);
    if (rowOk && headerOk) return t;
  }
  return null;
}

describe("the first line of a turn (CS-1)", () => {
  it("first-line-in-2s: the line shows within 2 seconds on the scripted turn of task 2875", () => {
    // The model is silent for 9.6 s in this script, so only a line that waits for nothing passes.
    expect(SLOW_TURN[0].at).toBeGreaterThan(5000);
    const at = firstVisibleLineMs(SLOW_TURN);
    expect(at, "no visible line at all").not.toBeNull();
    expect(at as number).toBeLessThanOrEqual(2000);
  });

  it("first-line-says-what: the line is the fixed phrase about what Mira does", () => {
    const v = seen(SLOW_TURN, 0);
    expect(v.row).toBe(FIRST_STEP);
    expect(v.header).toContain(FIRST_STEP);
    expect(GENERIC).not.toContain(FIRST_STEP);
    // A phrase of words, not a tool name and not model text.
    expect(FIRST_STEP).toMatch(/^[A-Z][a-z]+( [a-z]+)+$/);
  });

  it("first-line-says-what: the line holds for the whole silence, up to the first event", () => {
    for (const t of [0, 500, 2000, 5000, 9000]) {
      const v = seen(SLOW_TURN, t);
      expect(v.row, `row at ${t}`).toBe(FIRST_STEP);
      expect(v.header, `header at ${t}`).toContain(FIRST_STEP);
    }
  });

  it("first-line-gives-way: a running tool, then the answer text, take the line over", () => {
    const tool = seen(SLOW_TURN, 9700);
    expect(tool.row).not.toBe(FIRST_STEP);
    expect(tool.row ?? "").toMatch(/\S/);
    expect(tool.header).not.toContain(FIRST_STEP);
    const text = seen(SLOW_TURN, 12500);
    expect(text.row).toBe(WRITING_STEP);
    expect(text.header).not.toContain(FIRST_STEP);
  });

  it("text-before-tool-shows: a lead-in before the first tool shows at once and stays in the answer", () => {
    // The runtime sends a message id with each piece of text, so the segments hold the answer body
    // and the body is built from all of them. The `content` scratch is cleared at a tool start.
    const body = (m: ChatMessage) => (m.segments ?? []).map((s) => s.text).join("");
    const fold: StreamFold = { foldedAnswerIdx: -1 };
    let m = placeholder();
    m = applyStreamEvent(m, { type: "delta", content: "Checking your inbox. ", messageId: "s1" }, fold);
    expect(m.content).toBe("Checking your inbox. ");
    expect(body(m)).toBe("Checking your inbox. ");
    expect(stepOfMessage(m)).toBe(WRITING_STEP);
    m = applyStreamEvent(m, { type: "tool_start", id: "t1", name: "find_priority", args: {} }, fold);
    // The text is not folded into the reasoning, and it is not lost.
    expect(body(m)).toBe("Checking your inbox. ");
    expect(m.reasoningBlocks ?? []).toEqual([]);
    m = applyStreamEvent(m, { type: "tool_end", id: "t1", name: "find_priority", result: "{}" }, fold);
    m = applyStreamEvent(m, { type: "delta", content: "Two emails need an answer.", messageId: "s2" }, fold);
    expect(body(m)).toBe("Checking your inbox. Two emails need an answer.");
  });

  it("first-line-not-stale: answer text before any tool takes the line away, in the header and the row", () => {
    const script: Step[] = [{ at: 4000, event: { type: "delta", content: "Checking your inbox. ", messageId: "s1" } }];
    const before = seen(script, 3900);
    expect(before.header).toContain(FIRST_STEP);
    const after = seen(script, 4000);
    expect(after.header).not.toContain(FIRST_STEP);
    // The header shows the live snippet of the text that came, or the step words if there is none.
    expect(after.header).toMatch(/Checking your inbox|Writing the reply/);
    expect(after.row).toBe(WRITING_STEP);
  });

  it("first-line-not-stale: with answer text and no snippet to show, the header says the step words, never the first line", () => {
    // No live snippet, no tool, no reasoning: only the fallback decides. This is the case that the
    // reducer test above does not reach, because there the live snippet of the text wins the header.
    const html = renderToStaticMarkup(
      createElement(ThinkingContainer, { toolEvents: [], progressLines: [], isActive: true, firstLine: true, answerStarted: true }),
    );
    expect(html).not.toContain(FIRST_STEP);
    expect(html).toContain(WRITING_STEP);
  });

  it("first-line-not-stale: a run that this tab joins again has no first line, so the header is the old word", () => {
    const joined: ChatMessage = { ...placeholder(), firstLine: undefined };
    const html = renderToStaticMarkup(
      createElement(ThinkingContainer, { toolEvents: [], progressLines: [], isActive: true, firstLine: joined.firstLine, answerStarted: false }),
    );
    expect(html).not.toContain(FIRST_STEP);
    expect(html).toContain("Thinking…");
  });

  it("first-line-only-on-send: the flag is set once, on the placeholder of Send, and never saved", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const read = (rel: string) => readFileSync(path.join(here, rel), "utf8").replace(/\r\n/g, "\n");
    const hook = read("../hooks/useAgentChat.ts");
    expect(hook.match(/firstLine: true/g)).toHaveLength(1);
    // The save maps each field by name, so a field that it does not name is not saved.
    const chat = read("../components/AgentChat.tsx");
    expect(chat).not.toContain("firstLine");
    const sessions = read("./sessions.ts");
    expect(sessions).not.toContain("firstLine");
  });
});
