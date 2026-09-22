"use client";

import { useMemo, useState } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { categoricalAccent } from "@/lib/categorical";
import type { LensArea } from "../lib/api";
import { lensEnabled } from "../lib/lens";
import { itemsInArea, useTaskStore, viewCounts } from "../lib/taskStore";
import { GtdItem, ViewKey } from "../lib/types";

type NavRow = {
  view: ViewKey;
  label: string;
  /** Lucide icon NAME — the theme picks the pack (see DESIGN_SYSTEM.md). */
  icon: string;
  /** show the count badge */
  showCount?: boolean;
  /** not yet built — rendered disabled with a "soon" tag */
  soon?: boolean;
};

// The single flat nav. "My Next Actions" is one destination — its slices
// (Priority / Suggestion / Context / Energy grouping, list vs board) are
// configured IN the view (toolbar group-by + column settings), not as separate
// sidebar entries. Priority and Engage were removed as standalone views for
// that reason.
const PRIMARY: NavRow[] = [
  { view: "inbox", label: "Inbox", icon: "Inbox", showCount: true },
  { view: "next", label: "My Next Actions", icon: "ListChecks", showCount: true },
  { view: "waiting", label: "Waiting For", icon: "Clock", showCount: true },
  // ⚠️ Calendar left this list 2026-08-24 (D54, board WS-39 S2): it is its own
  // Personal Center app at `/calendar`. It is deliberately NOT re-added here as
  // a link — a sidebar row that navigates OUT of the app it sits in is the kind
  // of half-move that leaves two entry points for one surface.
  // Projects and People were removed here 2026-08-06 (owner decision): this
  // app is the personal lens. The company's projects live in `/projects` and
  // the directory in `/people`, each a whole app rather than a cramped tab
  // behind a task manager.
  { view: "someday", label: "Someday / Maybe", icon: "Lightbulb", showCount: true },
  { view: "done", label: "Done", icon: "CheckCircle2", showCount: true },
  { view: "archive", label: "Archive", icon: "Archive" },
];

// ⚠️ No "higher altitude" block below the views. D65 took that surface off
// (S6b repair, 2026-09-23), and S6c removes the view key. `naming`-style
// fence: `areas.test.ts` refuses the word in this file.

export function ListsSidebar({
  onNavigate,
  onOpenAssistant,
  assistantActive,
}: {
  onNavigate?: () => void;
  /** Open the AI assistant as a scene (email-app pattern). */
  onOpenAssistant?: () => void;
  /** Highlight the Assistant entry while its scene is open. */
  assistantActive?: boolean;
} = {}) {
  const items = useTaskStore((s) => s.items);
  const selectedView = useTaskStore((s) => s.selectedView);
  const selectedContext = useTaskStore((s) => s.selectedContext);
  const selectViewRaw = useTaskStore((s) => s.selectView);
  const openSettings = useTaskStore((s) => s.openSettings);
  const loadArchive = useTaskStore((s) => s.loadArchive);
  const loadDone = useTaskStore((s) => s.loadDone);
  const sourceFilter = useTaskStore((s) => s.sourceFilter);
  const selectedAreaId = useTaskStore((s) => s.selectedAreaId);
  const selectView: typeof selectViewRaw = (v) => {
    selectViewRaw(v);
    // Archived tasks aren't in the normal hydrate — pull them on demand.
    if (v === "archive") void loadArchive();
    // DONE tasks are excluded from the normal hydrate too — load on open.
    if (v === "done") void loadDone();
    onNavigate?.();
  };
  // Counts must honor the All / Mine / ClickUp source toggle, otherwise the
  // badges stay frozen at the "All" totals while the list below re-filters.
  // The same holds for a selected Area (S6b): the organised lists narrow to
  // it, so their badges narrow with them. ⚠️ NOT the Inbox: a capture lands
  // in the personal ROOT, before any Area, so an Area scope would empty the
  // Inbox and hide the badge. The Inbox is pre-organisation and never scoped.
  const counts = useMemo(() => {
    const scoped = viewCounts(itemsInArea(items, selectedAreaId), sourceFilter);
    if (selectedAreaId) scoped.inbox = viewCounts(items, sourceFilter).inbox;
    return scoped;
  }, [items, sourceFilter, selectedAreaId]);

  return (
    <nav className="flex h-full flex-col gap-1 overflow-y-auto p-3 text-sm">
      <div className="px-2 pb-2 pt-1">
        <h2 className="text-sm font-semibold text-foreground">My Tasks</h2>
        <p className="text-[11px] text-muted-foreground">Your lists, your Areas, your day</p>
      </div>

      {/* ⚠️ The view rows below were DELETED by mistake in WS-39 S3a-client
          slice 4 (b6192110), which meant to remove only the Workspaces list
          under them and took the whole nav with it. `NavButton`, `PRIMARY`
          and the assistant props survived unused, which is how it was
          noticed (S6b, 2026-09-23). Restored as they were. */}
      {PRIMARY.map((row) => {
        const count = counts[row.view];
        // My Next Actions stays highlighted even when an in-view @context pill is
        // active (selectedContext set) — it's still the Next Actions view.
        const active =
          selectedView === row.view &&
          (row.view === "next" ? true : !selectedContext);
        return (
          <NavButton
            key={row.view}
            row={row}
            active={active}
            count={count}
            onClick={() => selectView(row.view)}
          />
        );
      })}

      {/* S6b — my Areas. Only under the lens: the legacy store has no such
          rows, and a section that can only render empty is a dead branch. */}
      {lensEnabled() && <AreasSection items={items} onNavigate={onNavigate} />}

      {/* AI assistant — opens as a scene (mirrors the email app's left-rail
          Chat entry) instead of an always-on right rail. */}
      {onOpenAssistant && (
        <div className="mt-3 border-t border-border pt-3">
          <button
            type="button"
            onClick={() => {
              onOpenAssistant();
              onNavigate?.();
            }}
            aria-pressed={assistantActive}
            className={[
              "tech-transition flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left",
              assistantActive
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            ].join(" ")}
          >
            <Icon name="Sparkles" className="h-4 w-4 shrink-0" />
            <span className="flex-1">Assistant</span>
          </button>
        </div>
      )}

      {/* ⚠️ The read-only "Workspaces" list was DELETED here (WS-39
          S3a-client slice 4), superseding S1 repair round 1's compromise.
          That round kept the account ROWS visible because D52 speaks only
          of COLUMNS and is silent on rows — a fair reading at the time. This
          slice deletes the whole `/tasks/accounts` client, so there is
          nothing left to read them with, and a section that can only ever
          render empty is not a compromise, it is a dead branch.
          Provenance is not lost: a row imported before the retirement keeps
          its source badge and its deep link in the task detail, which is
          where somebody actually asks where a task came from. */}

      <div className="mt-3 border-t border-border pt-3">
        <button
          type="button"
          onClick={() => {
            openSettings();
            onNavigate?.();
          }}
          className="tech-transition flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-sm text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          <Icon name="Settings2" className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">Settings</span>
        </button>
      </div>
    </nav>
  );
}

function NavButton({
  row,
  active,
  count,
  onClick,
}: {
  row: NavRow;
  active: boolean;
  count?: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={row.soon}
      onClick={onClick}
      className={[
        "tech-transition flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left",
        row.soon
          ? "cursor-default text-muted-foreground/50"
          : active
            ? "bg-primary/10 text-primary"
            : "text-muted-foreground hover:bg-secondary hover:text-foreground",
      ].join(" ")}
    >
      <Icon name={row.icon} className="h-4 w-4 shrink-0" />
      <span className="flex-1 truncate">{row.label}</span>
      {row.soon ? (
        <span className="rounded bg-muted px-1.5 py-0.5 text-[9px] font-medium uppercase text-muted-foreground">
          soon
        </span>
      ) : row.showCount && count ? (
        <span
          className={[
            "min-w-[18px] rounded-full px-1.5 py-0.5 text-center text-[10px] font-semibold",
            active ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground",
          ].join(" ")}
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

// ── Areas (WS-39 S6b) ────────────────────────────────────────────────────────
//
// A member's own categories under one store: flat (D65), private, and the
// only structure they own. Each row is a SCOPE on every view (the store's
// `selectedAreaId`), not a view of its own, so "Home" narrows the inbox, the
// next actions and the badges together. Create is inline; rename and delete
// go through the shared `Modal`, and the delete says which of the two things
// the gateway did, because "deleted" over an archive would be a lie.

/** Live work in one Area, from the loaded rows — the same rule the badges use. */
function openInArea(items: GtdItem[], areaId: string): number {
  return itemsInArea(items, areaId).filter(
    (i) => !i.archivedAt && i.disposition !== "DONE",
  ).length;
}

function AreasSection({
  items,
  onNavigate,
}: {
  items: GtdItem[];
  onNavigate?: () => void;
}) {
  const areas = useTaskStore((s) => s.areas);
  const selectedAreaId = useTaskStore((s) => s.selectedAreaId);
  const selectArea = useTaskStore((s) => s.selectArea);
  const createArea = useTaskStore((s) => s.createArea);
  const renameArea = useTaskStore((s) => s.renameArea);
  const deleteArea = useTaskStore((s) => s.deleteArea);
  const toast = useToast();

  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState<LensArea | null>(null);
  const [renameTo, setRenameTo] = useState("");
  const [removing, setRemoving] = useState<LensArea | null>(null);

  const submitCreate = async () => {
    const clean = newName.trim();
    if (!clean || busy) return;
    setBusy(true);
    try {
      const made = await createArea(clean);
      if (made) {
        setNewName("");
        setCreating(false);
      }
    } finally {
      setBusy(false);
    }
  };

  const submitRename = async () => {
    if (!renaming || busy) return;
    setBusy(true);
    try {
      await renameArea(renaming.id, renameTo);
      setRenaming(null);
    } finally {
      setBusy(false);
    }
  };

  const submitDelete = async () => {
    if (!removing || busy) return;
    setBusy(true);
    try {
      const removal = await deleteArea(removing.id);
      setRemoving(null);
      if (removal) {
        // The gateway's own outcome, said back in its words.
        toast.show({
          key: `tasks-area:${removal.id}`,
          variant: "success",
          title:
            removal.outcome === "archived"
              ? `Archived "${removing.name}"`
              : `Deleted "${removing.name}"`,
          description:
            removal.outcome === "archived"
              ? `${removal.tasks} ${removal.tasks === 1 ? "task" : "tasks"} kept.`
              : undefined,
        });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 border-t border-border pt-3">
      <div className="flex items-center justify-between px-2 pb-1">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Areas
        </p>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          radius="keep"
          layout=""
          icon="Plus"
          aria-label="New area"
          title="New area"
          className="rounded-md"
          onClick={() => setCreating(true)}
        />
      </div>

      {areas.length === 0 && !creating && (
        <p className="px-2.5 py-1 text-[11px] text-muted-foreground">
          No areas yet. An area is a category of your own.
        </p>
      )}

      {areas.map((area) => {
        const active = selectedAreaId === area.id;
        const count = openInArea(items, area.id);
        return (
          <div
            key={area.id}
            className={[
              "group tech-transition flex w-full items-center gap-2 rounded-lg pr-1",
              active
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            ].join(" ")}
          >
            <button
              type="button"
              aria-pressed={active}
              onClick={() => {
                selectArea(area.id);
                onNavigate?.();
              }}
              className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-2 text-left"
            >
              <span
                aria-hidden
                className={`h-2 w-2 shrink-0 rounded-full ${categoricalAccent(area.name).dot}`}
              />
              <span className="flex-1 truncate">{area.name}</span>
              {count > 0 && (
                <span
                  className={[
                    "min-w-[18px] rounded-full px-1.5 py-0.5 text-center text-[10px] font-semibold",
                    active ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground",
                  ].join(" ")}
                >
                  {count}
                </span>
              )}
            </button>
            {/* Row actions. Hidden until hover or focus so the list reads as
                a list. `reveal-on-hover` (globals.css) also shows them on a
                touch display and on keyboard focus, which a bare Tailwind
                hover variant never would — `revealOnHover.test.ts` fences it. */}
            <span className="reveal-on-hover tech-transition flex shrink-0 items-center">
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                radius="keep"
                layout=""
                icon="Pencil"
                aria-label={`Rename ${area.name}`}
                title="Rename"
                className="rounded-md"
                onClick={() => {
                  setRenaming(area);
                  setRenameTo(area.name);
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                radius="keep"
                layout=""
                icon="Trash2"
                aria-label={`Remove ${area.name}`}
                title="Remove"
                className="rounded-md"
                onClick={() => setRemoving(area)}
              />
            </span>
          </div>
        );
      })}

      {creating && (
        <div className="flex items-center gap-1.5 px-2 py-1.5">
          <Input
            inputSize="sm"
            autoFocus
            value={newName}
            placeholder="Area name…"
            aria-label="New area name"
            disabled={busy}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void submitCreate();
              }
              if (e.key === "Escape") {
                setCreating(false);
                setNewName("");
              }
            }}
            className="min-w-0 flex-1"
          />
          <Button
            type="button"
            size="sm"
            loading={busy}
            disabled={!newName.trim()}
            onClick={() => void submitCreate()}
          >
            Add
          </Button>
        </div>
      )}

      <Modal
        open={renaming !== null}
        onClose={() => setRenaming(null)}
        title="Rename area"
        icon="Pencil"
        size="sm"
      >
        <form
          className="space-y-3 p-3 text-xs"
          onSubmit={(e) => {
            e.preventDefault();
            void submitRename();
          }}
        >
          <label className="block space-y-1">
            <span className="text-muted-foreground">Name</span>
            <Input
              inputSize="sm"
              autoFocus
              value={renameTo}
              disabled={busy}
              onChange={(e) => setRenameTo(e.target.value)}
              aria-label="Area name"
              className="w-full"
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setRenaming(null)} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" size="sm" loading={busy} disabled={!renameTo.trim()}>
              Rename
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title="Remove area"
        icon="Trash2"
        size="sm"
        description={
          removing
            ? `Tasks in "${removing.name}" are kept. An area that still holds tasks is archived, not deleted.`
            : undefined
        }
      >
        <div className="flex justify-end gap-2 p-3">
          <Button type="button" variant="secondary" size="sm" onClick={() => setRemoving(null)} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            icon="Trash2"
            loading={busy}
            onClick={() => void submitDelete()}
          >
            Remove
          </Button>
        </div>
      </Modal>
    </div>
  );
}

