"use client";

/**
 * The promote answers — one component, two doors (my_tasks_cutover.md §5 S6g).
 *
 * A personal task moves onto a company board from two places in My Tasks: the
 * Move dialog (`MoveTasksDialog` in its promote mode) and Clarify's Where step.
 * Until S6g the two asked different questions. Clarify never asked for the
 * destination's required fields, and the move was refused after the card had
 * already moved the row. This is the one set of questions both doors draw:
 *
 * 1. what the move maps (statuses and fields, from the server's preview);
 * 2. the destination's REQUIRED fields the task does not carry (migration 192);
 * 3. who owns it on the board (the Move dialog shows it; Clarify's own Owner
 *    step answers it there, so Clarify hides this one);
 * 4. what the move loses, above the button (D-PM-29).
 *
 * The answers go up through `onChange`, and the host turns them into ONE
 * request with `promotePlan` (`app/tasks/lib/promote.ts`).
 *
 * The preview loader and the mapping rows live here too, and the bulk Move
 * dialog reads them from here, so there is one drawing of a move and not two.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import SelectButton from "@/components/ui/SelectButton";

import { type MovePlan, projectsApi } from "../lib/api";
import { withAssignee, withoutAssignee } from "../lib/assignees";
import {
  type FieldDef,
  missingSentence,
  requiredBlanks,
  toInput,
  toWire,
} from "../lib/customFields";
import { destinations, nameOf } from "../lib/destinations";
import { askedFields, readPlan } from "../lib/movePlan";
import type { ProjectNode } from "../lib/tree";
import { AssigneeChips } from "./AssigneeChips";
import { AssigneePicker } from "./AssigneePicker";
import { FieldControl } from "./CustomFieldValues";

/**
 * The line both promote doors show before the move. `app/tasks/lib/promote.ts`
 * re-exports it, and `captureTo.test.ts` holds the two doors to it.
 */
export const PROMOTE_HINT =
  "It moves onto the board. It stays in your lists while you are an assignee.";

/**
 * What the promote door answers on top of the destination. Both travel to the
 * host for the ONE request, so a promoted task is never on a board owned by
 * nobody.
 */
export interface PromoteAnswers {
  /** The destination's required definitions the preview named as unanswered. */
  fields: FieldDef[];
  /** Control values keyed by `field_key`, as the inputs hold them. */
  draft: Record<string, unknown>;
  assignees: string[];
  initialAssignees: string[];
}

/** What `PromoteFields` reports to its host on every change. */
export interface PromoteFieldsState {
  /** The preview loaded and no required field is blank. */
  ready: boolean;
  /** The destination the SERVER resolved, once the preview answered. */
  destinationProjectId: string | null;
  answers: PromoteAnswers;
  /** Field keys the move drops, or null when it drops nothing. */
  drops: string[] | null;
  /** "Fill in PO first." or null. */
  blocked: string | null;
}

/** Control values → wire values, for the blank check the server will make. */
export function wireOf(
  defs: readonly FieldDef[],
  draft: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const def of defs) out[def.field_key] = toWire(def.field_type, draft[def.field_key]);
  return out;
}

/**
 * The server's preview of one move, and the required definitions it names.
 *
 * `askRequired` is the promote door's flag. Only it has a place to type an
 * answer, so only it reads the destination's definitions.
 */
export function useMovePreview(
  taskIds: readonly string[],
  destinationId: string | null,
  askRequired: boolean,
) {
  const [plan, setPlan] = useState<MovePlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  // null while the destination's definitions are on their way, so the button
  // cannot offer a move the server would refuse for a field it has not been
  // asked about yet.
  const [requiredDefs, setRequiredDefs] = useState<FieldDef[] | null>([]);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const key = taskIds.join(",");
  // A request token. Two destinations picked quickly are two previews in
  // flight, and the slow one used to land last and overwrite the fast one.
  const loadSeq = useRef(0);

  const load = useCallback(
    async (dest: string) => {
      const seq = ++loadSeq.current;
      const stale = () => seq !== loadSeq.current;
      setPlanning(true);
      setPlanError(null);
      // Cleared, not kept: the previous destination's mapping under a new
      // destination's name is the frame somebody agrees to by mistake.
      setPlan(null);
      setRequiredDefs([]);
      setDraft({});
      try {
        const next = await projectsApi.previewMove({
          task_ids: key.split(","),
          destination_project_id: dest,
        });
        if (stale()) return;
        setPlan(next);
        if (askRequired && (next.required_missing ?? []).length > 0) {
          setRequiredDefs(null);
          const { rows } = await projectsApi.fields(next.destination_project_id);
          if (stale()) return;
          const wanted = new Set(next.required_missing);
          const defs = (rows as FieldDef[]).filter(
            (def) => def.required && (wanted.has(def.name) || wanted.has(def.field_key)),
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
    [key, askRequired],
  );

  useEffect(() => {
    // The pattern the Move dialog has always used (a load that sets state).
    if (destinationId && key) void load(destinationId);
  }, [destinationId, key, load]);

  // With no destination the hook answers "nothing yet", whatever an older
  // destination left in state. A host that clears its pick sees no mapping.
  if (!destinationId) {
    return { plan: null, planning: false, planError: null, requiredDefs: [], draft: {}, setDraft };
  }
  return { plan, planning, planError, requiredDefs, draft, setDraft };
}

/**
 * The column header both mapping tables carry.
 *
 * ⚠️ Owner directive, 2026-09-19: name the two ends explicitly. An arrow alone
 * leaves the reader to infer which side is which, on the one screen where the
 * wrong direction rewrites every task in the selection.
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

type Reading = NonNullable<ReturnType<typeof readPlan>>;

/**
 * What a move maps: the clean line, the status rows and the field rows.
 *
 * `overrides` is the bulk path's. The single-task route picks the lane itself
 * (`remap_one_status`), so the promote door SHOWS the landing and offers no
 * override that its request could not carry.
 */
export function MoveMapping({
  reading,
  sourceName,
  destName,
  busy,
  overrides,
  onOverride,
}: {
  reading: Reading;
  sourceName: string;
  destName: string;
  busy?: boolean;
  overrides?: Record<string, string>;
  onOverride?: (fromId: string, toId: string) => void;
}) {
  const destStatuses = reading.destStatuses;
  return (
    <>
      {reading.clean ? (
        <p className="text-foreground">
          Nothing is remapped — the destination shares this task&rsquo;s statuses and
          fields.
        </p>
      ) : null}

      {reading.statuses.length > 0 ? (
        <div className="space-y-1">
          <p className="font-medium text-foreground">Statuses</p>
          <MapHeader source={sourceName} destination={destName} />
          {reading.statuses.map((row) => (
            <div key={row.from.id} className="flex items-center gap-2">
              {/* ⚠️ `basis-0` with a floor, not a bare `flex-1`: the Select
                  beside it grew, and the source lane collapsed to nothing. */}
              <span
                className="min-w-[6rem] flex-1 basis-0 truncate text-muted-foreground"
                title={row.from.name}
              >
                {row.from.name}
              </span>
              <Icon name="ArrowRight" className="h-3 w-3 shrink-0" />
              {!onOverride ? (
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
                  value={overrides?.[row.from.id] ?? row.to?.id ?? ""}
                  onChange={(next) => onOverride(row.from.id, next)}
                  options={[
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

      {/* Where a value LANDS, as much as which ones vanish (owner, 2026-09-19). */}
      {reading.fieldRows.length > 0 ? (
        <div className="space-y-1">
          <p className="font-medium text-foreground">Custom fields</p>
          <MapHeader source={sourceName} destination={destName} />
          {reading.fieldRows.map((row) => (
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
    </>
  );
}

/** What a move LOSES. Above the button, never in a toast (D-PM-29). */
export function MoveLosses({ reading }: { reading: Reading }) {
  const { drops, lostTypes, unregistered } = reading;
  return (
    <>
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
            The old value is written to each task&rsquo;s timeline, so it stays readable
            in history.
          </p>
        </div>
      ) : null}

      {lostTypes.length > 0 ? (
        <p className="text-muted-foreground">
          The destination has no {lostTypes.map((row) => row.from.name).join(", ")} type,
          so the task type is cleared.
        </p>
      ) : null}

      {unregistered.length > 0 ? (
        <p className="text-muted-foreground">
          {unregistered.join(", ")}{" "}
          {unregistered.length === 1 ? "is not a tag" : "are not tags"} in the
          destination. The names are kept; they simply have no colour there until
          somebody registers them.
        </p>
      ) : null}
    </>
  );
}

/**
 * The promote door's questions, for one task and one destination.
 *
 * Mounted with a destination. With none it draws nothing and reports
 * `ready: false`.
 */
export function PromoteFields({
  taskIds,
  destinationId,
  roots,
  initialAssignees,
  due,
  refusedFields,
  busy,
  showAssignees = true,
  onChange,
}: {
  taskIds: readonly string[];
  destinationId: string | null;
  /** The tree the destination came from, to name both ends of the mapping. */
  roots: readonly ProjectNode[];
  initialAssignees: string[];
  due?: string | null;
  /** The definitions a 422 named, drawn as inputs on the next attempt. */
  refusedFields?: FieldDef[];
  busy?: boolean;
  /** False in Clarify, whose Owner step already says who owns the task. */
  showAssignees?: boolean;
  onChange: (state: PromoteFieldsState) => void;
}) {
  const { plan, planning, planError, requiredDefs, draft, setDraft } = useMovePreview(
    taskIds,
    destinationId,
    true,
  );
  const [assignees, setAssignees] = useState<string[]>(() => initialAssignees);
  const [assigneeText, setAssigneeText] = useState("");

  const asked = askedFields(requiredDefs, refusedFields);
  const missing = asked ? requiredBlanks(asked, wireOf(asked, draft)).map((d) => d.name) : [];
  const blocked = missingSentence(missing);
  const reading = readPlan(plan);
  const drops = reading?.drops ?? [];
  const rows = destinations(roots);
  const sourceName = nameOf(rows, plan?.source_project_id ?? null);
  const destName = nameOf(rows, destinationId) || nameOf(rows, plan?.destination_project_id ?? null);
  const ready = !!plan && !planning && asked !== null && missing.length === 0;

  // Reported on every change of what the host needs. `onChange` is the host's
  // state setter (stable), and the report is a fresh object, so the effect
  // keys on the parts.
  const dropKeys = drops.map(([k]) => k).join(",");
  useEffect(() => {
    onChange({
      ready,
      destinationProjectId: plan?.destination_project_id ?? null,
      answers: { fields: asked ?? [], draft, assignees, initialAssignees },
      drops: dropKeys ? dropKeys.split(",") : null,
      blocked: blocked || null,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onChange, ready, plan, draft, assignees, dropKeys, blocked, requiredDefs, refusedFields]);

  if (!destinationId) return null;

  const addAssignee = (raw: string) => {
    setAssignees((current) => withAssignee(current, raw));
    setAssigneeText("");
  };

  return (
    <div className="space-y-3 text-xs" data-promote-fields>
      {planning ? (
        <p className="text-muted-foreground">Working out what would change…</p>
      ) : planError ? (
        <p className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive">
          {planError}
        </p>
      ) : reading ? (
        <>
          <MoveMapping reading={reading} sourceName={sourceName} destName={destName} busy={busy} />

          {requiredDefs === null ? (
            <p className="text-muted-foreground">
              Reading what {destName || "the destination"} requires…
            </p>
          ) : null}
          {asked && asked.length > 0 ? (
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

          {showAssignees ? (
            <div className="space-y-1">
              <p className="font-medium text-foreground">Assignees</p>
              <AssigneeChips
                assignees={assignees}
                disabled={busy}
                emptyClass="text-muted-foreground"
                onRemove={(who) => setAssignees((current) => withoutAssignee(current, who))}
              />
              <AssigneePicker
                value={assigneeText}
                onChange={setAssigneeText}
                onPick={addAssignee}
                onCommitText={() => {
                  if (assigneeText.trim()) addAssignee(assigneeText);
                }}
                commitOnBlur={false}
                disabled={busy}
                due={due ?? null}
                className="mt-0.5"
              />
            </div>
          ) : null}

          <MoveLosses reading={reading} />
        </>
      ) : null}
    </div>
  );
}
