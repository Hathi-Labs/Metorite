"use client";

/**
 * `?tab=settings` — the pipeline's management surface (WS-26f f2, §5.1).
 *
 * Three grids over the EXISTING admin API: deal stages, lead statuses, lost
 * reasons. Before this existed the API was headless — every stage rename,
 * reorder or probability was a curl, which is the second half of why the live
 * board was wrong (§5.1's delivery gap 2).
 *
 * Gated on `feature:crm` like the rest of the app (D-CRM-3 — the sales team
 * manages its own pipeline). The one control that needs more is the Zoho pull,
 * which is floored on `admin:access:manage` server-side; hiding it here is the
 * courtesy half.
 *
 * Everything server-shaped is pure and lives in ../lib/settings.ts: what a
 * drop renumbers, which rows that actually PATCHes, and the client half of
 * D-CRM-10's won=100 / lost=0 rule.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import SelectButton, { type SelectOption } from "@/components/ui/SelectButton";
import { useState } from "react";
import {
  REQUIREABLE_FIELD_LABELS,
  REQUIREABLE_FIELDS,
  statusTone,
  TONE_COLORS,
} from "../lib/board";
import {
  backfillRan,
  changedFields,
  reorder,
  validateLostReasonDraft,
  validateStatusDraft,
  type PositionPatch,
} from "../lib/settings";
import type {
  LostReason,
  StageMetadataReport,
  Status,
  StatusKind,
  StatusType,
} from "../lib/types";

const STATUS_TYPES: StatusType[] = ["open", "ongoing", "on_hold", "won", "lost"];

/** The two pickers of a status row, as `SelectButton` options (CRM-U2). */
const COLOR_OPTIONS: SelectOption[] = TONE_COLORS.map((color) => ({
  value: color,
  label: color,
}));
const TYPE_OPTIONS: SelectOption[] = STATUS_TYPES.map((type) => ({
  value: type,
  label: type,
}));

export default function PipelineSettings({
  dealStatuses,
  leadStatuses,
  lostReasons,
  saving,
  canPullStages,
  onSaveStatus,
  onRemoveStatus,
  onReorderStatuses,
  onSaveLostReason,
  onRemoveLostReason,
  onReorderLostReasons,
  onPullStages,
  top,
  readOnly = false,
}: {
  /** Mirror mode: Zoho owns the stages, and the gateway refuses a write. */
  readOnly?: boolean;
  /** The connection panel of an admin in a mirrored org (CRM-Z7). */
  top?: React.ReactNode;
  dealStatuses: Status[];
  leadStatuses: Status[];
  lostReasons: LostReason[];
  saving: boolean;
  canPullStages: boolean;
  onSaveStatus: (
    kind: StatusKind,
    statusId: string | null,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  onRemoveStatus: (kind: StatusKind, statusId: string) => Promise<boolean>;
  onReorderStatuses: (
    kind: StatusKind,
    patches: PositionPatch[]
  ) => Promise<void>;
  onSaveLostReason: (
    reasonId: string | null,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  onRemoveLostReason: (reasonId: string) => Promise<boolean>;
  onReorderLostReasons: (patches: PositionPatch[]) => Promise<void>;
  onPullStages: (apply: boolean) => Promise<StageMetadataReport | null>;
}) {
  return (
    <div className="flex-1 space-y-6 overflow-y-auto p-4 sm:p-6">
      {top}
      {readOnly ? (
        <p className="text-xs text-muted-foreground">Stages come from Zoho.</p>
      ) : (
        <StagePull canPull={canPullStages} saving={saving} onPull={onPullStages} />
      )}

      {/* `inert` and a disabled fieldset: nothing inside takes a click, a key
          or a drag. The gateway answers 409 mirror_mode_settings regardless. */}
      <fieldset
        disabled={readOnly}
        inert={readOnly}
        className={`m-0 min-w-0 space-y-6 border-0 p-0 ${readOnly ? "opacity-70" : ""}`}
      >
      <StatusGrid
        kind="deal"
        title="Deal stages"
        blurb="Lane order, colour and the win probability the forecast reads. A won stage always forecasts 100%, a lost stage 0% — the type is what closes a deal, the probability is what the pipeline number believes."
        statuses={dealStatuses}
        saving={saving}
        onSave={onSaveStatus}
        onRemove={onRemoveStatus}
        onReorder={onReorderStatuses}
      />

      <StatusGrid
        kind="lead"
        title="Lead statuses"
        blurb="The funnel before a deal exists. No probability — a lead forecasts nothing until it converts."
        statuses={leadStatuses}
        saving={saving}
        onSave={onSaveStatus}
        onRemove={onRemoveStatus}
        onReorder={onReorderStatuses}
      />

      <ReasonGrid
        reasons={lostReasons}
        saving={saving}
        onSave={onSaveLostReason}
        onRemove={onRemoveLostReason}
        onReorder={onReorderLostReasons}
      />
      </fieldset>
    </div>
  );
}

// ── f1 · the Zoho stage pull ────────────────────────────────────────────────

function StagePull({
  canPull,
  saving,
  onPull,
}: {
  canPull: boolean;
  saving: boolean;
  onPull: (apply: boolean) => Promise<StageMetadataReport | null>;
}) {
  const [report, setReport] = useState<StageMetadataReport | null>(null);

  if (!canPull) return null;

  return (
    <section className="rounded-xl border border-border bg-card/40">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">
            Repair from Zoho
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Reads the tenant&apos;s real lane order and forecast types. Preview
            writes nothing.
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          icon="Search"
          loading={saving}
          onClick={async () => setReport(await onPull(false))}
        >
          Preview
        </Button>
        <Button
          variant="primary"
          size="sm"
          icon="Wand2"
          disabled={saving || !report || report.outcome !== "dry_run"}
          onClick={async () => setReport(await onPull(true))}
          title={
            report?.outcome === "dry_run"
              ? "Apply the repair above"
              : "Run a preview first — this rewrites the pipeline"
          }
        >
          Apply
        </Button>
      </header>
      {report && <StageReport report={report} />}
    </section>
  );
}

function StageReport({ report }: { report: StageMetadataReport }) {
  return (
    <div className="space-y-3 px-4 py-3 text-xs">
      {report.outcome === "stopped" && (
        <p className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-warning">
          {report.stop_reason}
        </p>
      )}
      {report.missing_scope && (
        <p className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-warning">
          The Zoho token is not minted with{" "}
          <code className="font-mono">{report.missing_scope}</code>. Nothing was
          read — set the stages below by hand, or re-mint the token with that
          scope.
        </p>
      )}
      {report.errors.map((error) => (
        <p key={error} className="text-destructive">
          {error}
        </p>
      ))}
      {report.notes.map((note) => (
        <p key={note} className="text-muted-foreground">
          {note}
        </p>
      ))}

      {report.stages.length > 0 && (
        <div>
          <p className="mb-1 text-muted-foreground">
            {report.applied ? "Applied" : "Would apply"} — {report.changed}{" "}
            lane(s) change.
          </p>
          <ul className="space-y-0.5">
            {report.stages.map((change) => (
              <li
                key={change.name}
                className={
                  change.changed ? "text-foreground" : "text-muted-foreground"
                }
              >
                <span className="font-medium">{change.name}</span>{" "}
                <span className="opacity-70">
                  {change.action} · position{" "}
                  {change.position_before ?? "—"} → {change.position_after} ·
                  type {change.type_before ?? "—"} → {change.type_after} ·
                  probability {change.probability_before ?? "—"} →{" "}
                  {change.probability_after ?? "—"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.orphans.length > 0 && (
        <p className="text-muted-foreground">
          Kept, with no counterpart in Zoho (never deleted automatically):{" "}
          {report.orphans
            .map((o) => `${o.name} (${o.deals} deal${o.deals === 1 ? "" : "s"})`)
            .join(", ")}
        </p>
      )}

      {/* Only when the backfill actually ran. On the D-CRM-11 stop and on any
          unavailable outcome the database is never opened, and printing
          "0 stamped, 0 left blank" there reports a measurement nobody took —
          next to a banner saying nothing was read. */}
      {backfillRan(report) && report.closed_at && (
        <p className="text-muted-foreground">
          Close dates: {report.closed_at.stamped} stamped from Zoho&apos;s
          closing date
          {report.applied ? "" : ` (${report.closed_at.eligible} eligible)`},{" "}
          {report.closed_at.missing_close_date} left blank because the tenant
          gave no date.
        </p>
      )}
      {report.probability.outcome === "no_data" && (
        <p className="text-muted-foreground">
          Zoho carries no per-stage probability for this layout — set them below.
        </p>
      )}
    </div>
  );
}

// ── f2 · the status grids ───────────────────────────────────────────────────

function StatusGrid({
  kind,
  title,
  blurb,
  statuses,
  saving,
  onSave,
  onRemove,
  onReorder,
}: {
  kind: StatusKind;
  title: string;
  blurb: string;
  statuses: Status[];
  saving: boolean;
  onSave: (
    kind: StatusKind,
    statusId: string | null,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  onRemove: (kind: StatusKind, statusId: string) => Promise<boolean>;
  onReorder: (kind: StatusKind, patches: PositionPatch[]) => Promise<void>;
}) {
  const [rows, setRows] = useState<Status[]>(statuses);
  const [served, setServed] = useState<Status[]>(statuses);
  const [dragging, setDragging] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  // The server's answer is the truth; the local order exists only for the
  // instant between a drop and the re-read it triggers. Adjusted DURING render
  // rather than in an effect (React's "you might not need an effect"): an
  // effect would paint the stale order once and then correct it, which on a
  // reorder is a visible flicker back to where the row came from.
  if (served !== statuses) {
    setServed(statuses);
    setRows(statuses);
  }

  async function drop(to: number) {
    if (dragging === null) return;
    const plan = reorder(rows, dragging, to);
    setDragging(null);
    if (plan.patches.length === 0) return;
    setRows(plan.items);
    await onReorder(kind, plan.patches);
  }

  async function add() {
    const draft = {
      name: newName,
      type: "open" as StatusType,
      probability: kind === "deal" ? 0 : null,
    };
    const invalid = validateStatusDraft(draft, kind);
    if (invalid) {
      setError(invalid);
      return;
    }
    setError(null);
    const body: Record<string, unknown> = { name: newName.trim(), type: "open" };
    if (await onSave(kind, null, body)) {
      setNewName("");
      setAdding(false);
    }
  }

  return (
    <section className="rounded-xl border border-border bg-card/40">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{blurb}</p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          icon="Plus"
          onClick={() => setAdding((v) => !v)}
        >
          Add
        </Button>
      </header>

      {adding && (
        <div className="flex items-center gap-2 border-b border-border px-4 py-2">
          <Input
            autoFocus
            inputSize="sm"
            value={newName}
            placeholder="Stage name"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void add();
              if (e.key === "Escape") setAdding(false);
            }}
          />
          <Button size="sm" loading={saving} onClick={() => void add()}>
            Create
          </Button>
        </div>
      )}
      {error && (
        <p className="border-b border-border px-4 py-2 text-xs text-destructive">
          {error}
        </p>
      )}

      <ul>
        {rows.map((status, index) => (
          <li
            key={status.id}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              void drop(index);
            }}
            className="border-b border-border/60 last:border-b-0"
          >
            <StatusRow
              kind={kind}
              status={status}
              saving={saving}
              onDragStart={(e) => {
                e.dataTransfer.setData("text/plain", status.id);
                e.dataTransfer.effectAllowed = "move";
                setDragging(index);
              }}
              onDragEnd={() => setDragging(null)}
              onSave={(body) => onSave(kind, status.id, body)}
              onRemove={() => onRemove(kind, status.id)}
            />
          </li>
        ))}
        {rows.length === 0 && (
          <li className="px-4 py-6 text-center text-xs text-muted-foreground">
            No statuses yet.
          </li>
        )}
      </ul>
    </section>
  );
}

function StatusRow({
  kind,
  status,
  saving,
  onDragStart,
  onDragEnd,
  onSave,
  onRemove,
}: {
  kind: StatusKind;
  status: Status;
  saving: boolean;
  onDragStart: (e: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onSave: (body: Record<string, unknown>) => Promise<boolean>;
  onRemove: () => Promise<boolean>;
}) {
  const [draft, setDraft] = useState(status);
  const [served, setServed] = useState(status);
  const [invalid, setInvalid] = useState<string | null>(null);
  // A re-read replaces the draft: every write here re-reads the vocabulary,
  // and a row that kept showing what was typed after the server answered
  // something else is the stale-row-reads-as-success lie in miniature.
  if (served !== status) {
    setServed(status);
    setDraft(status);
  }

  const patch = changedFields(
    status as unknown as Record<string, unknown>,
    draft as unknown as Record<string, unknown>
  );
  const dirty = Object.keys(patch).length > 0;

  async function save() {
    const message = validateStatusDraft(
      {
        name: draft.name,
        type: draft.type,
        probability: draft.probability,
        color: draft.color,
        required_fields: draft.required_fields,
        max_dwell_days: draft.max_dwell_days,
      },
      kind
    );
    setInvalid(message);
    if (message) return;
    // Only what moved. `type` and `probability` are judged TOGETHER by the
    // server's clamp, so resending an unchanged one alongside a changed one is
    // the difference between a 422 the editor caused and one the data did.
    await onSave(patch);
  }

  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2">
      <span
        draggable
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        className="cursor-grab text-muted-foreground hover:text-foreground"
        title="Drag to reorder"
      >
        <Icon name="GripVertical" className="w-4 h-4" />
      </span>
      <span className={`w-2 h-2 shrink-0 rounded-full ${statusTone(draft)}`} />

      <Input
        inputSize="sm"
        value={draft.name}
        onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        className="min-w-32 flex-1"
      />

      <SelectButton
        label="Colour"
        value={draft.color || "gray"}
        options={COLOR_OPTIONS}
        defaultValue="gray"
        onChange={(color) => setDraft({ ...draft, color })}
        widthClass="w-24"
      />

      <SelectButton
        label="Type"
        value={draft.type}
        options={TYPE_OPTIONS}
        defaultValue="open"
        onChange={(type) => setDraft({ ...draft, type: type as StatusType })}
        widthClass="w-24"
      />

      {kind === "deal" && (
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          <Input
            inputSize="sm"
            type="number"
            min={0}
            max={100}
            value={draft.probability ?? 0}
            onChange={(e) =>
              setDraft({ ...draft, probability: Number(e.target.value) })
            }
            className="w-16"
            aria-label="Probability"
          />
          %
        </label>
      )}

      <label
        className="flex items-center gap-1 text-xs text-muted-foreground"
        title="New records land here"
      >
        <Checkbox
          size="sm"
          checked={draft.is_default}
          onChange={(e) => setDraft({ ...draft, is_default: e.target.checked })}
        />
        default
      </label>

      <Button
        size="icon-sm"
        variant="ghost"
        icon="Check"
        aria-label="Save"
        disabled={!dirty || saving}
        onClick={() => void save()}
      />
      <Button
        size="icon-sm"
        variant="ghost"
        icon="Trash2"
        aria-label="Delete"
        disabled={saving}
        onClick={() => void onRemove()}
      />

      {/* WS-26h — on its own line, because it is a sentence rather than a
          field: the lane above says what this stage IS, and this says what it
          demands and how long it tolerates a deal sitting in it. */}
      {kind === "deal" && (
        <div className="flex w-full flex-wrap items-center gap-3 pl-6">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Requires
          </span>
          {REQUIREABLE_FIELDS.map((field) => {
            const on = (draft.required_fields ?? []).includes(field);
            return (
              <label
                key={field}
                className="flex items-center gap-1 text-xs text-muted-foreground"
              >
                <Checkbox
                  size="sm"
                  checked={on}
                  onChange={() =>
                    setDraft({
                      ...draft,
                      // Rebuilt from the allowlist rather than pushed/spliced,
                      // so the stored order is always the allowlist's — the
                      // blocked-move 422 lists these, and an order that
                      // depended on click sequence would make the same stage
                      // read differently to two people who set it up.
                      required_fields: REQUIREABLE_FIELDS.filter((name) =>
                        name === field
                          ? !on
                          : (draft.required_fields ?? []).includes(name)
                      ),
                    })
                  }
                />
                {REQUIREABLE_FIELD_LABELS[field]}
              </label>
            );
          })}
          <label
            className="flex items-center gap-1 text-xs text-muted-foreground"
            title="Days in this stage before a card's age badge warns. Empty = never rots."
          >
            rots after
            <Input
              inputSize="sm"
              type="number"
              min={1}
              max={32767}
              value={draft.max_dwell_days ?? ""}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  // An emptied box is "never", which is NULL — not 0, which
                  // the gateway refuses precisely because it would paint every
                  // card in the stage amber the moment it arrived.
                  max_dwell_days:
                    e.target.value === "" ? null : Number(e.target.value),
                })
              }
              className="w-16"
              aria-label="Rot threshold in days"
            />
            days
          </label>
        </div>
      )}

      {invalid && (
        <p className="w-full text-xs text-destructive">{invalid}</p>
      )}
    </div>
  );
}

// ── f2 · lost reasons ───────────────────────────────────────────────────────

function ReasonGrid({
  reasons,
  saving,
  onSave,
  onRemove,
  onReorder,
}: {
  reasons: LostReason[];
  saving: boolean;
  onSave: (
    reasonId: string | null,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  onRemove: (reasonId: string) => Promise<boolean>;
  onReorder: (patches: PositionPatch[]) => Promise<void>;
}) {
  const [rows, setRows] = useState<LostReason[]>(reasons);
  const [served, setServed] = useState<LostReason[]>(reasons);
  const [dragging, setDragging] = useState<number | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (served !== reasons) {
    setServed(reasons);
    setRows(reasons);
  }

  async function drop(to: number) {
    if (dragging === null) return;
    const plan = reorder(rows, dragging, to);
    setDragging(null);
    if (plan.patches.length === 0) return;
    setRows(plan.items);
    await onReorder(plan.patches);
  }

  async function add() {
    const invalid = validateLostReasonDraft(newLabel);
    setError(invalid);
    if (invalid) return;
    if (await onSave(null, { label: newLabel.trim() })) setNewLabel("");
  }

  return (
    <section className="rounded-xl border border-border bg-card/40">
      <header className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">Lost reasons</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Required on every deal that enters a lost stage, which is why this
          list is worth curating — a nullable column with no gate collects
          blanks.
        </p>
      </header>

      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <Input
          inputSize="sm"
          value={newLabel}
          placeholder="Add a reason — e.g. Price"
          onChange={(e) => setNewLabel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void add();
          }}
        />
        <Button size="sm" loading={saving} onClick={() => void add()}>
          Add
        </Button>
      </div>
      {error && (
        <p className="border-b border-border px-4 py-2 text-xs text-destructive">
          {error}
        </p>
      )}

      <ul>
        {rows.map((reason, index) => (
          <li
            key={reason.id}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              void drop(index);
            }}
            className="border-b border-border/60 last:border-b-0"
          >
            <ReasonRow
              reason={reason}
              saving={saving}
              onDragStart={(e) => {
                e.dataTransfer.setData("text/plain", reason.id);
                e.dataTransfer.effectAllowed = "move";
                setDragging(index);
              }}
              onDragEnd={() => setDragging(null)}
              onSave={(label) => onSave(reason.id, { label })}
              onRemove={() => onRemove(reason.id)}
            />
          </li>
        ))}
        {rows.length === 0 && (
          <li className="px-4 py-6 text-center text-xs text-muted-foreground">
            No lost reasons yet — deals cannot be marked lost until there is at
            least one.
          </li>
        )}
      </ul>
    </section>
  );
}

function ReasonRow({
  reason,
  saving,
  onDragStart,
  onDragEnd,
  onSave,
  onRemove,
}: {
  reason: LostReason;
  saving: boolean;
  onDragStart: (e: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onSave: (label: string) => Promise<boolean>;
  onRemove: () => Promise<boolean>;
}) {
  const [label, setLabel] = useState(reason.label);
  const [served, setServed] = useState(reason.label);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  if (served !== reason.label) {
    setServed(reason.label);
    setLabel(reason.label);
  }

  async function save() {
    const message = validateLostReasonDraft(label);
    setInvalid(message);
    if (message) return;
    await onSave(label.trim());
  }

  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2">
      <span
        draggable
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        className="cursor-grab text-muted-foreground hover:text-foreground"
        title="Drag to reorder"
      >
        <Icon name="GripVertical" className="w-4 h-4" />
      </span>
      <Input
        inputSize="sm"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        className="min-w-32 flex-1"
      />
      <Button
        size="icon-sm"
        variant="ghost"
        icon="Check"
        aria-label="Save"
        disabled={label === reason.label || saving}
        onClick={() => void save()}
      />
      <Button
        size="icon-sm"
        variant="ghost"
        icon="Trash2"
        aria-label="Delete"
        disabled={saving}
        onClick={() => setConfirming(true)}
      />
      {invalid && <p className="w-full text-xs text-destructive">{invalid}</p>}
      <DeleteReasonDialog
        open={confirming}
        reason={reason}
        saving={saving}
        onCancel={() => setConfirming(false)}
        onConfirm={async () => {
          setConfirming(false);
          await onRemove();
        }}
      />
    </div>
  );
}

/**
 * The one destructive control on this screen, and it does not look like one.
 *
 * `crm_deals.lost_reason_id` is `ON DELETE SET NULL`, so deleting a reason does
 * not fail and does not warn — it silently blanks the attribution on every deal
 * that cited it, and that attribution is exactly what WS-26g's lost-reason
 * breakdown reads. The row keeps its `lost_note`, so the loss is partial and
 * therefore invisible: nothing on screen changes, and the report is simply
 * thinner next quarter.
 *
 * So the dialog names the consequence rather than asking "are you sure". It
 * does NOT claim a count — the endpoint does not return one, and inventing
 * "this will affect N deals" would be worse than saying nothing. Counting
 * before deleting is the better answer and is deferred, recorded in the ticket.
 */
function DeleteReasonDialog({
  open,
  reason,
  saving,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  reason: LostReason;
  saving: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  // On `ConfirmDialog` (CRM-U2): the one "are you sure?" dialog, on the
  // alert layer, with focus on the delete button so Enter confirms and
  // Escape cancels.
  return (
    <ConfirmDialog
      open={open}
      title={`Delete “${reason.label}”?`}
      body={
        "Every deal closed for this reason keeps its note but loses the reason itself. " +
        "The lost-reason breakdown in the reports will no longer count them."
      }
      note={
        "Nothing on the board changes, so this is not something you will notice later. " +
        "Renaming the reason keeps the attribution. Deleting it does not."
      }
      confirmLabel="Delete anyway"
      busy={saving}
      onCancel={onCancel}
      onConfirm={onConfirm}
    />
  );
}
