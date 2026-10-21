"use client";

/**
 * The `…` menu of a page (WS-61 KB-2d, `knowledge_base.md` §21.7).
 *
 * The entries come from ONE array, `PAGE_MENU` in `lib/pageMenu.ts`, and each
 * entry names when it shows. This file maps each entry to its act. A new
 * entry is one line there and one act here (KB-2f adds Favourite, KB-2g
 * adds Page history).
 *
 * A reader sees no Move, Archive or Restore. Archive asks first. Restore
 * acts at once, because it undoes nothing that a member could lose. Each
 * write re-reads what is on screen, whatever the answer was.
 */

import { useState } from "react";

import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import { themedIcon } from "@/components/Icon";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import { saveBlob } from "@/lib/export";

import { KNOWLEDGE_CACHE, KnowledgeError, archivePage } from "../lib/api";
import { useFullWidth } from "../lib/fullWidth";
import { pageHref } from "../lib/links";
import { markdownFileName, pageMarkdown, pageMenuItems, type PageMenuState } from "../lib/pageMenu";
import { printPage } from "../lib/printPage";
import type { PageDetail, Space } from "../lib/types";
import { useFavoriteToggle } from "./Favorites";
import MovePageDialog from "./MovePageDialog";
import { useRestorePage } from "./useRestorePage";

export default function PageMenu({
  page,
  spaces,
  editing,
  printBody,
}: {
  page: PageDetail;
  spaces: readonly Space[];
  editing: boolean;
  /** The element that holds the body as the editor drew it, for Print. */
  printBody: HTMLElement | null;
}) {
  const toast = useToast();
  const [fullWidth, setFullWidth] = useFullWidth();
  const [menu, setMenu] = useState<{ x: number; y: number; keyboard: boolean; button: HTMLButtonElement } | null>(
    null,
  );
  const [moving, setMoving] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [busy, setBusy] = useState(false);
  const { restoring, restore } = useRestorePage();
  const favorite = useFavoriteToggle(page);

  const state: PageMenuState = {
    canWrite: page.can_write,
    canManage: page.level === "manage",
    archived: page.state === "archived",
    editing,
    fullWidth,
    starred: favorite.starred,
  };

  const reread = () => invalidate(KNOWLEDGE_CACHE);

  async function copy(text: string, key: string, done: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.show({ key, title: done });
    } catch {
      toast.show({
        key,
        variant: "error",
        title: "Could not copy",
        description: "The browser refused the clipboard. Try again, or use Download as .md.",
      });
    }
  }

  async function archive() {
    setBusy(true);
    try {
      await archivePage(page.id);
      toast.show({ key: `kb-archive:${page.id}`, title: `Archived ${page.title}` });
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      toast.show({ key: `kb-archive:${page.id}`, variant: "error", title: "Could not archive the page", description: reason });
    } finally {
      setBusy(false);
      setArchiving(false);
      reread();
    }
  }

  /** The act of each entry of `PAGE_MENU`, by its id. */
  const acts: Record<string, () => void> = {
    "copy-link": () =>
      void copy(new URL(pageHref(page.id), window.location.origin).href, `kb-link:${page.id}`, "Link copied"),
    "copy-markdown": () => void copy(pageMarkdown(page), `kb-md:${page.id}`, "Markdown copied"),
    "download-md": () =>
      saveBlob(new Blob([pageMarkdown(page)], { type: "text/markdown;charset=utf-8" }), markdownFileName(page.title)),
    print: () => {
      if (printBody) void printPage(page.title, printBody);
    },
    "full-width": () => setFullWidth(!fullWidth),
    favorite: () => void favorite.toggle(),
    move: () => setMoving(true),
    archive: () => setArchiving(true),
    restore: () => void restore(page),
  };

  const items: CtxItem[] = pageMenuItems(state).map((row) =>
    row.kind === "sep"
      ? { kind: "sep" as const }
      : {
          kind: "item" as const,
          label: row.entry.label,
          icon: themedIcon(row.entry.icon),
          danger: row.entry.danger,
          checked: row.checked,
          onSelect: () => acts[row.entry.id]?.(),
        },
  );

  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        icon="MoreHorizontal"
        aria-label="Page actions"
        title="Page actions"
        aria-haspopup="menu"
        aria-expanded={menu !== null}
        disabled={busy || restoring !== null}
        onClick={(event) => {
          const button = event.currentTarget;
          const rect = button.getBoundingClientRect();
          setMenu({ x: rect.right - 224, y: rect.bottom + 2, keyboard: event.detail === 0, button });
        }}
      />
      {menu ? (
        <ContextMenu
          x={Math.max(4, menu.x)}
          y={menu.y}
          items={items}
          autoFocus={menu.keyboard}
          onClose={() => {
            setMenu(null);
            if (menu.keyboard) menu.button.focus();
          }}
        />
      ) : null}
      <MovePageDialog
        open={moving}
        page={page}
        spaces={spaces}
        onClose={() => setMoving(false)}
        onMoved={(_moved, space) => {
          setMoving(false);
          reread();
          toast.show({
            key: `kb-move:${page.id}`,
            variant: "success",
            title: space ? `Moved to ${space.name}` : "Page moved",
          });
        }}
      />
      <ConfirmDialog
        open={archiving}
        title="Archive this page?"
        subject={page.title}
        body="The page leaves the tree for each member. Its sub-pages stay in the space. A manager of the space can restore it."
        confirmLabel="Archive page"
        cancelLabel="Keep the page"
        confirmVariant="destructive"
        defaultFocus="cancel"
        icon="Archive"
        busy={busy}
        onCancel={() => setArchiving(false)}
        onConfirm={() => void archive()}
      />
    </>
  );
}
