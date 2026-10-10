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
 * ## Drawn by `ProjectPicker` (owner, 2026-10-10)
 *
 * The owner found this list as hard to read as the task Move picker: every
 * node of every space open, and a refusal printed beside each greyed row. It
 * now draws the same picker — a search box over a tree whose spaces start
 * closed, with the path to where the node lives now already open.
 *
 * ⚠️ **An illegal target is still SHOWN, muted, and still says why.** Two
 * rejected alternatives, and why:
 *
 * - *Hide illegal targets.* The tree then changes shape depending on what you
 *   are moving, so the picker no longer looks like the tree you know. People
 *   hunt for a space that is simply not drawn.
 * - *Offer everything and let the 422 explain.* That teaches the rule by
 *   error, one refusal at a time, after the dialog has already closed.
 *
 * What changed is WHERE the reason is said: once, under the list, for the row
 * the member clicked or reached with the arrows, and in the row's tooltip. A
 * search offers only the targets that can take the node. `moveRefusal` in
 * `lib/tree.ts` owns the rules and mirrors `assert_node_grammar`; nothing here
 * decides anything.
 */

import { useCallback, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";

import type { ProjectRow } from "../lib/api";
import { pathLabel, pickerNodes } from "../lib/pickerTree";
import { type ProjectNode, moveRefusal, pathTo } from "../lib/tree";
import { type PickerLeadGroup, ProjectPicker } from "./ProjectPicker";

/** The "Top level" row's value. A node id is a UUID, so it cannot collide. */
const TOP_LEVEL = "__top_level__";

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
  const searchRef = useRef<HTMLInputElement>(null);

  /** Why a node cannot take this one. Stable per node moved, so the tree is too. */
  const rule = useCallback(
    (node: ProjectNode) => moveRefusal(roots, moving.id, node.id),
    [roots, moving.id],
  );
  const topRefusal = moveRefusal(roots, moving.id, null);

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

  // The summary over the buttons names the destination by its full path,
  // because the row picked may since have been closed or searched away.
  const target = useMemo(
    () => (picked ? (pickerNodes(roots).find((n) => n.id === picked) ?? null) : null),
    [roots, picked],
  );

  const lead: PickerLeadGroup[] = [
    {
      key: "top",
      rows: [
        {
          value: TOP_LEVEL,
          label: "Top level",
          icon: <Icon name="Boxes" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />,
          refusal: topRefusal,
        },
      ],
    },
  ];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Move ${moving.name}`}
      description="Search, or open a space to find where it should live."
      icon="FolderInput"
      initialFocus={searchRef}
    >
      <div className="space-y-2 p-3 text-xs">
        <ProjectPicker
          roots={roots}
          rule={rule}
          lead={lead}
          markers={{ [currentParent ?? TOP_LEVEL]: "where it is now" }}
          value={picked ?? TOP_LEVEL}
          inputRef={searchRef}
          label={`Move ${moving.name} to`}
          onPick={(next) => setChosen(next === TOP_LEVEL ? null : next)}
        />
        <p className="truncate text-muted-foreground">
          {unchanged ? (
            "Pick a new place for it."
          ) : (
            <>
              Moves into{" "}
              <span className="font-medium text-foreground">
                {target ? pathLabel([...target.path, target.name]) : "the top level"}
              </span>
            </>
          )}
        </p>
      </div>

      <div className="flex justify-end gap-2 px-3 pb-3">
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
