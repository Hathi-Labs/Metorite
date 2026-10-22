"use client";

/**
 * The `…` menu of a space (WS-61 KB-2m): Import pages and Export space.
 *
 * Import shows for a writer only. Hiding it is a courtesy, because the
 * gateway checks the write grant on every page the import makes.
 */

import { useState } from "react";

import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import { themedIcon } from "@/components/Icon";
import Button from "@/components/ui/Button";

import { canWriteIn } from "../lib/labels";
import type { Space } from "../lib/types";
import ExportDialog from "./ExportDialog";
import ImportDialog from "./ImportDialog";

export default function SpaceMenu({ space }: { space: Space }) {
  const [menu, setMenu] = useState<{ x: number; y: number; keyboard: boolean; button: HTMLButtonElement } | null>(null);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);

  const items: CtxItem[] = [
    ...(canWriteIn(space.level)
      ? [{ kind: "item" as const, label: "Import pages", icon: themedIcon("Upload"), onSelect: () => setImporting(true) }]
      : []),
    { kind: "item" as const, label: "Export space", icon: themedIcon("Download"), onSelect: () => setExporting(true) },
  ];

  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        icon="MoreHorizontal"
        aria-label="Space actions"
        title="Space actions"
        aria-haspopup="menu"
        aria-expanded={menu !== null}
        className="shrink-0"
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
      <ImportDialog
        target={importing ? { spaceId: space.id, spaceName: space.name, parent: null } : null}
        onClose={() => setImporting(false)}
      />
      <ExportDialog
        target={exporting ? { spaceId: space.id, spaceName: space.name, page: null } : null}
        onClose={() => setExporting(false)}
      />
    </>
  );
}
