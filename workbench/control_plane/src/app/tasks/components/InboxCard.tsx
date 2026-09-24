"use client";

import AppIcon, { themedIcon } from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTaskStore } from "../lib/taskStore";
import { proposeClarification, type ClarifyDisposition } from "../lib/clarify";
import { MyTask, MyTasksProject, Person } from "../lib/types";
import type { ConnectedProvider } from "../lib/mockData";
import { detectDateHint, originEmailHref, relativeTime, snoozeOptions } from "../lib/utils";
import { AttachmentChips } from "./AttachmentComposer";
import { SourceBadge } from "./SourceBadge";
import { ContextMenu, type CtxItem } from "./ContextMenu";
import { PromoteDialog } from "./PromoteDialog";
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

  const hint = useMemo(
    () => buildHint(item, people, projects, providers),
    [item, people, projects, providers],
  );

  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const [draftTitle, setDraftTitle] = useState(item.title);
  const [draftNote, setDraftNote] = useState(item.notes ?? "");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  // S6c — an inbox capture can go straight to a board (H-59: "a Tasks inbox
  // item can be upgraded into the Projects app"). Lens only, like the card.
  const [promoting, setPromoting] = useState(false);
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
    ...(canPromote
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
    {
      kind: "item",
      label: "Delete",
      icon: themedIcon("Trash2"),
      danger: true,
      onSelect: () => requestDelete([item.id]),
    },
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

      <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary/60" />

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
          <span className="inline-flex items-center gap-1">
            <AppIcon name="Clock" className="h-3 w-3" />
            captured {relativeTime(item.createdAt)}
          </span>
          <SourceBadge source={item.source} provider={item.provider} size="xs" />
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
        <CardAction label="Edit" icon={themedIcon("Pencil")} onClick={onEditStart} />
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

      {/* Trash — always visible (not hover-gated), on touch and desktop, so
          removing a capture is never buried. */}
      <button
        type="button"
        title="Delete"
        aria-label="Delete"
        onClick={(e) => {
          e.stopPropagation();
          requestDelete([item.id]);
        }}
        className="tech-transition mt-0.5 shrink-0 rounded-md p-1.5 text-muted-foreground/70 hover:bg-destructive/10 hover:text-destructive"
      >
        <AppIcon name="Trash2" className="h-4 w-4" />
      </button>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={menuItems}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
    {/* OUTSIDE the clickable root, on purpose. The Modal portals to
        `document.body`, but React events bubble the REACT tree, not the
        DOM: a dialog rendered inside the root above called `openClarify`
        on every click in it, and the root's `onKeyDown` swallowed every
        Space typed into a required field. `TaskCard` hosts its dialog the
        same way. Review found it (S6c repair round). */}
    {promoting && (
      <PromoteDialog item={item} onClose={() => setPromoting(false)} />
    )}
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
