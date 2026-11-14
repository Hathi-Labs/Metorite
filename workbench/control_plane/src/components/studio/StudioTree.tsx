"use client";

/**
 * The Tree block (`ai_studio.md` §14 D18, slice D18-2b).
 *
 * The records of an object with a hierarchy rule, as an expandable tree. One read of the
 * `tree` door op gives up to 2,000 nodes, and the first level is open. The pure model is
 * `lib/studio/treeModel.ts`, and the move is the store of `lib/studio/treeMove.ts`.
 *
 * A drag of a row onto another row moves the node under it. A drag onto "Top level" moves
 * it to the top. A refusal puts the node back and shows one plain sentence in an alert line.
 * The "Move under" control of each row does the same for the keyboard and the phone: it opens a
 * list of the parents that the node may take, and says why for each one that it may not.
 * A viewer gets no drag and no move control.
 *
 * Only the rows that the open nodes show are drawn. A collapsed node draws none of its children.
 */
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import RecordsStatus from "@/components/studio/RecordsStatus";
import Button from "@/components/ui/Button";
import SelectButton, { type SelectOption } from "@/components/ui/SelectButton";
import { displayValue } from "@/lib/studio/blocks";
import { rowsMeta, sourceKey, type Row, type StudioDataDoor } from "@/lib/studio/dataDoor";
import { TREE_PARENT } from "@/lib/studio/realDoor";
import {
  buildTree, canMove, initialExpanded, MAX_TREE_NODES, moveRefusal, treeRows, type TreeNodeIn,
} from "@/lib/studio/treeModel";
import {
  createTreeMover, placedNodes, TOP_OVER, type TreeMover, type TreeMoveState,
} from "@/lib/studio/treeMove";
import type { ViewTree } from "@/lib/studio/viewBlocks";
import { useCachedResource } from "@/lib/useCachedResource";

/** The line under the tree when the read stopped at its cap. */
export const TREE_TRUNCATED_LINE =
  `Showing the first ${MAX_TREE_NODES.toLocaleString("en-US")} records. Narrow the tree to see the rest.`;
export const TREE_EMPTY_LINE = "No records yet.";

const NO_UNSUBSCRIBE = () => {};
const IDLE: TreeMoveState = { placed: {}, pending: new Set<string>(), alert: null, live: "", dragId: null, over: null };
const ROW_STEP_REM = 1.25;

export interface StudioTreeViewProps {
  model: ViewTree;
  rows: Row[];
  door: StudioDataDoor | null;
  /** For a test: a store to draw instead of the one the view makes. */
  mover?: TreeMover | null;
}

/** The tree drawn from rows that are read. `StudioTree` below reads them. */
export function StudioTreeView({ model, rows, door, mover: given }: StudioTreeViewProps) {
  const { labelField, metaFields } = model;
  const truncated = rowsMeta(rows)?.truncated === true;
  const nodes = useMemo(
    () => buildTree(rows, TREE_PARENT, (r) => (labelField ? String(r[labelField] ?? "") : "")),
    [rows, labelField],
  );
  const own = useMemo(
    () => (given === undefined && door && model.canMove && model.update ? createTreeMover(door, model.update, model.field) : null),
    [given, door, model.canMove, model.update, model.field],
  );
  const mover = given === undefined ? own : given;
  const subscribe = useCallback((fn: () => void) => (mover ? mover.subscribe(fn) : NO_UNSUBSCRIBE), [mover]);
  const st = useSyncExternalStore(subscribe, () => mover?.getState() ?? IDLE, () => mover?.getState() ?? IDLE);
  useEffect(() => mover?.settle(), [mover, nodes]);

  const shown = useMemo(() => placedNodes(nodes, st.placed), [nodes, st.placed]);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => initialExpanded(nodes, 1));
  const [moving, setMoving] = useState<string | null>(null);
  const list = useMemo(() => treeRows(shown, expanded), [shown, expanded]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n] as const)), [nodes]);
  const labelFor = useCallback((id: string) => byId.get(id)?.label ?? id, [byId]);
  const movable = mover !== null;

  const toggle = (id: string) => {
    const next = new Set(expanded);
    if (!next.delete(id)) next.add(id);
    setExpanded(next);
  };
  const send = (id: string, parent: string | null) => {
    if (!mover) return;
    // The new parent opens, so the member sees where the node went.
    if (parent !== null && !expanded.has(parent)) setExpanded(new Set(expanded).add(parent));
    void mover.move(shown, id, parent, labelFor);
  };

  if (nodes.length === 0) return <EmptyState icon="ListTree" message={TREE_EMPTY_LINE} />;

  return (
    <div className="space-y-2">
      {st.alert ? (
        <p role="alert" className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">{st.alert}</p>
      ) : null}
      <p role="status" className="text-xs text-muted-foreground empty:hidden">{st.live}</p>
      {movable ? (
        <div
          onDragOver={(e) => { if (mover?.dragOver(TOP_OVER)) e.preventDefault(); }}
          onDragLeave={(e) => {
            const to = e.relatedTarget as Node | null;
            mover?.dragLeave(!!to && !e.currentTarget.contains(to));
          }}
          onDrop={(e) => {
            e.preventDefault();
            const id = mover?.drop() ?? e.dataTransfer?.getData("text/plain") ?? null;
            if (id) send(id, null);
          }}
          className={`rounded-md border border-dashed border-border px-2 py-1 text-xs text-muted-foreground ${st.over === TOP_OVER ? "ring-2 ring-primary/40" : ""}`}
        >
          Top level
        </div>
      ) : null}
      <ul role="tree" aria-label="Tree" className="space-y-0.5">
        {list.map((row) => {
          const node = byId.get(row.id);
          const pending = st.pending.has(row.id);
          const drop = st.dragId !== null && st.over === row.id;
          return (
            <li
              key={row.id}
              role="treeitem"
              aria-level={row.depth + 1}
              aria-expanded={row.hasChildren ? row.expanded : undefined}
              aria-selected={false}
              aria-busy={pending || undefined}
              data-node-id={row.id}
              draggable={movable && !pending}
              onDragStart={(e) => {
                if (!mover || !mover.dragStart(row.id)) { e.preventDefault(); return; }
                e.dataTransfer.setData("text/plain", row.id);
                e.dataTransfer.effectAllowed = "move";
              }}
              onDragEnd={() => mover?.dragEnd()}
              onDragOver={movable ? (e) => { if (mover?.dragOver(row.id)) e.preventDefault(); } : undefined}
              onDragLeave={movable ? (e) => {
                const to = e.relatedTarget as Node | null;
                mover?.dragLeave(!!to && !e.currentTarget.contains(to));
              } : undefined}
              onDrop={movable ? (e) => {
                e.preventDefault();
                const id = mover?.drop() ?? e.dataTransfer?.getData("text/plain") ?? null;
                if (id) send(id, row.id);
              } : undefined}
              className={`list-none rounded-md ${drop ? "ring-2 ring-primary/40" : ""} ${pending ? "opacity-60" : ""}`}
            >
              <div className="flex items-center gap-1 py-0.5" style={{ paddingLeft: `${row.depth * ROW_STEP_REM}rem` }}>
                {row.hasChildren ? (
                  <Button
                    variant="ghost" size="icon-xs" icon={row.expanded ? "ChevronDown" : "ChevronRight"}
                    aria-label={`${row.expanded ? "Collapse" : "Expand"} ${row.label}`}
                    onClick={() => toggle(row.id)}
                  />
                ) : <span aria-hidden className="inline-block w-6 shrink-0" />}
                {movable && !pending ? (
                  <Icon name="GripVertical" size={14} aria-hidden className="h-3.5 w-3.5 shrink-0 cursor-grab text-muted-foreground" />
                ) : null}
                <span className="min-w-0 truncate text-sm text-foreground">{row.label}</span>
                {metaFields.map((f) => {
                  const text = displayValue(f, node?.data?.[f.key]);
                  return text === "" ? null : (
                    <span key={f.key} title={f.label ?? f.key} className="min-w-0 truncate text-xs text-muted-foreground">{text}</span>
                  );
                })}
                {movable && !pending ? (
                  <span className="ml-auto shrink-0">
                    {moving === row.id ? (
                      <SelectButton
                        label={`Move ${row.label} under`} prompt="Move to…" value="" autoOpen filterAbove={8}
                        widthClass="w-44" align="end"
                        options={parentOptions(shown, row.id)}
                        onChange={(v) => send(row.id, v === TOP_OVER ? null : v)}
                        onClose={() => setMoving(null)}
                      />
                    ) : (
                      <Button
                        variant="ghost" size="icon-xs" icon="CornerDownRight" aria-label={`Move ${row.label} under`}
                        title="Move to…"
                        onClick={() => setMoving(row.id)}
                      />
                    )}
                  </span>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      {truncated ? <p role="status" className="text-xs text-muted-foreground">{TREE_TRUNCATED_LINE}</p> : null}
    </div>
  );
}

/**
 * The parents that node `id` may take: "Top level", then every node in tree order. A node that the
 * move would break stays in the list, disabled, and the hint says why (the model's sentence).
 */
export function parentOptions(nodes: ReadonlyArray<TreeNodeIn>, id: string): SelectOption[] {
  const all = treeRows(nodes, new Set(nodes.map((n) => n.id)));
  const out: SelectOption[] = [];
  const top = canMove(nodes, id, null);
  out.push({ value: TOP_OVER, label: "Top level", ...(top.ok ? {} : { disabled: true, hint: moveRefusal(top.reason) }) });
  for (const r of all) {
    const check = canMove(nodes, id, r.id);
    out.push({
      value: r.id, label: r.label, depth: r.depth,
      ...(check.ok ? {} : { disabled: true, hint: moveRefusal(check.reason) }),
    });
  }
  return out;
}

/** The Tree block of a page: it reads the tree through the door, and draws its states. */
export default function StudioTree({ model, door }: { model: ViewTree; door: StudioDataDoor }) {
  const rows = useCachedResource<Row[]>(sourceKey(door, model.source), () => door.read(model.source));
  return (
    <div>
      <h2 className="mb-3 text-sm font-semibold text-foreground">{model.title}</h2>
      <RecordsStatus loading={rows.data === undefined && !rows.error} error={rows.error} onRetry={rows.refresh} />
      {rows.data !== undefined ? <StudioTreeView model={model} rows={rows.data} door={door} /> : null}
    </div>
  );
}
