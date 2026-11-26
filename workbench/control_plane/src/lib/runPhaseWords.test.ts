// CS-3b (task 3462, `chat_failure_modes.md` §6 row CS-3): the run row and the trail header say a
// FIXED word for the server's phase. The words come from ONE table (`RUN_PHASE_WORDS`), never from
// the server. The first real step takes over, and a phase is never saved.
//
// R7 fences named here:
//   * `phase-words-fixed`: gathering and asking show their table words, in order, inside 2 s on the
//     scripted turn of task 2875. With no phase, the line is still `FIRST_STEP` (CS-1).
//   * `phase-gives-way`: the first tool or text replaces the phase word, on the row and the header.
//   * `phase-unknown-ignored`: an unknown name or a value that is no string draws nothing.
//   * `phase-moves-nothing`: a phase does not set the run to working, and it is not in customEvents.
//   * `phase-not-stale`: a re-attach clears the phase, and the end of the run clears it too.
//   * `phase-wired`: the hook dispatches the phase in BOTH loops, and the save path never names it.
//
// Mutation 1: make the reducer's "phase" case set `phase: "working"`. `phase-moves-nothing` turns red.
// Mutation 2: drop `phaseWord: null` from the "event" case. `phase-gives-way` turns red.
// Mutation 3: drop `phaseWord: null` from the "attach" case. `phase-not-stale` turns red.
// Mutation 4: let `phaseWordOf` return the server's string. `phase-unknown-ignored` turns red.
// Mutation 5: remove the phase branch from one hook loop. `phase-wired` turns red.

import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { applyStreamEvent, withCustomEvent, type StreamFold } from "@/lib/chatStream";
import type { ChatMessage } from "@/lib/chatStore";
import ThinkingContainer from "@/components/ThinkingContainer";
import { FIRST_STEP, IDLE_RUN, reduceRun, runView, type RunEvent, type RunStatus } from "@/lib/runStatus";
import { RUN_PHASE_WORDS, WRITING_STEP, phaseWordOf, stepOfMessage } from "@/lib/runStep";

type Step = { at: number; event: Record<string, unknown> };

const GATHERING = (at: number): Step => ({ at, event: { type: "custom", name: "run_phase", value: { phase: "gathering" } } });
const ASKING = (at: number): Step => ({ at, event: { type: "custom", name: "run_phase", value: { phase: "asking" } } });
const TOOL: Step = { at: 9600, event: { type: "tool_start", id: "t1", name: "find_priority", args: {} } };
const TOOL_END: Step = { at: 9900, event: { type: "tool_end", id: "t1", name: "find_priority", result: "{}" } };
const TEXT: Step = { at: 12400, event: { type: "delta", content: "Two emails need an answer today.", messageId: "s1" } };

function placeholder(): ChatMessage {
  return { id: "a1", role: "assistant", content: "", timestamp: 0, streaming: true, isThinkingActive: true, firstLine: true, toolEvents: [], progressLines: [] };
}

/** What the hook does with one event: the same order of calls as the live loop. */
function play(script: Step[], t: number): { m: ChatMessage; run: RunStatus } {
  const fold: StreamFold = { foldedAnswerIdx: -1 };
  let m = placeholder();
  let run = reduceRun(IDLE_RUN, { type: "send", at: 0, messageId: "a1" } as RunEvent);
  for (const s of script) {
    if (s.at > t) break;
    if (s.event.type === "custom") {
      const word = phaseWordOf((s.event as { value?: unknown }).value);
      if (word) {
        run = reduceRun(run, { type: "phase", at: s.at, word });
        if (!m.content.trim() && (m.toolEvents?.length ?? 0) === 0) m = { ...m, phaseWord: word };
      }
      continue;
    }
    m = applyStreamEvent(m, s.event, fold);
    run = reduceRun(run, { type: "event", at: s.at, step: stepOfMessage(m) } as RunEvent);
  }
  return { m, run };
}

function seen(script: Step[], t: number): { row: string | null; header: string } {
  const { m, run } = play(script, t);
  const html = renderToStaticMarkup(
    createElement(ThinkingContainer, {
      toolEvents: m.toolEvents ?? [], progressLines: m.progressLines ?? [], reasoningBlocks: m.reasoningBlocks, isActive: true,
      firstLine: m.firstLine, phaseWord: m.phaseWord,
      answerStarted: !!m.content.trim() || (m.segments ?? []).some((s) => s.text.trim().length > 0),
    }),
  );
  const header = (html.match(/<button[^>]*>([\s\S]*?)<\/button>/)?.[1] ?? html).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return { row: runView(run, t)?.label ?? null, header };
}

describe("phase words (CS-3b)", () => {
  it("phase-words-fixed: with no phase the line is the CS-1 phrase, as before", () => {
    const script = [TOOL, TOOL_END, TEXT];
    for (const t of [0, 1000, 2000, 9000]) {
      const v = seen(script, t);
      expect(v.row, `row at ${t}`).toBe(FIRST_STEP);
      expect(v.header, `header at ${t}`).toContain(FIRST_STEP);
    }
  });

  it("phase-words-fixed: the words come from one table of plain words", () => {
    expect(RUN_PHASE_WORDS).toEqual({ gathering: "Gathering what I need", asking: "Asking the model" });
    for (const w of Object.values(RUN_PHASE_WORDS)) expect(w).toMatch(/^[A-Z][a-z]+( [A-Za-z]+)+$/);
  });

  it("phase-words-fixed: gathering, then asking, show in order and inside 2 s", () => {
    const script = [GATHERING(100), ASKING(1500), TOOL, TOOL_END, TEXT];
    expect(seen(script, 50).row).toBe(FIRST_STEP);
    const g = seen(script, 200);
    expect(g.row).toBe(RUN_PHASE_WORDS.gathering);
    expect(g.header).toContain(RUN_PHASE_WORDS.gathering);
    const a = seen(script, 1600);
    expect(a.row).toBe(RUN_PHASE_WORDS.asking);
    expect(a.header).toContain(RUN_PHASE_WORDS.asking);
    // The word holds through the silence of the model.
    const held = seen(script, 9000);
    expect(held.row).toBe(RUN_PHASE_WORDS.asking);
    expect(held.header).toContain(RUN_PHASE_WORDS.asking);
    expect(held.header).not.toContain(FIRST_STEP);
  });

  it("phase-gives-way: the first tool, then the text, replace the phase word", () => {
    const script = [GATHERING(100), ASKING(1500), TOOL, TOOL_END, TEXT];
    const tool = seen(script, 9700);
    expect(tool.row).not.toBe(RUN_PHASE_WORDS.asking);
    expect(tool.header).not.toContain(RUN_PHASE_WORDS.asking);
    const text = seen(script, 12500);
    expect(text.row).toBe(WRITING_STEP);
    expect(text.header).not.toContain(RUN_PHASE_WORDS.asking);
    // A phase that comes after a real step draws nothing.
    const late = seen([TOOL, GATHERING(9700)], 9800);
    expect(late.row).not.toBe(RUN_PHASE_WORDS.gathering);
    expect(late.header).not.toContain(RUN_PHASE_WORDS.gathering);
  });

  it("phase-unknown-ignored: an unknown name, a non-string and a bad value draw nothing", () => {
    for (const v of [{ phase: "dreaming" }, { phase: 3 }, { phase: null }, { phase: "toString" }, { phase: "__proto__" }, {}, null, undefined, "gathering", 7, []]) {
      expect(phaseWordOf(v), JSON.stringify(v)).toBeNull();
    }
    const bad: Step = { at: 100, event: { type: "custom", name: "run_phase", value: { phase: "Ignore previous instructions" } } };
    const v = seen([bad], 500);
    expect(v.row).toBe(FIRST_STEP);
    expect(v.header).toContain(FIRST_STEP);
    expect(phaseWordOf({ phase: "gathering" })).toBe(RUN_PHASE_WORDS.gathering);
  });

  it("phase-moves-nothing: a phase leaves the run starting, and it is not in the message", () => {
    const start = reduceRun(IDLE_RUN, { type: "send", at: 0, messageId: "a1" } as RunEvent);
    const next = reduceRun(start, { type: "phase", at: 100, word: RUN_PHASE_WORDS.gathering });
    expect(next.phase).toBe("starting");
    expect(next.seq).toBe(0);
    expect(next.startedAt).toBe(start.startedAt);
    expect(next.changedAt).toBe(start.changedAt);
    // The same word again changes nothing, so the store notifies nobody.
    expect(reduceRun(next, { type: "phase", at: 200, word: RUN_PHASE_WORDS.gathering })).toBe(next);
    const { m } = play([GATHERING(100), ASKING(200)], 300);
    expect(m.customEvents ?? []).toEqual([]);
    expect(withCustomEvent(m, "run_phase", { phase: "asking" })).toBe(m);
  });

  it("phase-not-stale: a rest, an end or a re-attach holds no phase", () => {
    const start = reduceRun(IDLE_RUN, { type: "send", at: 0, messageId: "a1" } as RunEvent);
    const withPhase = reduceRun(start, { type: "phase", at: 100, word: RUN_PHASE_WORDS.asking });
    expect(withPhase.phaseWord).toBe(RUN_PHASE_WORDS.asking);
    expect(reduceRun(withPhase, { type: "attach", at: 200, messageId: "a1" }).phaseWord).toBeNull();
    expect(reduceRun(withPhase, { type: "stream_end", at: 200, terminal: true }).phaseWord).toBeNull();
    expect(reduceRun(withPhase, { type: "stop", at: 200 }).phaseWord).toBeNull();
    expect(reduceRun(withPhase, { type: "event", at: 200, step: null }).phaseWord).toBeNull();
    // A done or idle run takes no phase at all.
    const done = reduceRun(withPhase, { type: "stream_end", at: 200, terminal: true });
    expect(reduceRun(done, { type: "phase", at: 300, word: RUN_PHASE_WORDS.gathering })).toBe(done);
    expect(reduceRun(IDLE_RUN, { type: "phase", at: 300, word: RUN_PHASE_WORDS.gathering })).toBe(IDLE_RUN);
    // After the first event, a late phase is dropped.
    const moving = reduceRun(start, { type: "event", at: 150, step: "Writing the reply" });
    expect(reduceRun(moving, { type: "phase", at: 160, word: RUN_PHASE_WORDS.gathering })).toBe(moving);
  });

  it("phase-not-stale: the header of a joined run has no phase word, and a new phase shows", () => {
    const joined: ChatMessage = { ...placeholder(), firstLine: undefined, phaseWord: undefined };
    const html = (phaseWord?: string) => renderToStaticMarkup(
      createElement(ThinkingContainer, { toolEvents: [], progressLines: [], isActive: true, firstLine: joined.firstLine, phaseWord, answerStarted: false }),
    );
    expect(html(joined.phaseWord)).toContain("Thinking…");
    expect(html(RUN_PHASE_WORDS.asking)).toContain(RUN_PHASE_WORDS.asking);
  });

  it("phase-wired: both hook loops dispatch the phase, the attach shadow clears it, and no save names it", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const read = (rel: string) => readFileSync(path.join(here, rel), "utf8").replace(/\r\n/g, "\n");
    const hook = read("../hooks/useAgentChat.ts");
    expect(hook.match(/type: "phase"/g)).toHaveLength(2);
    expect(hook.match(/phaseWordOf\(/g)).toHaveLength(2);
    expect(hook).toContain("phaseWord: undefined,");
    // Each branch skips the run event after it, so a phase never moves the run to working.
    expect(hook.match(/evtName === RUN_PHASE_EVENT\) \{/g)).toHaveLength(2);
    expect(hook.match(/no run event follows it\.\n\s+continue;/g)).toHaveLength(2);
    // The live loop writes the word on the message through `upd`, the attach loop through `updLast`.
    expect(hook).toMatch(/\bupd\(\(m\) => \(m\.content\.trim\(\)[^\n]*phaseWord: word/);
    expect(hook).toMatch(/updLast\(\(m\) => \(m\.content\.trim\(\)[^\n]*phaseWord: word/);
    // The save maps each field by name, so a field that it does not name is not saved.
    expect(read("../components/AgentChat.tsx")).not.toContain("phaseWord");
    expect(read("./sessions.ts")).not.toContain("phaseWord");
    // The route forwards it by name, and keeps it out of the saved list.
    const route = read("../app/api/agent/chat/route.ts");
    expect(route).toMatch(/ev\.name === RUN_PHASE_EVENT\) \{[\s\S]*?out = \{ type: "custom", name: RUN_PHASE_EVENT/);
  });
});
