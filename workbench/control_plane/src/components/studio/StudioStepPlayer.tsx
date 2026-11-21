"use client";

/**
 * The guided step player (`ai_studio.md` §14 D14, slice STU-D14a).
 *
 * One step of a run on each screen, with Back and Next, the progress, the title, the instruction,
 * the photo, the required check and a sign-off button. `StepView` draws from props alone, so a
 * test can draw each case. `StudioStepPlayer` holds the state: the place, the check, the note and
 * the sign-offs of this sitting. The rules (order, gate, write, sign-off line) are in
 * `stepPlayerModel.ts`. Fence: `StudioStepPlayer.test.ts`.
 *
 * A viewer (`canSign` false) sees each step and its result. It gets no sign-off, and the player
 * makes no `run` call. A row that lacks a field draws that part empty.
 * ⚠️ D14a runs on sample data. The live door waits for D14b.
 */
import { useId, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import { FormView } from "@/components/studio/StudioForm";
import Button from "@/components/ui/Button";
import EntityPill from "@/components/ui/EntityPill";
import ProgressBar from "@/components/ui/ProgressBar";
import { statusAccent } from "@/lib/statusAccent";
import type { Draft } from "@/lib/studio/blocks";
import type { FileAddress, Row, StudioDataDoor } from "@/lib/studio/dataDoor";
import type { FieldSpec } from "@/lib/studio/fieldCheck";
import {
  doneLine, donePercent, initialTick, isDone, orderSteps, photoSrc, progressLine, requiresCheck,
  resumeAt, signOff, signedLine, textOf, updateAction,
} from "@/lib/studio/stepPlayerModel";

const CHECK_FIELD: FieldSpec = { key: "check", type: "checkbox", label: "I did this check", required: true };
const NOTE_FIELD: FieldSpec = { key: "note", type: "text", label: "Note" };

/** The photo of a step, or its placeholder. Both keep one box. */
function Photo({ src }: { src: string | null }) {
  return (
    <div className="flex aspect-[4/3] w-full max-w-xs items-center justify-center overflow-hidden rounded-md bg-muted text-muted-foreground">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" decoding="async" className="size-full object-scale-down" />
      ) : (
        <span data-step-placeholder="" aria-hidden><Icon name="Image" size={20} /></span>
      )}
    </div>
  );
}

/** The state of a step as a chip. The hue comes from `statusAccent`. */
function DoneChip({ done }: { done: boolean }) {
  const name = done ? "Done" : "To do";
  return (
    <EntityPill fit kind="status" label={name}
      accent={statusAccent({ name, index: done ? 1 : 0, total: 2 })} />
  );
}

export interface StepViewProps {
  /** The steps in order. */
  steps: readonly Row[];
  index: number;
  canSign: boolean;
  address: FileAddress | null;
  ticked: boolean;
  note: string;
  busy?: boolean;
  /** The plain alert under the sign-off: a refused press, or a refusal of the door. */
  alert?: string | null;
  formId: string;
  onBack: () => void;
  onNext: () => void;
  onTick: (v: boolean) => void;
  onNote: (v: string) => void;
  onSign: () => void;
}

/** One screen of the player, from props alone. */
export function StepView({
  steps, index, canSign, address, ticked, note, busy = false, alert = null, formId,
  onBack, onNext, onTick, onNote, onSign,
}: StepViewProps) {
  if (steps.length === 0) return <p className="text-xs text-muted-foreground">No steps</p>;
  const at = Math.min(Math.max(0, index), steps.length - 1);
  const step = steps[at];
  const done = isDone(step);
  const signed = signedLine(step);
  const result = textOf(step, "note");
  const fields = requiresCheck(step) ? [CHECK_FIELD, NOTE_FIELD] : [NOTE_FIELD];
  return (
    <div aria-label="Step player" className="space-y-3">
      <ProgressBar percent={donePercent(steps)} label="Steps done" detail={progressLine(at, steps.length)} />
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 data-step-title="" className="text-sm font-semibold text-foreground">{textOf(step, "title")}</h3>
          <DoneChip done={done} />
        </div>
        {textOf(step, "instruction") ? (
          <p data-step-instruction="" className="whitespace-pre-wrap text-sm text-foreground">
            {textOf(step, "instruction")}
          </p>
        ) : null}
        <Photo src={photoSrc(step, address)} />
      </div>
      {done ? (
        <div role="status" className="space-y-1 text-xs text-muted-foreground">
          <p data-step-signed="">{signed ?? "This step is done."}</p>
          {result ? <p data-step-note="">Note: {result}</p> : null}
        </div>
      ) : canSign ? (
        <FormView
          fields={fields}
          drafts={{ check: ticked, note }}
          errors={{}}
          formId={formId}
          submitLabel="Sign off this step"
          submitting={busy}
          status={alert ? { ok: false, text: alert } : null}
          onDraft={(key: string, d: Draft) => (key === "check" ? onTick(d === true) : onNote(typeof d === "string" ? d : ""))}
          onBlur={() => undefined}
          onSubmit={onSign}
        />
      ) : (
        <p data-step-open="" className="text-xs text-muted-foreground">Not signed off yet.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" size="sm" disabled={at === 0} onClick={onBack}>Back</Button>
        <Button variant="secondary" size="sm" disabled={at >= steps.length - 1} onClick={onNext}>Next</Button>
        <span data-step-done="" className="ml-auto text-xs text-muted-foreground">{doneLine(steps)}</span>
      </div>
    </div>
  );
}

export interface StudioStepPlayerProps {
  /** The rows of the steps, as the door read them. The player sorts them. */
  rows: readonly Row[];
  door: StudioDataDoor;
  /** The object of the step lines, as in `object:<name>.update`. */
  object: string;
  /** False for a viewer: no update action, so no sign-off and no `run` call. */
  canSign: boolean;
}

export function StudioStepPlayer({ rows, door, object, canSign }: StudioStepPlayerProps) {
  const formId = useId().replace(/:/g, "");
  const [signs, setSigns] = useState<Record<string, Row>>({});
  const steps = useMemo(() => orderSteps(rows).map((s) => ({ ...s, ...(signs[String(s.id)] ?? {}) })), [rows, signs]);
  const [index, setIndex] = useState(() => resumeAt(orderSteps(rows)));
  const step = steps[Math.min(index, steps.length - 1)];
  const [ticks, setTicks] = useState<Record<string, boolean>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [alert, setAlert] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const id = String(step?.id ?? "");

  const go = (to: number) => {
    setAlert(null);
    setIndex(Math.min(Math.max(0, to), Math.max(0, steps.length - 1)));
  };
  const sign = async () => {
    if (!canSign || !step || busy) return;
    setBusy(true);
    setAlert(null);
    const out = await signOff(door, updateAction(object), step, ticks[id] ?? initialTick(step), notes[id]);
    setBusy(false);
    if (!out.ok) {
      // The member stays on the step, and reads why.
      setAlert(out.message);
      return;
    }
    const note = (notes[id] ?? "").trim();
    setSigns((s) => ({ ...s, [id]: { done: true, ...(note ? { note } : {}), ...(out.meta ?? {}) } }));
    // The member stays here and reads the sign-off. Next moves on.
  };

  return (
    <StepView steps={steps} index={index} canSign={canSign} address={door.fileAddress ?? null}
      ticked={ticks[id] ?? initialTick(step)} note={notes[id] ?? ""} busy={busy} alert={alert} formId={formId}
      onBack={() => go(index - 1)} onNext={() => go(index + 1)}
      onTick={(v) => { setAlert(null); setTicks((t) => ({ ...t, [id]: v })); }}
      onNote={(v) => setNotes((n) => ({ ...n, [id]: v }))}
      onSign={sign} />
  );
}

export default StudioStepPlayer;
