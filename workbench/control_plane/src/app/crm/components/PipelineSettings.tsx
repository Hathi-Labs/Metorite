"use client";

/**
 * `?tab=settings` — CRM settings (WS-26f f2, WS-53 CRM-Q3).
 *
 * The cards, in this order: Setup and sync (it holds the connection, CRM-X0),
 * Access, Deal stages, Lead statuses, Lost reasons, Fields, Field mapping and
 * Sync health. A card heading is the label of its rail row (CRM-C1). The page passes Setup and Access in for an admin. Fields is for an
 * admin. The Field map (CRM-Q7a) and Sync health (CRM-Q9) are for an admin in
 * Mirror mode. In Native mode the Field mapping section holds the
 * lead-conversion mapping (CRM-C6a), for a studio admin only.
 *
 * **Sections** (WS-53 CRM-C1, crm_platform.md §20.4). With a `section`, the
 * component draws the cards of that section only, and the rail opens one
 * section at a time. `lib/settingsSections.ts` holds the list of sections.
 * With no `section`, it draws every card in one column, as before.
 *
 * The grids drive the EXISTING admin API: deal stages, lead statuses, lost
 * reasons. Before this existed the API was headless — every stage rename,
 * reorder or probability was a curl, which is the second half of why the live
 * board was wrong (§5.1's delivery gap 2).
 *
 * Gated on `feature:crm` like the rest of the app (D-CRM-3 — the sales team
 * manages its own pipeline). The one control that needs more is the Zoho pull,
 * which is floored on `admin:access:manage` server-side; hiding it here is the
 * courtesy half.
 *
 * Several pipelines (CRM-Q2) show one read-only section each. The stage routes
 * for a second pipeline are not in this build, so the grid edits the stages of
 * the one implicit pipeline only.
 *
 * **Mirror mode** (WS-53 CRM-C3, crm_platform.md §20.6 item 14). Zoho owns the
 * stages, and an admin edits the Metorite overlay beside them: the order, the
 * probability, the colour, hide and the rot threshold. Each row shows the Zoho
 * value beside the Metorite value. A drag calls the order route. The sync never
 * writes the overlay, and nothing here writes Zoho (D95). A member who is not an
 * admin still reads the lists.
 *
 * Everything server-shaped is pure and lives in ../lib/settings.ts: what a
 * drop renumbers, which rows that actually PATCHes, and the client half of
 * D-CRM-10's won=100 / lost=0 rule.
 */

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import SelectButton, { type SelectOption } from "@/components/ui/SelectButton";
import { useState } from "react";
import {
  laneGroup,
  REQUIREABLE_FIELD_LABELS,
  REQUIREABLE_FIELDS,
  stageAccent,
} from "../lib/board";
import { colorOptions } from "../lib/colorOptions";
import { useCrmModeValue } from "../lib/modeContext";
import { showPicker } from "../lib/pipelines";
import {
  CLAMPED_PROBABILITY,
  changedFields,
  draftOf,
  hasOverlay,
  orderAfterDrop,
  overlayPatch,
  reorder,
  resetToSource,
  STATUS_TYPE_LABELS,
  validateLostReasonDraft,
  validateOverlayProbability,
  validateStatusDraft,
  type PositionPatch,
} from "../lib/settings";
import type { SettingsSectionId } from "../lib/settingsSections";
import { useCrmStore } from "../lib/store";
import ConvertMappingCard from "./ConvertMappingCard";
import FieldMapCard from "./FieldMapCard";
import FieldsCard from "./FieldsCard";
import { Card, NameList, PipelineSections } from "./SettingsCards";
import SuggestionsCard from "./Suggestions";
import SyncHealthCard from "./SyncHealthCard";
import type {
  DealPipeline,
  LostReason,
  Status,
  StatusKind,
  StatusType,
} from "../lib/types";

const STATUS_TYPES: StatusType[] = ["open", "ongoing", "on_hold", "won", "lost"];

/**
 * The draft of one row, kept in the CRM store under `key` (CRM-C1 review).
 * A section change unmounts the row, and the draft stays for its return. A
 * re-read of `served` drops it, as the local state did before.
 */
function useSettingsDraft<T>(
  key: string,
  served: T
): [T, (next: T) => void, () => void] {
  const stored = useCrmStore((state) => state.settingsDrafts[key]);
  const setDraft = useCrmStore((state) => state.setSettingsDraft);
  return [
    draftOf(stored, served),
    (next: T) => setDraft(key, Object.is(next, served) ? null : { base: served, value: next }),
    // A saved draft leaves the store. Kept, it would come back when another
    // admin sets the served value back to its base, and a Save would then
    // overwrite their newer value (CRM-C1 re-review).
    () => setDraft(key, null),
  ];
}

/** The type picker of a status row, as `SelectButton` options (CRM-U2). The
 *  colour picker is `colorOptions` (`lib/colorOptions.ts`). */
const TYPE_OPTIONS: SelectOption[] = STATUS_TYPES.map((type) => ({
  value: type,
  label: STATUS_TYPE_LABELS[type],
}));

/**
 * The stages in board order, as small chips with their win chance (CRM-U6).
 * It re-renders from the rows of the grid, so a move shows here at once. The
 * deal board draws open work, then on hold, won and lost, and the strip does
 * the same. A hidden stage has no lane unless it holds records, so it is not
 * drawn here.
 */
function OrderStrip({ kind, statuses }: { kind: StatusKind; statuses: Status[] }) {
  const shown = statuses
    .map((status, index) => ({ status, index }))
    .filter(({ status }) => !status.hidden)
    .sort((a, b) =>
      kind === "deal"
        ? laneGroup(a.status.type) - laneGroup(b.status.type) || a.index - b.index
        : a.index - b.index
    )
    .map(({ status }) => status);
  if (shown.length === 0) return null;
  return (
    <ol
      aria-label="Board order"
      className="flex flex-wrap items-center gap-1 border-b border-border px-4 py-2"
    >
      {shown.map((status, index) => (
        <li key={status.id} className="flex items-center gap-1">
          {index > 0 && (
            <Icon name="ChevronRight" className="w-3 h-3 text-muted-foreground" />
          )}
          <Badge tone="neutral" size="xs" title={`${status.name}: ${status.probability ?? 0}% win chance`}>
            {status.name}
            <span className="text-muted-foreground tabular-nums">
              {" "}
              {status.probability ?? 0}%
            </span>
          </Badge>
        </li>
      ))}
    </ol>
  );
}

/** Up and Down for one row. They write the same order as a drag does. */
function MoveButtons({
  name,
  index,
  count,
  disabled,
  onMove,
}: {
  name: string;
  index: number;
  count: number;
  disabled: boolean;
  onMove: (to: number) => void;
}) {
  return (
    <span className="flex items-center">
      <Button
        variant="ghost"
        size="icon-xs"
        icon="ChevronUp"
        aria-label="Move up"
        title={`Move ${name} up`}
        disabled={disabled || index === 0}
        onClick={() => onMove(index - 1)}
      />
      <Button
        variant="ghost"
        size="icon-xs"
        icon="ChevronDown"
        aria-label="Move down"
        title={`Move ${name} down`}
        disabled={disabled || index === count - 1}
        onClick={() => onMove(index + 1)}
      />
    </span>
  );
}

/** Why a won or a lost stage has a fixed win chance (D-CRM-10). */
function pinnedReason(type: StatusType): string | undefined {
  const pinned = CLAMPED_PROBABILITY[type];
  if (pinned === undefined) return undefined;
  return `A ${STATUS_TYPE_LABELS[type].toLowerCase()} stage always forecasts ${pinned}%. The type decides this.`;
}

/**
 * The colour of a Mirror row (CRM-C3, item 16): `colorOptions`, and "by type"
 * for no Metorite colour. Zoho has no stage colour, so with none the board
 * colours the lane by its type.
 */
function overlayColorOptions(stored: string | null | undefined): SelectOption[] {
  return [{ value: "", label: "by type" }, ...colorOptions(stored)];
}

export default function PipelineSettings({
  dealStatuses,
  leadStatuses,
  lostReasons,
  saving,
  onSaveStatus,
  onRemoveStatus,
  onReorderStatuses,
  onSaveLostReason,
  onRemoveLostReason,
  onReorderLostReasons,
  setup,
  access,
  pipelines = null,
  showAdminCards = false,
  readOnly = false,
  studioAdmin = false,
  section,
  questions,
  rules,
}: {
  /**
   * The settings section on screen (CRM-C1). Only its cards draw. With no
   * value, every card draws. The page decides which sections the caller and
   * the mode can open, and these props hide the rest of the cards.
   */
  section?: SettingsSectionId;
  /** Mirror mode: Zoho owns the stages, and the gateway refuses a write. */
  readOnly?: boolean;
  /**
   * Open questions (CRM-Q32-3), Mirror mode only. The page passes it only
   * while the read of the questions says the section is on, because a 404
   * `questions_off` hides it (§18.14.5).
   */
  questions?: React.ReactNode;
  /**
   * Business rules (CRM-Q33), Mirror mode only, for each member. The page
   * passes it only while `GET /crm/rules` says the section is on.
   */
  rules?: React.ReactNode;
  /**
   * `studio_admin` of the layout (CRM-C6a). In Native mode it draws the
   * lead-conversion mapping. The page gives the same value to the sections.
   */
  studioAdmin?: boolean;
  /** The Setup hub (it holds the connection, CRM-X0) and the Access card of an admin, in this order. */
  setup?: React.ReactNode;
  access?: React.ReactNode;
  /** The deal pipelines (CRM-Q2). `null` while the gateway has no such route. */
  pipelines?: DealPipeline[] | null;
  /** The Fields and Sync health cards are for an admin. */
  showAdminCards?: boolean;
  dealStatuses: Status[];
  leadStatuses: Status[];
  lostReasons: LostReason[];
  saving: boolean;
  /** Retired: the legacy stage repair is no longer drawn (CRM-L2). */
  canPullStages?: boolean;
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
  onPullStages?: unknown;
}) {
  const several = showPicker(pipelines);
  const orderStatuses = useCrmStore((state) => state.orderStatuses);
  const resetStatusOrder = useCrmStore((state) => state.resetStatusOrder);
  /** Does the section on screen hold this card? With no section, each does. */
  const show = (id: SettingsSectionId) => !section || section === id;
  return (
    <div className="flex-1 space-y-6 overflow-y-auto p-4 sm:p-6">
      {show("setup") && setup}
      {show("access") && access}
      {/* CRM-Q33: the business rules, for each member in Mirror mode. */}
      {readOnly && show("rules") && rules}
      {readOnly && showAdminCards ? (
        <>
          {show("stages") && (
            <>
              {/* The editor below is "Deal stages", so this list is the pipelines. */}
              <PipelineSections
                title="Pipelines"
                pipelines={pipelines}
                fallback={dealStatuses}
                note="Stages come from Zoho."
              />
              <MirrorStatusGrid
                kind="deal"
                title="Deal stages"
                blurb="Zoho owns the stages. The Metorite order, probability, colour and hide stay in Metorite, and the sync never changes them. A Metorite probability moves the forecast only of a deal with no probability of its own, and most Zoho deals have one."
                statuses={dealStatuses}
                saving={saving}
                onSave={onSaveStatus}
                onOrder={orderStatuses}
                onResetOrder={resetStatusOrder}
              />
            </>
          )}
          {show("lead-statuses") && (
            <MirrorStatusGrid
              kind="lead"
              title="Lead statuses"
              blurb="Zoho owns the Lead Status values. The Metorite order, win chance, colour and hide stay in Metorite."
              statuses={leadStatuses}
              saving={saving}
              onSave={onSaveStatus}
              onOrder={orderStatuses}
              onResetOrder={resetStatusOrder}
            />
          )}
          {show("lost-reasons") && (
            <MirrorReasonGrid reasons={lostReasons} saving={saving} onSave={onSaveLostReason} />
          )}
        </>
      ) : readOnly ? (
        <>
          {show("stages") && (
            <PipelineSections
              pipelines={pipelines}
              fallback={dealStatuses}
              note="Stages come from Zoho."
            />
          )}
          {show("lead-statuses") && (
            <Card title="Lead statuses" note="Lead Status values come from Zoho.">
              <NameList names={leadStatuses.map((x) => x.name)} />
            </Card>
          )}
          {show("lost-reasons") && (
            <Card title="Lost reasons" note="Lost reasons come from Zoho.">
              <NameList names={lostReasons.map((x) => x.label)} />
            </Card>
          )}
        </>
      ) : (
        <>
          {show("stages") &&
            (several ? (
              <PipelineSections
                pipelines={pipelines}
                fallback={dealStatuses}
                note="Stage editing for several pipelines is not available yet."
              />
            ) : (
              <StatusGrid
                kind="deal"
                title="Deal stages"
                blurb="Board order, colour and the win chance the forecast reads. The order and the win chance are separate settings. A won stage always forecasts 100%, a lost stage 0%. The type closes a deal. The win chance feeds the pipeline number."
                statuses={dealStatuses}
                saving={saving}
                onSave={onSaveStatus}
                onRemove={onRemoveStatus}
                onReorder={onReorderStatuses}
              />
            ))}

          {show("lead-statuses") && (
            <StatusGrid
              kind="lead"
              title="Lead statuses"
              blurb="The funnel before a deal exists. The win chance tells the board how many leads in a status you expect to convert. It does not move the money forecast."
              statuses={leadStatuses}
              saving={saving}
              onSave={onSaveStatus}
              onRemove={onRemoveStatus}
              onReorder={onReorderStatuses}
            />
          )}

          {show("lost-reasons") && (
            <ReasonGrid
              reasons={lostReasons}
              saving={saving}
              onSave={onSaveLostReason}
              onRemove={onRemoveLostReason}
              onReorder={onReorderLostReasons}
            />
          )}
        </>
      )}
      {showAdminCards && (
        <>
          {show("fields") && <FieldsCard readOnly={readOnly} />}
          {/* The Field map and Sync health read the mirror, so Native mode
              does not draw them. */}
          {readOnly && show("field-mapping") && <FieldMapCard />}
          {/* CRM-C6a: the lead-conversion mapping, Native mode, a studio admin. */}
          {!readOnly && studioAdmin && show("field-mapping") && <ConvertMappingCard />}
          {/* CRM-Q32-3: the open questions, Mirror mode only, before Suggestions. */}
          {readOnly && show("questions") && questions}
          {/* CRM-Q20-2: the suggestions of the mapping, Mirror mode only. */}
          {readOnly && show("suggestions") && <SuggestionsCard />}
          {readOnly && show("sync-health") && <SyncHealthCard />}
        </>
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
  const [newName, setNewName] = useSettingsDraft(`new-status:${kind}`, "");
  const [adding, setAdding] = useState(newName !== "");
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

  async function moveTo(from: number, to: number) {
    const plan = reorder(rows, from, to);
    if (plan.patches.length === 0) return;
    setRows(plan.items);
    await onReorder(kind, plan.patches);
  }

  async function drop(to: number) {
    if (dragging === null) return;
    const from = dragging;
    setDragging(null);
    await moveTo(from, to);
  }

  async function add() {
    const draft = {
      name: newName,
      type: "open" as StatusType,
      probability: 0,
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
      <OrderStrip kind={kind} statuses={rows} />

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
              index={index}
              count={rows.length}
              onMove={(to) => void moveTo(index, to)}
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
  index,
  count,
  onMove,
  onDragStart,
  onDragEnd,
  onSave,
  onRemove,
}: {
  kind: StatusKind;
  status: Status;
  saving: boolean;
  index: number;
  count: number;
  onMove: (to: number) => void;
  onDragStart: (e: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onSave: (body: Record<string, unknown>) => Promise<boolean>;
  onRemove: () => Promise<boolean>;
}) {
  // A re-read replaces the draft: every write here re-reads the vocabulary,
  // and a row that kept showing what was typed after the server answered
  // something else is the stale-row-reads-as-success lie in miniature. The
  // draft lives in the store, so a section change keeps it (CRM-C1).
  const [draft, setDraft, clearDraft] = useSettingsDraft(`status:${kind}:${status.id}`, status);
  // CRM-C2: the dot shows the colour the board shows, so it reads the mode.
  const mode = useCrmModeValue();
  const [invalid, setInvalid] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const pinned = CLAMPED_PROBABILITY[draft.type];
  const ruleCount = (draft.required_fields ?? []).length + (draft.max_dwell_days ? 1 : 0);

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
    if (await onSave(patch)) clearDraft();
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
      <MoveButtons name={status.name} index={index} count={count} disabled={saving} onMove={onMove} />
      <span className={`w-2 h-2 shrink-0 rounded-full ${stageAccent(draft, mode).dot}`} />

      <Input
        inputSize="sm"
        value={draft.name}
        onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        className="min-w-32 flex-1"
      />

      <SelectButton
        label="Colour"
        value={draft.color || "gray"}
        options={colorOptions(draft.color)}
        defaultValue="gray"
        onChange={(color) => setDraft({ ...draft, color })}
        widthClass="w-24"
      />

      <SelectButton
        label="Type"
        value={draft.type}
        options={TYPE_OPTIONS}
        defaultValue="open"
        onChange={(type) => {
          // A won or lost type fixes the win chance, so the draft follows it
          // and the server never sees a contradiction.
          const fixed = CLAMPED_PROBABILITY[type as StatusType];
          setDraft({
            ...draft,
            type: type as StatusType,
            ...(fixed !== undefined ? { probability: fixed } : {}),
          });
        }}
        widthClass="w-28"
      />

      <label
        className="flex items-center gap-1 text-xs text-muted-foreground"
        title={pinnedReason(draft.type)}
      >
        Win chance
        <Input
          inputSize="sm"
          type="number"
          min={0}
          max={100}
          value={pinned ?? draft.probability ?? 0}
          disabled={pinned !== undefined}
          onChange={(e) =>
            setDraft({ ...draft, probability: Number(e.target.value) })
          }
          className="w-16"
          aria-label="Win chance"
        />
        %
      </label>

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

      <HideBox
        checked={Boolean(draft.hidden)}
        onChange={(hidden) => setDraft({ ...draft, hidden })}
      />

      <Button size="sm" disabled={!dirty || saving} onClick={() => void save()}>
        Save
      </Button>
      {dirty && (
        <Button
          size="sm"
          variant="ghost"
          disabled={saving}
          onClick={() => {
            clearDraft();
            setInvalid(null);
          }}
        >
          Cancel
        </Button>
      )}
      {kind === "deal" && (
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={rulesOpen}
          onClick={() => setRulesOpen((open) => !open)}
        >
          {ruleCount > 0 ? `Rules (${ruleCount})` : "Rules"}
        </Button>
      )}
      <Button
        size="icon-sm"
        variant="ghost"
        icon="Trash2"
        aria-label="Delete"
        disabled={saving}
        onClick={() => setConfirming(true)}
      />
      <ConfirmDialog
        open={confirming}
        title={`Delete \u201c${status.name}\u201d?`}
        body="The stage leaves the settings and the board. The server refuses the delete while any record is still in it."
        confirmLabel="Delete"
        busy={saving}
        onCancel={() => setConfirming(false)}
        onConfirm={async () => {
          setConfirming(false);
          await onRemove();
        }}
      />

      {/* WS-26h — on its own line, because it is a sentence rather than a
          field: the lane above says what this stage IS, and this says what it
          demands and how long it tolerates a deal sitting in it. CRM-U6: it
          folds behind "Rules", so the grid reads one line per stage. */}
      {kind === "deal" && rulesOpen && (
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

/** The "hidden" box of a row (CRM-C3). No picker offers a hidden row. */
function HideBox({
  checked,
  disabled = false,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (hidden: boolean) => void;
}) {
  return (
    <label
      className="flex items-center gap-1 text-xs text-muted-foreground"
      title="No picker offers a hidden row. A board shows its lane only when the lane holds records."
    >
      <Checkbox
        size="sm"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      hidden
    </label>
  );
}

// ── CRM-C3 · the Mirror editor ──────────────────────────────────────────────

/**
 * The stages or the Lead Status values of a Mirror org, for an admin.
 *
 * A drag sends every id of the kind, in the new order, to the order route,
 * which writes the Metorite order in one transaction. "Reset order to Zoho"
 * clears that order, so the Zoho order applies again.
 */
export function MirrorStatusGrid({
  kind,
  title,
  blurb,
  statuses,
  saving,
  onSave,
  onOrder,
  onResetOrder,
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
  onOrder: (kind: StatusKind, ids: string[]) => Promise<boolean>;
  onResetOrder: (kind: StatusKind) => Promise<boolean>;
}) {
  const [rows, setRows] = useState<Status[]>(statuses);
  const [served, setServed] = useState<Status[]>(statuses);
  const [dragging, setDragging] = useState<number | null>(null);
  // The server's answer is the truth, as in `StatusGrid`.
  if (served !== statuses) {
    setServed(statuses);
    setRows(statuses);
  }
  const ordered = statuses.some((status) => status.admin_order);

  async function moveTo(from: number, to: number) {
    const ids = orderAfterDrop(rows, from, to);
    if (!ids) return;
    setRows(ids.map((id) => rows.find((row) => row.id === id)!));
    await onOrder(kind, ids);
  }

  async function drop(to: number) {
    if (dragging === null) return;
    const from = dragging;
    setDragging(null);
    await moveTo(from, to);
  }

  return (
    <section className="rounded-xl border border-border bg-card/40" aria-label={title}>
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{blurb}</p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          icon="RotateCcw"
          disabled={!ordered || saving}
          onClick={() => void onResetOrder(kind)}
        >
          Reset order to Zoho
        </Button>
      </header>
      <OrderStrip kind={kind} statuses={rows} />
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
            <MirrorStatusRow
              kind={kind}
              status={status}
              saving={saving}
              index={index}
              count={rows.length}
              onMove={(to) => void moveTo(index, to)}
              onDragStart={(e) => {
                e.dataTransfer.setData("text/plain", status.id);
                e.dataTransfer.effectAllowed = "move";
                setDragging(index);
              }}
              onDragEnd={() => setDragging(null)}
              onSave={(body) => onSave(kind, status.id, body)}
            />
          </li>
        ))}
        {rows.length === 0 && (
          <li className="px-4 py-6 text-center text-xs text-muted-foreground">
            No values from Zoho yet. The next sync brings them.
          </li>
        )}
      </ul>
    </section>
  );
}

function MirrorStatusRow({
  kind,
  status,
  saving,
  index,
  count,
  onMove,
  onDragStart,
  onDragEnd,
  onSave,
}: {
  kind: StatusKind;
  status: Status;
  saving: boolean;
  index: number;
  count: number;
  onMove: (to: number) => void;
  onDragStart: (e: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onSave: (body: Record<string, unknown>) => Promise<boolean>;
}) {
  // In the store, as in `StatusRow`, so a section change keeps it (CRM-C1).
  const [draft, setDraft, clearDraft] = useSettingsDraft(`overlay:${kind}:${status.id}`, status);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const pinned = CLAMPED_PROBABILITY[status.type];
  const patch = overlayPatch(
    kind,
    status as unknown as Record<string, unknown>,
    draft as unknown as Record<string, unknown>
  );
  const dirty = Object.keys(patch).length > 0;
  const gone = Boolean(status.source_gone_at);

  async function save() {
    const message = validateOverlayProbability(draft.ui_probability);
    setInvalid(message);
    if (message) return;
    if (await onSave(patch)) clearDraft();
  }

  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2">
      <span
        draggable
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        className="cursor-grab text-muted-foreground hover:text-foreground"
        title="Drag to set the Metorite order"
      >
        <Icon name="GripVertical" className="w-4 h-4" />
      </span>
      <MoveButtons name={status.name} index={index} count={count} disabled={saving} onMove={onMove} />
      <span
        className={`w-2 h-2 shrink-0 rounded-full ${stageAccent(draft, "mirror").dot}`}
      />
      <span className="min-w-32 flex-1 truncate text-sm text-foreground">{status.name}</span>
      {gone && (
        <Badge tone="warning" title="The Zoho picklist no longer lists this value. Its records keep it.">
          Removed in Zoho
        </Badge>
      )}

      <label
        className="flex items-center gap-1 text-xs text-muted-foreground"
        title={pinnedReason(status.type)}
      >
        Win chance
        {kind === "deal" && (
          <>
            <span title="The probability in Zoho">Zoho {status.source_probability ?? 0}%</span>
            <span aria-hidden>·</span>
            Metorite
          </>
        )}
        <Input
          inputSize="sm"
          type="number"
          min={0}
          max={100}
          value={pinned ?? draft.ui_probability ?? ""}
          disabled={pinned !== undefined}
          placeholder={String(status.source_probability ?? 0)}
          onChange={(e) =>
            setDraft({
              ...draft,
              ui_probability: e.target.value === "" ? null : Number(e.target.value),
            })
          }
          className="w-16"
          aria-label="Metorite probability"
        />
        %
      </label>

      <SelectButton
        label="Colour"
        value={draft.ui_color ?? ""}
        options={overlayColorOptions(draft.ui_color)}
        defaultValue=""
        onChange={(color) => setDraft({ ...draft, ui_color: color || null })}
        widthClass="w-24"
      />

      <HideBox
        checked={Boolean(draft.hidden)}
        onChange={(hidden) => setDraft({ ...draft, hidden })}
      />

      {kind === "deal" && (
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={rulesOpen}
          onClick={() => setRulesOpen((open) => !open)}
        >
          {draft.max_dwell_days ? "Rules (1)" : "Rules"}
        </Button>
      )}

      <Button size="sm" disabled={!dirty || saving} onClick={() => void save()}>
        Save
      </Button>
      {dirty && (
        <Button
          size="sm"
          variant="ghost"
          disabled={saving}
          onClick={() => {
            clearDraft();
            setInvalid(null);
          }}
        >
          Cancel
        </Button>
      )}
      <Button
        size="sm"
        variant="ghost"
        icon="RotateCcw"
        disabled={!hasOverlay(kind, status as unknown as Record<string, unknown>) || saving}
        onClick={() => void onSave(resetToSource(kind))}
      >
        Reset to Zoho
      </Button>

      {kind === "deal" && rulesOpen && (
        <div className="flex w-full flex-wrap items-center gap-3 pl-6">
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
                  max_dwell_days: e.target.value === "" ? null : Number(e.target.value),
                })
              }
              className="w-16"
              aria-label="Rot threshold in days"
            />
            days
          </label>
        </div>
      )}

      {invalid && <p className="w-full text-xs text-destructive">{invalid}</p>}
    </div>
  );
}

/** The lost reasons of a Mirror org: the labels come from Zoho, and hide stays here. */
export function MirrorReasonGrid({
  reasons,
  saving,
  onSave,
}: {
  reasons: LostReason[];
  saving: boolean;
  onSave: (reasonId: string | null, body: Record<string, unknown>) => Promise<boolean>;
}) {
  return (
    <Card
      title="Lost reasons"
      note="The reasons come from Zoho. A hidden reason stays on its deals, and no picker offers it."
    >
      {reasons.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">None yet.</p>
      ) : (
        <ul>
          {[...reasons]
            .sort((a, b) => a.position - b.position)
            .map((reason) => (
              <li
                key={reason.id}
                className="flex items-center gap-2 border-b border-border/60 px-4 py-2 last:border-b-0"
              >
                <span className="min-w-32 flex-1 truncate text-sm text-foreground">
                  {reason.label}
                </span>
                <HideBox
                  checked={Boolean(reason.hidden)}
                  disabled={saving}
                  onChange={(hidden) => void onSave(reason.id, { hidden })}
                />
              </li>
            ))}
        </ul>
      )}
    </Card>
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
  const [newLabel, setNewLabel] = useSettingsDraft("new-reason", "");
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
              onHide={(hidden) => onSave(reason.id, { hidden })}
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
  onHide,
  onRemove,
}: {
  reason: LostReason;
  saving: boolean;
  onDragStart: (e: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onSave: (label: string) => Promise<boolean>;
  onHide: (hidden: boolean) => Promise<boolean>;
  onRemove: () => Promise<boolean>;
}) {
  // In the store, as in `StatusRow`, so a section change keeps it (CRM-C1).
  const [label, setLabel, clearDraft] = useSettingsDraft(`reason:${reason.id}`, reason.label);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  async function save() {
    const message = validateLostReasonDraft(label);
    setInvalid(message);
    if (message) return;
    if (await onSave(label.trim())) clearDraft();
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
      <HideBox
        checked={Boolean(reason.hidden)}
        disabled={saving}
        onChange={(hidden) => void onHide(hidden)}
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
