"use client";

/**
 * The Board block (`ai_studio.md` §14 D3, slice STU-D3).
 *
 * The records of a source, in one lane for each option of a select field.
 * It reuses the board of the product: `Board`, `BoardColumn`, `StageChip`
 * (through the lane header) and `TaskCardShell`. The drag is native HTML5, as
 * on the Projects, My Tasks and CRM boards. The words of a card come from
 * `displayValue`, and a select or a person draws through `ValueElement`, as
 * the Table and the Detail do.
 *
 * A drop moves the card and runs the update action once (`boardMove.ts`).
 * A refusal puts the card back and shows the message in an alert line. The
 * keyboard moves a card with Space and the arrows, and a "Move to" menu does
 * the same for touch. At 390 px the lanes stack as a list.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useState, useSyncExternalStore } from "react";

import { ValueElement } from "@/components/CardFieldValue";
import { Board } from "@/components/board/Board";
import { BoardColumn } from "@/components/board/BoardColumn";
import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import { TaskCardShell, TaskCardTitle } from "@/components/TaskCardShell";
import Button from "@/components/ui/Button";
import { statusAccent } from "@/lib/statusAccent";
import { displayValue, labelOf } from "@/lib/studio/blocks";
import { buildLanes, keyStep, optionOf, parseBoard, recordsOf, rowId, type BoardConfig, type Lane } from "@/lib/studio/boardModel";
import { createMover, type MoverState } from "@/lib/studio/boardMove";
import { moreLine, type Row, type StudioDataDoor } from "@/lib/studio/dataDoor";
import type { FieldSpec } from "@/lib/studio/fieldCheck";

/** The value of one card field: a chip for a select or a person, else the words. */
function CardValue({ field, value }: { field: FieldSpec; value: unknown }) {
  const text = displayValue(field, value);
  if (text === "") return null;
  const items = Array.isArray(value) ? value : [value];
  const kind = field.type === "select" || field.type === "multiSelect" ? "tag"
    : field.type === "person" ? "person" : null;
  return (
    <span title={labelOf(field)} className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-1 text-xs text-muted-foreground">
      {kind
        ? items.map((v, i) => <ValueElement key={i} item={{ text: String(v), named: true }} kind={kind} />)
        : <span className="min-w-0 break-words">{text}</span>}
    </span>
  );
}

const NO_UNSUBSCRIBE = () => {};

function BoardView({ config, rows: given, door }: { config: BoardConfig; rows: Row[]; door: StudioDataDoor | null }) {
  const { update, group } = config;
  const more = moreLine(given);
  // Records only, once, for every use below.
  const rows = useMemo(() => recordsOf(given) as Row[], [given]);
  const mover = useMemo(
    () => (door && update ? createMover(door, update, group.key) : null),
    [door, update, group.key],
  );
  const idle = useMemo<MoverState>(() => ({ placed: {}, pending: new Set<string>(), alert: null, live: "", dragId: null, over: null, hold: null, focusId: null }), []);
  const subscribe = useCallback((fn: () => void) => (mover ? mover.subscribe(fn) : NO_UNSUBSCRIBE), [mover]);
  const st = useSyncExternalStore(
    subscribe,
    () => mover?.getState() ?? idle,
    () => mover?.getState() ?? idle,
  );
  useEffect(() => mover?.settle(), [mover, rows]);

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const { over, hold, dragId } = st;
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);

  const lanes = buildLanes(config, rows, { placed: st.placed, expanded });
  const laneIds = lanes.map((l) => l.id);
  const laneById = (id: string): Lane | undefined => lanes.find((l) => l.id === id);
  const movable = mover !== null;

  // A moved card mounts anew in its new lane. A keyboard move asks the store for focus, and the card takes it here.
  useLayoutEffect(() => {
    if (st.focusId === null) return;
    const card = document.querySelector<HTMLElement>(`[data-card-id="${CSS.escape(st.focusId)}"] [role=button]`);
    card?.focus();
    mover?.clearFocus();
  }, [st.focusId, mover]);

  const send = (id: string, to: string | undefined, keyboard = false) => {
    const target = to ? laneById(to) : undefined;
    const row = rows.find((r) => rowId(r) === id);
    if (!mover || !target || !row) return;
    void mover.move(id, optionOf(config, row, st.placed), target.value, target.title, { keyboard });
  };

  const onKey = (e: React.KeyboardEvent, id: string, lane: Lane) => {
    if (!movable || st.pending.has(id) || e.target !== e.currentTarget.querySelector("[role=button]")) return;
    const step = keyStep(hold, e.key, { id, lane: lane.id }, laneIds);
    if (!step.handled) return;
    e.preventDefault();
    mover?.setHold(step.hold);
    if (step.drop) {
      send(step.drop.id, step.drop.to, true);
    } else if (step.cancelled) mover?.say("Move cancelled");
    else if (step.hold) {
      mover?.say(`${step.hold.over === step.hold.from ? "Picked up. " : ""}Over ${laneById(step.hold.over)?.title ?? ""}`);
    }
  };

  const menuRow = menu ? rows.find((r) => rowId(r) === menu.id) : undefined;
  const menuItems: CtxItem[] = menu && menuRow
    ? [
      { kind: "label", label: "Move to" },
      ...lanes
        .filter((l) => l.value !== optionOf(config, menuRow, st.placed))
        .map((l): CtxItem => ({ kind: "item", label: l.title, onSelect: () => send(menu.id, l.id) })),
    ]
    : [];

  return (
    <div className="space-y-2">
      {st.alert ? (
        <p role="alert" className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">{st.alert}</p>
      ) : null}
      {more ? <p role="status" className="text-xs text-muted-foreground">{`${more} The lane counts cover these records only.`}</p> : null}
      <p className="sr-only" aria-live="polite">{st.live}</p>
      <Board
        aria-label="Board"
        onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) mover?.blur(); }}
        className="max-sm:flex-col max-sm:[&>*]:w-full">
        {lanes.map((lane, i) => (
          <BoardColumn
            key={lane.id}
            as="ul"
            header={{
              accent: statusAccent({ name: lane.value ?? undefined, index: lane.value === null ? 0 : i, total: lanes.length }),
              title: lane.title,
              count: lane.total,
            }}
            dropTarget={(dragId !== null && over === lane.id) || hold?.over === lane.id}
            empty={lane.total === 0 ? "No cards" : undefined}
            footer={lane.hidden > 0 ? (
              <Button variant="text" size="sm" onClick={() => setExpanded(new Set(expanded).add(lane.id))}>
                {`Show more (${lane.hidden})`}
              </Button>
            ) : undefined}
            onDragOver={movable ? (e) => {
              if (mover?.dragOver(lane.id)) e.preventDefault();
            } : undefined}
            onDragLeave={movable ? (e) => {
              // A leave with no related target (the pointer left the window) keeps the ring until the drag ends.
              const to = e.relatedTarget as Node | null;
              mover?.dragLeave(!!to && !e.currentTarget.contains(to));
            } : undefined}
            onDrop={movable ? (e) => {
              e.preventDefault();
              const id = mover?.drop() ?? e.dataTransfer?.getData("text/plain") ?? null;
              if (id) send(id, lane.id);
            } : undefined}
          >
            {lane.rows.map((row, at) => {
              const id = rowId(row);
              const pending = id !== null && st.pending.has(id);
              const held = id !== null && hold?.id === id;
              const title = config.titleField ? displayValue(config.titleField, row[config.titleField.key]) : "";
              const label = `${title || "Untitled"}, in ${lane.title}`;
              return (
                <li
                  key={id ?? `row-${at}`}
                  data-card-id={id ?? undefined}
                  aria-busy={pending || undefined}
                  onKeyDown={id !== null ? (e) => onKey(e, id, lane) : undefined}
                  className="list-none"
                >
                  <TaskCardShell
                    draggable={movable && id !== null && !pending}
                    selected={held}
                    atCursor={false}
                    completed={pending}
                    ariaLabel={held ? `${label}. Picked up. Use the arrow keys, then Space.` : label}
                    onDragStart={(e) => {
                      if (id === null) return;
                      e.dataTransfer.setData("text/plain", id);
                      e.dataTransfer.effectAllowed = "move";
                      mover?.dragStart(id);
                    }}
                    onDragEnd={() => mover?.dragEnd()}
                  >
                    <div className="flex items-start gap-1">
                      <TaskCardTitle>{title || "Untitled"}</TaskCardTitle>
                      {movable && id !== null && !pending ? (
                        <span className="ml-auto shrink-0">
                          <Button
                            variant="ghost" size="icon-xs" icon="ArrowRightLeft" aria-label={`Move ${title || "card"} to another lane`}
                            onClick={(e) => {
                              e.stopPropagation();
                              const r = e.currentTarget.getBoundingClientRect();
                              setMenu({ id, x: r.left, y: r.bottom + 4 });
                            }}
                          />
                        </span>
                      ) : null}
                    </div>
                    {config.cardFields.length > 0 ? (
                      <div className="flex flex-col gap-1">
                        {config.cardFields.map((f) => <CardValue key={f.key} field={f} value={row[f.key]} />)}
                      </div>
                    ) : null}
                  </TaskCardShell>
                </li>
              );
            })}
          </BoardColumn>
        ))}
      </Board>
      {menu ? <ContextMenu x={menu.x} y={menu.y} items={menuItems} autoFocus onClose={() => setMenu(null)} /> : null}
    </div>
  );
}

/** The error state of a board that cannot draw. */
export function BoardError({ reason }: { reason: string }) {
  return <p role="alert" className="text-xs text-destructive">{reason}</p>;
}

export function StudioBoard({ data, rows, door }: { data: unknown; rows: Row[]; door: StudioDataDoor | null }) {
  const parsed = useMemo(() => parseBoard(data), [data]);
  if (!parsed.ok) return <BoardError reason={parsed.reason} />;
  return <BoardView config={parsed.config} rows={rows} door={door} />;
}

export default StudioBoard;
