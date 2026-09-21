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
import SelectButton from "@/components/ui/SelectButton";
import Icon from "@/components/Icon";
import { useState } from "react";

import { AssigneePicker } from "./AssigneePicker";
import { TagPicker } from "./TagPicker";

import type { StatusRow, TagRow } from "../lib/api";
import { labelWith } from "../lib/grouping";
import { type BulkDraft, EMPTY_DRAFT, buildRequest } from "../lib/selection";

/**
 * The house active pair, the same two strings `FilterBar` uses.
 *
 * ⚠️ Copied rather than imported, exactly as `FilterBar` holds them: they are
 * two utility strings, and an import between two sibling components to share
 * a constant is a dependency for nothing. If a third surface wants them they
 * move to one place.
 */
const OFF_DEFAULT = "border-primary/50 bg-primary/10 text-primary";
const AT_DEFAULT = "";

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
  /**
   * The project's tag registry, for the two tag pickers.
   *
   * Defaulted to empty rather than required: a selection can span projects,
   * and a surface with no registry should still let somebody TYPE a tag. The
   * picker then suggests nothing and creates on Enter, which is the honest
   * behaviour and exactly what the four text boxes did before.
   */
  tags?: readonly TagRow[];
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
  /**
   * The lifecycle verbs, on the whole selection. Owner request, 2026-09-20.
   *
   * ⚠️ **Separate from `onApply`, for the reason `onMove` gives above and one
   * more.** A patch SETS FIELDS and composes; these three do not compose with
   * anything or with each other, and `delete` makes any other half of the
   * request meaningless. The gateway refuses the combination by name rather
   * than guessing an order, so offering them through the same button would
   * build a request it is written to reject.
   */
  onAction?: (action: "archive" | "unarchive" | "delete") => void;
  /**
   * Open the merge card for the whole selection.
   *
   * ⚠️ Separate from `onAction` on purpose. Those three ACT on the
   * click; this opens a card, because a merge needs one more answer
   * — which task survives — and it is the least reversible thing on
   * this bar.
   */
  onMerge?: () => void;
  /** See TaskBoard's prop of the same name. */
  personLabels?: ReadonlyMap<string, string>;
  /** The last outcome sentence, or null. */
  notice: string | null;
}

export function BulkBar({
  count,
  statuses,
  tags = [],
  busy,
  onClear,
  onApply,
  onAction,
  onMerge,
  personLabels,
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

        {/* ⚠️ BUTTONS, not `<select>`s (H-94, owner 2026-08-26 — restated
            2026-09-20 for this bar). A native `<select>` takes its open list
            from the PLATFORM: on Windows that is a white panel with a blue
            highlight bar, which is the one control in the row that is not
            drawn by this design system. `FilterBar` was converted and this bar
            was missed, so the two rows of dropdowns on one screen did not
            match each other. */}
        <SelectButton
          label="Set status"
          widthClass="w-[9rem]"
          className={draft.status ? OFF_DEFAULT : AT_DEFAULT}
          value={draft.status}
          onChange={(next) => set({ status: next })}
          // De-duplicated by NAME: a selection can span projects whose lanes
          // share names, and offering "Done" twice is a choice with no
          // difference.
          options={[
            { value: "", label: "Status…" },
            ...[...new Set(statuses.map((s) => s.name))].map((name) => ({
              value: name,
              label: name,
            })),
          ]}
        />

        <SelectButton
          label="Set priority"
          widthClass="w-[8rem]"
          className={draft.importance ? OFF_DEFAULT : AT_DEFAULT}
          value={draft.importance}
          onChange={(next) => set({ importance: next })}
          options={IMPORTANCE.map(([value, label]) => ({ value, label }))}
        />

        {/* ⚠️ The add/remove fields are PAIRS, and the pair is the unit that
            wraps. Left loose on the row, "Remove tags…" wrapped away from
            "Add tags…" and landed under the assignee fields, where it reads
            as a fourth unrelated box. Grouping costs one div and keeps the
            two halves of one idea on one line at every width.

            ⚠️ **These were four free-text boxes** until 2026-09-20. Owner
            direction: a selection should suggest as you type, the way the
            task panel already does. So they are the panel's OWN pickers —
            `AssigneePicker` (directory-backed, people and agents in one list
            with their warnings) and `TagPicker` (the project's registry, with
            "create" shown rather than silent). Widened with presentation
            props rather than copied, so the bar and the panel cannot drift
            into suggesting different things.

            The DRAFT is still a comma-separated string, so `buildRequest`
            and `lib/selection.ts` are untouched. A picker writes into the
            same field a person could type into, and free text still works —
            the server accepts any non-empty string, and a picker that
            refuses what the API accepts is a UI inventing a rule. */}
        <div className="flex items-start gap-1">
          <PeopleField
            personLabels={personLabels}
            label="Assign to…"
            value={draft.assigneeAdd}
            busy={busy}
            onChange={(next) => set({ assigneeAdd: next })}
          />
          <PeopleField
            personLabels={personLabels}
            label="Unassign…"
            value={draft.assigneeRemove}
            busy={busy}
            onChange={(next) => set({ assigneeRemove: next })}
          />
        </div>
        <div className="flex items-start gap-1">
          <div className="w-36">
            <TagPicker
              compact
              placeholder="Add tags…"
              ariaLabel="Add tags"
              registry={[...tags]}
              disabled={busy}
              value={asList(draft.tagAdd)}
              onChange={(next) => set({ tagAdd: next.join(", ") })}
            />
          </div>
          <div className="w-36">
            <TagPicker
              compact
              placeholder="Remove tags…"
              ariaLabel="Remove tags"
              registry={[...tags]}
              disabled={busy}
              value={asList(draft.tagRemove)}
              onChange={(next) => set({ tagRemove: next.join(", ") })}
            />
          </div>
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
          {/* ⚠️ Beside Apply, not inside it. These three are verbs, not
              fields — see `onAction`. Archive and Restore are both offered
              because a selection can hold either kind and the member cannot
              see which from here; the gateway answers per task and the
              notice says how many of each. */}
          {/* Before Archive, because merging is the thing you do INSTEAD of
              filing three duplicates away one by one — and it is the reason
              a lot of multi-selections exist in the first place. */}
          {onMerge ? (
            <Button
              variant="secondary"
              size="sm"
              icon="Merge"
              loading={busy}
              title="Fold these into one task, keeping everything"
              onClick={onMerge}
            >
              Merge…
            </Button>
          ) : null}
          {onAction ? (
            <>
              <Button
                variant="secondary"
                size="sm"
                icon="Archive"
                loading={busy}
                title="File these out of every board, list and search"
                onClick={() => onAction("archive")}
              >
                Archive
              </Button>
              <Button
                variant="secondary"
                size="sm"
                icon="ArchiveRestore"
                loading={busy}
                title="Bring these back onto their boards"
                onClick={() => onAction("unarchive")}
              >
                Restore
              </Button>
              <Button
                variant="secondary"
                size="sm"
                icon="Trash2"
                loading={busy}
                title="Delete these for good"
                onClick={() => onAction("delete")}
              >
                Delete
              </Button>
            </>
          ) : null}
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

/** A CSV draft field as a list, and back. `lib/selection.list` does the same
 *  split on the way to the request; this is its mirror for the UI. */
const asList = (raw: string): string[] =>
  raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

/**
 * One assignee field: the chips already queued, plus the directory picker.
 *
 * ⚠️ The picker's text is a LOCAL query, not the draft. Bound to the draft it
 * would search for "priya@x, bob@y" the moment a second person was queued,
 * and the directory would answer nothing — a field that stops suggesting once
 * you have used it. So the query clears on every pick and the committed
 * people live in chips beside it, which is the model `TagPicker` already uses
 * for the same reason.
 *
 * Free text still lands: Enter commits whatever was typed, because the server
 * takes any non-empty string and `AssigneePicker`'s own header says a picker
 * that refuses what the API accepts is a UI inventing a rule.
 */
function PeopleField({
  label,
  value,
  busy,
  onChange,
  personLabels,
}: {
  label: string;
  value: string;
  busy: boolean;
  onChange: (next: string) => void;
  personLabels?: ReadonlyMap<string, string>;
}) {
  const [query, setQuery] = useState("");
  const people = asList(value);

  const add = (who: string) => {
    const trimmed = who.trim();
    // A comma would split one address into two unusable halves downstream.
    if (!trimmed || trimmed.includes(",")) return;
    if (people.some((p) => p.toLowerCase() === trimmed.toLowerCase())) {
      setQuery("");
      return;
    }
    onChange([...people, trimmed].join(", "));
    setQuery("");
  };

  return (
    <div className="w-36">
      <div className="flex flex-wrap gap-1 empty:hidden">
        {people.map((who) => (
          <span
            key={who}
            className="inline-flex max-w-full items-center gap-1 rounded-md bg-secondary px-1.5 py-0.5 text-[11px] text-foreground"
          >
            <span className="truncate">{labelWith(personLabels)(who)}</span>
            <button
              type="button"
              disabled={busy}
              aria-label={`Remove ${who}`}
              onClick={() => onChange(people.filter((p) => p !== who).join(", "))}
              className="shrink-0 opacity-70 hover:opacity-100"
            >
              <Icon name="X" size={10} />
            </button>
          </span>
        ))}
      </div>
      <AssigneePicker
        value={query}
        onChange={setQuery}
        onPick={add}
        onCommitText={() => add(query)}
        // ⚠️ Enter only. A blur here is the member moving to the next field
        // on the same row, not finishing this one — see the prop's note.
        commitOnBlur={false}
        disabled={busy}
        placeholder={label}
        ariaLabel={label.replace(/…$/, "")}
        className={people.length ? "mt-1" : ""}
      />
    </div>
  );
}
