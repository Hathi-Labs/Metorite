"use client";

/**
 * TaskCardForm — the edit form of a `create_task` card (`projects_ai_chat.md`
 * §32, slice EAC-2, task 3469).
 *
 * It draws inside `ConfirmationCard`, under the card's summary and rows, in
 * the place of the rows that it edits: the due date and the people. The other
 * five values of the card (start, status, the two priority flags, the
 * description) are not edited in this slice (EAC-3 owns them). The card's own
 * rows still show them, and the answer sends them back as the card showed them.
 *
 * The member changes the due date, takes a person off, or adds a person, then
 * presses the Approve of the card. `lib/taskEditCard.ts` builds the answer and
 * owns every rule. This file draws, and holds no rule.
 *
 * - The date is the shared `Input` with `type="date"`. No shared picker exists
 *   (`DESIGN_SYSTEM.md`), and this file builds none.
 * - The people are the shared `AssigneePicker` and `AssigneeChips`. The picker
 *   adds a person only when the member picks a row or presses Enter. A typed
 *   name must match one person EXACTLY (`matchTyped`), because the server
 *   refuses any other name AFTER the Approve, with the act spent.
 * - An address that the directory does not know shows its warning BEFORE the
 *   Approve (H-162), from the same lookup as the receipt.
 * - Enter in a field never approves. Escape closes the list and clears the
 *   typed text. "Back to the card" puts the original values back.
 *
 * Fences: `src/lib/taskEditCard.test.ts`, `src/components/taskCardForm.test.ts`
 * and `e2e/task-edit-card.spec.ts`.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import AssigneeChips from "@/app/projects/components/AssigneeChips";
import AssigneePicker from "@/app/projects/components/AssigneePicker";
import { projectsApi } from "@/app/projects/lib/api";
import type { PickerResponse } from "@/app/projects/lib/assignees";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { Input } from "@/components/ui/Input";
import {
  MAX_ASSIGNEES,
  addAssignee,
  changedSummary,
  fullLine,
  isUnchanged,
  matchTyped,
  norm,
  removeAssignee,
  strangerLine,
  strangersOf,
  typedLine,
  type DirectoryRow,
  type TaskCardEdits,
  type TaskEditForm,
} from "@/lib/taskEditCard";

/**
 * The card's edits and the text of the person field. A change of tab unmounts
 * the form, so `onEdits` keeps the edits for the next mount (`savedEdits`).
 * With no form, the hook holds nothing and reports nothing.
 */
export function useTaskCardEdits(
  form: TaskEditForm | undefined,
  saved: TaskCardEdits | undefined,
  onEdits?: (edits: TaskCardEdits) => void,
) {
  const [edits, setEdits] = useState<TaskCardEdits | null>(() => (form ? (saved ?? form.values) : null));
  const [typed, setTyped] = useState("");
  const keep = useRef(onEdits);
  useEffect(() => { keep.current = onEdits; }, [onEdits]);
  const had = useRef(saved !== undefined);
  useEffect(() => {
    if (!form || !edits) return;
    // A card that nobody touched keeps nothing. After a change, and after a
    // reset to the original, it keeps the edits.
    if (isUnchanged(form.values, edits) && !had.current) return;
    had.current = true;
    keep.current?.(edits);
  }, [form, edits]);
  return { edits, setEdits, typed, setTyped };
}

/** The directory rows of an answer of `GET /projects/assignees`: people and agents in one list. */
function rowsOf(res: PickerResponse | null): DirectoryRow[] {
  const out: DirectoryRow[] = [];
  for (const group of [res?.people, res?.agents]) {
    if (!Array.isArray(group)) continue;
    for (const r of group) {
      if (typeof r?.assignee === "string" && typeof r?.name === "string") out.push({ assignee: r.assignee, name: r.name });
    }
  }
  return out;
}

export interface TaskCardFormProps {
  form: TaskEditForm;
  edits: TaskCardEdits;
  onChange: (next: TaskCardEdits) => void;
  /** The text in the person field that no Enter added yet. */
  typed: string;
  onTyped: (text: string) => void;
  disabled?: boolean;
}

export default function TaskCardForm({ form, edits, onChange, typed, onTyped, disabled = false }: TaskCardFormProps) {
  const original = form.values;
  const edited = !isUnchanged(original, edits);
  // What the directory said, by lower-case address, and which addresses it was asked about.
  const [names, setNames] = useState<ReadonlyMap<string, string>>(() => new Map());
  const [asked, setAsked] = useState<ReadonlySet<string>>(() => new Set());
  const [lookupFailed, setLookupFailed] = useState(false);
  const [line, setLine] = useState("");
  const addresses = useMemo(() => edits.assignees.filter((a) => a.includes("@")).map(norm), [edits.assignees]);
  const needed = useMemo(() => addresses.filter((a) => !asked.has(a)), [addresses, asked]);
  useEffect(() => {
    if (needed.length === 0) return;
    let live = true;
    void (async () => {
      try {
        const res = await projectsApi.personNames(needed);
        if (!live) return;
        setLookupFailed(false);
        setNames((prev) => {
          const next = new Map(prev);
          for (const [k, v] of Object.entries(res?.names ?? {})) next.set(k.toLowerCase(), String(v));
          return next;
        });
        setAsked((prev) => new Set([...prev, ...needed]));
      } catch {
        if (live) setLookupFailed(true);
      }
    })();
    return () => { live = false; };
  }, [needed]);
  const known = useMemo(() => new Set(names.keys()), [names]);
  // Only an address that the directory has answered for can be called unknown.
  const strangers = strangersOf(edits.assignees.filter((a) => asked.has(norm(a))), known);
  const warning = strangerLine(strangers);
  const labels = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of edits.assignees) {
      const n = names.get(norm(a));
      if (n) m.set(a, n);
    }
    return m;
  }, [edits.assignees, names]);

  const pick = (assignee: string) => {
    setLine("");
    onTyped("");
    onChange({ ...edits, assignees: addAssignee(edits.assignees, assignee) });
  };
  const commit = async () => {
    if (!typed.trim()) return;
    // An address or `agent:` passes. A name asks the directory, and must match one person exactly.
    const direct = matchTyped(typed, []);
    if (direct.kind === "add") { pick(direct.assignee); return; }
    let rows: DirectoryRow[];
    try {
      rows = rowsOf(await projectsApi.suggestAssignees(typed.trim()));
    } catch {
      setLine("Metorite could not reach the directory. Type the address, then press Enter.");
      return;
    }
    const found = matchTyped(typed, rows);
    if (found.kind === "add") pick(found.assignee);
    else setLine(typedLine(found));
  };
  const full = fullLine(edits.assignees.length);
  const dueId = `task-due-${form.projectId}`;

  return (
    <div
      className="space-y-2.5"
      data-task-edit-card=""
      data-task-edit-state={edited ? "edited" : "original"}
      // Escape closes the list of the picker (it handles its own key), and it clears the text that no Enter added.
      onKeyDown={(e) => {
        if (e.key === "Escape" && typed) { onTyped(""); setLine(""); }
      }}
    >
      <div className="flex flex-col gap-1 text-xs sm:flex-row sm:items-center sm:gap-2">
        <label htmlFor={dueId} className="shrink-0 text-muted-foreground sm:w-20">Due date</label>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <Input
            id={dueId}
            type="date"
            inputSize="sm"
            value={edits.due}
            min="1000-01-01"
            max="9999-12-31"
            onChange={(e) => onChange({ ...edits, due: e.target.value })}
            disabled={disabled}
            className="w-44 min-w-0"
            data-task-edit-due=""
          />
          {edits.due && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onChange({ ...edits, due: "" })}
              disabled={disabled}
              data-task-edit-clear-due=""
            >
              No due date
            </Button>
          )}
        </div>
      </div>

      <div className="space-y-1.5 text-xs" data-task-edit-people="">
        <span className="text-muted-foreground">People</span>
        <AssigneeChips
          assignees={edits.assignees}
          disabled={disabled}
          personLabels={labels}
          onRemove={(who) => onChange({ ...edits, assignees: removeAssignee(edits.assignees, who) })}
        />
        <AssigneePicker
          value={typed}
          onChange={(text) => { onTyped(text); setLine(""); }}
          onPick={pick}
          onCommitText={() => void commit()}
          commitOnBlur={false}
          disabled={disabled || edits.assignees.length >= MAX_ASSIGNEES}
          due={edits.due || null}
          placeholder="Add a person: pick one, or type a name or an address"
          ariaLabel="Add a person"
          className="w-full"
        />
        {(line || full) && (
          <p className="break-words text-[11px] text-foreground" role="status" data-task-edit-line="">{line || full}</p>
        )}
        {warning && (
          <p className="flex items-start gap-1 break-words text-[11px] text-warning" role="status" data-task-edit-stranger="">
            <Icon name="AlertTriangle" size={12} className="mt-px shrink-0" />
            {warning}
          </p>
        )}
        {lookupFailed && !warning && (
          <p className="break-words text-[11px] text-muted-foreground" data-task-edit-lookup-failed="">
            Metorite could not check these addresses against the directory.
          </p>
        )}
      </div>

      {edited && (
        <div className="flex flex-wrap items-center gap-2" data-task-edit-edited="">
          <Badge size="xs">Edited</Badge>
          <span className="min-w-0 flex-1 break-words text-[11px] text-muted-foreground">{changedSummary(original, edits)}</span>
          <Button
            variant="ghost"
            size="sm"
            icon="RotateCcw"
            onClick={() => { onChange({ ...original, assignees: [...original.assignees] }); onTyped(""); setLine(""); }}
            disabled={disabled}
            data-task-edit-reset=""
          >
            Back to the card
          </Button>
        </div>
      )}
    </div>
  );
}
