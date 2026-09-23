"use client";

import Icon, { themedIcon } from "@/components/Icon";
import { Checkbox } from "@/components/ui/Checkbox";
import { TaskCardShell, TaskCardTitle } from "@/components/TaskCardShell";
import { AvatarStack, TaskMeta } from "@/components/TaskMeta";
import { useState } from "react";
import { GtdItem } from "../lib/types";
import { gtdMetaChips } from "../lib/cardMeta";
import { useTaskStore } from "../lib/taskStore";
import { useCardActions } from "../lib/useCardActions";
import { contextAccent } from "../lib/contextColors";
import { SourceBadge } from "./SourceBadge";
import { PriorityBadge, SuggestionBadge } from "./PriorityControls";
import { StatusPill } from "./StatusPill";
import { ContextMenu, type CtxItem } from "./ContextMenu";
import { ProjectLabel } from "./ProjectLabel";
import { PromoteDialog } from "./PromoteDialog";

// Due/overdue read the REAL clock (isOverdue and relativeTime both default
// nowMs to Date.now()). A frozen `MOCK_NOW` demo constant used to be passed
// here, so every card's due label drifted further from the truth each day.

const ENERGY_DOT: Record<string, string> = {
  low: "bg-success",
  medium: "bg-warning",
  high: "bg-destructive",
};

// A rich, PM-tool-style task card (Jira/Linear-shaped) used by both the board
// columns and the list view. Clicking opens the full-page focus modal. On the
// board it's draggable (native HTML5 DnD wired by the parent column).
export function TaskCard({
  item,
  variant = "board",
  draggable = false,
  showPriority = false,
  showStage = true,
  selected = false,
  atCursor = false,
  innerRef,
  onToggleSelected,
  onDragStart,
  onDragEnd,
}: {
  item: GtdItem;
  /** "board" = full card (default); "row" = denser one-line-ish list row. */
  variant?: "board" | "row";
  draggable?: boolean;
  /** Show the matrix-cell badge (the Priority view / ranked list). Off
   *  elsewhere so a card carries at most the one relevant priority signal. */
  showPriority?: boolean;
  /** Show the status/stage pill on the card. On surfaces that ALREADY convey
   *  the stage structurally — the Kanban columns, the status-grouped list
   *  headers — it's redundant, so those pass `false`. Everywhere the stage
   *  isn't otherwise visible (Engage, Priority, a lens-grouped list, the flat
   *  lists) it stays on so the status lives on the card itself. */
  showStage?: boolean;
  selected?: boolean;
  /** The keyboard cursor stands on this card (`@/lib/cursor`). Handed to the
   *  shell, which owns the ring — the board must not draw its own (S1). */
  atCursor?: boolean;
  /** Registers the card's own element with the landing flash (`useFlash`), the
   *  same element /projects attaches: the thing that flashes is the card. */
  innerRef?: (element: HTMLElement | null) => void;
  /** Toggles this card's membership of the bulk selection; `shift` extends the
   *  range from the anchor (`@/lib/selection`). Its presence is what puts the
   *  checkbox on the card — there is no selection *mode* on the card any more
   *  (S1, /projects' pattern): the checkbox is a second target beside the card,
   *  so a click on the card always means "open" and never "select". */
  onToggleSelected?: (shift: boolean) => void;
  onDragStart?: (e: React.DragEvent) => void;
  onDragEnd?: (e: React.DragEvent) => void;
}) {
  const openFocus = useTaskStore((s) => s.openFocus);
  const projects = useTaskStore((s) => s.projects);
  const urgentWindowHours = useTaskStore((s) => s.settings.urgentWindowHours);
  const project = item.projectId
    ? projects.find((p) => p.id === item.projectId)
    : undefined;
  // The owner set, as the shared `AvatarStack` wants it — plain identifiers.
  // `assignees` is the full list and `assignee` its first entry, but only the
  // singular one is filled for a local task, so fall back rather than draw a
  // named owner with no avatar.
  const assignees = item.assignees?.length
    ? item.assignees
    : item.assignee
      ? [item.assignee]
      : [];
  // Owners beyond the primary (shown as a "+N" on the avatar).
  const extraAssignees = Math.max(0, assignees.length - 1);
  const assigneeNames = assignees.map((p) => p.name);

  // Shared actions (schedule / stage / done / eliminate) power both the inline
  // controls and the right-click menu, so they never drift.
  const actions = useCardActions(item);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  // S6c — the promote door. Local to the card: the dialog reads the tree on
  // open and nothing else needs to know it is up.
  const [promoting, setPromoting] = useState(false);
  const menuItems: CtxItem[] = [
    {
      kind: "item",
      label: "Schedule on calendar",
      icon: themedIcon("CalendarClock"),
      onSelect: actions.schedule,
    },
    ...(actions.canPromote
      ? [
          {
            kind: "item" as const,
            label: "Move to project…",
            icon: themedIcon("FolderInput"),
            onSelect: () => setPromoting(true),
          },
        ]
      : []),
    { kind: "sep" },
    { kind: "label", label: "Change status" },
    ...actions.categories.map(
      (c): CtxItem => ({
        kind: "item",
        label: actions.categoryLabel(c),
        checked: c === actions.currentCategory,
        onSelect: () => actions.setCategory(c),
      }),
    ),
    { kind: "sep" },
    {
      kind: "item",
      label: actions.isDone ? "Mark as not done" : "Mark as Done",
      icon: themedIcon("Check"),
      onSelect: actions.toggleDone,
    },
    {
      kind: "item",
      label: "Eliminate…",
      icon: themedIcon("Trash2"),
      danger: true,
      onSelect: actions.eliminate,
    },
  ];
  const openMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setMenu({ x: e.clientX, y: e.clientY });
  };
  const contextMenu = (
    <>
      {menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={menuItems}
          onClose={() => setMenu(null)}
        />
      ) : null}
      {promoting ? (
        <PromoteDialog item={item} onClose={() => setPromoting(false)} />
      ) : null}
    </>
  );

  const meta = (
    <>
      {item.context && (
        <span
          className={[
            "inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px]",
            contextAccent(item.context).chip,
          ].join(" ")}
        >
          {item.context}
        </span>
      )}
      {/* Deep-work marker: this task needs an unbroken flow block — the
          planner protects one, and Focus Mode opens on a longer timer. */}
      {item.deepWork && (
        <span
          title="Deep work — needs an unbroken flow state"
          className="inline-flex items-center gap-1 rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary"
        >
          <Icon name="Waves" className="h-3 w-3" />
          Deep
        </span>
      )}
      {item.energy && (
        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
          <span className={`h-1.5 w-1.5 rounded-full ${ENERGY_DOT[item.energy]}`} />
          {item.energy}
        </span>
      )}
      {/* The shared facts — due/overdue, subtasks, attachments, estimate — in
          the shared chip vocabulary (WS-27s): `gtdMetaChips` adapts GtdItem to
          `taskMeta`'s descriptors and the one `TaskMeta` renderer draws them,
          so a task reads identically here and on /projects. The GTD-only
          badges around it (context, deep, energy, source, priority) stay —
          one grammar for the shared facts, not an erased identity. */}
      <TaskMeta chips={gtdMetaChips(item)} className="min-w-0 max-w-full" />
      {item.origin?.kind === "email" && (
        <span
          className="inline-flex items-center gap-1 text-[10px] text-muted-foreground"
          title={`From email — ${item.origin.fromName || item.origin.fromEmail || ""}`}
        >
          <Icon name="Mail" className="h-3 w-3" />
        </span>
      )}
      {/* Priority signal — LIST ROW only. The matrix-cell PILL (what it IS)
          and the action NUDGE (what to DO about it) ride in the wrapping meta
          here; the BOARD card pins the same pair to its top corners instead
          (priority top-left above the title, nudge top-right). Label hidden on
          the dense Priority view, which is already grouped by level. */}
      {variant === "row" && (
        <>
          <PriorityBadge
            item={item}
            urgentWindowHours={urgentWindowHours}
            showLabel={!showPriority}
            hideLowPriority={!showPriority}
          />
          {!showPriority && (
            <SuggestionBadge item={item} urgentWindowHours={urgentWindowHours} compact />
          )}
        </>
      )}
    </>
  );

  if (variant === "row") {
    // A div (not a <button>) so the SuggestionBadge's own buttons are valid
    // nested interactive elements; Enter/Space + role keep it keyboard-usable.
    return (
      <>
        <div
          ref={innerRef}
          role="button"
          tabIndex={0}
          onClick={() => openFocus(item.id)}
          onContextMenu={openMenu}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              openFocus(item.id);
            }
          }}
          // Mobile: two-line row (full-width title, chips wrap underneath) so
          // titles aren't crushed to a few characters; sm:+ single line as before.
          className="tech-transition group flex w-full cursor-pointer flex-col items-stretch gap-1 border-b border-border px-3.5 py-2.5 text-left hover:bg-secondary/50 sm:flex-row sm:items-center sm:gap-2.5"
        >
          <div className="flex min-w-0 flex-1 items-center gap-2.5">
            {showStage && <StatusPill item={item} />}
            <span className="min-w-0 flex-1 truncate text-sm text-foreground">
              {item.title}
            </span>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {meta}
            {project && (
              <ProjectLabel
                item={item}
                name={project.outcome}
                className="hidden items-center gap-1 text-[10px] text-muted-foreground sm:inline-flex"
                nameClass="max-w-[120px] truncate"
              />
            )}
            <ScheduleButton onClick={actions.schedule} />
            {/* `max={1}` keeps this app's one-avatar-plus-"+N" reading, which
                is what the deleted local AvatarStack drew; /projects shows up
                to three because its rows are wider. Same component either way
                (S1) — the cap is a caller's decision, the pixels are not. */}
            <AvatarStack people={assigneeNames} max={1} />
            <SourceBadge source={item.source} provider={item.provider} size="xs" />
          </div>
        </div>
        {contextMenu}
      </>
    );
  }

  // The card opens the task. Always — there is no mode in which a click on it
  // means something else (S1, /projects' rule). Selecting is the checkbox's
  // job, and it is a different target; `shift` is ignored here because the
  // range-extend gesture belongs to the control that starts a range.
  const activate = () => openFocus(item.id);
  const done = Boolean(item.completedAt);
  return (
    <>
      {/* WS-27ad: the box is `@/components/TaskCardShell`, the same one the
          /projects board draws — same radius, padding, `bg-card` surface,
          border and shadow lift. What goes INSIDE stays this app's: the GTD
          badges, the context menu and the priority pair are not concepts
          /projects has.

          S1: the checkbox is a SIBLING of the box, not a child of it — the
          same `flex items-start gap-1.5` row /projects lays out. Inside the
          card it sat on top of the drag grip in the one corner both wanted,
          and a card that is also a checkbox is one gesture with two meanings.
          Outside, there are two targets and the card's whole surface stays the
          open affordance. */}
      <div className="flex items-start gap-1.5">
        {onToggleSelected ? (
          <Checkbox
            checked={selected}
            onChange={(e) =>
              onToggleSelected((e.nativeEvent as MouseEvent).shiftKey)
            }
            // The click must not also open the task.
            onClick={(e) => e.stopPropagation()}
            aria-label={selected ? "Deselect task" : "Select task"}
            className="mt-3"
          />
        ) : null}
        <TaskCardShell
          className="w-full min-w-0"
          innerRef={innerRef}
          draggable={draggable}
          completed={done}
          atCursor={atCursor}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          onActivate={activate}
          onContextMenu={openMenu}
          selected={selected}
        >
          {/* Top corners: priority pill top-LEFT (above the title — always
              shown on the board, it's the ranking signal) · the action nudge
              top-RIGHT. No `pr-5` any more: the corner the grip and the
              checkbox used to share is free. The drag grip is GONE rather than
              relocated — the whole card is `draggable`, so it never was a
              handle, only a hint pointing at a spot that is not special;
              /projects draws none; and the shell's hover lift is the affordance
              both boards already use to say "pick me up". */}
          <div className="flex items-start gap-2">
            <PriorityBadge item={item} urgentWindowHours={urgentWindowHours} />
            <span className="ml-auto shrink-0">
              <SuggestionBadge
                item={item}
                urgentWindowHours={urgentWindowHours}
                compact
              />
            </span>
          </div>
          <div className="flex items-start gap-2">
            {/* Shown whenever the surface does not already say what the stage
                is — the board passes `showStage={false}` because its columns
                ARE the stage. It used to be hidden in select mode too, which
                was a consequence of the card being the checkbox; it is not one
                any more. The pill stays interactive (S1). */}
            {showStage && <StatusPill item={item} />}
            <TaskCardTitle completed={done}>{item.title}</TaskCardTitle>
          </div>
          {item.nextAction && item.nextAction !== item.title && (
            <p className="line-clamp-2 text-[11px] text-muted-foreground">
              {item.nextAction}
            </p>
          )}
          {project && (
            <ProjectLabel
              item={item}
              name={project.outcome}
              className="inline-flex w-fit items-center gap-1 rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground"
              nameClass="max-w-[160px] truncate"
            />
          )}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">{meta}</div>
          <div className="mt-0.5 flex items-center justify-between">
            <SourceBadge source={item.source} provider={item.provider} size="xs" />
            <div className="flex items-center gap-1.5">
              <ScheduleButton onClick={actions.schedule} />
              {item.assignee && (
                <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                  <AvatarStack people={assigneeNames} max={1} />
                  <span className="max-w-[90px] truncate">
                    {extraAssignees > 0
                      ? `${item.assignee.name} +${extraAssignees}`
                      : item.assignee.name}
                  </span>
                </span>
              )}
            </div>
          </div>
        </TaskCardShell>
      </div>
      {contextMenu}
    </>
  );
}

// A quiet, always-visible "Schedule on calendar" button (touch-friendly — the
// context menu is right-click only). Opens the shared SchedulePopup.
function ScheduleButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title="Schedule on calendar"
      aria-label="Schedule on calendar"
      className="tech-transition rounded p-1 text-muted-foreground/70 hover:bg-secondary hover:text-primary"
    >
      <Icon name="CalendarClock" className="h-3.5 w-3.5" />
    </button>
  );
}

// S1: the local `Avatar` / `AvatarStack` that used to live here are gone. They
// were a second copy of `@/components/TaskMeta`'s pair — same initials, same
// "+N" badge, same ring — differing only in that this one took a Person and a
// count while the shared one takes identifiers. A second implementation of a
// shared seam is a defect (AGENTS.md rule 4), and this one was the reason a
// /tasks avatar and a /projects avatar could drift apart without either app's
// tests noticing.
