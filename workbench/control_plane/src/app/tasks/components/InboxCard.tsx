"use client";

import AppIcon, { themedIcon } from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { taskDeepLink } from "@/app/projects/lib/card";
import { type InboxKind, inboxRowActions } from "../lib/inbox";
import { useTaskStore } from "../lib/taskStore";
import { proposeClarification, type ClarifyDisposition } from "../lib/clarify";
import { MyTask, MyTasksProject, Person } from "../lib/types";
import type { ConnectedProvider } from "../lib/mockData";
import { detectDateHint, originEmailHref, relativeTime, snoozeOptions } from "../lib/utils";
import { AttachmentChips } from "./AttachmentComposer";
import { ContextMenu, type CtxItem } from "./ContextMenu";
import { InboxOrigin } from "./InboxOrigin";
import { useCardActions } from "../lib/useCardActions";

// The assistant's at-a-glance read of a capture — shown on the card so you see
// the *shape* of your inbox (what's yours, what to delegate, what's a project,
// where it goes) before opening anything. It's a hint; Clarify still decides.
const DISP_HINT: Record<ClarifyDisposition, { label: string; Icon: ThemedIcon }> = {
  NEXT: { label: "Next", Icon: themedIcon("ListChecks") },
  PROJECT: { label: "Project", Icon: themedIcon("FolderKanban") },
  WAITING: { label: "Delegate", Icon: themedIcon("UserPlus") },
  CALENDAR: { label: "Schedule", Icon: themedIcon("CalendarClock") },
  DO_NOW: { label: "Do now", Icon: themedIcon("Zap") },
  SOMEDAY: { label: "Someday", Icon: themedIcon("Lightbulb") },
  REFERENCE: { label: "Reference", Icon: themedIcon("FileText") },
  TRASH: { label: "Trash", Icon: themedIcon("Trash2") },
};
const shortText = (s: string, n = 22) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

export interface InboxCardProps {
  item: MyTask;
  /** S6g — a capture in my tree, or a row on a company board (`inbox.ts`). */
  kind: InboxKind;
  /** S6g — open the one promote dialog (the Inbox hosts it). Personal rows. */
  onMove: () => void;
  cursor: boolean;
  selected: boolean;
  selectionMode: boolean;
  editing: boolean;
  /** `shift` extends the selection from the anchor (`@/lib/selection`). */
  onSelectToggle: (shift: boolean) => void;
  onEditStart: () => void;
  onEditEnd: () => void;
}

export function InboxCard({
  item,
  kind,
  onMove,
  cursor,
  selected,
  selectionMode,
  editing,
  onSelectToggle,
  onEditStart,
  onEditEnd,
}: InboxCardProps) {
  const openClarify = useTaskStore((s) => s.openClarify);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const requestDelete = useTaskStore((s) => s.requestDelete);
  const openSchedule = useTaskStore((s) => s.openSchedule);
  const deferItem = useTaskStore((s) => s.deferItem);
  const updateItem = useTaskStore((s) => s.updateItem);
  const people = useTaskStore((s) => s.people);
  const projects = useTaskStore((s) => s.projects);
  const providers = useTaskStore((s) => s.providers);
  // S6c — the one rule for the promote door (`promoteAllowed`), read the way
  // TaskCard and ItemDetail read it, so the three cannot disagree.
  const { canPromote } = useCardActions(item);
  const router = useRouter();
  const personal = kind === "personal";
  const actions = inboxRowActions({
    kind,
    canPromote,
    move: onMove,
    // ⚠️ "Not mine" is a TRASH on MY overlay, through the one-tap dispose
    // path (`lensBulkDispose`, action `personal`). Never `requestDelete`:
    // the delete path PURGES when its undo window closes, and on a board
    // task that is a hard DELETE of the team's task (S6g audit).
    notMine: () => quickDispose(item.id, "TRASH"),
    remove: () => requestDelete([item.id]),
    openBoard: () => router.push(taskDeepLink(item)),
  });

  const hint = useMemo(
    () => buildHint(item, people, projects, providers),
    [item, people, projects, providers],
  );

  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const [draftTitle, setDraftTitle] = useState(item.title);
  const [draftNote, setDraftNote] = useState(item.notes ?? "");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // Keep the keyboard cursor row visible as you navigate with j/k.
  useEffect(() => {
    if (cursor) rootRef.current?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const dateHint = detectDateHint(item.title);

  if (editing) {
    const save = () => {
      updateItem(item.id, { title: draftTitle, notes: draftNote });
      onEditEnd();
    };
    return (
      <div className="flex flex-col gap-2 rounded-xl border border-primary/40 bg-card px-4 py-3">
        <div className="flex items-center gap-2">
          <AppIcon name="Pencil" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <input
            autoFocus
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                save();
              } else if (e.key === "Escape") {
                e.preventDefault();
                onEditEnd();
              }
            }}
            className="flex-1 bg-transparent text-sm text-foreground focus:outline-none"
          />
          <button
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={save}
            aria-label="Save"
            className="tech-transition rounded-md p-1 text-primary hover:bg-primary/10"
          >
            <AppIcon name="Check" className="h-4 w-4" />
          </button>
        </div>
        <textarea
          value={draftNote}
          onChange={(e) => setDraftNote(e.target.value)}
          placeholder="Add a note (optional)…"
          rows={2}
          className="w-full resize-none rounded-md border border-border bg-background/60 px-2.5 py-1.5 text-sm text-muted-foreground focus:border-primary/40 focus:outline-none"
        />
      </div>
    );
  }

  const menuItems: CtxItem[] = [
    {
      kind: "item",
      label: "Clarify…",
      icon: themedIcon("Sparkles"),
      onSelect: () => openClarify(item.id),
    },
    {
      kind: "item",
      label: "Schedule on calendar",
      icon: themedIcon("CalendarClock"),
      onSelect: () => openSchedule(item.id),
    },
    ...actions.map((a) =>
      a.id === "remove" || a.id === "notMine"
        ? null
        : {
            kind: "item" as const,
            label: a.label,
            icon: themedIcon(a.icon),
            onSelect: a.run,
          },
    ).filter((x): x is NonNullable<typeof x> => x !== null),
    { kind: "sep" },
    {
      kind: "item",
      label: "Move to Someday / Maybe",
      icon: themedIcon("Lightbulb"),
      onSelect: () => quickDispose(item.id, "SOMEDAY"),
    },
    {
      kind: "item",
      label: "Move to Reference",
      icon: themedIcon("FileText"),
      onSelect: () => quickDispose(item.id, "REFERENCE"),
    },
    { kind: "sep" },
    ...actions
      .filter((a) => a.id === "remove" || a.id === "notMine")
      .map((a) => ({
        kind: "item" as const,
        label: a.label,
        icon: themedIcon(a.icon),
        danger: a.id === "remove",
        onSelect: a.run,
      })),
  ];
  const openMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setMenu({ x: e.clientX, y: e.clientY });
  };

  return (
    <>
    <div
      ref={rootRef}
      role="button"
      tabIndex={0}
      data-cursor={cursor || undefined}
      onClick={() => openClarify(item.id)}
      onContextMenu={openMenu}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openClarify(item.id);
        }
      }}
      className={[
        "group tech-transition relative flex items-start gap-3 rounded-xl border bg-card px-4 py-3.5",
        cursor
          ? "border-primary ring-1 ring-primary/40"
          : selected
            ? "border-primary/50 bg-primary/5"
            : "border-border hover:border-primary/40 hover:bg-secondary/30",
      ].join(" ")}
    >
      {/* selection checkbox */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onSelectToggle(e.shiftKey);
        }}
        aria-label={selected ? "Deselect" : "Select"}
        className={[
          "mt-0.5 shrink-0 tech-transition",
          selected || selectionMode
            ? "opacity-100"
            : "reveal-on-hover",
          selected ? "text-primary" : "text-muted-foreground hover:text-foreground",
        ].join(" ")}
      >
        {selected ? (
          <AppIcon name="CheckSquare" className="h-4 w-4" />
        ) : (
          <AppIcon name="Square" className="h-4 w-4" />
        )}
      </button>

      <div
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${personal ? "bg-primary/60" : "bg-muted-foreground/50"}`}
      />

      <div className="min-w-0 flex-1 cursor-pointer">
        <p className="text-sm leading-snug text-foreground">{item.title}</p>
        <HintRow hint={hint} />
        {item.notes && (
          <p className="mt-1 flex items-start gap-1 text-xs leading-snug text-muted-foreground">
            <AppIcon name="StickyNote" className="mt-0.5 h-3 w-3 shrink-0" />
            <span className="line-clamp-2">{item.notes}</span>
          </p>
        )}
        {item.attachments && item.attachments.length > 0 && (
          <div className="mt-1">
            <AttachmentChips attachments={item.attachments} />
          </div>
        )}
        {item.origin?.kind === "email" && (
          <p className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground">
            <AppIcon name="Mail" className="h-3 w-3 shrink-0" />
            <span className="truncate">
              From email — {item.origin.fromName || item.origin.fromEmail}
              {item.origin.subject ? ` · “${item.origin.subject}”` : ""}
            </span>
            {originEmailHref(item.origin) && (
              <a
                href={originEmailHref(item.origin)!}
                onClick={(e) => e.stopPropagation()}
                className="tech-transition shrink-0 font-medium text-primary hover:underline"
              >
                Open
              </a>
            )}
          </p>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          {/* S6g — where it comes from. It wraps under the title on a
              phone rather than pushing the actions off the row. */}
          <InboxOrigin item={item} kind={kind} />
          <span className="inline-flex items-center gap-1">
            <AppIcon name="Clock" className="h-3 w-3" />
            {personal ? "captured" : "assigned"} {relativeTime(item.createdAt)}
          </span>
          {dateHint && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setSnoozeOpen(true);
              }}
              title="Detected a date — snooze to it?"
              className="tech-transition inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/5 px-1.5 py-0.5 text-[10px] text-primary hover:bg-primary/10"
            >
              <AppIcon name="CalendarClock" className="h-3 w-3" />
              {dateHint}?
            </button>
          )}
        </div>
      </div>

      {/* Secondary quick-actions (desktop hover; on touch, tap card → Clarify) */}
      <div className="hidden shrink-0 items-center gap-0.5 tech-transition reveal-on-hover sm:flex">
        {/* A board task's title is the team's. Edit it on the board. */}
        {personal && (
          <CardAction label="Edit" icon={themedIcon("Pencil")} onClick={onEditStart} />
        )}
        <div className="relative">
          <CardAction
            label="Snooze"
            icon={themedIcon("CalendarClock")}
            onClick={() => setSnoozeOpen((v) => !v)}
          />
          {snoozeOpen && (
            <SnoozeMenu
              onPick={(iso) => {
                deferItem(item.id, iso);
                setSnoozeOpen(false);
              }}
              onClose={() => setSnoozeOpen(false)}
            />
          )}
        </div>
        <CardAction
          label="Someday"
          icon={themedIcon("Lightbulb")}
          onClick={() => quickDispose(item.id, "SOMEDAY")}
        />
        <CardAction
          label="Reference"
          icon={themedIcon("FileText")}
          onClick={() => quickDispose(item.id, "REFERENCE")}
        />
        <CardAction
          label="Clarify"
          icon={themedIcon("Sparkles")}
          primary
          onClick={() => openClarify(item.id)}
        />
      </div>

      {/* S6g — the kind's own actions, ALWAYS visible (never hover-gated),
          on touch and desktop. A personal row: Move to project, Delete. A
          board row: Not mine, Open on board. */}
      <div className="mt-0.5 flex shrink-0 items-center gap-0.5">
        {actions.map((a) => (
          <button
            key={a.id}
            type="button"
            title={a.title}
            aria-label={a.label}
            onClick={(e) => {
              e.stopPropagation();
              a.run();
            }}
            className={[
              "tech-transition shrink-0 rounded-md p-1.5",
              a.id === "remove"
                ? "text-muted-foreground/70 hover:bg-destructive/10 hover:text-destructive"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            ].join(" ")}
          >
            <AppIcon name={a.icon} className="h-4 w-4" />
          </button>
        ))}
      </div>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={menuItems}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
    {/* S6g — the promote dialog is hosted by the Inbox, outside every
        row, so the card, the table and the `m` key open ONE dialog. */}
    </>
  );
}

/** Build the compact "what this will become" hint for a capture. */
function buildHint(
  item: MyTask,
  people: Person[],
  projects: MyTasksProject[],
  providers: ConnectedProvider[],
) {
  const p = proposeClarification(item, people, projects);
  const base = DISP_HINT[p.disposition];
  const parts: string[] = [];
  if (p.disposition === "WAITING" && p.suggestedAssignee) {
    parts.push(p.suggestedAssignee.name);
  }
  const proj = p.projectId ? projects.find((x) => x.id === p.projectId) : undefined;
  if (proj) parts.push(shortText(proj.outcome));
  else if ((p.disposition === "NEXT" || p.disposition === "CALENDAR") && p.context) {
    parts.push(p.context);
  }
  const provider =
    p.target && p.target.source === "SYNCED"
      ? providers.find((cp) =>
          p.target!.accountId
            ? cp.id === p.target!.accountId
            : cp.provider === p.target!.provider,
        )?.label
      : undefined;
  return { ...base, detail: parts.join(" · "), provider };
}

function HintRow({
  hint,
}: {
  hint: { label: string; Icon: ThemedIcon; detail: string; provider?: string };
}) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px]">
      <span className="inline-flex items-center gap-1">
        <AppIcon name="Sparkles" className="h-3 w-3 text-primary/60" />
        <hint.Icon className="h-3 w-3 text-muted-foreground" />
        <span className="font-medium text-foreground/75">{hint.label}</span>
      </span>
      {hint.detail && (
        <span className="truncate text-muted-foreground/70">· {hint.detail}</span>
      )}
      {hint.provider && (
        <span className="inline-flex items-center gap-0.5 rounded-full bg-secondary px-1.5 py-px text-[10px] text-muted-foreground">
          <AppIcon name="Cloud" className="h-2.5 w-2.5" />
          {hint.provider}
        </span>
      )}
    </div>
  );
}

function SnoozeMenu({
  onPick,
  onClose,
}: {
  onPick: (iso: string) => void;
  onClose: () => void;
}) {
  const opts = snoozeOptions();
  return (
    <>
      {/* click-away */}
      <div
        className="fixed inset-0 z-40"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
      />
      <div
        className="absolute right-0 top-full z-50 mt-1 w-44 rounded-lg border border-border bg-popover p-1 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Snooze until
        </p>
        {opts.map((o) => (
          <button
            key={o.label}
            type="button"
            onClick={() => onPick(o.iso)}
            className="tech-transition flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground hover:bg-secondary"
          >
            <AppIcon name="CalendarClock" className="h-3.5 w-3.5 text-muted-foreground" />
            {o.label}
          </button>
        ))}
        <label className="mt-1 flex items-center gap-2 border-t border-border px-2 py-1.5 text-[11px] text-muted-foreground">
          Pick date
          <input
            type="date"
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => {
              if (e.target.value) onPick(new Date(e.target.value).toISOString());
            }}
            className="flex-1 rounded border border-border bg-background/60 px-1 py-0.5 text-[11px] text-foreground focus:outline-none"
          />
        </label>
      </div>
    </>
  );
}

function CardAction({
  label,
  icon: Icon,
  onClick,
  primary,
  danger,
}: {
  label: string;
  icon: ThemedIcon;
  onClick: () => void;
  primary?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={[
        "tech-transition rounded-md p-1.5",
        primary
          ? "text-muted-foreground hover:bg-primary/10 hover:text-primary"
          : danger
            ? "text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            : "text-muted-foreground hover:bg-secondary hover:text-foreground",
      ].join(" ")}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );
}

