"use client";

/**
 * Projects · file import — the organization's recent imports, and Discard
 * (WS-41 I-6).
 *
 * Spec: `project-docs/specs/project_import.md` §6.9 "Discard".
 *
 * The list lets an admin open a run again after a page reload, and discard a
 * finished one for 14 days. The SERVER decides what a discard may remove and
 * when it must refuse. This file only asks, shows the answer, and names what
 * stopped it.
 */

import { useCallback, useEffect, useState } from "react";

import Badge, { type BadgeTone } from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";

import { ProjectsApiError } from "../lib/api";
import { importApi } from "../lib/importApi";
import {
  type DiscardRefusal,
  type ImportRunSummary,
  type RunState,
  discardLines,
  discardRefusal,
  runLine,
} from "../lib/importFlow";

/** The most runs the list shows. The route returns up to 50. */
const SHOWN = 5;

const TONE: Record<RunState, BadgeTone> = {
  uploaded: "neutral",
  planned: "neutral",
  applying: "primary",
  done: "success",
  failed: "destructive",
  discarded: "neutral",
};

export type DiscardOutcome = { ok: true; lines: string[] } | { ok: false; refusal: DiscardRefusal };

/**
 * "Discard this import", with its confirmation. Reports the outcome to the
 * caller, which draws it where the admin is looking.
 */
export function DiscardImportButton({
  runId,
  onOutcome,
  size,
}: {
  runId: string;
  onOutcome: (outcome: DiscardOutcome) => void;
  size?: "sm";
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  const discard = useCallback(async () => {
    setBusy(true);
    try {
      const out = await importApi.discard(runId);
      onOutcome({ ok: true, lines: discardLines(out.discarded) });
    } catch (err) {
      const detail = err instanceof ProjectsApiError ? err.detail : undefined;
      onOutcome({
        ok: false,
        refusal: discardRefusal(detail) ?? { message: (err as Error).message, blocking: [] },
      });
    } finally {
      setBusy(false);
      setAsking(false);
    }
  }, [runId, onOutcome]);

  return (
    <>
      <Button variant="secondary" size={size} icon="Trash2" onClick={() => setAsking(true)} disabled={busy}>
        Discard
      </Button>
      <ConfirmDialog
        open={asking}
        title="Discard this import?"
        body="This deletes the spaces, lists and tasks this import created, and the comments it added. It cannot be undone."
        note="If somebody changed an imported task or added work inside an imported space, the discard stops and names it. Nothing is deleted then."
        confirmLabel="Discard"
        confirmVariant="destructive"
        defaultFocus="cancel"
        icon="Trash2"
        busy={busy}
        onConfirm={() => void discard()}
        onCancel={() => setAsking(false)}
      />
    </>
  );
}

/** What a discard said, drawn in the dialog. */
export function DiscardNotice({ outcome }: { outcome: DiscardOutcome }) {
  if (outcome.ok) {
    return (
      <div role="status" className="rounded-md border border-border bg-card px-2 py-1.5 text-xs">
        <p className="font-medium">Discarded.</p>
        <ul className="list-disc pl-5">
          {outcome.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </div>
    );
  }
  const { message, blocking } = outcome.refusal;
  return (
    <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
      <p>{message}</p>
      {blocking.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {blocking.map((b) => (
            <li key={b.id}>{b.title || b.id}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function ImportHistory({
  refreshKey,
  onOpen,
  onOutcome,
}: {
  /** Bumped by the caller after a discard, so the list reads again. */
  refreshKey: number;
  onOpen: (runId: string) => void;
  onOutcome: (outcome: DiscardOutcome) => void;
}) {
  const [runs, setRuns] = useState<ImportRunSummary[] | null>(null);

  useEffect(() => {
    let live = true;
    importApi
      .list()
      .then((rows) => {
        if (!live) return;
        // An open run is replaced by the next upload, so it is not listed.
        setRuns(rows.filter((r) => r.state !== "uploaded" && r.state !== "planned").slice(0, SHOWN));
      })
      .catch(() => live && setRuns([]));
    return () => {
      live = false;
    };
  }, [refreshKey]);

  if (!runs || runs.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5 text-sm">
      <h3 className="text-xs font-semibold">Earlier imports</h3>
      <ul className="divide-y divide-border rounded-md border border-border">
        {runs.map((run) => {
          const line = runLine(run);
          return (
            <li key={run.id} className="flex flex-wrap items-center gap-2 px-2 py-1.5">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-xs">{line.when}</span>
                  <Badge tone={TONE[run.state]}>{line.state}</Badge>
                </div>
                <div className="text-[11px] text-muted-foreground">{line.what}</div>
              </div>
              {run.state !== "discarded" && (
                <Button variant="ghost" size="sm" onClick={() => onOpen(run.id)}>
                  Open
                </Button>
              )}
              {run.discardable && (
                <DiscardImportButton runId={run.id} size="sm" onOutcome={onOutcome} />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
