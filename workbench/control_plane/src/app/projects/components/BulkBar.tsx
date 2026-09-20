"use client";

/**
 * Projects · the bulk-edit bar (WS-27n).
 *
 * Appears only when something is selected, and disappears the moment nothing
 * is — a permanently-present bar of controls that mostly do nothing is a bar
 * people learn to ignore.
 *
 * **Every control is add/remove or set-one-field; none is a replace.** "Assign
 * these to Priya" means *also* Priya, and a replace across a selection would
 * wipe every individual assignment the selected tasks already carried.
 *
 * **Status is offered by NAME**, from the statuses of the project on screen.
 * A selection can span projects, so the gateway resolves the name against each
 * task's own root and reports per task when a project has no such lane — which
 * is why the outcome line names failures rather than the button pretending.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useState } from "react";

import type { StatusRow } from "../lib/api";
import { type BulkDraft, EMPTY_DRAFT, buildRequest } from "../lib/selection";

const SELECT =
  "cc-control rounded-lg border border-border bg-background px-2 py-1.5 " +
  "text-xs text-foreground outline-none focus:border-primary/50";

const IMPORTANCE = [
  ["", "Priority…"],
  ["3", "Urgent"],
  ["2", "High"],
  ["1", "Normal"],
  ["0", "Low"],
] as const;

interface Props {
  count: number;
  statuses: StatusRow[];
  busy: boolean;
  onClear: () => void;
  onApply: (request: ReturnType<typeof buildRequest>) => void;
  /**
   * WS-27bl §9.13.4 — open the move card for the whole selection.
   *
   * ⚠️ Separate from `onApply`, and it must stay separate. A bulk EDIT is a
   * patch applied to rows; a bulk MOVE crosses two vocabularies and has to be
   * agreed to after the member sees the mapping. Folding it into the patch
   * would let somebody move fifty tasks from a dropdown.
   */
  onMove?: () => void;
  /** The last outcome sentence, or null. */
  notice: string | null;
}

export function BulkBar({
  count,
  statuses,
  busy,
  onClear,
  onApply,
  onMove,
  notice,
}: Props) {
  const [draft, setDraft] = useState<BulkDraft>(EMPTY_DRAFT);
  const request = buildRequest(Array.from({ length: count }, (_, i) => `#${i}`), draft);

  const set = (patch: Partial<BulkDraft>) =>
    setDraft((current) => ({ ...current, ...patch }));

  return (
    <div className="border-b border-border bg-muted px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="primary">{count} selected</Badge>

        {onMove ? (
          <Button
            variant="secondary"
            size="sm"
            icon="FolderInput"
            disabled={busy}
            onClick={onMove}
          >
            Move to project…
          </Button>
        ) : null}

        <select
          aria-label="Set status"
          className={SELECT}
          value={draft.status}
          onChange={(e) => set({ status: e.target.value })}
        >
          <option value="">Status…</option>
          {/* De-duplicated by NAME: a selection can span projects whose lanes
              share names, and offering "Done" twice is a choice with no
              difference. */}
          {[...new Set(statuses.map((s) => s.name))].map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        <select
          aria-label="Set priority"
          className={SELECT}
          value={draft.importance}
          onChange={(e) => set({ importance: e.target.value })}
        >
          {IMPORTANCE.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>

        {/* ⚠️ The add/remove fields are PAIRS, and the pair is the unit that
            wraps. Left loose on the row, "Remove tags…" wrapped away from
            "Add tags…" and landed under the assignee fields, where it reads
            as a fourth unrelated box. Grouping costs one div and keeps the
            two halves of one idea on one line at every width. */}
        <div className="flex items-center gap-1">
          <Input
            inputSize="sm"
            className="w-36"
            aria-label="Assign to"
            placeholder="Assign to…"
            value={draft.assigneeAdd}
            onChange={(e) => set({ assigneeAdd: e.target.value })}
          />
          <Input
            inputSize="sm"
            className="w-36"
            aria-label="Unassign"
            placeholder="Unassign…"
            value={draft.assigneeRemove}
            onChange={(e) => set({ assigneeRemove: e.target.value })}
          />
        </div>
        <div className="flex items-center gap-1">
          <Input
            inputSize="sm"
            className="w-28"
            aria-label="Add tags"
            placeholder="Add tags…"
            value={draft.tagAdd}
            onChange={(e) => set({ tagAdd: e.target.value })}
          />
          <Input
            inputSize="sm"
            className="w-28"
            aria-label="Remove tags"
            placeholder="Remove tags…"
            value={draft.tagRemove}
            onChange={(e) => set({ tagRemove: e.target.value })}
          />
        </div>

        {/* `ml-auto` pins the two actions to the trailing edge, so the button
            that WRITES is always in the same place no matter how the row
            above it wrapped. A confirm button that moves with the window is
            one people learn to hunt for. */}
        <div className="ml-auto flex items-center gap-2">
          <Button
            size="sm"
            loading={busy}
            // Disabled rather than firing and being told 422: the gateway
            // refuses a no-op, and a button that can only fail is worse than
            // one that says it is not ready.
            disabled={!request}
            title={request ? undefined : "Choose something to change first"}
            onClick={() => {
              onApply(request);
              setDraft(EMPTY_DRAFT);
            }}
          >
            Apply to {count}
          </Button>
          <Button variant="ghost" size="sm" icon="X" onClick={onClear}>
            Clear
          </Button>
        </div>
      </div>

      {notice ? (
        <p className="mt-1 text-xs text-muted-foreground">{notice}</p>
      ) : null}
    </div>
  );
}
