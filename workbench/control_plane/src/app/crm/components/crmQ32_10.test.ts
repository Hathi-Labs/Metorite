// "Re-run AI analysis" (WS-53 CRM-Q32-10), spec crm_platform.md §18.14.14,
// and the cache fix of the same batch: a CRM gate of org A must not hide a
// section of org B. The runner has no DOM, so the rules of the button live
// in `lib/analysis.ts`, and the wiring is read from the source.
import { readFileSync } from "node:fs";

import { afterEach, describe, expect, it } from "vitest";

import { bindIdentity, clearAll, peek, put, read as cacheRead } from "@/lib/dataCache";

import { CrmError } from "../lib/api";
import {
  RERUN_HINT,
  RERUN_LABEL,
  RUNNING_LABEL,
  STEP_WORDS,
  justFinished,
  outcomeLine,
  progressLine,
  rerunError,
  rerunView,
  type AnalysisRun,
} from "../lib/analysis";
import { QUESTIONS_KEY, questionsGate, readQuestions, type QuestionsBody } from "../lib/questions";

const source = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

function run(over: Partial<AnalysisRun> = {}): AnalysisRun {
  return {
    id: "r1",
    state: "running",
    started_at: "2026-10-19T12:00:00Z",
    finished_at: null,
    steps: { mapping: "running", labels: "skipped", questions: "running" },
    outcome: null,
    ...over,
  };
}

afterEach(() => {
  clearAll();
  bindIdentity(null);
});

describe("the button", () => {
  it("offers the analysis when nothing runs", () => {
    expect(rerunView(null, false)).toEqual({ label: RERUN_LABEL, busy: false, line: null });
    const ended = run({ state: "finished", outcome: "done", finished_at: "2026-10-19T12:01:00Z" });
    expect(rerunView(ended, false)).toEqual({ label: RERUN_LABEL, busy: false, line: null });
  });

  it("says Analysing… with a line while the press goes out, and while it runs", () => {
    expect(rerunView(null, true)).toEqual({ label: RUNNING_LABEL, busy: true, line: "Starting…" });
    const view = rerunView(run(), false);
    expect(view.label).toBe("Analysing…");
    expect(view.busy).toBe(true);
    expect(view.line).toBe(`Step 1 of 2: ${STEP_WORDS.mapping}…`);
  });

  it("counts only the steps that run, and moves on as each one ends", () => {
    expect(progressLine(run({ steps: { mapping: "done", labels: "skipped", questions: "running" } })))
      .toBe(`Step 2 of 2: ${STEP_WORDS.questions}…`);
    expect(progressLine(run({ steps: { mapping: "done", labels: "running", questions: "running" } })))
      .toBe(`Step 2 of 3: ${STEP_WORDS.labels}…`);
    expect(progressLine(run({ steps: { mapping: "done", labels: "done", questions: "done" } })))
      .toBe("Finishing…");
    expect(progressLine(run({ state: "finished" }))).toBeNull();
  });

  it("sees the end of the analysis it watched, and only that one", () => {
    const ended = run({ state: "finished", outcome: "done" });
    expect(justFinished(run(), ended)).toBe(true);
    expect(justFinished(null, ended)).toBe(false); // a finished run read at mount: no toast
    expect(justFinished(ended, ended)).toBe(false);
    expect(justFinished(run(), run({ id: "r2", state: "finished" }))).toBe(false);
  });

  it("says what happened in plain words", () => {
    expect(outcomeLine("done")).toBe("Analysis done. The questions and suggestions are up to date.");
    expect(outcomeLine("limit")).toContain("AI cost limit");
    expect(outcomeLine("failed")).toContain("did not finish");
    expect(rerunError(new CrmError("x", 403))).toBe("Only a CRM admin can re-run the analysis.");
    expect(rerunError(new CrmError("x", 404, "analysis_off"))).toContain("not on");
    expect(rerunError(new Error("net"))).toBe("The analysis did not start. Try again.");
  });

  it("names no engine word on the screen", () => {
    const words = [
      RERUN_LABEL, RUNNING_LABEL, RERUN_HINT, ...Object.values(STEP_WORDS),
      outcomeLine("done"), outcomeLine("limit"), outcomeLine("failed"),
      rerunError(new CrmError("x", 403)), rerunError(new CrmError("x", 404)),
      rerunError(new CrmError("x", 409)), rerunError(null),
    ].join(" ");
    expect(words).not.toMatch(/\bJev\b|\btier\b|\bdecide\b|pipeline/i);
  });
});

describe("the wiring", () => {
  it("draws the button in the Open questions and the Suggestions sections", () => {
    expect(source("./OpenQuestions.tsx")).toContain("tools={<AnalysisRerun />}");
    expect(source("./Suggestions.tsx")).toContain("tools={<AnalysisRerun />}");
  });

  it("reads every CRM key again when the analysis ends, and polls only while it runs", () => {
    const comp = source("./AnalysisRerun.tsx");
    expect(comp).toContain('invalidate("crm:")');
    expect(comp).toContain("if (!running) return;");
    expect(comp).toContain("if (allowed === false) return null;");
  });

  it("binds the org of the session to the read cache in the shell", () => {
    const shell = source("../../../components/AppShell.tsx");
    expect(shell).toContain("bindIdentity(signedInAs, cacheOrg);");
  });
});

describe("a gate of org A does not hide a section of org B (2026-10-19)", () => {
  const body: QuestionsBody = { open: 17, shown: [], more: 0, answered: [], plan: 2 };
  const off = () =>
    readQuestions(() => Promise.reject(new CrmError("questions_off", 404, "questions_off")));

  it("a switch of org drops the cached off gate, and the read of org B shows the section", async () => {
    bindIdentity("admin@example.com", "org-a");
    const a = await cacheRead(QUESTIONS_KEY, off);
    expect(questionsGate(a, false)).toBe("off");
    expect(peek(QUESTIONS_KEY)?.data).toEqual({ off: true });

    // The same member, now in org B.
    expect(bindIdentity("admin@example.com", "org-b")).toBe(true);
    expect(peek(QUESTIONS_KEY)).toBeUndefined();
    // Nothing cached: the gate waits, it does not say off.
    expect(questionsGate(undefined, false)).toBe("unknown");
    const b = await cacheRead(QUESTIONS_KEY, () => readQuestions(() => Promise.resolve(body)));
    expect(questionsGate(b, false)).toBe("on");
  });

  it("a cached off gate is read again on the next mount, past the fresh window", async () => {
    // A mount reads with `force: false`. A cached off answer is painted first
    // and replaced by the newer read. Here the entry is stale at once.
    put(QUESTIONS_KEY, { off: true });
    const again = await cacheRead(QUESTIONS_KEY, () => readQuestions(() => Promise.resolve(body)), {
      ttl: 0,
    });
    expect(questionsGate(again, false)).toBe("on");
  });
});
