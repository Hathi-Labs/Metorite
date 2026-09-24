"use client";

/**
 * Projects · "Move to project…" — WS-27bl §9.13.4.
 *
 * The card `remap_task_statuses` has referred to since it was written and
 * nobody had built: *"the answer a human gave in the mapping card"*.
 *
 * ## The order on screen IS the argument
 *
 * Destination, then what changes, then what is LOST, then the button. The
 * losses sit above the button and never in a toast afterwards (D-PM-29) —
 * a member who learns what a move cost after agreeing to it was not asked.
 *
 * ## Why every number comes from the server
 *
 * A move crosses two vocabularies, and only the server sees both at once.
 * `previewMove` resolves the status map, the field map, the drops, the tag
 * gaps and the type gap; this component renders that answer and posts back an
 * override. It computes none of it, so what you were shown and what happens
 * are one computation rather than two that drift.
 *
 * ## Two silences
 *
 * A mapping row whose source and destination agree needs no attention, so the
 * card leads with what CHANGES. And a destination that shares the task's
 * status set and root produces no mapping at all — the card then says the move
 * is clean rather than drawing three empty tables.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import SelectButton from "@/components/ui/SelectButton";

import type { FieldDef } from "../lib/customFields";
import { destinations, nameOf } from "../lib/destinations";
import { readPlan } from "../lib/movePlan";
import type { ProjectNode } from "../lib/tree";
import {
  MoveLosses,
  MoveMapping,
  PROMOTE_HINT,
  type PromoteAnswers,
  PromoteFields,
  type PromoteFieldsState,
  useMovePreview,
} from "./PromoteFields";

/**
 * S6c — what the promote door answers on top of the destination. The type
 * lives with `PromoteFields` since S6g, which both promote doors draw.
 */
export type { PromoteAnswers };

interface Props {
  /** The tasks to move. Empty or null closes the card. */
  taskIds: readonly string[] | null;
  /** Every node the member can see, for the destination picker. */
  roots: readonly ProjectNode[];
  onClose: () => void;
  /** Confirmed. The page owns the call, the toast and the refetch. */
  onConfirm: (
    destinationId: string,
    statusMap: Record<string, string>,
    acceptedDrops: string[] | null,
    promote?: PromoteAnswers
  ) => void;
  busy?: boolean;
  /** The server's refusal, rendered IN the card — see H-120's sibling note. */
  error?: string | null;
  /**
   * S6g — the destination the card opens on. The capture door sets it when a
   * `#Project` capture needs its required fields answered first.
   */
  initialDestination?: string | null;
  /**
   * S6c — the promote door. Set by My Tasks, absent on the Projects board.
   * The single-task route picks the lane itself (`remap_one_status`), so the
   * status rows read as a plain mapping here, with no override.
   */
  promote?: {
    /** Who owns the task now, pre-filled so a move never silently unassigns. */
    initialAssignees: string[];
    /** The task's due date, to sharpen the picker's availability warning. */
    due?: string | null;
    /**
     * The definitions a 422 named (`ProjectsApiError.detail.fields`). Drawn
     * as inputs beside the ones the preview asked for, so the member answers
     * the exact field the refusal named rather than reading its name.
     */
    refusedFields?: FieldDef[];
  };
}

export function MoveTasksDialog({
  taskIds,
  roots,
  onClose,
  onConfirm,
  busy,
  error,
  initialDestination,
  promote,
}: Props) {
  const [destination, setDestination] = useState<string | null>(initialDestination ?? null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  // S6g — the promote door's answers come up from `PromoteFields`, the one
  // component Clarify draws too.
  const [answers, setAnswers] = useState<PromoteFieldsState | null>(null);

  const ids = taskIds ?? [];
  const promoting = Boolean(promote);
  // The bulk path's preview. The promote path reads its own inside
  // `PromoteFields`, so this one stays idle there (no destination).
  const bulk = useMovePreview(ids, promoting ? null : destination, false);

  if (!taskIds || ids.length === 0) return null;

  const destRows = destinations(roots);
  // ⚠️ Every array the card reads off the preview is guarded ONCE, in
  // `readPlan`, and `movePlan.test.ts` feeds it the bulk e2e stub.
  const plan = bulk.plan;
  const reading = readPlan(plan);
  const drops = reading?.drops ?? [];
  const requiredMissing = reading?.requiredMissing ?? [];
  const sourceName = nameOf(destRows, plan?.source_project_id ?? null);
  // ⚠️ From the PICKED node first, not the plan's id (owner, 2026-09-19).
  const destName =
    nameOf(destRows, destination) || nameOf(destRows, plan?.destination_project_id ?? null);

  const promoteDrops = answers?.drops ?? null;
  const canMove = promoting
    ? !!destination && !!answers?.ready && !!answers.destinationProjectId
    : !!plan && !bulk.planning;
  const loud = promoting ? (promoteDrops?.length ?? 0) > 0 : drops.length > 0;

  return (
    <Modal
      open
      onClose={onClose}
      title={
        promote ? "Move to project" : ids.length === 1 ? "Move task" : `Move ${ids.length} tasks`
      }
      icon="FolderInput"
      size="md"
      description={
        promote
          ? `${PROMOTE_HINT} Statuses and fields are mapped into the project's own vocabulary.`
          : "Statuses and custom fields are mapped into the destination's own vocabulary."
      }
    >
      <div className="space-y-3 p-3 text-xs">
        <label className="block space-y-1">
          <span className="text-muted-foreground">Move to</span>
          {/* ⚠️ `SelectButton`, NOT the `Select` primitive (owner, 2026-09-20).
              `Select` is a native `<select>`, and the open list is drawn by the
              operating system. The tree survives the move: `depth` indents
              each row, and a folder stays in the list, disabled, because it
              explains the indent of the project beneath it. */}
          <SelectButton
            label="Move to"
            widthClass="w-full"
            value={destination ?? ""}
            onChange={(next) => {
              setDestination(next || null);
              setOverrides({});
              setAnswers(null);
            }}
            options={[
              { value: "", label: "Pick a project…" },
              ...destRows.map(({ node, depth, legal }) => ({
                value: node.id,
                label: node.name,
                depth,
                disabled: !legal,
                hint: legal ? undefined : "a folder holds projects, not tasks",
              })),
            ]}
          />
        </label>

        {promote ? (
          /* S6g — the one set of promote questions, shared with Clarify. */
          <PromoteFields
            key={destination ?? ""}
            taskIds={ids}
            destinationId={destination}
            roots={roots}
            initialAssignees={promote.initialAssignees}
            due={promote.due ?? null}
            refusedFields={promote.refusedFields}
            busy={busy}
            onChange={setAnswers}
          />
        ) : bulk.planning ? (
          <p className="text-muted-foreground">Working out what would change…</p>
        ) : bulk.planError ? (
          <p className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive">
            {bulk.planError}
          </p>
        ) : reading ? (
          <>
            <MoveMapping
              reading={reading}
              sourceName={sourceName}
              destName={destName}
              busy={busy}
              overrides={overrides}
              onOverride={(from, to) => setOverrides((prev) => ({ ...prev, [from]: to }))}
            />
            {/* Migration 192. On the bulk path there is nowhere to type an
                answer, so the card SAYS which fields block the move rather
                than letting the apply teach it by 422. */}
            {requiredMissing.length > 0 ? (
              <p className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive">
                {destName || "The destination"} requires {requiredMissing.join(", ")}, which{" "}
                {ids.length === 1 ? "this task does" : "these tasks do"} not carry. Fill
                them in first.
              </p>
            ) : null}
            <MoveLosses reading={reading} />
          </>
        ) : null}

        {error ? (
          <p
            role="alert"
            className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive"
          >
            {error}
          </p>
        ) : null}
      </div>

      <div className="mt-1 flex justify-end gap-2 px-3 pb-3">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          size="sm"
          icon="FolderInput"
          loading={busy}
          /* A plan is required, not merely a destination: agreeing before the
             card knows what the move costs is agreeing to nothing. On the
             promote door a blank required field holds the button too, and
             `title` says which one. */
          disabled={!canMove}
          title={(promoting ? answers?.blocked : null) || undefined}
          variant={loud ? "destructive" : "primary"}
          onClick={() => {
            if (promoting) {
              if (!answers?.ready || !answers.destinationProjectId) return;
              onConfirm(answers.destinationProjectId, {}, promoteDrops, answers.answers);
              return;
            }
            if (!plan) return;
            onConfirm(
              plan.destination_project_id,
              overrides,
              drops.length > 0 ? drops.map(([key]) => key) : null,
            );
          }}
        >
          {loud ? "Move and drop" : "Move"}
        </Button>
      </div>
    </Modal>
  );
}

export default MoveTasksDialog;
