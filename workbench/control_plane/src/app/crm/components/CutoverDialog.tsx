"use client";

/**
 * "Switch to Metorite" — the dialog of the switch from Zoho (WS-53 CRM-Z10b,
 * spec `crm_platform.md` §3.4, D133).
 *
 * Modelled on `StartOverDialog`. Up to three steps for the admin: what
 * happens, with the counts behind a fold. A "Leave out" tick for each module
 * that is not complete. Then the typed `SWITCH`. Then the dialog runs the
 * final sync, reads its status every 3 seconds, switches, and shows what Zoho
 * said to the revoke. It is a `Modal` with the shared primitives, so it has
 * the focus trap, Escape and the scrim of every other dialog.
 *
 * `CutoverBody` only draws one step, so a test can render each one. The
 * dialog owns the state through `switchReducer` and makes the calls through
 * `runSwitch` (`lib/cutoverSwitch.ts`). The UI is a courtesy. The gateway is
 * the boundary, and it checks each step again.
 */

import { useReducer } from "react";

import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import {
  STATE_WORDS,
  formatNumber,
  getCutoverStatus,
  incompleteOf,
  movingTotal,
  startCutover,
  switchToMetorite,
  type CutoverPreview,
} from "../lib/cutover";
import {
  STEP_TITLES,
  STEP_WORDS,
  SWITCH_FAILED,
  SWITCH_LABEL,
  SWITCH_WORD,
  allLeftOut,
  canSwitch,
  closeSwitch,
  initialSwitch,
  progressWords,
  resultLines,
  runSwitch,
  switchReducer,
  type SwitchEvent,
  type SwitchState,
} from "../lib/cutoverSwitch";
import { invalidateCrmMode } from "../lib/useCrmMode";

function Counts({ preview }: { preview: CutoverPreview }) {
  const reordered = preview.pipelines_in_another_order ?? 0;
  return (
    <CollapsibleSection label="Show the counts" defaultOpen={false}>
      <dl className="mt-2 divide-y divide-border/60 rounded-lg border border-border">
        {preview.records.map((row) => (
          <div key={row.key} className="flex items-center gap-3 px-3 py-1.5 text-xs">
            <dt className="min-w-0 flex-1 break-words text-muted-foreground">{row.label}</dt>
            <dd className="tabular-nums text-foreground">{formatNumber(row.metorite.from_zoho)}</dd>
          </div>
        ))}
        <div className="flex items-center gap-3 px-3 py-1.5 text-xs font-medium">
          <dt className="flex-1 text-foreground">From Zoho, in all</dt>
          <dd className="tabular-nums text-foreground">{formatNumber(movingTotal(preview))}</dd>
        </div>
      </dl>
      <ul className="mt-2 space-y-1 text-[11px] text-muted-foreground">
        {preview.unmatched_owner_names > 0 && (
          <li>
            {formatNumber(preview.unmatched_owner_names)} Zoho owner
            {preview.unmatched_owner_names === 1 ? "" : "s"} with no Metorite member. Their
            records keep the name and stay without an owner.
          </li>
        )}
        {preview.stays_in_zoho.length > 0 && (
          <li>Stays in Zoho: {preview.stays_in_zoho.join(", ")}.</li>
        )}
        {reordered > 0 && (
          <li>
            {formatNumber(reordered)} pipeline{reordered === 1 ? " lists" : "s list"} stages in
            another order. Metorite uses one order.
          </li>
        )}
      </ul>
    </CollapsibleSection>
  );
}

export function CutoverBody({
  state,
  preview,
  onEvent,
  onSwitch,
  onCancel,
  onClose,
}: {
  state: SwitchState;
  preview: CutoverPreview;
  onEvent: (event: SwitchEvent) => void;
  onSwitch: () => void;
  onCancel: () => void;
  onClose: () => void;
}) {
  const gaps = incompleteOf(preview);
  const shown = state.steps.filter(
    (step): step is keyof typeof STEP_TITLES => step in STEP_TITLES,
  );
  const at = shown.indexOf(state.step as keyof typeof STEP_TITLES);
  const asking = state.step !== "progress" && state.step !== "done";
  return (
    <div className="flex flex-col gap-4 px-5 pb-5">
      {asking && (
        <ol className="flex gap-2 text-[11px]" aria-label="Steps">
          {shown.map((step, i) => (
            <li
              key={step}
              aria-current={i === at ? "step" : undefined}
              className={i === at ? "font-medium text-foreground" : "text-muted-foreground"}
            >
              {`${i + 1}. ${STEP_TITLES[step]}`}
            </li>
          ))}
        </ol>
      )}

      {state.step === "intro" && (
        <>
          <p className="text-xs text-foreground">{STEP_WORDS.intro}</p>
          <Counts preview={preview} />
        </>
      )}

      {state.step === "leave" && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-foreground">{STEP_WORDS.leave}</p>
          <ul className="divide-y divide-border/60 rounded-lg border border-border">
            {gaps.map((row) => {
              const id = `crm-leave-out-${row.key.replace(/[^a-z0-9]/gi, "-")}`;
              return (
                <li key={row.key} className="flex items-start gap-2 px-3 py-2 text-xs">
                  <Checkbox
                    id={id}
                    checked={state.leaveOut.includes(row.key)}
                    onChange={(e) => onEvent({ type: "tick", key: row.key, on: e.target.checked })}
                  />
                  <label htmlFor={id} className="min-w-0 flex-1 break-words">
                    <span className="text-foreground">{`Leave out ${row.label}`}</span>
                    <span className="block text-[11px] text-muted-foreground">
                      {STATE_WORDS[row.reason]}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {!allLeftOut(state, gaps) && (
            <p className="text-[11px] text-muted-foreground">
              Tick each module to go on, or fix it in Zoho and sync first.
            </p>
          )}
        </div>
      )}

      {state.step === "confirm" && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-foreground">{STEP_WORDS.confirm}</p>
          {state.leaveOut.length > 0 && (
            <p className="text-[11px] text-muted-foreground">
              Left out:{" "}
              {gaps
                .filter((row) => state.leaveOut.includes(row.key))
                .map((row) => row.label)
                .join(", ")}
              .
            </p>
          )}
          <label className="text-xs text-muted-foreground" htmlFor="crm-switch-word">
            {`Type ${SWITCH_WORD} to confirm.`}
          </label>
          <Input
            id="crm-switch-word"
            value={state.typed}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={state.error ? true : undefined}
            aria-describedby={state.error ? "crm-switch-error" : undefined}
            onChange={(e) => onEvent({ type: "type", text: e.target.value })}
          />
        </div>
      )}

      {state.step === "progress" && (
        <p role="status" className="text-xs text-foreground">
          {progressWords(state.phase)}
        </p>
      )}

      {state.step === "done" && state.result && (
        <div role="status" className="flex flex-col gap-1.5">
          {resultLines(state.result).map((line) => (
            <p key={line} className="text-xs text-foreground">
              {line}
            </p>
          ))}
        </div>
      )}

      {state.error && (
        <p id="crm-switch-error" role="alert" className="text-xs text-destructive">
          {state.error}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {state.step === "done" ? (
          <Button variant="primary" size="sm" onClick={onClose}>
            Close
          </Button>
        ) : (
          <>
            {at > 0 && state.step !== "progress" ? (
              <Button variant="ghost" size="sm" onClick={() => onEvent({ type: "back" })}>
                Back
              </Button>
            ) : (
              <Button variant="ghost" size="sm" disabled={state.busy} onClick={onCancel}>
                Cancel
              </Button>
            )}
            {state.step === "confirm" || state.step === "progress" ? (
              <Button
                variant="primary"
                size="sm"
                icon="ArrowRightLeft"
                loading={state.busy}
                disabled={!canSwitch(state)}
                onClick={onSwitch}
              >
                {SWITCH_LABEL}
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                disabled={state.step === "leave" && !allLeftOut(state, gaps)}
                onClick={() => onEvent({ type: "next" })}
              >
                Next
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function CutoverDialog({
  open,
  preview,
  onClose,
}: {
  open: boolean;
  preview: CutoverPreview;
  onClose: () => void;
}) {
  const [state, send] = useReducer(switchReducer, preview, initialSwitch);

  async function go() {
    if (!canSwitch(state)) return;
    send({ type: "submit" });
    const out = await runSwitch(state.leaveOut, {
      start: startCutover,
      status: getCutoverStatus,
      commit: switchToMetorite,
      wait,
      onPhase: (phase) => send({ type: "phase", phase }),
    });
    if ("result" in out) send({ type: "done", result: out.result });
    else send({ type: "failed", message: out.error || SWITCH_FAILED });
  }

  const close = () => closeSwitch(state, { reload: invalidateCrmMode, close: onClose });

  return (
    <Modal
      open={open}
      onClose={close}
      title={SWITCH_LABEL}
      icon="AlertTriangle"
      size="md"
      layer="alert"
    >
      <CutoverBody
        state={state}
        preview={preview}
        onEvent={send}
        onSwitch={() => void go()}
        onCancel={close}
        onClose={close}
      />
    </Modal>
  );
}
