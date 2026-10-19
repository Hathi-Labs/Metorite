// ── Re-run the AI analysis on demand (WS-53 CRM-Q32-10) ───────────────────
//
// Spec: project-docs/specs/crm_platform.md section 18.14.14. An admin starts
// the analysis that a sync runs, now: the modules and fields, the note
// labels, then the open questions and the suggestions.
//
// The gateway runs it (`POST /crm/analysis/rerun`) and reports it
// (`GET /crm/analysis/status`). This file holds the wire types, every word
// of the button and the rule of the progress line, as pure functions, so
// the test holds them without a browser. No engine word reaches the screen.

import { call, CrmError } from "./api";

// ── The wire ───────────────────────────────────────────────────────────────

export type AnalysisStep = "mapping" | "labels" | "questions";
export type StepState = "running" | "done" | "skipped";
export type AnalysisOutcome = "done" | "limit" | "failed";

/** One analysis (`AnalysisModel`). */
export type AnalysisRun = {
  id: string;
  state: "running" | "finished";
  started_at: string;
  finished_at: string | null;
  steps: Partial<Record<AnalysisStep, StepState>>;
  outcome: AnalysisOutcome | null;
};

/** `POST /crm/analysis/rerun`. `started` is false when one already ran. */
export type AnalysisStarted = AnalysisRun & { started: boolean };

/**
 * `GET /crm/analysis/status`. `allowed` is false when no step of the
 * analysis can run for the org, and the button then hides.
 */
export type AnalysisStatus = { run: AnalysisRun | null; allowed?: boolean };

export function rerunAnalysis(): Promise<AnalysisStarted> {
  return call<AnalysisStarted>("/analysis/rerun", { method: "POST" });
}

export function analysisStatus(): Promise<AnalysisStatus> {
  return call<AnalysisStatus>("/analysis/status");
}

/** How often the button reads the status while an analysis runs. */
export const POLL_MS = 2_000;

// ── The words ──────────────────────────────────────────────────────────────

export const RERUN_LABEL = "Re-run AI analysis";
export const RUNNING_LABEL = "Analysing…";
export const RERUN_HINT =
  "Look at your CRM data again now, instead of after the next sync. Answers you gave stay as they are.";

/** The words of each step, in the order the analysis runs them. */
export const STEP_WORDS: Record<AnalysisStep, string> = {
  mapping: "Reading your modules and fields",
  labels: "Reading your notes",
  questions: "Updating the questions and suggestions",
};

const ORDER: AnalysisStep[] = ["mapping", "labels", "questions"];

/**
 * The progress line while an analysis runs: the first step that runs, and
 * its place among the steps that do not skip. Null when nothing runs.
 */
export function progressLine(run: AnalysisRun | null): string | null {
  if (!run || run.state !== "running") return null;
  const live = ORDER.filter((s) => run.steps[s] !== "skipped");
  const current = live.find((s) => run.steps[s] !== "done");
  if (!current) return "Finishing…";
  return `Step ${live.indexOf(current) + 1} of ${live.length}: ${STEP_WORDS[current]}…`;
}

/** The line after an analysis ends. */
export function outcomeLine(outcome: AnalysisOutcome | null): string {
  if (outcome === "limit") {
    return "The analysis stopped at the AI cost limit. What it found is saved. The rest runs after a later sync.";
  }
  if (outcome === "failed") return "The analysis did not finish. Try again later.";
  return "Analysis done. The questions and suggestions are up to date.";
}

/** The words of a refused press. */
export function rerunError(err: unknown): string {
  if (err instanceof CrmError) {
    if (err.status === 403) return "Only a CRM admin can re-run the analysis.";
    if (err.status === 404) return "The AI analysis is not on for your organization.";
    if (err.status === 409) return "The analysis is for a CRM that mirrors Zoho.";
  }
  return "The analysis did not start. Try again.";
}

/** What the button draws. */
export type RerunView = { label: string; busy: boolean; line: string | null };

export function rerunView(run: AnalysisRun | null, pressing: boolean): RerunView {
  const running = pressing || run?.state === "running";
  return {
    label: running ? RUNNING_LABEL : RERUN_LABEL,
    busy: running,
    line: running ? (progressLine(run) ?? "Starting…") : null,
  };
}

/** True when a read shows that the analysis this tab watched has ended. */
export function justFinished(before: AnalysisRun | null, after: AnalysisRun | null): boolean {
  return !!before && before.state === "running" && !!after && after.id === before.id
    && after.state === "finished";
}
