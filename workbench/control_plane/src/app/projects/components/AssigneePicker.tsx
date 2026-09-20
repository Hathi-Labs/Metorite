"use client";

/**
 * Projects · the directory-backed assignee picker (WS-28e).
 *
 * Spec: `project-docs/specs/people_center_app.md` §6.1 · D-PM-4 · D-PC-12.
 *
 * Wraps the free-text assignee input with suggestions from the directory:
 * people and agents in ONE list under two headings — handing work to an agent
 * is the same gesture as handing it to a colleague — with each row carrying
 * why this person is or is not a good idea right now (away, overloaded,
 * engagement ending). **Warnings are shown, never enforced**, and free text
 * still works exactly as before: the server accepts any non-empty string, and
 * a picker that refuses what the API accepts is a UI inventing a rule.
 */

import { useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { Input } from "@/components/ui/Input";

import { projectsApi } from "../lib/api";
import {
  type PickerResponse,
  describePickerRow,
  pickerGroups,
} from "../lib/assignees";

const DEBOUNCE_MS = 200;

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** Called with the chosen assignee string (email or `agent:<name>`). */
  onPick: (assignee: string) => void;
  /** Fired on Enter/blur with the raw text — the pre-picker behaviour, kept. */
  onCommitText: () => void;
  disabled?: boolean;
  /** The task's due date (ISO), to sharpen the engagement-end warning. */
  due?: string | null;
  /**
   * Presentation, for the second surface (WS-27n's bulk bar, 2026-09-20).
   *
   * The panel says "Add an assignee"; the bulk bar has TWO of these and they
   * mean opposite things, so neither can use a fixed label. Defaults keep the
   * panel byte-identical — this widened the seam rather than forking it,
   * which is the rule the bulk bar's status control already follows.
   */
  placeholder?: string;
  ariaLabel?: string;
  className?: string;
  /**
   * Whether losing focus commits whatever is typed. Default `true`, which is
   * the task panel's behaviour and predates this prop.
   *
   * ⚠️ The bulk bar passes `false`, and the reason is a defect seen on the
   * local stack: with four controls on one row, typing "ow" and then
   * clicking the tag field beside it QUEUED "ow" as an assignee. In the
   * panel a blur is the member leaving a finished field. In a row of fields
   * it is the member moving to the next one, and those are opposite
   * intentions. Enter still commits free text on both.
   */
  commitOnBlur?: boolean;
}

export function AssigneePicker({
  value,
  onChange,
  onPick,
  onCommitText,
  disabled,
  due,
  placeholder = "name, email or agent:name",
  ariaLabel = "Add an assignee",
  className = "mt-1.5",
  commitOnBlur = true,
}: Props) {
  const [open, setOpen] = useState(false);
  const [res, setRes] = useState<PickerResponse | null>(null);
  /**
   * ⚠️ Three states that used to be ONE, and the one was silence.
   *
   * The list rendered only `open && groups.length > 0`, so "still fetching",
   * "nothing matches what you typed", "the request failed" and "the
   * directory is empty" all drew exactly nothing. Every one of them reads as
   * a dead control, which is what was reported: the field does not populate.
   *
   * ⚠️ **"Still fetching" is `res === null`, NOT a `loading` flag.** There was
   * a `loading` boolean here and it was set INSIDE the debounce callback, so
   * for the first `DEBOUNCE_MS` of every fresh open nothing was loading and
   * no response had arrived — and the picker told the member "Nobody in the
   * directory yet" on a tenant full of people. `res === null` cannot have
   * that gap: it means "no answer yet" from the first render onwards.
   */
  const [failed, setFailed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!open) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      void (async () => {
        setFailed(false);
        try {
          setRes(await projectsApi.suggestAssignees(
            value.trim(), due ? due.slice(0, 10) : null));
        } catch {
          // Suggestions are a convenience and the input still works without
          // them — but the member is TOLD, rather than left looking at a
          // control that appears to do nothing.
          setRes(null);
          setFailed(true);
        }
      })();
    }, DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value, open, due]);

  // One rule, tested without a DOM — see `pickerGroups`.
  const groups = pickerGroups(res);

  return (
    <div className="relative">
      <Input
        className={className}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            setOpen(false);
            onCommitText();
          }
          if (e.key === "Escape") setOpen(false);
        }}
        onBlur={() => {
          // Delay so a click on a suggestion lands before the list closes.
          setTimeout(() => {
            setOpen(false);
            if (commitOnBlur) onCommitText();
          }, 150);
        }}
        placeholder={placeholder}
        aria-label={ariaLabel}
        aria-expanded={open}
      />
      {open && (
        <div className="absolute inset-x-0 top-full z-20 mt-1 max-h-72 overflow-y-auto rounded-lg border border-border bg-popover p-1 shadow-md">
          {/* ⚠️ Every branch draws SOMETHING. A picker that renders nothing
              is indistinguishable from a broken one, and free text still
              works in all of them — the server accepts any non-empty
              string, so this list never gates what may be assigned. */}
          {res === null && !failed ? (
            <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
              Looking for teammates…
            </p>
          ) : failed ? (
            <p className="px-2 py-1.5 text-[11px] text-foreground">
              Couldn&rsquo;t reach the directory. You can still type an email
              or <code>agent:name</code>.
            </p>
          ) : groups.length === 0 ? (
            <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
              {value.trim()
                ? `Nobody matches “${value.trim()}”. Type a full email to assign anyway.`
                : "Nobody in the directory yet. Add people in the People app, or type an email."}
            </p>
          ) : null}
          {groups.map((group) => (
            <div key={group.heading}>
              <p className="px-2 pb-0.5 pt-1.5 text-[10px] font-medium uppercase text-muted-foreground">
                {group.heading}
              </p>
              {group.rows.map((row) => {
                const line = describePickerRow(row);
                return (
                  <button
                    key={row.assignee}
                    type="button"
                    // onMouseDown, not onClick: the input's blur fires first
                    // otherwise and unmounts the list under the pointer.
                    onMouseDown={(e) => {
                      e.preventDefault();
                      setOpen(false);
                      onPick(row.assignee);
                    }}
                    className="cc-control flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left hover:bg-muted/40"
                  >
                    <span className="flex items-center gap-1.5 text-xs text-foreground">
                      <Icon
                        name={row.kind === "agent" ? "Bot" : "User"}
                        className="size-3 shrink-0 text-muted-foreground"
                      />
                      {row.name}
                      {row.title && (
                        <span className="text-[10px] text-muted-foreground">
                          {row.title}
                        </span>
                      )}
                      {row.top_skills.length > 0 && (
                        <span className="text-[10px] text-muted-foreground">
                          {row.top_skills.join(" · ")}
                        </span>
                      )}
                    </span>
                    {(line || row.description) && (
                      <span
                        className={`text-[10px] ${
                          row.warnings.length
                            ? "text-foreground"
                            : "text-muted-foreground"
                        }`}
                      >
                        {row.description ?? line}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default AssigneePicker;
