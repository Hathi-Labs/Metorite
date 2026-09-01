"use client";

/**
 * Projects · "Move to…" — WS-27bk §9.12.4 slice 1.
 *
 * ⚠️ **THIS SHIPS BEFORE THE DRAG, and that order is the design.** A tree
 * whose only re-parent gesture is a mouse drag excludes anybody who does not
 * use one. This dialog is reachable from the row menu, so it works from the
 * keyboard, and it is the accessible path the drag will sit on top of rather
 * than replace.
 *
 * ⚠️ **An illegal target is SHOWN, disabled, with its reason.** Two rejected
 * alternatives, and why:
 *
 * - *Hide illegal targets.* The tree then changes shape depending on what you
 *   are moving, so the picker no longer looks like the tree you know. People
 *   hunt for a space that is simply not drawn.
 * - *Offer everything and let the 422 explain.* That teaches the rule by
 *   error, one refusal at a time, after the dialog has already closed.
 *
 * Showing the row greyed with "A folder cannot hold another folder" beside it
 * teaches the grammar in place. `moveRefusal` in `lib/tree.ts` owns the rules
 * and mirrors `assert_node_grammar`; nothing here decides anything.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { useMemo, useState } from "react";

import type { ProjectRow } from "../lib/api";
import {
  LEVEL_ICONS,
  type ProjectNode,
  moveRefusal,
  nodeKind,
  nodeLevel,
  pathTo,
} from "../lib/tree";

interface Row {
  node: ProjectNode | null;
  depth: number;
  /** Null when this target is legal. */
  refusal: string | null;
}

/**
 * Every target, in tree order, each with its verdict.
 *
 * The ROOT comes first as a real row — "move this out to the top level" is a
 * legitimate act, and without a row for it the only way to make a space is to
 * create one.
 */
export function moveTargets(
  roots: readonly ProjectNode[],
  moving: string,
): Row[] {
  const out: Row[] = [
    { node: null, depth: 0, refusal: moveRefusal(roots, moving, null) },
  ];
  const walk = (nodes: readonly ProjectNode[], depth: number) => {
    for (const node of nodes) {
      out.push({ node, depth, refusal: moveRefusal(roots, moving, node.id) });
      walk(node.children ?? [], depth + 1);
    }
  };
  walk(roots, 0);
  return out;
}

export function MoveDialog({
  open,
  moving,
  roots,
  busy,
  onClose,
  onMove,
}: {
  open: boolean;
  moving: ProjectRow;
  roots: readonly ProjectNode[];
  busy?: boolean;
  onClose: () => void;
  onMove: (parentId: string | null) => void;
}) {
  const [chosen, setChosen] = useState<string | null | undefined>(undefined);

  const rows = useMemo(
    () => moveTargets(roots, moving.id),
    [roots, moving.id],
  );

  /**
   * Where it is now, so the dialog can say so and refuse a no-op.
   *
   * Read from the tree rather than from `moving.parent_project_id`: the row
   * handed in may have come from a list built before the last refetch, and
   * the tree is the one being shown.
   */
  const currentParent = useMemo(() => {
    const path = pathTo(roots, moving.id);
    return path.length > 1 ? path[path.length - 2].id : null;
  }, [roots, moving.id]);

  const picked = chosen === undefined ? currentParent : chosen;
  const unchanged = picked === currentParent;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Move ${moving.name}`}
      description="Pick where it should live. Greyed rows say why they cannot take it."
      icon="FolderInput"
    >
      <div className="max-h-80 overflow-y-auto">
        {rows.map((row) => {
          const id = row.node?.id ?? null;
          const legal = row.refusal === null;
          const isCurrent = id === currentParent;
          const selected = id === picked;
          const label = row.node?.name ?? "Top level";
          const icon = row.node
            ? LEVEL_ICONS[
                nodeLevel(nodeKind(row.node), 0) === "folder"
                  ? "folder"
                  : "space"
              ]
            : "Boxes";
          return (
            <button
              key={id ?? "__root__"}
              type="button"
              disabled={!legal || busy}
              onClick={() => setChosen(id)}
              /* `title` carries the refusal for a pointer, and the text beside
                 it carries the same words for everybody else. A tooltip alone
                 would put the explanation somewhere a keyboard cannot reach. */
              title={row.refusal ?? undefined}
              className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs ${
                selected
                  ? "bg-primary/10 text-primary"
                  : legal
                    ? "text-foreground hover:bg-muted"
                    : "cursor-not-allowed text-muted-foreground"
              }`}
              style={{ paddingLeft: `${row.depth * 14 + 8}px` }}
            >
              <Icon name={icon} className="h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{label}</span>
              {isCurrent ? (
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  where it is now
                </span>
              ) : null}
              {row.refusal ? (
                <span className="shrink-0 text-[10px]">{row.refusal}</span>
              ) : null}
            </button>
          );
        })}
      </div>

      <div className="mt-3 flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          size="sm"
          loading={busy}
          /* A move to where it already is writes an activity row and a refetch
             for nothing, so the button says so by being unavailable. */
          disabled={unchanged}
          onClick={() => onMove(picked)}
        >
          Move
        </Button>
      </div>
    </Modal>
  );
}
