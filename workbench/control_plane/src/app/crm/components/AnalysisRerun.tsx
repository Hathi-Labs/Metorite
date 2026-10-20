"use client";

// ── "Re-run AI analysis" (WS-53 CRM-Q32-10) ────────────────────────────────
//
// Spec: project-docs/specs/crm_platform.md section 18.14.14. The button of
// the Open questions and Suggestions sections, for an admin in Mirror mode.
// A press starts the analysis that a sync runs. While it runs, the button
// says "Analysing…" with a progress line, and reads the status every
// `POLL_MS`. When it ends, every CRM read is read again, so the questions
// and the suggestions show what it found. The words and the rules live in
// `lib/analysis.ts`, where the test holds them.

import { useCallback, useEffect, useRef, useState } from "react";

import Button from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import {
  POLL_MS,
  RERUN_HINT,
  analysisStatus,
  justFinished,
  outcomeLine,
  rerunAnalysis,
  rerunError,
  rerunView,
  type AnalysisRun,
} from "../lib/analysis";

export default function AnalysisRerun() {
  const toast = useToast();
  const [run, setRun] = useState<AnalysisRun | null>(null);
  // `null` until the status read answers. False hides the button: no step of
  // the analysis can run for this org.
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [pressing, setPressing] = useState(false);
  // The run of the last read, for the "it just ended" check. A ref, because
  // a poll's `.then` must compare against the newest read, not its render's.
  const last = useRef<AnalysisRun | null>(null);

  const apply = useCallback(
    (next: AnalysisRun | null) => {
      const before = last.current;
      last.current = next;
      setRun(next);
      if (next && justFinished(before, next)) {
        // Read every CRM key again: the questions and the suggestions changed.
        invalidate("crm:");
        toast.show({
          key: "crm-analysis",
          variant: next.outcome === "done" ? "success" : "error",
          title: outcomeLine(next.outcome),
        });
      }
    },
    [toast],
  );

  // The first read: an analysis that runs already shows as running.
  useEffect(() => {
    let alive = true;
    analysisStatus()
      .then((body) => {
        if (!alive) return;
        setAllowed(body.allowed !== false);
        apply(body.run);
      })
      .catch(() => {
        if (alive) setAllowed(true);
      });
    return () => {
      alive = false;
    };
  }, [apply]);

  const running = run?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      if (document.hidden) return;
      analysisStatus()
        .then((body) => apply(body.run))
        .catch(() => {
          /* the next tick reads again */
        });
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running, apply]);

  async function press() {
    setPressing(true);
    try {
      apply(await rerunAnalysis());
    } catch (err) {
      toast.show({ key: "crm-analysis", variant: "error", title: rerunError(err) });
    } finally {
      setPressing(false);
    }
  }

  if (allowed === false) return null;
  const view = rerunView(run, pressing);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <Button
        variant="secondary"
        size="sm"
        icon="Sparkles"
        loading={view.busy}
        disabled={view.busy || allowed === null}
        title={RERUN_HINT}
        onClick={() => void press()}
      >
        {view.label}
      </Button>
      {view.line ? (
        <p className="text-xs text-muted-foreground" role="status" aria-live="polite">
          {view.line}
        </p>
      ) : null}
    </div>
  );
}
