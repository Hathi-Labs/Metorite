"use client";

/**
 * The start-over dialog (WS-53 CRM-X0, spec `crm_platform.md` §19.11 item 5).
 *
 * Three steps: the counts, "Download a copy", and a typed `DELETE`. The final
 * button reads "Remove N records and continue". It is a `Modal` with the
 * shared primitives, so it has the focus trap, Escape and the scrim of every
 * other dialog.
 *
 * `StartOverBody` only draws one step, so a test can render each one. The
 * dialog owns the state through `startOverReducer` and makes the call through
 * `runStartOver` (`lib/startOver.ts`).
 */

import { useReducer } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import * as api from "../lib/api";
import { countLines, formatCount, type RecordCounts, type ResetThen } from "../lib/setup";
import {
  CONFIRM_WORD,
  INITIAL,
  STEPS,
  canConfirm,
  confirmLabel,
  countsSentence,
  runStartOver,
  startOverReducer,
  type StartOverEvent,
  type StartOverState,
} from "../lib/startOver";
import { invalidateCrmMode } from "../lib/useCrmMode";

const STEP_TITLES = ["Records", "Copy", "Confirm"] as const;

export function StartOverBody({
  state,
  explain,
  counts,
  onEvent,
  onDownload,
  onConfirm,
  onCancel,
}: {
  state: StartOverState;
  /** The one sentence of the action that opened the dialog. */
  explain: string;
  counts: RecordCounts | null;
  onEvent: (event: StartOverEvent) => void;
  onDownload: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const total = counts?.total ?? 0;
  const at = STEPS.indexOf(state.step);
  return (
    <div className="flex flex-col gap-4 px-5 pb-5">
      <ol className="flex gap-2 text-[11px]" aria-label="Steps">
        {STEP_TITLES.map((title, i) => (
          <li
            key={title}
            aria-current={i === at ? "step" : undefined}
            className={i === at ? "font-medium text-foreground" : "text-muted-foreground"}
          >
            {i + 1}. {title}
          </li>
        ))}
      </ol>

      <p className="text-xs text-foreground">{explain}</p>

      {state.step === "counts" && (
        <>
          <p className="text-xs text-muted-foreground">{countsSentence(total)}</p>
          <dl className="divide-y divide-border/60 rounded-lg border border-border">
            {countLines(counts?.counts).map((line) => (
              <div key={line.key} className="flex items-center gap-3 px-3 py-1.5 text-xs">
                <dt className="flex-1 text-muted-foreground">{line.label}</dt>
                <dd className="tabular-nums text-foreground">{formatCount(line.count)}</dd>
              </div>
            ))}
            <div className="flex items-center gap-3 px-3 py-1.5 text-xs font-medium">
              <dt className="flex-1 text-foreground">In all</dt>
              <dd className="tabular-nums text-foreground">{formatCount(total)}</dd>
            </div>
          </dl>
        </>
      )}

      {state.step === "download" && (
        <div className="flex flex-col items-start gap-2">
          <p className="text-xs text-muted-foreground">
            Download a copy first. The ZIP holds one CSV file for each kind of record, with every
            row. Metorite cannot give the records back after the change.
          </p>
          <Button
            variant="secondary"
            size="sm"
            icon={state.downloaded ? "Check" : "Download"}
            onClick={onDownload}
          >
            {state.downloaded ? "Download started" : "Download a copy"}
          </Button>
        </div>
      )}

      {state.step === "confirm" && (
        <div className="flex flex-col gap-2">
          <label className="text-xs text-muted-foreground" htmlFor="crm-start-over-word">
            Type {CONFIRM_WORD} to confirm.
          </label>
          <Input
            id="crm-start-over-word"
            value={state.typed}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => onEvent({ type: "type", text: e.target.value })}
          />
          {!state.downloaded && (
            <p className="flex items-center gap-1.5 text-[11px] text-warning">
              <Icon name="AlertTriangle" size={12} />
              You did not download a copy.
            </p>
          )}
        </div>
      )}

      {state.error && (
        <p role="alert" className="text-xs text-destructive">
          {state.error}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {at > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={state.busy}
            onClick={() => onEvent({ type: "back" })}
          >
            Back
          </Button>
        ) : (
          <Button variant="ghost" size="sm" onClick={onCancel}>
            Cancel
          </Button>
        )}
        {state.step === "confirm" ? (
          <Button
            variant="destructive"
            size="sm"
            icon="Trash2"
            loading={state.busy}
            disabled={!canConfirm(state)}
            onClick={onConfirm}
          >
            {confirmLabel(total)}
          </Button>
        ) : (
          <Button variant="primary" size="sm" onClick={() => onEvent({ type: "next" })}>
            {state.step === "download" && !state.downloaded ? "Go on without a copy" : "Next"}
          </Button>
        )}
      </div>
    </div>
  );
}

export default function StartOverDialog({
  open,
  title,
  explain,
  then,
  counts,
  onClose,
}: {
  open: boolean;
  title: string;
  explain: string;
  then: ResetThen;
  counts: RecordCounts | null;
  onClose: () => void;
}) {
  const [state, send] = useReducer(startOverReducer, INITIAL);

  async function confirm() {
    if (!canConfirm(state)) return;
    send({ type: "submit" });
    const failed = await runStartOver(then, {
      reset: api.resetCrm,
      invalidate: invalidateCrmMode,
      go: (url) => window.location.assign(url),
    });
    if (failed) {
      send({ type: "failed", message: failed });
      return;
    }
    if (then !== "mirror") onClose();
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!state.busy) onClose();
      }}
      title={title}
      icon="AlertTriangle"
      size="md"
      layer="alert"
    >
      <StartOverBody
        state={state}
        explain={explain}
        counts={counts}
        onEvent={send}
        onDownload={() => {
          api.downloadAllRecords();
          send({ type: "downloaded" });
        }}
        onConfirm={() => void confirm()}
        onCancel={onClose}
      />
    </Modal>
  );
}
