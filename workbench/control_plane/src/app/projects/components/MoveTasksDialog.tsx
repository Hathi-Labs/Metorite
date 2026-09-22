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

import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import SelectButton from "@/components/ui/SelectButton";

import { type MovePlan, projectsApi } from "../lib/api";
import {
  classify,
  withAssignee,
  withoutAssignee,
} from "../lib/assignees";
import {
  type FieldDef,
  missingSentence,
  requiredBlanks,
  toInput,
  toWire,
} from "../lib/customFields";
import { askedFields, readPlan } from "../lib/movePlan";
import { LEVEL_ICONS, type ProjectNode, nodeKind, nodeLevel } from "../lib/tree";
import { AssigneePicker } from "./AssigneePicker";
import { FieldControl } from "./CustomFieldValues";

/**
 * S6c — what the promote door answers on top of the destination.
 *
 * My Tasks opens THIS dialog (my_tasks_cutover.md §4.8 point 5: one dialog,
 * never a second drawing) and asks two more questions after "where": the
 * destination's REQUIRED fields the task does not carry (migration 192), and
 * who owns it there. Both travel to `onConfirm` for the ONE request
 * `apiMoveTask` sends, so a promoted task is never on a board owned by nobody.
 */
export interface PromoteAnswers {
  /** The destination's required definitions the preview named as unanswered. */
  fields: FieldDef[];
  /** Control values keyed by `field_key`, as the inputs hold them. */
  draft: Record<string, unknown>;
  assignees: string[];
  initialAssignees: string[];
}

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

/** Control values → wire values, for the blank check the server will make. */
function wireOf(
  defs: readonly FieldDef[],
  draft: Record<string, unknown>
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const def of defs) out[def.field_key] = toWire(def.field_type, draft[def.field_key]);
  return out;
}

/** Every project a task may land in, flattened in tree order. */
function destinations(
  roots: readonly ProjectNode[],
  depth = 0
): { node: ProjectNode; depth: number; legal: boolean }[] {
  const out: { node: ProjectNode; depth: number; legal: boolean }[] = [];
  for (const node of roots) {
    out.push({
      node,
      depth,
      // A folder holds projects, not tasks — the server refuses it with those
      // words, and the picker says so first rather than teaching by 422.
      legal: nodeLevel(nodeKind(node), depth) !== "folder",
    });
    if (node.children?.length) out.push(...destinations(node.children, depth + 1));
  }
  return out;
}

/** A node's display name, for naming the two ends of the mapping. */
function nameOf(rows: { node: ProjectNode }[], id: string | null): string {
  if (!id) return "";
  return rows.find((row) => row.node.id === id)?.node.name ?? "";
}

/**
 * The column header both mapping tables carry.
 *
 * ⚠️ **Owner directive, 2026-09-19: name the two ends explicitly.** The rows
 * read `A → B`, and an arrow alone leaves the reader to infer which side is
 * which — on the one screen where getting the direction backwards silently
 * rewrites every task in the selection. So each column says the PROJECT it
 * belongs to, and whether that is where the tasks are now or where they are
 * going.
 */
function MapHeader({ source, destination }: { source: string; destination: string }) {
  return (
    <div className="flex items-center gap-2 border-b border-border pb-1">
      <span className="min-w-[6rem] flex-1 basis-0 truncate font-medium text-foreground">
        From{source ? `: ${source}` : ""}
        <span className="ml-1 font-normal text-muted-foreground">(now)</span>
      </span>
      <span className="h-3 w-3 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 basis-0 truncate font-medium text-foreground">
        To{destination ? `: ${destination}` : ""}
        <span className="ml-1 font-normal text-muted-foreground">(after)</span>
      </span>
    </div>
  );
}

export function MoveTasksDialog({
  taskIds,
  roots,
  onClose,
  onConfirm,
  busy,
  error,
  promote,
}: Props) {
  const [destination, setDestination] = useState<string | null>(null);
  const [plan, setPlan] = useState<MovePlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  // S6c — the promote door's two extra answers. `requiredDefs` is null while
  // the destination's definitions are still on their way, so the button
  // cannot offer a move the server would refuse for a field it has not
  // been asked about yet.
  const [requiredDefs, setRequiredDefs] = useState<FieldDef[] | null>([]);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [assignees, setAssignees] = useState<string[]>(
    () => promote?.initialAssignees ?? []
  );
  const [assigneeText, setAssigneeText] = useState("");

  const ids = taskIds ?? [];
  const key = ids.join(",");
  const promoting = Boolean(promote);
  // A request token. Two destinations picked quickly are two previews in
  // flight, and the slow one used to land last and overwrite the fast one:
  // the card then showed one project's name over another's mapping.
  const loadSeq = useRef(0);

  const load = useCallback(
    async (destinationId: string) => {
      const seq = ++loadSeq.current;
      const stale = () => seq !== loadSeq.current;
      setPlanning(true);
      setPlanError(null);
      // Cleared, not kept: the previous destination's mapping under a new
      // destination's name is the frame somebody agrees to by mistake.
      setPlan(null);
      setOverrides({});
      setRequiredDefs([]);
      setDraft({});
      try {
        const next = await projectsApi.previewMove({
          task_ids: [...ids],
          destination_project_id: destinationId,
        });
        if (stale()) return;
        setPlan(next);
        // S6c. The preview names the required fields the task does not
        // answer; the definitions say what KIND of answer each wants. Only
        // the promote door asks — the bulk move has no place to type one.
        if (promote && (next.required_missing ?? []).length > 0) {
          setRequiredDefs(null);
          const { rows } = await projectsApi.fields(next.destination_project_id);
          if (stale()) return;
          const wanted = new Set(next.required_missing);
          const defs = (rows as FieldDef[]).filter(
            (def) => def.required && (wanted.has(def.name) || wanted.has(def.field_key))
          );
          const seed: Record<string, unknown> = {};
          for (const def of defs) seed[def.field_key] = toInput(def.field_type, undefined);
          setDraft(seed);
          setRequiredDefs(defs);
        }
      } catch (err) {
        if (stale()) return;
        setPlanError(String((err as Error).message));
        setRequiredDefs([]);
      } finally {
        if (!stale()) setPlanning(false);
      }
    },
    [key, promoting] // eslint-disable-line react-hooks/exhaustive-deps
  );

  useEffect(() => {
    if (destination) void load(destination);
  }, [destination, load]);

  if (!taskIds || ids.length === 0) return null;

  // The fields to ask about: the preview's, plus any a refusal named. A
  // refused field the preview did not list (a definition added between the
  // preview and the move) is drawn with an empty control, not merely named.
  const asked = askedFields(requiredDefs, promote?.refusedFields);
  const missing = asked
    ? requiredBlanks(asked, wireOf(asked, draft)).map((def) => def.name)
    : [];
  const blocked = missingSentence(missing);
  const addAssignee = (raw: string) => {
    setAssignees((current) => withAssignee(current, raw));
    setAssigneeText("");
  };

  const destRows = destinations(roots);
  // ⚠️ Every array the card reads off the preview is guarded ONCE, in
  // `readPlan`, and `movePlan.test.ts` feeds it the bulk e2e stub — which
  // carries no promote-only field. The S6c version read
  // `plan.required_missing.length` inline, the bulk preview omits that key,
  // and the page went white on PR #395's browser suite.
  //
  // `destStatuses` is from the PLAN, not from the page. The page holds only
  // the selected project's lanes and the destination is never the selected
  // project, so the old source answered `undefined` every time — every
  // dropdown rendered one option and the override was a shipped no-op.
  //
  // An orphan is listed in `fieldRows` with an explicit "dropped" rather
  // than omitted: a table that silently skips the fields it cannot carry is
  // the table that made this feature necessary.
  const reading = readPlan(plan);
  const drops = reading?.drops ?? [];
  const lostTypes = reading?.lostTypes ?? [];
  const unregistered = reading?.unregistered ?? [];
  const destStatuses = reading?.destStatuses ?? [];
  const fieldRows = reading?.fieldRows ?? [];
  const requiredMissing = reading?.requiredMissing ?? [];
  const sourceName = nameOf(destRows, plan?.source_project_id ?? null);
  // ⚠️ From the PICKED node first, not the plan's id. The member chose it from
  // this very list, so the name is always resolvable — whereas the plan's id
  // may name a node the loaded tree does not contain, and the header would
  // then degrade to a bare "To", which is the exact ambiguity the explicit
  // headers exist to remove (owner directive, 2026-09-19).
  const destName =
    nameOf(destRows, destination) ||
    nameOf(destRows, plan?.destination_project_id ?? null);
  const clean = reading?.clean ?? false;

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
          ? "The task leaves your list for the board. Statuses and fields are mapped into the project's own vocabulary."
          : "Statuses and custom fields are mapped into the destination's own vocabulary."
      }
    >
      <div className="space-y-3 p-3 text-xs">
        <label className="block space-y-1">
          <span className="text-muted-foreground">Move to</span>
          {/* ⚠️ `SelectButton`, NOT the `Select` primitive (owner, 2026-09-20).
              `Select` is a native `<select>` wearing the house paint on its
              TRIGGER. The open list is drawn by the operating system, so on
              Windows this picker was a white panel with a blue highlight bar
              in the middle of a dark dialog. The trigger looked right, which
              is why it survived the H-94 sweep — and why the conformance
              rule missed it: that regex is `/<select/g`, case sensitive,
              so the capital-S wrapper never counted.

              The tree survives the move. `depth` indents each row, where the
              old markup padded the option TEXT with non-breaking spaces.
              That works only inside a native `<option>`, whose text the
              browser renders verbatim. */}
          <SelectButton
            label="Move to"
            widthClass="w-full"
            value={destination ?? ""}
            onChange={(next) => setDestination(next || null)}
            options={[
              { value: "", label: "Pick a project…" },
              ...destRows.map(({ node, depth, legal }) => ({
                value: node.id,
                label: node.name,
                depth,
                disabled: !legal,
                // A folder stays in the list rather than being dropped: it is
                // what explains the indent of the project beneath it.
                hint: legal ? undefined : "a folder holds projects, not tasks",
              })),
            ]}
          />
        </label>

        {planning ? (
          <p className="text-muted-foreground">Working out what would change…</p>
        ) : planError ? (
          <p className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive">
            {planError}
          </p>
        ) : plan ? (
          <>
            {clean ? (
              <p className="text-foreground">
                Nothing is remapped — the destination shares this task&rsquo;s
                statuses and fields.
              </p>
            ) : null}

            {reading && reading.statuses.length > 0 ? (
              <div className="space-y-1">
                <p className="font-medium text-foreground">Statuses</p>
                <MapHeader source={sourceName} destination={destName} />
                {reading.statuses.map((row) => (
                  <div key={row.from.id} className="flex items-center gap-2">
                    {/* ⚠️ `basis-0` with a floor, not a bare `flex-1`.
                        The Select beside this one also grew, and its intrinsic
                        width won every time — the source lane collapsed to
                        nothing and the row read "→ Doing", which is a mapping
                        card that cannot say what maps FROM what. Found by
                        looking at it; no test in this tree measures layout. */}
                    <span
                      className="min-w-[6rem] flex-1 basis-0 truncate text-muted-foreground"
                      title={row.from.name}
                    >
                      {row.from.name}
                    </span>
                    <Icon name="ArrowRight" className="h-3 w-3 shrink-0" />
                    {/* ⚠️ `SelectButton`, for the reason the destination
                        picker above carries: `Select` opens the PLATFORM's
                        list. A mapping card that asks the member to agree to
                        a remap should not hand them an operating-system
                        widget to agree with. */}
                    {promote ? (
                      /* The single-task route picks the lane by category
                         (`remap_one_status`) and takes no map, so the promote
                         door SHOWS the landing and offers no override that
                         the request could not carry. */
                      <span
                        className="min-w-0 flex-1 basis-0 truncate text-foreground"
                        title={row.to?.name ?? undefined}
                      >
                        {row.to?.name ?? "the board's first lane"}
                      </span>
                    ) : (
                    <SelectButton
                      label={`Map ${row.from.name} to`}
                      widthClass="min-w-0 flex-1 basis-0"
                      disabled={busy}
                      value={overrides[row.from.id] ?? row.to?.id ?? ""}
                      onChange={(next) =>
                        setOverrides((prev) => ({ ...prev, [row.from.id]: next }))
                      }
                      options={[
                        // A lane the automatic rule chose that is somehow not
                        // in the list still renders, so the row never shows a
                        // blank selection.
                        ...(row.to && !destStatuses.some((s) => s.id === row.to?.id)
                          ? [{ value: row.to.id, label: row.to.name }]
                          : []),
                        ...(!row.to ? [{ value: "", label: "Pick a lane…" }] : []),
                        ...destStatuses.map((status) => ({
                          value: status.id,
                          label: status.name,
                        })),
                      ]}
                    />
                    )}
                  </div>
                ))}
              </div>
            ) : null}

            {/* The FIELD mapping, which the first version only ever showed as
                losses. A member moving work needs to see where a value LANDS
                as much as which ones vanish — owner directive, 2026-09-19. */}
            {fieldRows.length > 0 ? (
              <div className="space-y-1">
                <p className="font-medium text-foreground">Custom fields</p>
                <MapHeader source={sourceName} destination={destName} />
                {fieldRows.map((row) => (
                  <div key={row.from} className="flex items-center gap-2">
                    <span
                      className="min-w-[6rem] flex-1 basis-0 truncate text-muted-foreground"
                      title={row.from}
                    >
                      {row.from}
                    </span>
                    <Icon name="ArrowRight" className="h-3 w-3 shrink-0" />
                    <span
                      className={`min-w-0 flex-1 basis-0 truncate ${
                        row.to ? "text-foreground" : "text-destructive"
                      }`}
                      title={row.to ?? undefined}
                    >
                      {row.to ?? "nothing — dropped"}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}

            {/* Migration 192. On the bulk path there is nowhere to type an
                answer, so the card SAYS which fields block the move rather
                than letting the apply teach it by 422. */}
            {!promote && requiredMissing.length > 0 ? (
              <p className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive">
                {destName || "The destination"} requires{" "}
                {requiredMissing.join(", ")}, which{" "}
                {ids.length === 1 ? "this task does" : "these tasks do"} not
                carry. Fill them in first.
              </p>
            ) : null}

            {/* S6c — the promote door's required fields. One control per
                definition, the same one the task panel draws
                (`FieldControl`). A blank one names itself on the button. */}
            {promote && requiredDefs === null ? (
              <p className="text-muted-foreground">
                Reading what {destName || "the destination"} requires…
              </p>
            ) : null}
            {promote && asked && asked.length > 0 ? (
              <div className="space-y-2">
                <p className="font-medium text-foreground">
                  Required in {destName || "the destination"}
                </p>
                <dl className="space-y-2">
                  {asked.map((def) => (
                    <div key={def.id}>
                      <dt className="text-[11px] text-muted-foreground">
                        {def.name}
                        {def.description ? (
                          <span className="ml-1 opacity-70">— {def.description}</span>
                        ) : null}
                      </dt>
                      <dd className="mt-0.5">
                        <FieldControl
                          def={def}
                          value={draft[def.field_key] ?? toInput(def.field_type, undefined)}
                          disabled={busy}
                          onChange={(next) =>
                            setDraft((current) => ({ ...current, [def.field_key]: next }))
                          }
                        />
                      </dd>
                    </div>
                  ))}
                </dl>
                {blocked ? <p className="text-destructive">{blocked}</p> : null}
              </div>
            ) : null}

            {/* S6c — who owns it on the board. Pre-filled with the current
                owners; the same chips and the same directory picker as the
                task panel. Sent with the move in ONE request, so a promoted
                task is never on a board with nobody behind it. */}
            {promote ? (
              <div className="space-y-1">
                <p className="font-medium text-foreground">Assignees</p>
                <div className="flex flex-wrap items-center gap-1">
                  {assignees.map((who) => {
                    const kind = classify(who);
                    return (
                      <Badge
                        key={who}
                        tone={kind === "unknown" ? "warning" : "neutral"}
                        icon={kind === "agent" ? "Bot" : undefined}
                        title={kind === "unknown" ? "Not an email or agent:<name>" : who}
                      >
                        {who}
                        <button
                          type="button"
                          disabled={busy}
                          aria-label={`Unassign ${who}`}
                          onClick={() =>
                            setAssignees((current) => withoutAssignee(current, who))
                          }
                          className="opacity-70 hover:opacity-100"
                        >
                          <Icon name="X" className="h-3 w-3" />
                        </button>
                      </Badge>
                    );
                  })}
                  {assignees.length === 0 ? (
                    <span className="text-muted-foreground">Nobody yet</span>
                  ) : null}
                </div>
                <AssigneePicker
                  value={assigneeText}
                  onChange={setAssigneeText}
                  onPick={addAssignee}
                  onCommitText={() => {
                    if (assigneeText.trim()) addAssignee(assigneeText);
                  }}
                  commitOnBlur={false}
                  disabled={busy}
                  due={promote.due ?? null}
                  className="mt-0.5"
                />
              </div>
            ) : null}

            {/* ⚠️ Losses, above the button. Never in a toast (D-PM-29). */}
            {drops.length > 0 ? (
              <div className="space-y-1 rounded border border-destructive/40 bg-destructive/10 p-2">
                <p className="font-medium text-destructive">
                  These values have no field in the destination and are dropped
                </p>
                <ul className="space-y-0.5 text-destructive">
                  {drops.map(([fieldKey, rows]) => (
                    <li key={fieldKey}>
                      <span className="font-medium">{fieldKey}</span> — on{" "}
                      {rows.length === 1 ? "1 task" : `${rows.length} tasks`}
                    </li>
                  ))}
                </ul>
                <p className="text-muted-foreground">
                  The old value is written to each task&rsquo;s timeline, so it
                  stays readable in history.
                </p>
              </div>
            ) : null}

            {lostTypes.length > 0 ? (
              <p className="text-muted-foreground">
                The destination has no{" "}
                {lostTypes.map((row) => row.from.name).join(", ")} type, so the
                task type is cleared.
              </p>
            ) : null}

            {unregistered.length > 0 ? (
              <p className="text-muted-foreground">
                {unregistered.join(", ")}{" "}
                {unregistered.length === 1 ? "is not a tag" : "are not tags"} in
                the destination. The names are kept; they simply have no colour
                there until somebody registers them.
              </p>
            ) : null}
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
          disabled={!plan || planning || asked === null || missing.length > 0}
          title={blocked || undefined}
          variant={drops.length > 0 ? "destructive" : "primary"}
          onClick={() =>
            plan &&
            onConfirm(
              plan.destination_project_id,
              overrides,
              drops.length > 0 ? drops.map(([key]) => key) : null,
              promote
                ? {
                    fields: asked ?? [],
                    draft,
                    assignees,
                    initialAssignees: promote.initialAssignees,
                  }
                : undefined
            )
          }
        >
          {drops.length > 0 ? "Move and drop" : "Move"}
        </Button>
      </div>
    </Modal>
  );
}

export default MoveTasksDialog;
