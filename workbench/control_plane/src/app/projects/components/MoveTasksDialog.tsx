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

import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { Select } from "@/components/ui/Input";

import {
  type MovePlan,
  type ProjectRow,
  type StatusRow,
  projectsApi,
} from "../lib/api";
import { LEVEL_ICONS, type ProjectNode, nodeKind, nodeLevel } from "../lib/tree";

interface Props {
  /** The tasks to move. Empty or null closes the card. */
  taskIds: readonly string[] | null;
  /** Every node the member can see, for the destination picker. */
  roots: readonly ProjectNode[];
  /** The destination's lanes, once one is picked — for the override dropdowns. */
  statusesFor?: (projectId: string) => readonly StatusRow[] | undefined;
  onClose: () => void;
  /** Confirmed. The page owns the call, the toast and the refetch. */
  onConfirm: (
    destinationId: string,
    statusMap: Record<string, string>,
    acceptDrops: boolean
  ) => void;
  busy?: boolean;
  /** The server's refusal, rendered IN the card — see H-120's sibling note. */
  error?: string | null;
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
  statusesFor,
  onClose,
  onConfirm,
  busy,
  error,
}: Props) {
  const [destination, setDestination] = useState<string | null>(null);
  const [plan, setPlan] = useState<MovePlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});

  const ids = taskIds ?? [];
  const key = ids.join(",");

  const load = useCallback(
    async (destinationId: string) => {
      setPlanning(true);
      setPlanError(null);
      // Cleared, not kept: the previous destination's mapping under a new
      // destination's name is the frame somebody agrees to by mistake.
      setPlan(null);
      setOverrides({});
      try {
        setPlan(
          await projectsApi.previewMove({
            task_ids: [...ids],
            destination_project_id: destinationId,
          })
        );
      } catch (err) {
        setPlanError(String((err as Error).message));
      } finally {
        setPlanning(false);
      }
    },
    [key] // eslint-disable-line react-hooks/exhaustive-deps
  );

  useEffect(() => {
    if (destination) void load(destination);
  }, [destination, load]);

  if (!taskIds || ids.length === 0) return null;

  const destRows = destinations(roots);
  const drops = Object.entries(plan?.drops ?? {});
  const lostTypes = (plan?.types ?? []).filter((row) => !row.to);
  const unregistered = plan?.tags.unregistered ?? [];
  const destStatuses = destination ? (statusesFor?.(destination) ?? []) : [];
  /**
   * Every source field and where it lands, mapped ones first.
   *
   * An orphan is listed with an explicit "dropped" rather than omitted: a
   * table that silently skips the fields it cannot carry is the table that
   * made this feature necessary.
   */
  const fieldRows: { from: string; to: string | null }[] = [
    ...Object.entries(plan?.field_map ?? {}).map(([from, to]) => ({ from, to })),
    ...(plan?.orphan_fields ?? []).map((field) => ({
      from: field.name || field.field_key,
      to: null,
    })),
  ];
  const sourceName = nameOf(destRows, plan?.source_project_id ?? null);
  // ⚠️ From the PICKED node first, not the plan's id. The member chose it from
  // this very list, so the name is always resolvable — whereas the plan's id
  // may name a node the loaded tree does not contain, and the header would
  // then degrade to a bare "To", which is the exact ambiguity the explicit
  // headers exist to remove (owner directive, 2026-09-19).
  const destName =
    nameOf(destRows, destination) ||
    nameOf(destRows, plan?.destination_project_id ?? null);
  const clean =
    plan !== null &&
    !plan.crosses_status_set &&
    !plan.crosses_root &&
    drops.length === 0;

  return (
    <Modal
      open
      onClose={onClose}
      title={ids.length === 1 ? "Move task" : `Move ${ids.length} tasks`}
      icon="FolderInput"
      size="md"
      description="Statuses and custom fields are mapped into the destination's own vocabulary."
    >
      <div className="space-y-3 p-3 text-xs">
        <label className="block space-y-1">
          <span className="text-muted-foreground">Move to</span>
          <Select
            inputSize="sm"
            value={destination ?? ""}
            disabled={busy}
            onChange={(e) => setDestination(e.target.value || null)}
          >
            <option value="">Pick a project…</option>
            {destRows.map(({ node, depth, legal }) => (
              <option key={node.id} value={node.id} disabled={!legal}>
                {" ".repeat(depth * 2)}
                {node.name}
                {legal ? "" : " — a folder holds projects, not tasks"}
              </option>
            ))}
          </Select>
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

            {plan.statuses.length > 0 ? (
              <div className="space-y-1">
                <p className="font-medium text-foreground">Statuses</p>
                <MapHeader source={sourceName} destination={destName} />
                {plan.statuses.map((row) => (
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
                    <Select
                      inputSize="sm"
                      className="min-w-0 flex-1 basis-0"
                      disabled={busy}
                      value={overrides[row.from.id] ?? row.to?.id ?? ""}
                      onChange={(e) =>
                        setOverrides((prev) => ({
                          ...prev,
                          [row.from.id]: e.target.value,
                        }))
                      }
                    >
                      {destStatuses.length === 0 && row.to ? (
                        <option value={row.to.id}>{row.to.name}</option>
                      ) : null}
                      {destStatuses.map((status) => (
                        <option key={status.id} value={status.id}>
                          {status.name}
                        </option>
                      ))}
                    </Select>
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
             card knows what the move costs is agreeing to nothing. */
          disabled={!plan || planning}
          variant={drops.length > 0 ? "destructive" : "primary"}
          onClick={() =>
            plan &&
            onConfirm(plan.destination_project_id, overrides, drops.length > 0)
          }
        >
          {drops.length > 0 ? "Move and drop" : "Move"}
        </Button>
      </div>
    </Modal>
  );
}

export default MoveTasksDialog;
