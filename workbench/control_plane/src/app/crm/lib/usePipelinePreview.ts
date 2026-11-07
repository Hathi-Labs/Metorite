"use client";

// ── The preview of a new pipeline (WS-CRM CRM-M3a) ────────────────────────
//
// The dialog asks the gateway what a create would make
// (`GET /crm/pipelines/preview`). It asks 300 ms after the last change, and
// the answer to the newest draft wins (DESIGN_SYSTEM.md §11, M17). An older
// answer that comes late is dropped.

import { useEffect, useState } from "react";

import * as api from "./api";
import { TRY_AGAIN } from "./api";
import { previewKey, type PipelinePlan } from "./newPipeline";

export type PipelinePreview =
  /** The draft has nothing to plan yet. */
  | { state: "idle" }
  /** A read runs. `plan` is the last answer, or null. */
  | { state: "loading"; plan: PipelinePlan | null }
  | { state: "ready"; plan: PipelinePlan }
  /** The gateway refused the plan. `message` holds its words. */
  | { state: "refused"; message: string };

/** How long the dialog waits after a key before it asks. */
export const PREVIEW_DELAY_MS = 300;

export function usePipelinePreview(
  body: Record<string, unknown> | null,
  delayMs = PREVIEW_DELAY_MS,
): PipelinePreview {
  const key = previewKey(body);
  const [answer, setAnswer] = useState<{ key: string; value: PipelinePreview } | null>(null);
  useEffect(() => {
    if (!key) return;
    let live = true;
    const timer = setTimeout(() => {
      api.previewPipeline(JSON.parse(key) as Record<string, unknown>).then(
        (plan) => {
          if (live) setAnswer({ key, value: { state: "ready", plan } });
        },
        (err: unknown) => {
          const message = err instanceof Error && err.message ? err.message : TRY_AGAIN;
          if (live) setAnswer({ key, value: { state: "refused", message } });
        },
      );
    }, delayMs);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [key, delayMs]);
  if (!key) return { state: "idle" };
  if (answer?.key === key) return answer.value;
  return { state: "loading", plan: answer?.value.state === "ready" ? answer.value.plan : null };
}
