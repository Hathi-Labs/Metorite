"use client";

/**
 * The task panel's header, shared by Projects' `TaskPanel` and My Tasks'
 * `ItemDetail` (2026-09-24).
 *
 * The two headers carried the same controls in two orders, under two names,
 * with two sizes of close button. Projects said "Close task" at `icon-sm`, My
 * Tasks said "Close detail" at `icon-xs`. Projects' expand said "Open as a
 * full card", My Tasks' said "Open full page" and was a raw `<button>`. And
 * the title was editable in one app and not in the other.
 *
 * One order now, in both apps:
 *
 *   Space › Project › #ref [copy link] · app chips … · app actions · [expand] [close]
 *   Title (click to edit)
 *
 * - `where` — the task's place in the tree, from its space down to its
 *   project. A card opened from the chat or a link says nothing else about
 *   where the task lives (owner, 2026-10-12). Omit it, or pass an empty
 *   path, and the row starts at the ref.
 * - `taskRef` — the task's reference ("#42"), or "Task" when it has none.
 * - `linkFor` — the URL the copy button puts on the clipboard. Omit it and
 *   the button is absent.
 * - `chips` — the app's own chips (a status, a disposition picker, the
 *   source, Focus).
 * - `actions` — the app's own icon buttons (Archive, Delete).
 * - `expand` — the side panel, or the same panel as a full card.
 * - `onClose` — omit it where the surface draws its own close.
 *
 * Every icon button is a `Button`. Fence: `TaskPanelHeader.test.ts`.
 */

import { type ReactNode, useState } from "react";

import AppIcon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { CLOSE_TASK_LABEL, EXPAND_ICONS, EXPAND_LABELS } from "@/lib/taskPanel";
import { findTaskRefs, TaskRefText, type TaskRefResolver } from "@/lib/taskRefs";

export interface TaskHeaderExpand {
  /** The panel is the full card now. */
  expanded: boolean;
  onToggle: () => void;
}

/** One step of a task's place in the tree: a space, a folder or a project. */
export interface TaskCrumb {
  id: string;
  name: string;
}

export interface TaskWhere {
  /** From the space down to the task's own project. */
  path: readonly TaskCrumb[];
  /** Opens that node. Omit it and the crumbs are plain text. */
  onOpen?: (id: string) => void;
}

export function TaskHeaderRow({
  where,
  taskRef,
  linkFor,
  chips,
  actions,
  expand,
  onClose,
}: {
  where?: TaskWhere;
  taskRef: string;
  linkFor?: () => string;
  chips?: ReactNode;
  actions?: ReactNode;
  expand?: TaskHeaderExpand;
  onClose?: () => void;
}) {
  // The copy's "it worked" flash. A copy with no acknowledgement gets
  // clicked three times.
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (!linkFor) return;
    try {
      await navigator.clipboard.writeText(linkFor());
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // The clipboard can be unavailable (permissions, an insecure context).
      // The link is not lost: the deep link shows in the address bar.
    }
  }

  const stop = expand?.expanded ? "full" : "side";

  const ref = (
    <>
      <p className="truncate text-xs text-muted-foreground">{taskRef}</p>
      {linkFor ? (
        <Button
          variant="ghost"
          size="icon-xs"
          icon={copied ? "Check" : "Link"}
          aria-label="Copy a link to this task"
          title="Copy a link that opens this task"
          onClick={() => void copy()}
        />
      ) : null}
    </>
  );

  return (
    <div className="mb-1.5 flex items-start justify-between gap-2">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        {where && where.path.length > 0 ? (
          <TaskWhereCrumbs where={where} current={ref} />
        ) : (
          <div className="flex min-w-0 items-center gap-0.5">{ref}</div>
        )}
        {chips}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        {actions}
        {/* ⚠️ NO `aria-pressed`. The label names the ACTION, so a pressed
            state would announce one fact twice. */}
        {expand ? (
          <Button
            variant="ghost"
            size="icon-xs"
            icon={EXPAND_ICONS[stop]}
            aria-label={EXPAND_LABELS[stop]}
            title={EXPAND_LABELS[stop]}
            onClick={expand.onToggle}
          />
        ) : null}
        {onClose ? (
          <Button
            variant="ghost"
            size="icon-sm"
            icon="X"
            aria-label={CLOSE_TASK_LABEL}
            title={CLOSE_TASK_LABEL}
            onClick={onClose}
          />
        ) : null}
      </div>
    </div>
  );
}

/** The breadcrumb's separator. It LEADS its item, so a wrapped line starts
 *  with "›" and no line ends on a dangling one. */
function Chevron() {
  return <AppIcon name="ChevronRight" aria-hidden className="h-3 w-3 shrink-0 text-muted-foreground" />;
}

/**
 * Where the task lives: Space › Folder › Project › Subproject › #ref. The
 * ref and its copy-link are the LAST crumb (`aria-current`), as a breadcrumb
 * ends on the current page, so a wrapped line keeps the ref beside its
 * project instead of on a line of its own. The task's own project is in the
 * foreground colour, because that is the answer the reader came for. Each
 * crumb opens its node when the host passes `onOpen`.
 *
 * A long name truncates at `max-w-40`, with the full name in its tooltip, and
 * the row wraps rather than pushing the close button off the panel.
 */
function TaskWhereCrumbs({ where, current }: { where: TaskWhere; current: ReactNode }) {
  const last = where.path.length - 1;
  return (
    <nav aria-label="Where this task is" className="min-w-0">
      <ol className="flex min-w-0 flex-wrap items-center gap-0.5 text-xs text-muted-foreground">
        {where.path.map((crumb, i) => {
          const tone = i === last ? "font-medium text-foreground" : "";
          return (
            <li key={crumb.id} className="flex min-w-0 items-center gap-0.5">
              {i > 0 ? <Chevron /> : null}
              {where.onOpen ? (
                <Button
                  variant="ghost"
                  size="none"
                  radius="keep"
                  layout="block"
                  title={`Open ${crumb.name}`}
                  onClick={() => where.onOpen?.(crumb.id)}
                  className="max-w-40 truncate rounded-md px-1 py-0.5 text-xs"
                >
                  {/* The tone on a span: a Button's className is for layout
                      only, and the ghost variant owns the colour. */}
                  <span className={tone}>{crumb.name}</span>
                </Button>
              ) : (
                <span title={crumb.name} className={`max-w-40 truncate px-1 ${tone}`}>
                  {crumb.name}
                </span>
              )}
            </li>
          );
        })}
        <li aria-current="page" className="flex min-w-0 items-center gap-0.5">
          <Chevron />
          {current}
        </li>
      </ol>
    </nav>
  );
}

/**
 * The task title, edited in place. Click it, type, then Enter or click away
 * to save. Escape puts it back. Shift+Enter is a new line.
 *
 * An h2: the app bar holds the page's one h1. The button sits INSIDE
 * the heading, because a heading is not allowed inside a button.
 *
 * `onSave` gets the trimmed text, and only when it is new and not empty.
 *
 * A `#123` in the title draws as the task pill (WS-57 TR-5), through
 * `refResolver`, the task's root store. A pill is a link, and a link may not
 * sit inside a button. So a title with a reference puts its words in a span
 * that a click edits, and the edit button is the pencil beside them. A title
 * with no reference keeps the one button.
 */
export function EditableTaskTitle({
  value,
  onSave,
  refResolver,
}: {
  value: string;
  onSave: (next: string) => void;
  /** The store the `#123` pills read. Absent: the app's store. */
  refResolver?: TaskRefResolver;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const start = () => {
    setDraft(value);
    setEditing(true);
  };

  if (!editing && findTaskRefs(value).length > 0) {
    return (
      <h2 className="text-base font-semibold leading-snug text-foreground">
        <span className="group flex w-full items-start gap-2">
          <span
            className="min-w-0 flex-1 cursor-text"
            onClick={(e) => {
              if (!(e.target as HTMLElement | null)?.closest("a, button")) start();
            }}
          >
            <TaskRefText text={value} resolver={refResolver} />
          </span>
          <Button
            variant="ghost"
            size="icon-xs"
            icon="Pencil"
            aria-label="Edit title"
            title="Edit title"
            className="shrink-0 reveal-on-hover"
            onClick={start}
          />
        </span>
      </h2>
    );
  }

  if (!editing) {
    return (
      <h2 className="text-base font-semibold leading-snug text-foreground">
        <button
          type="button"
          onClick={() => {
            setDraft(value);
            setEditing(true);
          }}
          className="group flex w-full items-start gap-2 text-left"
          title="Click to edit"
        >
          <span className="min-w-0 flex-1">{value}</span>
          <AppIcon
            name="Pencil"
            className="mt-1 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-opacity reveal-on-hover"
          />
        </button>
      </h2>
    );
  }

  const save = () => {
    const next = titleToSave(draft, value);
    if (next !== null) onSave(next);
    setEditing(false);
  };

  return (
    <textarea
      autoFocus
      value={draft}
      rows={2}
      aria-label="Task title"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={save}
      onKeyDown={(e) =>
        titleKeyDown(e, {
          save,
          cancel: () => {
            setDraft(value);
            setEditing(false);
          },
        })
      }
      className="w-full resize-none rounded-md border border-primary/40 bg-background px-2 py-1 text-base font-semibold leading-snug text-foreground focus:outline-none"
    />
  );
}

/**
 * A key in the title editor. Enter saves and Shift+Enter is a new line.
 * Escape cancels the edit.
 *
 * ⚠️ Escape STOPS here. The panel closes on Escape too (`panelEscape`), so
 * without the stop one key would undo the edit AND close the panel. Fence:
 * `TaskPanelHeader.test.ts`.
 */
export function titleKeyDown(
  e: Pick<KeyboardEvent, "key" | "shiftKey" | "preventDefault" | "stopPropagation">,
  on: { save: () => void; cancel: () => void },
): void {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    on.save();
  } else if (e.key === "Escape") {
    e.stopPropagation();
    on.cancel();
  }
}

/**
 * What a title edit saves: the trimmed text, or null when there is nothing to
 * save — an empty title, or the one already there. A pure function, so
 * `TaskPanelHeader.test.ts` can pin it without a DOM.
 */
export function titleToSave(draft: string, current: string): string | null {
  const next = draft.trim();
  if (!next || next === current) return null;
  return next;
}
