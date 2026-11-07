"use client";

/**
 * "New pipeline" (WS-CRM CRM-M3a, crm_conversational.md §8 CRM-M3).
 *
 * An admin of a Native CRM names a deal pipeline, picks where it starts (a
 * copy of a pipeline, or a list of stages) and reads the preview. The preview
 * is the gateway's plan (`GET /crm/pipelines/preview`). It shows which stage
 * the pipelines share, which stage is new, and which won or lost stage the CRM
 * adds so that a deal can close. Create sends the same body to
 * `POST /crm/pipelines`. The chat tool `crm_create_pipeline` shows the same
 * plan on its card.
 *
 * `lib/newPipeline.ts` holds the rules, and `lib/usePipelinePreview.ts` the
 * read. This file only draws them.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import SelectButton, { type SelectOption } from "@/components/ui/SelectButton";
import Tabs, { type TabDef } from "@/components/Tabs";
import { useId, useState } from "react";
import {
  blankRow,
  draftProblem,
  emptyDraft,
  foldName,
  MAX_NEW_STAGES,
  moveRow,
  NEW_STAGE_TYPE_LABELS,
  PLAN_SOURCE_HELP,
  PLAN_SOURCE_WORDS,
  pipelineBody,
  removeRow,
  renameRow,
  storedTypes,
  type NewPipelineDraft,
  type NewStageType,
  type PipelinePlan,
  type PipelineStart,
} from "../lib/newPipeline";
import { orderedPipelines } from "../lib/pipelines";
import { STATUS_TYPE_LABELS } from "../lib/settings";
import { useCrmStore } from "../lib/store";
import { usePipelinePreview, type PipelinePreview } from "../lib/usePipelinePreview";
import type { DealPipeline, StatusType } from "../lib/types";

const START_TABS: TabDef[] = [
  { id: "copy", label: "A copy of a pipeline" },
  { id: "empty", label: "An empty list" },
];

const TYPE_OPTIONS: SelectOption[] = (Object.keys(NEW_STAGE_TYPE_LABELS) as NewStageType[]).map(
  (type) => ({ value: type, label: NEW_STAGE_TYPE_LABELS[type] }),
);

export default function NewPipelineDialog({
  open,
  pipelines,
  saving,
  onClose,
  onCreate,
}: {
  open: boolean;
  /** `GET /crm/pipelines`. Null or empty: the dialog starts from a list. */
  pipelines: DealPipeline[] | null;
  saving: boolean;
  onClose: () => void;
  /** Null when the pipeline is made, else the words of the refusal. */
  onCreate: (body: Record<string, unknown>) => Promise<string | null>;
}) {
  return (
    <Modal
      open={open}
      // While the create runs, Escape and the scrim do not close the dialog,
      // so its refusal keeps a place to show.
      onClose={saving ? () => undefined : onClose}
      title="New pipeline"
      description="The stages that one kind of deal moves through, in order."
      size="lg"
      className="max-h-[85vh]"
    >
      {open && (
        <NewPipelineForm pipelines={pipelines} saving={saving} onCancel={onClose} onCreate={onCreate} />
      )}
    </Modal>
  );
}

function NewPipelineForm({
  pipelines,
  saving,
  onCancel,
  onCreate,
}: {
  pipelines: DealPipeline[] | null;
  saving: boolean;
  onCancel: () => void;
  onCreate: (body: Record<string, unknown>) => Promise<string | null>;
}) {
  // The draft lives in the store, so a new tree at the phone width keeps it.
  const stored = useCrmStore((state) => state.newPipeline.draft);
  const setNewPipeline = useCrmStore((state) => state.setNewPipeline);
  const draft = stored ?? emptyDraft(pipelines);
  const setDraft = (next: NewPipelineDraft) => setNewPipeline({ draft: next });
  // A field error shows after a Create that the draft could not send.
  const [tried, setTried] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const id = useId();
  const sources = pipelines && pipelines.length > 0 ? orderedPipelines(pipelines) : [];
  const body = pipelineBody(draft);
  const preview = usePipelinePreview(body);
  const dealStatuses = useCrmStore((state) => state.dealStatuses);
  const problem = draftProblem(draft);
  const change = (next: Partial<NewPipelineDraft>) => {
    setDraft({ ...draft, ...next });
    setRefusal(null);
  };

  async function submit() {
    setTried(true);
    setRefusal(null);
    if (problem || !body || saving) return;
    setRefusal(await onCreate(body));
  }

  const shownError = refusal ?? (tried ? problem : null);
  // A refused plan blocks Create. A failed read does not: Retry asks again,
  // and the create itself checks the plan.
  const blocked = preview.state === "refused" && !preview.retryable;

  return (
    <form
      noValidate
      className="flex min-h-0 flex-1 flex-col"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        <div className="space-y-1">
          <label htmlFor={`${id}-name`} className="block text-xs font-medium text-foreground">
            Name
          </label>
          <Input
            id={`${id}-name`}
            inputSize="sm"
            autoFocus
            placeholder="AMC contracts"
            value={draft.name}
            aria-invalid={tried && !draft.name.trim() ? true : undefined}
            onChange={(e) => change({ name: e.target.value })}
          />
        </div>

        <div className="space-y-2">
          <span className="block text-xs font-medium text-foreground">Start from</span>
          {sources.length > 0 && (
            <Tabs
              tabs={START_TABS}
              activeTab={draft.start}
              onTabChange={(start) => change({ start: start as PipelineStart })}
              variant="segmented"
              chrome="inline"
            />
          )}
          {draft.start === "copy" && sources.length > 0 ? (
            <SelectButton
              label="Pipeline to copy"
              prefix="Copy of:"
              value={draft.copyFrom ?? ""}
              options={sources.map((p) => ({ value: p.id, label: p.name }))}
              defaultValue={draft.copyFrom ?? ""}
              onChange={(copyFrom) => change({ copyFrom })}
              widthClass="w-full sm:w-72"
            />
          ) : (
            <StageRows
              draft={draft}
              disabled={saving}
              reused={storedTypes(dealStatuses, preview.lastPlan)}
              onChange={(stages) => change({ stages })}
            />
          )}
        </div>

        <PlanPreview preview={preview} onRetry={preview.retry} />
      </div>
      {shownError && (
        <p role="alert" className="px-4 pb-2 text-xs text-destructive">
          {shownError}
        </p>
      )}
      <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button type="button" variant="secondary" size="sm" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" size="sm" loading={saving} disabled={blocked}>
          {saving ? "Creating…" : "Create pipeline"}
        </Button>
      </div>
    </form>
  );
}

/** The stage list of "An empty list": a name and a type on each row, in order. */
function StageRows({
  draft,
  disabled,
  reused,
  onChange,
}: {
  draft: NewPipelineDraft;
  disabled: boolean;
  /** The stored type of each stage that the plan reuses, by folded name. */
  reused: Map<string, string>;
  onChange: (stages: NewPipelineDraft["stages"]) => void;
}) {
  const rows = draft.stages;
  const set = (index: number, next: Partial<(typeof rows)[number]>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...next } : row)));
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted-foreground">
        A name that the CRM has already is that same stage. A new name becomes a new stage.
      </p>
      <ol className="space-y-2" aria-label="Stages, in order">
        {rows.map((row, index) => (
          // On a phone the type and the buttons take a second line, so the
          // name keeps the width of the dialog.
          <li key={row.key} className="flex flex-wrap items-center gap-2 sm:flex-nowrap">
            <span className="w-5 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
              {index + 1}
            </span>
            <Input
              inputSize="sm"
              aria-label={`Stage ${index + 1} name`}
              placeholder="Stage name"
              value={row.name}
              disabled={disabled}
              onChange={(e) => set(index, renameRow(row, e.target.value))}
              className="min-w-0 flex-1"
            />
            <span className="ml-7 flex w-full items-center gap-2 sm:ml-0 sm:w-auto">
              {reused.has(foldName(row.name)) ? (
                // A stage that the CRM has keeps its own type.
                <span
                  className="w-24 shrink-0 truncate px-1 text-xs text-muted-foreground"
                  title="A stage the CRM has. It keeps its type."
                >
                  {STATUS_TYPE_LABELS[reused.get(foldName(row.name)) as StatusType] ??
                    reused.get(foldName(row.name))}
                </span>
              ) : (
                <SelectButton
                  label={`Stage ${index + 1} type`}
                  value={row.type}
                  options={TYPE_OPTIONS}
                  defaultValue="open"
                  disabled={disabled}
                  onChange={(type) => set(index, { type: type as NewStageType })}
                  widthClass="w-24 shrink-0"
                />
              )}
              <span className="ml-auto flex shrink-0 items-center sm:ml-0">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  icon="ChevronUp"
                  aria-label={`Move stage ${index + 1} up`}
                  disabled={disabled || index === 0}
                  onClick={() => onChange(moveRow(rows, index, index - 1))}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  icon="ChevronDown"
                  aria-label={`Move stage ${index + 1} down`}
                  disabled={disabled || index === rows.length - 1}
                  onClick={() => onChange(moveRow(rows, index, index + 1))}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  icon="X"
                  aria-label={`Remove stage ${index + 1}`}
                  disabled={disabled}
                  onClick={() => onChange(removeRow(rows, row.key))}
                />
              </span>
            </span>
          </li>
        ))}
      </ol>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        icon="Plus"
        disabled={disabled || rows.length >= MAX_NEW_STAGES}
        onClick={() => onChange([...rows, blankRow()])}
      >
        Add stage
      </Button>
    </div>
  );
}

/** The plan of the gateway, in order: what Create makes. */
export function PlanPreview({
  preview,
  onRetry,
}: {
  preview: PipelinePreview;
  /** Asks again after a failed read. */
  onRetry?: () => void;
}) {
  const plan: PipelinePlan | null =
    preview.state === "ready" ? preview.plan : preview.state === "loading" ? preview.plan : null;
  const marked = plan?.stages.some((stage) => stage.source !== "existing") ?? false;
  return (
    <section aria-label="Preview" className="rounded-lg border border-border bg-background/40">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <h3 className="text-xs font-medium text-foreground">Preview</h3>
        {plan && (
          <span className="ml-auto text-[11px] text-muted-foreground">
            {plan.copy_of ? `A copy of ${plan.copy_of} · ` : ""}
            {plan.stages.length} {plan.stages.length === 1 ? "stage" : "stages"}
          </span>
        )}
      </header>
      {/* One status region, so a screen reader hears each new answer (M9). It
          keeps one height in each state, so nothing under it moves (M1). */}
      <div role="status" aria-live="polite" className="min-h-28 px-3 py-2">
        {preview.state === "idle" ? (
          <p className="py-2 text-xs text-muted-foreground">
            The preview shows here when the pipeline has a name and a stage.
          </p>
        ) : preview.state === "refused" ? (
          <div className="flex items-start gap-2 py-2">
            <p className="min-w-0 flex-1 text-xs text-destructive">{preview.message}</p>
            {preview.retryable && onRetry && (
              <Button type="button" variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry}>
                Try again
              </Button>
            )}
          </div>
        ) : plan ? (
          <>
            <ol className={preview.state === "loading" ? "opacity-60" : ""}>
              {plan.stages.map((stage, index) => (
                <li key={`${stage.id ?? "new"}-${stage.name}`} className="flex items-center gap-2 py-1 text-xs">
                  <span className="w-5 shrink-0 text-right tabular-nums text-muted-foreground">{index + 1}</span>
                  <span className="min-w-0 flex-1 truncate text-foreground" title={stage.name}>
                    {stage.name}
                  </span>
                  <span className="shrink-0 text-muted-foreground">
                    {STATUS_TYPE_LABELS[stage.type as StatusType] ?? stage.type}
                    {stage.hidden ? " · hidden" : ""}
                  </span>
                  {stage.source !== "existing" && (
                    <Badge
                      tone={stage.source === "new" ? "primary" : "neutral"}
                      size="xs"
                      title={PLAN_SOURCE_HELP[stage.source]}
                      className="shrink-0"
                    >
                      {PLAN_SOURCE_WORDS[stage.source]}
                    </Badge>
                  )}
                </li>
              ))}
            </ol>
            {marked && (
              <p className="pt-1 text-[11px] text-muted-foreground">
                New: Create makes the stage. Added: the CRM adds its own stage, so that a deal can close.
              </p>
            )}
          </>
        ) : (
          <p className="py-2 text-xs text-muted-foreground">Checking the stages…</p>
        )}
      </div>
    </section>
  );
}
