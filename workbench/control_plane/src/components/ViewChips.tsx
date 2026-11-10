"use client";

/**
 * ViewChips — row 1 of a list toolbar: which view am I looking at
 * (WS-53 CRM-U8).
 *
 * One strip of chips: the default view ("All deals"), the member's private
 * views, then the shared views. After them, the one save control that
 * `dirtyState` allows: "+ Save view", or "Update view" and "Reset". The app
 * puts its own controls at the right end through `trailing`.
 *
 * A chip is the house `Button`. A right click on a chip you may change, and the
 * "View options" button that sits after the strip, open one menu: Rename,
 * Share or Make private (admin), and Delete. Delete asks first. Rename and
 * Save open a small dialog, so a click away cannot lose a name.
 *
 * It is generic on purpose. An app passes its default label, its views, and
 * the callbacks. It owns no fetch and no table. The CRM is the first user.
 * Projects (`pm_views`) is the next one: it passes the same props and adds no
 * second strip. Fence: `src/lib/savedViews/index.test.ts` for the rules, and
 * `src/app/crm/components/crmU8.test.ts` for the markup.
 */

import { useRef, useState, type ReactNode } from "react";

import { themedIcon } from "@/components/Icon";

import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import ScrollStrip from "@/components/ScrollStrip";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import {
  canEditView,
  chipList,
  viewById,
  type DirtyState,
  type SavedView,
} from "@/lib/savedViews";

export type ViewChipsProps<V extends SavedView> = {
  /** The words of the default view, such as "All deals". */
  allLabel: string;
  views: readonly V[];
  /** The id of the view in effect, or null. */
  activeId: string | null;
  dirty: DirtyState;
  isAdmin: boolean;
  /** The member's email, for the order. Optional: `mine` decides first. */
  me?: string | null;
  busy?: boolean;
  /** Open a view, or the default view with null. */
  onSelect: (view: V | null) => void;
  onSave: (name: string, shared: boolean) => Promise<void>;
  onUpdate: () => Promise<void>;
  onReset: () => void;
  onRename: (view: V, name: string) => Promise<void>;
  onShare: (view: V) => Promise<void>;
  onDelete: (view: V) => Promise<void>;
  deleteCopy: (view: V) => { title: string; body: string };
  /** The controls at the right end: layout, options, columns, export. */
  trailing?: ReactNode;
  /** Names the strip for a screen reader. */
  stripLabel?: string;
};

type Menu = { id: string; x: number; y: number; keyboard: boolean };

export default function ViewChips<V extends SavedView>({
  allLabel,
  views,
  activeId,
  dirty,
  isAdmin,
  me = null,
  busy = false,
  onSelect,
  onSave,
  onUpdate,
  onReset,
  onRename,
  onShare,
  onDelete,
  deleteCopy,
  trailing,
  stripLabel = "Views",
}: ViewChipsProps<V>) {
  const chips = chipList(allLabel, views, activeId, me);
  const active = viewById(views, activeId);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [renaming, setRenaming] = useState<V | null>(null);
  const [deleting, setDeleting] = useState<V | null>(null);
  const optionsSlot = useRef<HTMLSpanElement | null>(null);

  const byId = (id: string) => views.find((v) => v.id === id) ?? null;

  function menuItems(view: V): CtxItem[] {
    const items: CtxItem[] = [
      {
        kind: "item",
        label: "Rename",
        icon: themedIcon("Pencil"),
        onSelect: () => setRenaming(view),
      },
    ];
    if (isAdmin) {
      items.push({
        kind: "item",
        label: view.shared ? "Make private" : "Share with everyone",
        icon: themedIcon(view.shared ? "Lock" : "Users"),
        onSelect: () => void onShare(view),
      });
    }
    items.push({ kind: "sep" });
    items.push({
      kind: "item",
      label: "Delete",
      icon: themedIcon("Trash2"),
      danger: true,
      onSelect: () => setDeleting(view),
    });
    return items;
  }

  const menuView = menu ? byId(menu.id) : null;

  return (
    <div
      className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-1.5 sm:px-4"
      data-testid="crm-view-chips"
    >
      {/* One strip holds the chips AND the dirty controls, so a narrow pane
          gives the strip its whole width and a phone scrolls to "Save view"
          instead of cutting the first chip. */}
      <div className="min-w-0 flex-1">
        <ScrollStrip
          label={stripLabel}
          revealKey={activeId ?? ""}
          className="gap-1"
        >
          {chips.map((chip) => (
            <Button
              key={chip.id ?? "__all"}
              variant="ghost"
              size="sm"
              className="shrink-0 whitespace-nowrap"
              selected={chip.active}
              icon={chip.shared ? "Users" : undefined}
              title={chip.shared ? `${chip.label} (shared)` : undefined}
              onClick={() => onSelect(chip.id ? byId(chip.id) : null)}
              onContextMenu={(e) => {
                if (!chip.id) return;
                const view = byId(chip.id);
                if (!view || !canEditView(view, isAdmin)) return;
                e.preventDefault();
                setMenu({
                  id: view.id,
                  x: e.clientX,
                  y: e.clientY,
                  keyboard: false,
                });
              }}
            >
              {chip.label}
            </Button>
          ))}
          {active && canEditView(active, isAdmin) ? (
            <span ref={optionsSlot} className="inline-flex shrink-0">
              <Button
                variant="ghost"
                size="icon-xs"
                icon="MoreHorizontal"
                aria-label={`View options for ${active.name}`}
                aria-haspopup="menu"
                onClick={(e) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  setMenu({
                    id: active.id,
                    x: r.left,
                    y: r.bottom + 2,
                    keyboard: true,
                  });
                }}
              />
            </span>
          ) : null}
          {dirty === "unsaved" ? (
            <SaveButton isAdmin={isAdmin} busy={busy} onSave={onSave} />
          ) : null}
          {dirty === "changed" && active ? (
            <>
              {canEditView(active, isAdmin) ? (
                <Button
                  size="sm"
                  icon="Save"
                  className="shrink-0 whitespace-nowrap"
                  loading={busy}
                  title={`${active.name} has unsaved changes`}
                  onClick={() => void onUpdate()}
                >
                  Update view
                </Button>
              ) : null}
              <Button
                variant="ghost"
                size="sm"
                icon="RotateCcw"
                className="shrink-0 whitespace-nowrap"
                onClick={onReset}
              >
                Reset
              </Button>
            </>
          ) : null}
        </ScrollStrip>
      </div>
      {trailing ? (
        <div className="flex shrink-0 items-center gap-1">{trailing}</div>
      ) : null}

      {menu && menuView ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={menuItems(menuView)}
          autoFocus={menu.keyboard}
          onClose={() => {
            setMenu(null);
            if (menu.keyboard)
              optionsSlot.current?.querySelector("button")?.focus();
          }}
        />
      ) : null}

      <RenameDialog
        view={renaming}
        onClose={() => setRenaming(null)}
        onRename={async (name) => {
          if (renaming) await onRename(renaming, name);
          setRenaming(null);
        }}
      />

      {deleting ? (
        <ConfirmDialog
          open
          title={deleteCopy(deleting).title}
          body={deleteCopy(deleting).body}
          confirmLabel="Delete view"
          busy={busy}
          onCancel={() => setDeleting(null)}
          onConfirm={async () => {
            const gone = deleting;
            await onDelete(gone);
            setDeleting(null);
          }}
        />
      ) : null}
    </div>
  );
}

/** "+ Save view" and the small dialog that names the new view. */
function SaveButton({
  isAdmin,
  busy,
  onSave,
}: {
  isAdmin: boolean;
  busy: boolean;
  onSave: (name: string, shared: boolean) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [share, setShare] = useState(false);
  const [working, setWorking] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setWorking(true);
    try {
      await onSave(trimmed, isAdmin && share);
      setName("");
      setShare(false);
      setOpen(false);
    } finally {
      setWorking(false);
    }
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        icon="Plus"
        className="shrink-0 whitespace-nowrap"
        onClick={() => setOpen(true)}
      >
        Save view
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Save view"
        icon="Bookmark"
        size="sm"
      >
        <form className="flex flex-col gap-3 p-4" onSubmit={submit}>
          <Input
            autoFocus
            inputSize="sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="View name"
            aria-label="View name"
            maxLength={120}
          />
          {isAdmin ? (
            <label className="flex items-center gap-2 text-xs text-foreground">
              <Checkbox
                checked={share}
                onChange={() => setShare((s) => !s)}
                aria-label="Share with everyone"
              />
              Share with everyone
            </label>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              loading={working || busy}
              disabled={!name.trim()}
            >
              Save
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}

/** The rename dialog. A dialog, not an inline box, so a click away does not lose the name. */
function RenameDialog({
  view,
  onClose,
  onRename,
}: {
  view: SavedView | null;
  onClose: () => void;
  onRename: (name: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState<{ id: string; name: string } | null>(null);
  // Seat the text when a new view opens the dialog. State that follows a prop.
  if (view && draft?.id !== view.id) setDraft({ id: view.id, name: view.name });
  if (!view && draft) setDraft(null);
  const name = draft?.name ?? "";

  return (
    <Modal
      open={view !== null}
      onClose={onClose}
      title="Rename view"
      icon="Pencil"
      size="sm"
    >
      <form
        className="flex flex-col gap-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) void onRename(name.trim());
        }}
      >
        <Input
          autoFocus
          inputSize="sm"
          value={name}
          onChange={(e) =>
            setDraft((d) => (d ? { ...d, name: e.target.value } : d))
          }
          aria-label="View name"
          maxLength={120}
        />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={!name.trim()}>
            Rename
          </Button>
        </div>
      </form>
    </Modal>
  );
}
