"use client";

/**
 * `?tab=settings` — CRM settings (WS-26f f2, WS-53 CRM-Q3).
 *
 * The cards, in this order: Setup and sync (it holds the connection, CRM-X0),
 * Access, Deal stages, Lead statuses, Lost reasons, Fields, Field mapping and
 * Sync health. A card heading is the label of its rail row (CRM-C1). The page passes Setup and Access in for an admin. Fields is for an
 * admin. The Field map (CRM-Q7a) and Sync health (CRM-Q9) are for an admin in
 * Mirror mode. In Native mode the Field mapping section holds the
 * lead-conversion mapping (CRM-C6a), for a studio admin only. The Fields
 * section also holds Record names (CRM-Z10g), under the Fields card.
 *
 * **Sections** (WS-53 CRM-C1, crm_platform.md §20.4). With a `section`, the
 * component draws the cards of that section only, and the rail opens one
 * section at a time. `lib/settingsSections.ts` holds the list of sections.
 * With no `section`, it draws every card in one column, as before.
 *
 * The cards drive the EXISTING admin API: deal stages, lead statuses, lost
 * reasons. Before this existed the API was headless — every stage rename,
 * reorder or probability was a curl, which is the second half of why the live
 * board was wrong (§5.1's delivery gap 2).
 *
 * **The stage list is the board** (WS-53 CRM-U11, D137, §20.15). Deal stages
 * and Lead statuses are each one `StageList`: one row for each stage, in the
 * order of `orderLanes`, the function the board calls. A move saves at once
 * through the order route. Edit opens one `Modal` for the rest.
 *
 * Gated on `feature:crm` like the rest of the app (D-CRM-3 — the sales team
 * manages its own pipeline). The one control that needs more is the Zoho pull,
 * which is floored on `admin:access:manage` server-side; hiding it here is the
 * courtesy half.
 *
 * Native mode with several pipelines (CRM-Q2) shows one read-only section for
 * each. The stage routes for a second pipeline are not in this build. Mirror
 * mode takes any count of pipelines, with a picker over the list.
 *
 * **Mirror mode** (WS-53 CRM-C3, crm_platform.md §20.6 item 14). Zoho owns the
 * stages, and an admin edits the Metorite overlay: the order, the win chance,
 * the colour, hide and the warning days. The sync never writes the overlay,
 * and nothing here writes Zoho (D95). A member who is not an admin still reads
 * the lists.
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
import Modal from "@/components/ui/Modal";
import SelectButton, { type SelectOption } from "@/components/ui/SelectButton";
import Tabs, { type TabDef } from "@/components/Tabs";
import { useId, useState } from "react";
import {
  orderLanes,
  REQUIREABLE_FIELD_LABELS,
  REQUIREABLE_FIELDS,
  stageAccent,
  stageIcon,
} from "../lib/board";
import { colorOptions } from "../lib/colorOptions";
import { orderedPipelines, placesOf, showPicker } from "../lib/pipelines";
import {
  CLAMPED_PROBABILITY,
  changedFields,
  draftOf,
  orderAfterMove,
  overlayPatch,
  reorder,
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
import RecordNamesCard from "./RecordNamesCard";
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
  onSaveLostReason,
  onRemoveLostReason,
  onReorderLostReasons,
  setup,
  access,
  pipelines = null,
  pipelineId = null,
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
  /** The pipeline the Deals board shows (CRM-U11). The stage list opens on it. */
  pipelineId?: string | null;
  /** The Fields and Sync health cards are for an admin. */
  showAdminCards?: boolean;
  dealStatuses: Status[];
  leadStatuses: Status[];
  lostReasons: LostReason[];
  saving: boolean;
  onSaveStatus: (
    kind: StatusKind,
    statusId: string | null,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  onRemoveStatus: (kind: StatusKind, statusId: string) => Promise<boolean>;
  onSaveLostReason: (
    reasonId: string | null,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  onRemoveLostReason: (reasonId: string) => Promise<boolean>;
  onReorderLostReasons: (patches: PositionPatch[]) => Promise<void>;
}) {
  const several = showPicker(pipelines);
  const orderStatuses = useCrmStore((state) => state.orderStatuses);
  const resetStatusOrder = useCrmStore((state) => state.resetStatusOrder);
  const addStage = useCrmStore((state) => state.addStageBeforeClosed);
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
            <StageList
              kind="deal"
              mode="mirror"
              statuses={dealStatuses}
              pipelines={pipelines}
              pipelineId={pipelineId}
              saving={saving}
              onSave={onSaveStatus}
              onOrder={orderStatuses}
              onResetOrder={resetStatusOrder}
            />
          )}
          {show("lead-statuses") && (
            <StageList
              kind="lead"
              mode="mirror"
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
              <StageList
                kind="deal"
                mode="native"
                statuses={dealStatuses}
                pipelines={pipelines}
                saving={saving}
                onSave={onSaveStatus}
                onOrder={orderStatuses}
                onRemove={onRemoveStatus}
                onAdd={addStage}
              />
            ))}

          {show("lead-statuses") && (
            <StageList
              kind="lead"
              mode="native"
              statuses={leadStatuses}
              saving={saving}
              onSave={onSaveStatus}
              onOrder={orderStatuses}
              onRemove={onRemoveStatus}
              onAdd={addStage}
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
          {/* CRM-Z10g: the names of the record types. Editable in Native
              mode, one line in Mirror mode. */}
          {show("fields") && <RecordNamesCard isAdmin={showAdminCards} />}
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

// ── CRM-U11 · the stage list is the board (D137, crm_platform.md §20.15) ────

/** The words of one list: the deal stages or the lead statuses. */
const LIST_WORDS: Record<
  StatusKind,
  { title: string; line: string; zoho: string; add: string; empty: Record<"mirror" | "native", string> }
> = {
  deal: {
    title: "Deal stages",
    line: "These are the columns of your Deals board, left to right. Drag a stage, or use its arrows, to move its column.",
    zoho: "Zoho owns the stage names. Your order, colours and win chances stay in Metorite. Nothing here changes Zoho.",
    add: "Add stage",
    empty: {
      mirror: "No stages from Zoho yet. The next sync brings them.",
      native: "No stages yet. Add the first one.",
    },
  },
  lead: {
    title: "Lead statuses",
    line: "These are the columns of your Leads board, left to right. Drag a status, or use its arrows, to move its column.",
    zoho: "Zoho owns the status names. Your order, colours and win chances stay in Metorite. Nothing here changes Zoho.",
    add: "Add status",
    empty: {
      mirror: "No statuses from Zoho yet. The next sync brings them.",
      native: "No statuses yet. Add the first one.",
    },
  },
};

/** The two choices of "Column order" (Mirror mode). */
const ORDER_TABS: TabDef[] = [
  { id: "zoho", label: "Same as Zoho" },
  { id: "custom", label: "Custom" },
];

/** The Edit dialog of a stage that does not exist yet. */
const NEW = "new";

const NO_PLACES: ReadonlyMap<string, number> = new Map();

/** A status as a lane of `orderLanes`, the board's one order. */
const laneOf = (status: Status) => ({ status, pipeline_position: null });

/**
 * The stage list of one kind: the columns of its board, left to right.
 *
 * The list orders the stages with `orderLanes`, the function the board calls,
 * with the places of the pipeline on screen. So a move here shows the same
 * order on the board. A move saves at once, every id of the kind, through the
 * order route. On a failure the row goes back and the page shows the error.
 *
 * Mirror mode serves any count of pipelines. Native mode serves one pipeline
 * or none, and `PipelineSections` keeps the read-only list for more.
 */
export function StageList({
  kind,
  mode,
  statuses,
  pipelines = null,
  pipelineId = null,
  saving,
  onSave,
  onOrder,
  onResetOrder,
  onRemove,
  onAdd,
}: {
  kind: StatusKind;
  mode: "mirror" | "native";
  /** Every row of the kind, hidden and gone ones included. */
  statuses: Status[];
  /** `GET /crm/pipelines`, or null with no such route. */
  pipelines?: DealPipeline[] | null;
  /** The pipeline the list opens on. Null: the default pipeline. */
  pipelineId?: string | null;
  saving: boolean;
  onSave: (
    kind: StatusKind,
    statusId: string | null,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  /** Every id of the kind, in the new order. */
  onOrder: (kind: StatusKind, ids: string[]) => Promise<boolean>;
  /** Mirror mode: "Same as Zoho" clears the saved order. */
  onResetOrder?: (kind: StatusKind) => Promise<boolean>;
  /** Native mode: Delete in the Edit dialog. */
  onRemove?: (kind: StatusKind, statusId: string) => Promise<boolean>;
  /** Native mode: "Add stage". The id of the new stage, or null. */
  onAdd?: (kind: StatusKind, body: Record<string, unknown>) => Promise<string | null>;
}) {
  const words = LIST_WORDS[kind];
  const mirror = mode === "mirror";
  const [served, setServed] = useState<Status[]>(statuses);
  // The full order after a move, until the re-read brings the server's.
  const [moved, setMoved] = useState<string[] | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const [picked, setPicked] = useState<string | null>(pipelineId);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirmingReset, setConfirmingReset] = useState(false);
  // The server's answer is the truth, as in the lost reasons grid. Adjusted
  // during render, so a re-read never paints the stale order once.
  if (served !== statuses) {
    setServed(statuses);
    setMoved(null);
  }

  const several = mirror && kind === "deal" && showPicker(pipelines);
  const onScreen =
    several && pipelines
      ? (pipelines.find((p) => p.id === picked) ??
        pipelines.find((p) => p.is_default) ??
        orderedPipelines(pipelines)[0])
      : null;
  const places = kind === "deal" ? placesOf(pipelines, onScreen?.id ?? picked) : NO_PLACES;
  // Every stage that some pipeline lists. Null with no pipeline list.
  const listed =
    kind === "deal" && pipelines && pipelines.length > 0
      ? new Set(pipelines.flatMap((p) => p.stages.map((stage) => stage.id)))
      : null;
  const fullIds = moved ?? orderLanes(statuses.map(laneOf), mode, places).map((l) => l.status.id);
  const byId = new Map(statuses.map((status) => [status.id, status]));
  // With two pipelines the list is a part: the stages of the pipeline on
  // screen, and the stages that no pipeline lists.
  const shown = fullIds
    .map((id) => byId.get(id))
    .filter(
      (status): status is Status =>
        !!status && (!several || places.has(status.id) || !listed?.has(status.id))
    );
  const saved = statuses.some((status) => status.admin_order);
  const custom = saved || moved !== null;

  async function saveOrder(ids: string[]) {
    setMoved(ids);
    if (!(await onOrder(kind, ids))) setMoved(null);
  }

  async function move(from: number, to: number) {
    const ids = orderAfterMove(fullIds, shown.map((status) => status.id), from, to);
    if (ids) await saveOrder(ids);
  }

  async function drop(to: number) {
    // A drop while a save runs would send a second order call (verifier F1).
    if (dragging === null || saving) return;
    const from = dragging;
    setDragging(null);
    await move(from, to);
  }

  function chooseOrder(choice: string) {
    if (saving) return;
    if (choice === "zoho") {
      // With no saved order the list is the Zoho order already.
      if (saved) setConfirmingReset(true);
      return;
    }
    // "Custom" keeps the order on screen as the saved order.
    if (!custom) void saveOrder(fullIds);
  }

  const close = () => setEditing(null);

  return (
    <section className="rounded-xl border border-border bg-card/40" aria-label={words.title}>
      <header className="space-y-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{words.title}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{words.line}</p>
        </div>
        {mirror && (
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-foreground">Column order</span>
              <Tabs
                tabs={ORDER_TABS}
                activeTab={custom ? "custom" : "zoho"}
                onTabChange={chooseOrder}
                variant="segmented"
                chrome="inline"
              />
            </div>
            <p className="text-xs text-muted-foreground">{words.zoho}</p>
          </div>
        )}
        {several && pipelines && onScreen && (
          <SelectButton
            label="Pipeline"
            prefix="Pipeline:"
            value={onScreen.id}
            options={orderedPipelines(pipelines).map((p) => ({ value: p.id, label: p.name }))}
            defaultValue={onScreen.id}
            onChange={setPicked}
            widthClass="w-64 max-w-full"
          />
        )}
      </header>
      <ul>
        {shown.map((status, index) => (
          <li
            key={status.id}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              void drop(index);
            }}
            className="border-b border-border/60 last:border-b-0"
          >
            <StageRow
              mode={mode}
              status={status}
              index={index}
              count={shown.length}
              saving={saving}
              notInPipeline={mirror && listed !== null && !places.has(status.id)}
              onMove={(to) => void move(index, to)}
              onDragStart={(e) => {
                e.dataTransfer.setData("text/plain", status.id);
                e.dataTransfer.effectAllowed = "move";
                setDragging(index);
              }}
              onDragEnd={() => setDragging(null)}
              onEdit={() => setEditing(status.id)}
            />
          </li>
        ))}
        {shown.length === 0 && (
          <li className="px-4 py-6 text-center text-xs text-muted-foreground">
            {words.empty[mode]}
          </li>
        )}
      </ul>
      {!mirror && onAdd && (
        <div className="border-t border-border px-4 py-3">
          <Button
            variant="secondary"
            size="sm"
            icon="Plus"
            disabled={saving}
            onClick={() => setEditing(NEW)}
          >
            {words.add}
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={confirmingReset}
        title="Use the Zoho order again?"
        body="Your custom order will be lost."
        confirmLabel="Use the Zoho order"
        busy={saving}
        onCancel={() => setConfirmingReset(false)}
        onConfirm={async () => {
          setConfirmingReset(false);
          await onResetOrder?.(kind);
        }}
      />
      <StageEditModal
        kind={kind}
        mode={mode}
        editing={editing}
        statuses={statuses}
        saving={saving}
        onClose={close}
        onSave={async (statusId, body) => {
          const ok = await onSave(kind, statusId, body);
          if (ok) close();
          return ok;
        }}
        onRemove={async (statusId) => {
          const ok = onRemove ? await onRemove(kind, statusId) : false;
          if (ok) close();
          return ok;
        }}
        onAdd={async (body) => {
          const id = onAdd ? await onAdd(kind, body) : null;
          if (id) close();
          return id !== null;
        }}
      />
    </section>
  );
}

/** One row: one line. No input, Save, Cancel or Reset sits on a row. */
function StageRow({
  mode,
  status,
  index,
  count,
  saving,
  notInPipeline,
  onMove,
  onDragStart,
  onDragEnd,
  onEdit,
}: {
  mode: "mirror" | "native";
  status: Status;
  index: number;
  count: number;
  saving: boolean;
  notInPipeline: boolean;
  onMove: (to: number) => void;
  onDragStart: (e: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onEdit: () => void;
}) {
  const chance = CLAMPED_PROBABILITY[status.type] ?? status.probability ?? 0;
  return (
    <div className="flex items-center gap-2 px-4 py-2">
      <span
        draggable={!saving}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        className="shrink-0 cursor-grab text-muted-foreground hover:text-foreground"
        title="Drag to move this column"
      >
        <Icon name="GripVertical" className="w-4 h-4" />
      </span>
      <MoveButtons name={status.name} index={index} count={count} disabled={saving} onMove={onMove} />
      <span
        aria-hidden="true"
        className={`h-2 w-2 shrink-0 rounded-full ${stageAccent(status, mode).dot}`}
      />
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
        <span className="min-w-0 max-w-full truncate text-sm text-foreground">{status.name}</span>
        <StageTags status={status} notInPipeline={notInPipeline} />
      </div>
      <span className="shrink-0 text-xs tabular-nums text-muted-foreground" title="Win chance">
        {chance}%
      </span>
      <Button size="sm" variant="ghost" aria-label={`Edit ${status.name}`} onClick={onEdit}>
        Edit
      </Button>
    </div>
  );
}

/** The tags of a row, only where one applies. */
function StageTags({ status, notInPipeline }: { status: Status; notInPipeline: boolean }) {
  const icon = stageIcon(status.type) ?? undefined;
  return (
    <>
      {status.type === "won" && <Badge icon={icon}>Won</Badge>}
      {status.type === "lost" && <Badge icon={icon}>Lost</Badge>}
      {status.type === "on_hold" && <Badge icon={icon}>On hold</Badge>}
      {status.hidden && (
        <Badge icon="EyeOff" title="The board shows this column only while it holds records.">
          Hidden
        </Badge>
      )}
      {notInPipeline && (
        <Badge title="The Zoho pipeline does not list this stage.">Not in the Zoho pipeline</Badge>
      )}
      {status.source_gone_at && (
        <Badge tone="warning" title="The Zoho picklist no longer lists this value. Its records keep it.">
          Removed in Zoho
        </Badge>
      )}
    </>
  );
}

/** The Edit dialog of a stage, and the empty one of "Add stage". */
function StageEditModal({
  kind,
  mode,
  editing,
  statuses,
  saving,
  onClose,
  onSave,
  onRemove,
  onAdd,
}: {
  kind: StatusKind;
  mode: "mirror" | "native";
  /** The id of the stage, `NEW`, or null when closed. */
  editing: string | null;
  statuses: Status[];
  saving: boolean;
  onClose: () => void;
  onSave: (statusId: string, body: Record<string, unknown>) => Promise<boolean>;
  onRemove: (statusId: string) => Promise<boolean>;
  onAdd: (body: Record<string, unknown>) => Promise<boolean>;
}) {
  const status = editing && editing !== NEW ? (statuses.find((s) => s.id === editing) ?? null) : null;
  const adding = editing === NEW && mode === "native";
  const open = adding || status !== null;
  return (
    <Modal
      open={open}
      // While a write runs, Escape and the scrim do not close the dialog, so
      // its refusal keeps a place to show.
      onClose={saving ? () => undefined : onClose}
      title={adding ? LIST_WORDS[kind].add : status?.name}
      size="md"
      className="max-h-[85vh]"
    >
      {status && mode === "mirror" ? (
        <MirrorStageForm
          key={status.id}
          kind={kind}
          status={status}
          saving={saving}
          onSave={(body) => onSave(status.id, body)}
          onCancel={onClose}
        />
      ) : open ? (
        <NativeStageForm
          key={status?.id ?? NEW}
          kind={kind}
          status={status}
          saving={saving}
          onSave={(body) => (status ? onSave(status.id, body) : Promise.resolve(false))}
          onAdd={onAdd}
          onRemove={() => (status ? onRemove(status.id) : Promise.resolve(false))}
          onCancel={onClose}
        />
      ) : null}
    </Modal>
  );
}

/** One field of the Edit dialog: a label, the control, and a help line. */
function Field({
  label,
  htmlFor,
  help,
  children,
}: {
  label: string;
  htmlFor?: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={htmlFor} className="block text-xs font-medium text-foreground">
        {label}
      </label>
      {children}
      {help && <p className="text-[11px] text-muted-foreground">{help}</p>}
    </div>
  );
}

/** "Show this column on the board": the inverse of hide (CRM-C3). */
function ShowOnBoardField({
  kind,
  hidden,
  onChange,
}: {
  kind: StatusKind;
  hidden: boolean;
  onChange: (hidden: boolean) => void;
}) {
  return (
    <div className="space-y-1">
      <label className="flex items-center gap-2 text-xs text-foreground">
        <Checkbox size="sm" checked={!hidden} onChange={(e) => onChange(!e.target.checked)} />
        Show this column on the board
      </label>
      <p className="pl-5 text-[11px] text-muted-foreground">
        {kind === "deal"
          ? "Off: the column shows only while it holds deals, and no picker offers the stage."
          : "Off: the column shows only while it holds leads, and no picker offers the status."}
      </p>
    </div>
  );
}

/** "Warn when a deal stays more than N days" (WS-26h). Empty: never warn. */
function DwellField({
  id,
  days,
  onChange,
}: {
  id: string;
  days: number | null | undefined;
  onChange: (days: number | null) => void;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="flex flex-wrap items-center gap-2 text-xs text-foreground">
        Warn when a deal stays more than
        <Input
          id={id}
          inputSize="sm"
          type="number"
          min={1}
          max={32767}
          value={days ?? ""}
          // An emptied box is "never", which is null. The gateway refuses 0.
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
          className="w-20"
        />
        days
      </label>
      <p className="text-[11px] text-muted-foreground">Leave it empty to never warn.</p>
    </div>
  );
}

/** "Fields a deal needs to enter this stage" (WS-26h). The move gate reads them. */
function RequiredFieldsField({
  value,
  onChange,
}: {
  value: string[];
  onChange: (fields: string[]) => void;
}) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-xs font-medium text-foreground">
        Fields a deal needs to enter this stage
      </legend>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {REQUIREABLE_FIELDS.map((field) => {
          const on = value.includes(field);
          return (
            <label key={field} className="flex items-center gap-1.5 text-xs text-foreground">
              <Checkbox
                size="sm"
                checked={on}
                // Rebuilt from the allowlist, so the stored order is always
                // the allowlist's, whatever the order of the clicks.
                onChange={() =>
                  onChange(
                    REQUIREABLE_FIELDS.filter((name) => (name === field ? !on : value.includes(name)))
                  )
                }
              />
              {REQUIREABLE_FIELD_LABELS[field]}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

/** The error of the dialog: the client rule, or the refusal of the server. */
function FormError({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p role="alert" className="px-4 pb-2 text-xs text-destructive">
      {text}
    </p>
  );
}

/** One line of help for each stage type (CRM-U11 item 6). */
const TYPE_HELP: Record<StatusKind, Record<StatusType, string>> = {
  deal: {
    open: "Work in progress. Its deals count in the forecast.",
    ongoing: "Work in progress, as Open.",
    on_hold: "Paused. Its deals stay out of the forecast.",
    won: "A deal here is won. It always forecasts 100%.",
    lost: "A deal here is lost. A move here asks for a lost reason.",
  },
  lead: {
    open: "A lead that is still in work.",
    ongoing: "A lead that is still in work, as Open.",
    on_hold: "A lead that waits.",
    won: "The funnel ends here, with a win.",
    lost: "The funnel ends here, with a loss.",
  },
};

/**
 * The type choices: Open, On hold, Won and Lost. "In progress" shows only for
 * a stage that has it already, so an old stage keeps its type.
 */
function typeOptions(kind: StatusKind, current: StatusType): SelectOption[] {
  const types: StatusType[] = ["open", "on_hold", "won", "lost"];
  if (current === "ongoing") types.splice(1, 0, "ongoing");
  return types.map((type) => ({
    value: type,
    label: STATUS_TYPE_LABELS[type],
    description: TYPE_HELP[kind][type],
  }));
}

/** The empty draft of "Add stage". One object, so the draft store can see it. */
const NEW_STAGE: Status = {
  id: "",
  name: "",
  color: "gray",
  position: 0,
  type: "open",
  is_default: false,
  probability: 0,
  required_fields: [],
  max_dwell_days: null,
  hidden: false,
};

/** The create body of "Add stage": the name, the type, and what was set. */
function newStageBody(kind: StatusKind, draft: Status): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: draft.name.trim(),
    type: draft.type,
    probability: CLAMPED_PROBABILITY[draft.type] ?? draft.probability ?? 0,
  };
  if (draft.color && draft.color !== "gray") body.color = draft.color;
  if (draft.hidden) body.hidden = true;
  if (draft.is_default) body.is_default = true;
  if (kind === "deal") {
    if ((draft.required_fields ?? []).length > 0) body.required_fields = draft.required_fields;
    if (draft.max_dwell_days != null) body.max_dwell_days = draft.max_dwell_days;
  }
  return body;
}

/**
 * The Edit dialog of a Native stage, and of "Add stage". The admin owns every
 * field: the name, the type, the colour, the win chance, show, the default,
 * and for a deal stage the warning and the required fields.
 */
export function NativeStageForm({
  kind,
  status,
  saving,
  onSave,
  onAdd,
  onRemove,
  onCancel,
}: {
  kind: StatusKind;
  /** Null for "Add stage". */
  status: Status | null;
  saving: boolean;
  onSave: (body: Record<string, unknown>) => Promise<boolean>;
  onAdd: (body: Record<string, unknown>) => Promise<boolean>;
  onRemove: () => Promise<boolean>;
  onCancel: () => void;
}) {
  // In the store, as each settings draft is (CRM-C1), so a closed dialog
  // keeps an edit for its return. A re-read of the stage drops it.
  const [draft, setDraft, clearDraft] = useSettingsDraft(`status:${kind}:${status?.id ?? NEW}`, status ?? NEW_STAGE);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const storeError = useCrmStore((state) => state.error);
  const id = useId();
  const pinned = CLAMPED_PROBABILITY[draft.type];

  async function save() {
    const message = validateStatusDraft(
      {
        name: draft.name,
        type: draft.type,
        probability: pinned ?? draft.probability,
        color: draft.color,
        required_fields: draft.required_fields,
        max_dwell_days: draft.max_dwell_days,
      },
      kind
    );
    setInvalid(message);
    setFailed(false);
    if (message) return;
    if (!status) {
      if (await onAdd(newStageBody(kind, draft))) clearDraft();
      else setFailed(true);
      return;
    }
    // Only what moved. `type` and `probability` are judged TOGETHER by the
    // server's clamp, so an unchanged one is never sent with a changed one.
    const patch = changedFields(
      status as unknown as Record<string, unknown>,
      draft as unknown as Record<string, unknown>
    );
    if (typeof patch.name === "string") patch.name = patch.name.trim();
    if (Object.keys(patch).length === 0) {
      onCancel();
      return;
    }
    if (await onSave(patch)) clearDraft();
    else setFailed(true);
  }

  return (
    <form
      noValidate
      className="flex min-h-0 flex-1 flex-col"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        <Field label="Name" htmlFor={`${id}-name`}>
          <Input
            id={`${id}-name`}
            inputSize="sm"
            autoFocus={!status}
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </Field>
        <Field label="Type" htmlFor={`${id}-type`} help={TYPE_HELP[kind][draft.type]}>
          <SelectButton
            id={`${id}-type`}
            label="Type"
            value={draft.type}
            options={typeOptions(kind, status?.type ?? draft.type)}
            defaultValue={draft.type}
            onChange={(type) => {
              // A won or lost type fixes the win chance, so the draft follows
              // it and the server never sees a contradiction.
              const fixed = CLAMPED_PROBABILITY[type as StatusType];
              setDraft({
                ...draft,
                type: type as StatusType,
                ...(fixed !== undefined ? { probability: fixed } : {}),
              });
            }}
            widthClass="w-full sm:w-56"
          />
        </Field>
        <Field label="Colour" htmlFor={`${id}-colour`}>
          <SelectButton
            id={`${id}-colour`}
            label="Colour"
            value={draft.color || "gray"}
            options={colorOptions(draft.color)}
            defaultValue={draft.color || "gray"}
            onChange={(color) => setDraft({ ...draft, color })}
            widthClass="w-full sm:w-56"
          />
        </Field>
        <Field label="Win chance" htmlFor={`${id}-chance`} help={pinnedReason(draft.type)}>
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Input
              id={`${id}-chance`}
              inputSize="sm"
              type="number"
              min={0}
              max={100}
              value={pinned ?? draft.probability ?? 0}
              disabled={pinned !== undefined}
              onChange={(e) => setDraft({ ...draft, probability: Number(e.target.value) })}
              className="w-20"
            />
            %
          </span>
        </Field>
        <ShowOnBoardField
          kind={kind}
          hidden={Boolean(draft.hidden)}
          onChange={(hidden) => setDraft({ ...draft, hidden })}
        />
        <label className="flex items-center gap-2 text-xs text-foreground">
          <Checkbox
            size="sm"
            checked={draft.is_default}
            onChange={(e) => setDraft({ ...draft, is_default: e.target.checked })}
          />
          {kind === "deal" ? "New deals start in this stage" : "New leads start in this status"}
        </label>
        {kind === "deal" && (
          <>
            <DwellField
              id={`${id}-dwell`}
              days={draft.max_dwell_days}
              onChange={(days) => setDraft({ ...draft, max_dwell_days: days })}
            />
            <RequiredFieldsField
              value={draft.required_fields ?? []}
              onChange={(fields) => setDraft({ ...draft, required_fields: fields })}
            />
          </>
        )}
      </div>
      <FormError text={invalid ?? (failed ? (storeError ?? "The save did not go through. Try again.") : null)} />
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
        {status && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            icon="Trash2"
            disabled={saving}
            onClick={() => setConfirming(true)}
          >
            {kind === "deal" ? "Delete stage" : "Delete status"}
          </Button>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={saving}
            onClick={() => {
              clearDraft();
              onCancel();
            }}
          >
            Cancel
          </Button>
          <Button type="submit" size="sm" loading={saving}>
            {status ? "Save" : LIST_WORDS[kind].add}
          </Button>
        </div>
      </div>
      {status && (
        <ConfirmDialog
          open={confirming}
          title={`Delete “${status.name}”?`}
          body="The stage leaves the settings and the board. The server refuses the delete while any record is still in it."
          confirmLabel="Delete"
          busy={saving}
          onCancel={() => setConfirming(false)}
          onConfirm={async () => {
            setConfirming(false);
            if (await onRemove()) clearDraft();
            else setFailed(true);
          }}
        />
      )}
    </form>
  );
}

/**
 * The Edit dialog of a Mirror stage (CRM-C3 overlay). Zoho owns the name and
 * the type. The admin sets the Metorite colour, win chance, show, and for a
 * deal stage the warning. Nothing here writes Zoho (D95).
 */
export function MirrorStageForm({
  kind,
  status,
  saving,
  onSave,
  onCancel,
}: {
  kind: StatusKind;
  status: Status;
  saving: boolean;
  onSave: (body: Record<string, unknown>) => Promise<boolean>;
  onCancel: () => void;
}) {
  // In the store, as each settings draft is (CRM-C1).
  const [draft, setDraft, clearDraft] = useSettingsDraft(`overlay:${kind}:${status.id}`, status);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const storeError = useCrmStore((state) => state.error);
  const id = useId();
  const pinned = CLAMPED_PROBABILITY[status.type];
  const zoho = status.source_probability ?? 0;

  async function save() {
    const message = validateOverlayProbability(draft.ui_probability);
    setInvalid(message);
    setFailed(false);
    if (message) return;
    const patch = overlayPatch(
      kind,
      status as unknown as Record<string, unknown>,
      draft as unknown as Record<string, unknown>
    );
    if (Object.keys(patch).length === 0) {
      onCancel();
      return;
    }
    if (await onSave(patch)) clearDraft();
    else setFailed(true);
  }

  return (
    <form
      noValidate
      className="flex min-h-0 flex-1 flex-col"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        <Field
          label="Colour"
          htmlFor={`${id}-colour`}
          help="Zoho has no stage colour. With none, the type picks it."
        >
          <SelectButton
            id={`${id}-colour`}
            label="Colour"
            value={draft.ui_color ?? ""}
            options={overlayColorOptions(draft.ui_color)}
            defaultValue={draft.ui_color ?? ""}
            onChange={(color) => setDraft({ ...draft, ui_color: color || null })}
            widthClass="w-full sm:w-56"
          />
        </Field>
        <Field label="Win chance" htmlFor={`${id}-chance`} help={pinnedReason(status.type)}>
          <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Input
                id={`${id}-chance`}
                inputSize="sm"
                type="number"
                min={0}
                max={100}
                value={pinned ?? draft.ui_probability ?? ""}
                // Empty: the Zoho value applies, and the placeholder shows it.
                placeholder={String(zoho)}
                disabled={pinned !== undefined}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    ui_probability: e.target.value === "" ? null : Number(e.target.value),
                  })
                }
                className="w-20"
              />
              %
            </span>
            {pinned === undefined && (
              <>
                <span>Zoho: {zoho}%</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={draft.ui_probability == null}
                  onClick={() => setDraft({ ...draft, ui_probability: null })}
                >
                  Use Zoho&apos;s value
                </Button>
              </>
            )}
          </span>
        </Field>
        <ShowOnBoardField
          kind={kind}
          hidden={Boolean(draft.hidden)}
          onChange={(hidden) => setDraft({ ...draft, hidden })}
        />
        {kind === "deal" && (
          <DwellField
            id={`${id}-dwell`}
            days={draft.max_dwell_days}
            onChange={(days) => setDraft({ ...draft, max_dwell_days: days })}
          />
        )}
      </div>
      <FormError text={invalid ?? (failed ? (storeError ?? "The save did not go through. Try again.") : null)} />
      <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={saving}
          onClick={() => {
            clearDraft();
            onCancel();
          }}
        >
          Cancel
        </Button>
        <Button type="submit" size="sm" loading={saving}>
          Save
        </Button>
      </div>
    </form>
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
          A member must pick one of these when a deal is lost.
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
