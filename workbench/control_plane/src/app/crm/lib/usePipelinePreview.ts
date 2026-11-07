"use client";

// ── The preview of a new pipeline (WS-CRM CRM-M3a) ────────────────────────
//
// The dialog asks the gateway what a create would make
// (`GET /crm/pipelines/preview`). It asks 300 ms after the last change, and
// the answer to the newest draft wins (DESIGN_SYSTEM.md §11, M17). An older
// answer that comes late is dropped.

import { useEffect, useState } from "react";

import * as api from "./api";
import { CrmError, TRY_AGAIN } from "./api";
import { previewKey, type PipelinePlan } from "./newPipeline";

export type PipelinePreview =
  /** The draft has nothing to plan yet. */
  | { state: "idle" }
  /** A read runs. `plan` is the last answer, or null. */
  | { state: "loading"; plan: PipelinePlan | null }
  | { state: "ready"; plan: PipelinePlan }
  /**
   * The gateway refused the plan, and `message` holds its words. `retryable`
   * is true for a failed read (a 5xx or no answer), not for a refusal of the
   * plan: then Retry asks again, and Create stays open.
   */
  | { state: "refused"; message: string; retryable: boolean };

/** How long the dialog waits after a key before it asks. */
export const PREVIEW_DELAY_MS = 300;

/** The words of a failed read: what happened, and what to do next. */
export const PREVIEW_FAILED = "The plan did not load. Try again. Create still checks each stage.";

/** A failed read, not a refusal of the plan: no answer, or a 5xx. */
export function retryableFailure(err: unknown): boolean {
  return !(err instanceof CrmError) || err.status >= 500;
}

export function usePipelinePreview(
  body: Record<string, unknown> | null,
  delayMs = PREVIEW_DELAY_MS,
): PipelinePreview & { retry: () => void; lastPlan: PipelinePlan | null } {
  const [round, setRound] = useState(0);
  // The last plan that loaded. The rows keep the stored type of each stage
  // that it reuses while a new read runs or fails.
  const [lastPlan, setLastPlan] = useState<PipelinePlan | null>(null);
  const base = previewKey(body);
  // Each Retry is a new key, so the effect asks again for the same body.
  const key = base === null ? null : `${round}:${base}`;
  const [answer, setAnswer] = useState<{ key: string; value: PipelinePreview } | null>(null);
  useEffect(() => {
    if (!key || base === null) return;
    let live = true;
    const timer = setTimeout(() => {
      api.previewPipeline(JSON.parse(base) as Record<string, unknown>).then(
        (plan) => {
          if (!live) return;
          setAnswer({ key, value: { state: "ready", plan } });
          setLastPlan(plan);
        },
        (err: unknown) => {
          const retryable = retryableFailure(err);
          const message = retryable
            ? PREVIEW_FAILED
            : err instanceof Error && err.message
              ? err.message
              : TRY_AGAIN;
          if (live) setAnswer({ key, value: { state: "refused", message, retryable } });
        },
      );
    }, delayMs);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [key, base, delayMs]);
  const retry = () => setRound((n) => n + 1);
  if (!key) return { state: "idle", retry, lastPlan };
  if (answer?.key === key) return { ...answer.value, retry, lastPlan };
  return {
    state: "loading",
    plan: answer?.value.state === "ready" ? answer.value.plan : null,
    retry,
    lastPlan,
  };
}
