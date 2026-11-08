"use client";

/**
 * "New pipeline" (WS-CRM CRM-M3a, crm_conversational.md §8 CRM-M3), and the
 * same dialog in lead mode (CRM-M4a, §8 CRM-M4).
 *
 * An admin of a Native CRM names a pipeline, picks where it starts (a copy of
 * a pipeline of the same kind, or a list of stages) and reads the preview. In
 * lead mode the stages are lead statuses, and "Feeds deals into" picks the
 * deal pipeline that a converted lead's deal joins. The preview is the
 * gateway's plan (`GET /crm/pipelines/preview`). It shows which stage the
 * pipelines share, which stage is new, and which won or lost stage the CRM
 * adds so that a record can close. Create sends the same body to
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
import { useId, useMemo, useState } from "react";
import {
  blankRow,
  defaultDealName,
  defaultFeedWords,
  draftProblem,
  emptyDraft,
  foldName,
  kindOf,
  MAX_NEW_STAGES,
  moveRow,
  NEW_STAGE_TYPE_LABELS,
  PIPELINE_WORDS,
  PLAN_SOURCE_WORDS,
  pipelineBody,
  planSourceHelp,
  removeRow,
  renameRow,
  settleRow,
  storedTypes,
  type NewPipelineDraft,
  type NewStageType,
  type PipelineKind,
  type PipelinePlan,
  type PipelineStart,
} from "../lib/newPipeline";
import { orderedPipelines } from "../lib/pipelines";
import { STATUS_TYPE_LABELS } from "../lib/settings";
import { useCrmStore } from "../lib/store";
import { usePipelinePreview, type PipelinePreview } from "../lib/usePipelinePreview";
import type { DealPipeline, StatusType } from "../lib/types";

const TYPE_OPTIONS: SelectOption[] = (Object.keys(NEW_STAGE_TYPE_LABELS) as NewStageType[]).map(
  (type) => ({ value: type, label: NEW_STAGE_TYPE_LABELS[type] }),
);

/** The two starts of the dialog, in the words of one kind. */
function startTabs(kind: PipelineKind): TabDef[] {
  return [
    { id: "copy", label: `A copy of a ${PIPELINE_WORDS[kind].pipeline}` },
    { id: "empty", label: "An empty list" },
  ];
}

export default function NewPipelineDialog({
  open,
  kind = "deal",
  pipelines,
  dealPipelines = null,
  saving,
  onClose,
  onCreate,
}: {
  open: boolean;
  /** CRM-M4a: `lead` draws the dialog in lead mode. */
  kind?: PipelineKind;
  /**
   * The pipelines of this kind, the ones a copy may start from: `GET
   * /crm/pipelines`, or `?kind=lead` in lead mode. Null or empty: the dialog
   * starts from a list.
   */
  pipelines: DealPipeline[] | null;
  /** Lead mode: the deal pipelines that "Feeds deals into" offers. */
  dealPipelines?: DealPipeline[] | null;
  saving: boolean;
  onClose: () => void;
  /** Null when the pipeline is made, else the words of the refusal. */
  onCreate: (body: Record<string, unknown>) => Promise<string | null>;
}) {
  const words = PIPELINE_WORDS[kind];
  return (
    <Modal
      open={open}
      // While the create runs, Escape and the scrim do not close the dialog,
      // so its refusal keeps a place to show.
      onClose={saving ? () => undefined : onClose}
      title={words.title}
      description={words.description}
      size="lg"
      className="max-h-[85vh]"
    >
      {open && (
        <NewPipelineForm
          kind={kind}
          pipelines={pipelines}
          dealPipelines={dealPipelines}
          saving={saving}
          onCancel={onClose}
          onCreate={onCreate}
        />
      )}
    </Modal>
  );
}

/** The form of the dialog. Exported for the tests. */
export function NewPipelineForm({
  kind = "deal",
  pipelines,
  dealPipelines = null,
  saving,
  onCancel,
  onCreate,
}: {
  kind?: PipelineKind;
  pipelines: DealPipeline[] | null;
  dealPipelines?: DealPipeline[] | null;
  saving: boolean;
  onCancel: () => void;
  onCreate: (body: Record<string, unknown>) => Promise<string | null>;
}) {
  const words = PIPELINE_WORDS[kind];
  // The draft lives in the store, so a new tree at the phone width keeps it.
  const stored = useCrmStore((state) => state.newPipeline.draft);
  const setNewPipeline = useCrmStore((state) => state.setNewPipeline);
  const draft = stored ?? emptyDraft(pipelines, kind);
  const setDraft = (next: NewPipelineDraft) => setNewPipeline({ draft: next });
  // A field error shows after a Create that the draft could not send.
  const [tried, setTried] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const id = useId();
  const sources = pipelines && pipelines.length > 0 ? orderedPipelines(pipelines) : [];
  const targets = kind === "lead" && dealPipelines ? orderedPipelines(dealPipelines) : [];
  // The stages (or lead statuses) that the CRM has. Their rows show the
  // stored type and send none, so the row and the body agree.
  const statuses = useCrmStore((state) => (kind === "lead" ? state.leadStatuses : state.dealStatuses));
  const known = useMemo(() => storedTypes(statuses), [statuses]);
  const body = pipelineBody(draft, known, kind);
  const preview = usePipelinePreview(body);
  const problem = draftProblem(draft, kind);
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
  // No pick follows the org's default deal pipeline, and sends no feed.
  const fed = draft.feeds ? (targets.find((p) => p.name === draft.feeds) ?? null) : null;
  const feedOptions: SelectOption[] = [
    { value: "", label: `${defaultFeedWords(defaultDealName(dealPipelines)).replace(/^t/, "T")}` },
    ...targets.map((p) => ({ value: p.id, label: p.name })),
  ];

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
            placeholder={words.example}
            value={draft.name}
            aria-invalid={tried && !draft.name.trim() ? true : undefined}
            onChange={(e) => change({ name: e.target.value })}
          />
        </div>

        <div className="space-y-2">
          <span className="block text-xs font-medium text-foreground">Start from</span>
          {sources.length > 0 && (
            <Tabs
              tabs={startTabs(kind)}
              activeTab={draft.start}
              onTabChange={(start) => change({ start: start as PipelineStart })}
              variant="segmented"
              chrome="inline"
            />
          )}
          {draft.start === "copy" && sources.length > 0 ? (
            <SelectButton
              label={kind === "lead" ? "Lead pipeline to copy" : "Pipeline to copy"}
              prefix="Copy of:"
              value={draft.copyFrom ?? ""}
              options={sources.map((p) => ({ value: p.id, label: p.name }))}
              defaultValue={draft.copyFrom ?? ""}
              onChange={(copyFrom) => change({ copyFrom })}
              widthClass="w-full sm:w-72"
            />
          ) : (
            <StageRows
              kind={kind}
              draft={draft}
              disabled={saving}
              reused={known}
              onChange={(stages) => change({ stages })}
            />
          )}
        </div>

        {targets.length > 0 && (
          <div className="space-y-1">
            <span className="block text-xs font-medium text-foreground">Feeds deals into</span>
            <SelectButton
              label="Feeds deals into"
              prefix="Deals go to:"
              value={fed?.id ?? ""}
              options={feedOptions}
              defaultValue=""
              disabled={saving}
              onChange={(value) => change({ feeds: targets.find((p) => p.id === value)?.name ?? null })}
              widthClass="w-full sm:w-72"
            />
            <p className="text-[11px] text-muted-foreground">
              When a lead of this pipeline converts, its deal starts in this deal pipeline.
            </p>
          </div>
        )}

        <PlanPreview preview={preview} onRetry={preview.retry} kind={kind} />
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
          {saving ? "Creating…" : words.create}
        </Button>
      </div>
    </form>
  );
}

/** The stage list of "An empty list": a name and a type on each row, in order. */
function StageRows({
  kind,
  draft,
  disabled,
  reused,
  onChange,
}: {
  kind: PipelineKind;
  draft: NewPipelineDraft;
  disabled: boolean;
  /** The stored type of each stage that the plan reuses, by folded name. */
  reused: Map<string, string>;
  onChange: (stages: NewPipelineDraft["stages"]) => void;
}) {
  const words = PIPELINE_WORDS[kind];
  const rows = draft.stages;
  const set = (index: number, next: Partial<(typeof rows)[number]>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...next } : row)));
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted-foreground">
        {`A name that the CRM has already is that same ${words.stage}. A new name becomes a new ${words.stage}.`}
      </p>
      <ol className="space-y-2" aria-label={kind === "lead" ? "Statuses, in order" : "Stages, in order"}>
        {rows.map((row, index) => (
          // On a phone the type and the buttons take a second line, so the
          // name keeps the width of the dialog.
          <li key={row.key} className="flex flex-wrap items-center gap-2 sm:flex-nowrap">
            <span className="w-5 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
              {index + 1}
            </span>
            <Input
              inputSize="sm"
              aria-label={`${words.Stage} ${index + 1} name`}
              placeholder={`${words.Stage} name`}
              value={row.name}
              disabled={disabled}
              onChange={(e) => set(index, settleRow(renameRow(row, e.target.value, kind), reused))}
              className="min-w-0 flex-1"
            />
            <span className="ml-7 flex w-full items-center gap-2 sm:ml-0 sm:w-auto">
              {reused.has(foldName(row.name)) ? (
                // A stage that the CRM has keeps its own type.
                <span
                  className="w-24 shrink-0 truncate px-1 text-xs text-muted-foreground"
                  title={`A ${words.stage} the CRM has. It keeps its type.`}
                >
                  {STATUS_TYPE_LABELS[reused.get(foldName(row.name)) as StatusType] ??
                    reused.get(foldName(row.name))}
                </span>
              ) : (
                <SelectButton
                  label={`${words.Stage} ${index + 1} type`}
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
                  aria-label={`Move ${words.stage} ${index + 1} up`}
                  disabled={disabled || index === 0}
                  onClick={() => onChange(moveRow(rows, index, index - 1))}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  icon="ChevronDown"
                  aria-label={`Move ${words.stage} ${index + 1} down`}
                  disabled={disabled || index === rows.length - 1}
                  onClick={() => onChange(moveRow(rows, index, index + 1))}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  icon="X"
                  aria-label={`Remove ${words.stage} ${index + 1}`}
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
        {`Add ${words.stage}`}
      </Button>
    </div>
  );
}

/** The plan of the gateway, in order: what Create makes. */
export function PlanPreview({
  preview,
  onRetry,
  kind,
}: {
  preview: PipelinePreview;
  /** Asks again after a failed read. */
  onRetry?: () => void;
  /** The kind of the dialog. With none, the kind of the plan (CRM-M4a). */
  kind?: PipelineKind;
}) {
  const plan: PipelinePlan | null =
    preview.state === "ready" ? preview.plan : preview.state === "loading" ? preview.plan : null;
  const shown = kind ?? kindOf(plan);
  const words = PIPELINE_WORDS[shown];
  const marked = plan?.stages.some((stage) => stage.source !== "existing") ?? false;
  return (
    <section aria-label="Preview" className="rounded-lg border border-border bg-background/40">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <h3 className="text-xs font-medium text-foreground">Preview</h3>
        {plan && (
          <span className="ml-auto text-[11px] text-muted-foreground">
            {`${plan.copy_of ? `A copy of ${plan.copy_of} · ` : ""}${plan.stages.length} ${
              plan.stages.length === 1 ? words.stage : words.stages
            }`}
          </span>
        )}
      </header>
      {/* One status region, so a screen reader hears each new answer (M9). It
          keeps one height in each state, so nothing under it moves (M1). */}
      <div role="status" aria-live="polite" className="min-h-28 px-3 py-2">
        {preview.state === "idle" ? (
          <p className="py-2 text-xs text-muted-foreground">
            {`The preview shows here when the ${words.pipeline} has a name and a ${words.stage}.`}
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
                      title={planSourceHelp(stage.source, shown)}
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
                {`New: Create makes the ${words.stage}. Added: the CRM adds its own ${words.stage}, ` +
                  `so that a ${words.record} can close.`}
              </p>
            )}
            {shown === "lead" && (
              <p className="pt-1 text-[11px] text-muted-foreground">
                {`Feeds deals into ${plan.feeds ?? defaultFeedWords(plan.default_deal_pipeline)}.`}
              </p>
            )}
          </>
        ) : (
          <p className="py-2 text-xs text-muted-foreground">{`Checking the ${words.stages}…`}</p>
        )}
      </div>
    </section>
  );
}
