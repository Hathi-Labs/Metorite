"use client";

/**
 * A BUTTON that opens a list of options — the filter row's dropdown.
 *
 * Owner direction, 2026-08-26 (H-94): *"Every dropdown in the row becomes a
 * button. At the default value, draw a two-headed arrow in place of the single
 * down arrow."* A native `<select>` reads as a field waiting for input. These
 * are not fields — they are a current state you can change, which is what a
 * button says.
 *
 * ⚠️ **The two-headed arrow is the whole point of the icon, and it is not
 * decoration.** A row of four controls all showing a down chevron says nothing
 * about which of them are doing anything. `ChevronsUpDown` at the default and
 * `ChevronDown` off it means the row answers *"what have I changed?"* from the
 * glyphs alone, before the colour is read — which matters because the active
 * tint is the only other cue and colour is the one people cannot all see.
 *
 * ⚠️ **NO MOTION.** Owner directive 2026-08-26, restated in H-94: a `MOTION.md`
 * landed in this tree the same day and the owner removed it. The panel appears
 * and disappears as a state change. Do not add a duration, a transition or an
 * easing curve here, and do not re-open the question without asking.
 *
 * ## ⚠️ The panel is PORTALLED, and it has to be
 *
 * An absolutely-positioned panel is clipped by the nearest ancestor that is
 * not `overflow: visible`. `Modal.tsx`'s body is `overflow-hidden`, so inside
 * a dialog this control drew a list with its bottom cut off — measured in
 * `MoveTasksDialog` on 2026-09-20: six options in the DOM, a 154px panel, and
 * only two of them visible. A native `<select>` never had that problem
 * because the browser draws its list outside the page entirely, which is the
 * one thing the platform widget was better at.
 *
 * So the panel renders into `document.body` at `position: fixed`, measured
 * from the trigger. It carries `PREVENT_OUTSIDE_CLICK`, the marker
 * `lib/outsideClick.ts` defines for exactly this: a portalled child is, by
 * containment, OUTSIDE the popover that raised it, so without the marker the
 * first click on an option would dismiss the panel instead of choosing. That
 * file was written ahead of this need and says so; this is the need.
 *
 * ⚠️ **Not built on `@base-ui/react`, and that is the rule rather than a
 * shortcut.** `AGENTS.md` rule 8 / D-PM-15 make `src/components/ui/Modal.tsx`
 * the ONE file that may import the substrate. H-94 names the sanctioned answer
 * for a popover outside it: `src/lib/outsideClick.ts`. So this uses that, and
 * it does not hand-roll a second containment check — the walker is what makes
 * a portalled child not count as "outside".
 */
import { useCallback, useEffect, useId, useReducer, useRef, useState } from "react";

import Icon from "@/components/Icon";
import AnchoredPanel, { type PanelLayer } from "@/components/ui/AnchoredPanel";
import Input from "@/components/ui/Input";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

/**
 * Which arrow the trigger wears — the one decision in this file worth a test.
 *
 * ⚠️ **Two heads at the default, one off it** (owner, 2026-08-26). A row of
 * four controls all showing a down chevron says nothing about which of them
 * are doing anything. This is the only cue besides the active tint, and it is
 * the one that survives when colour does not — so it is extracted rather than
 * left inline, and `SelectButton.test.ts` pins it.
 *
 * Kept as a pure function deliberately: `vitest.config.ts` is
 * `environment: "node"`, so a JSX assertion is not available here and a
 * source-text fence would be the third one this repo has watched pass on a
 * broken value.
 */
export function arrowFor(value: string, defaultValue: string): string {
  return value === defaultValue ? "ChevronsUpDown" : "ChevronDown";
}

/**
 * The tint a filter-row control wears when it is NOT at its default.
 *
 * The house active pair (`AGENTS.md` rule 6), tinted rather than filled,
 * because a select still has to read as a field you can open. The CALLER
 * applies it through `className`, because only the caller knows whether a
 * sibling flag also counts as off-default. One spelling for both filter rows,
 * Projects' `FilterBar` and My Tasks' `TaskToolbar`.
 */
export const OFF_DEFAULT = "border-primary/50 bg-primary/10 text-primary";

/**
 * The options a filter query keeps, with their group headings (WS-27bn R5b).
 *
 * A case-insensitive match on the label, the hint or `keywords`, so a member
 * finds a person by name or by address, when the list shows no address. An empty query keeps every option. A pure
 * function, for the reason `arrowFor` is one.
 */
export function filterOptions(
  options: readonly SelectOption[],
  query: string
): SelectOption[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...options];
  return options.filter(
    (o) =>
      o.label.toLowerCase().includes(q) ||
      (o.hint ?? "").toLowerCase().includes(q) ||
      (o.keywords ?? "").toLowerCase().includes(q)
  );
}

/**
 * The list's open state and its filter query, as ONE state (R5b-1 repair).
 *
 * ⚠️ **Every close clears the query.** A pick, Escape, a click outside and a
 * second click on the trigger all close the list. Before this reducer, the
 * last two kept the query, so the list opened again already filtered.
 */
export interface SelectPanelState {
  open: boolean;
  query: string;
}

export type SelectPanelAction =
  | { type: "toggle" }
  | { type: "close" }
  | { type: "query"; query: string };

export function selectPanelReducer(
  state: SelectPanelState,
  action: SelectPanelAction
): SelectPanelState {
  switch (action.type) {
    case "toggle":
      return { open: !state.open, query: "" };
    case "close":
      return { open: false, query: "" };
    case "query":
      return state.open ? { ...state, query: action.query } : state;
  }
}

/** One heading and its options, in the caller's order. */
export interface OptionGroup {
  /** `undefined` for options with no heading. */
  group: string | undefined;
  options: SelectOption[];
}

/**
 * The options, cut into runs of one group (R5b-1 repair). The list draws
 * each run with a group name as `role="group"` with that name as its label,
 * so a screen reader says the heading. Order is kept, so the caller keeps
 * each group together.
 */
export function groupOptions(options: readonly SelectOption[]): OptionGroup[] {
  const out: OptionGroup[] = [];
  for (const option of options) {
    const last = out.at(-1);
    if (last && last.group === option.group) last.options.push(option);
    else out.push({ group: option.group, options: [option] });
  }
  return out;
}

export interface SelectOption {
  value: string;
  label: string;
  /** Drawn after the label, muted — a count, a hint, an address. */
  hint?: string;
  /**
   * WS-27bn R5b. Words the filter matches and the list never draws, such as
   * the address of a person whose row shows only the name.
   */
  keywords?: string;
  /**
   * Indent, for a list that is really a TREE. One step per level.
   *
   * ⚠️ Not leading spaces in the label. A native `<option>` is the only
   * place that trick works, because the browser renders its text verbatim;
   * in real markup the spaces collapse and every row lines up again. The
   * project picker in `MoveTasksDialog` is what asked for this.
   */
  depth?: number;
  /**
   * Offered but not choosable, with `hint` saying why.
   *
   * Kept rather than dropped, because the row is part of the SHAPE the
   * member is reading: a folder between two projects explains the
   * indentation of the project under it. Dropping it would flatten the tree
   * into a list that no longer says what contains what.
   */
  disabled?: boolean;
  /**
   * WS-27bn R5b. A heading drawn above the first option of each group. The
   * list keeps the caller's order, so the caller keeps each group together.
   */
  group?: string;
}

export interface SelectButtonProps {
  /** What this control is, for assistive technology. */
  label: string;
  value: string;
  options: readonly SelectOption[];
  onChange: (value: string) => void;
  /**
   * The value that counts as "not filtering". Decides the arrow, and nothing
   * else — the CALLER still owns the active tint, because only it knows
   * whether a sibling flag (`unassigned`) also counts as off-default.
   */
  defaultValue?: string;
  /** The house active pair, applied by the caller. See `FilterBar`. */
  className?: string;
  /** Widest the trigger may grow. The row is `flex-wrap`; this keeps it sane. */
  widthClass?: string;
  /** A write is in flight. The trigger refuses to open. */
  disabled?: boolean;
  /**
   * Open the list on mount, for a control that IS the act of choosing.
   *
   * `TableView`'s cell editors are the case: the member has already clicked
   * the cell to start editing, and a button they must then click again to
   * open is two gestures for one decision. The native `<select>` these
   * replaced had the same flaw — `autoFocus` focuses it, it does not open
   * it — so this is the conversion fixing something on the way past.
   */
  autoOpen?: boolean;
  /**
   * The list closed — by a pick, by Escape, or by a click outside.
   *
   * A cell editor has to put the cell back afterwards, and it cannot see any
   * of those three from out here. Fired for all of them, so the caller
   * handles one event instead of guessing at three.
   */
  onClose?: () => void;
  /**
   * The list's paint layer (`AnchoredPanel`). `top` for a control that can
   * sit inside a hand-rolled overlay above `z-50`, such as the shared task
   * body inside My Tasks' `TaskFocusModal` (`z-[80]`).
   */
  layer?: PanelLayer;
  /**
   * WS-27bn R5b. A muted word before the value on the trigger, so a chip
   * reads as words: "About: Me". It also joins the accessible name.
   */
  prefix?: string;
  /**
   * WS-27bn R5b. What the trigger says, muted, when no option has the
   * value. A chip that asks for a choice says so, and does not look set.
   */
  prompt?: string;
  /**
   * WS-27bn R5b. Show a filter box when the list has MORE options than
   * this. Absent: no filter box.
   */
  filterAbove?: number;
}

export function SelectButton({
  label,
  value,
  options,
  onChange,
  defaultValue = "",
  className = "",
  widthClass = "w-[9rem]",
  disabled = false,
  autoOpen = false,
  onClose,
  layer,
  prefix,
  prompt,
  filterAbove,
}: SelectButtonProps) {
  const [{ open, query }, dispatch] = useReducer(selectPanelReducer, {
    open: autoOpen,
    query: "",
  });
  const filtering = filterAbove !== undefined && options.length > filterAbove;
  const shown = filtering ? filterOptions(options, query) : options;
  // ⚠️ A list with no filter box and no group keeps the markup it always
  // had: the panel IS the listbox. The 19 callers from before R5b pass
  // neither, so they render unchanged.
  const structured = filtering || options.some((o) => o.group !== undefined);
  const root = useRef<HTMLDivElement | null>(null);
  const listId = useId();
  /**
   * Fire `onClose` on the true→false edge, not on every render where the
   * list happens to be shut. A handler that ran on mount would close a cell
   * editor before the member had chosen anything.
   */
  const wasOpen = useRef(open);
  useEffect(() => {
    if (wasOpen.current && !open) onClose?.();
    wasOpen.current = open;
  }, [open, onClose]);

  /**
   * The trigger, as state rather than a ref, so the panel re-measures when
   * it mounts. A ref does not re-render, and the panel would then place
   * itself against `null` on the first open.
   */
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);

  const current = options.find((o) => o.value === value);

  // ⚠️ Dismiss through the shared walker, never `root.contains(target)`. A
  // control that renders elsewhere in the DOM is, by containment, OUTSIDE the
  // popover that raised it — `outsideClick.ts`'s header carries the case.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(root.current))) {
        dispatch({ type: "close" });
      }
    };
    // ⚠️ Escape closes, and focus returns to the trigger. Without the return
    // the next Tab starts from the top of the document, which on this row
    // means walking the whole filter bar again.
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        dispatch({ type: "close" });
        root.current?.querySelector("button")?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = useCallback(
    (next: string) => {
      onChange(next);
      dispatch({ type: "close" });
      root.current?.querySelector("button")?.focus();
    },
    [onChange]
  );

  return (
    <div ref={root} className={`relative ${widthClass}`}>
      <button
        ref={setTrigger}
        type="button"
        aria-label={prefix ? `${prefix} ${current?.label ?? prompt ?? label}` : label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        disabled={disabled}
        onClick={() => dispatch({ type: "toggle" })}
        className={`cc-control flex h-7 w-full items-center gap-1 rounded-md border border-border bg-card px-2 text-left text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60 ${className}`}
      >
        <span className="min-w-0 flex-1 truncate pr-px">
          {prefix ? (
            <span className="text-muted-foreground">{prefix} </span>
          ) : null}
          {current ? (
            current.label
          ) : prompt ? (
            <span className="italic text-muted-foreground">{prompt}</span>
          ) : (
            label
          )}
        </span>
        {/* ⚠️ Two heads at the default, one off it. See the header. */}
        <Icon
          name={arrowFor(value, defaultValue)}
          size={13}
          className="shrink-0 opacity-70"
        />
      </button>

      <AnchoredPanel
        anchor={trigger}
        open={open}
        layer={layer}
        className="max-h-64 w-max p-1"
        panelProps={structured ? undefined : { id: listId, role: "listbox", "aria-label": label }}
      >
        <SelectList
          label={label}
          listId={listId}
          value={value}
          options={shown}
          filtering={filtering}
          structured={structured}
          query={query}
          onQuery={(next) => dispatch({ type: "query", query: next })}
          onPick={pick}
        />
      </AnchoredPanel>
    </div>
  );
}

/** One option row. */
function OptionRow({
  option,
  selected,
  onPick,
}: {
  option: SelectOption;
  selected: boolean;
  onPick: (value: string) => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      aria-disabled={option.disabled || undefined}
      disabled={option.disabled}
      onClick={() => onPick(option.value)}
      className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs ${
        option.disabled
          ? "cursor-not-allowed text-muted-foreground"
          : "hover:bg-muted"
      } ${selected ? "bg-muted font-medium" : ""}`}
      // A tree's indent, in `rem` so it follows the member's density.
      // `px` here would stop matching the text beside it at compact.
      style={
        option.depth ? { paddingLeft: `${0.5 + option.depth * 0.75}rem` } : undefined
      }
    >
      <span className="min-w-0 flex-1 truncate pr-px">{option.label}</span>
      {option.hint ? (
        <span className="shrink-0 text-[11px] text-muted-foreground">
          {option.hint}
        </span>
      ) : null}
    </button>
  );
}

/**
 * The panel's content. Exported so a test can render it without a DOM: the
 * panel itself is a portal, which a server render cannot draw.
 *
 * ⚠️ **The filter box sits ABOVE the listbox, never inside it** (R5b-1
 * repair). A listbox holds options, and a text field inside one is a
 * control that assistive technology does not expect there. Each group is
 * `role="group"` with its heading as the label, and the heading is
 * `aria-hidden`, so a screen reader says it once.
 *
 * When `structured` is false, the caller has made the panel the listbox,
 * and this draws the option rows only, as before R5b.
 */
export function SelectList({
  label,
  listId,
  value,
  options,
  filtering,
  structured,
  query,
  onQuery,
  onPick,
}: {
  label: string;
  listId: string;
  value: string;
  options: readonly SelectOption[];
  filtering: boolean;
  structured: boolean;
  query: string;
  onQuery: (query: string) => void;
  onPick: (value: string) => void;
}) {
  if (!structured) {
    return (
      <>
        {options.map((option) => (
          <OptionRow
            key={option.value}
            option={option}
            selected={option.value === value}
            onPick={onPick}
          />
        ))}
      </>
    );
  }
  return (
    <>
      {filtering ? (
        <div className="p-1">
          <Input
            inputSize="sm"
            autoFocus
            className="w-full"
            placeholder="Type to filter"
            aria-label={`Filter ${label}`}
            aria-controls={listId}
            value={query}
            onChange={(e) => onQuery(e.target.value)}
          />
        </div>
      ) : null}
      {filtering && options.length === 0 ? (
        <p className="px-2 py-1 text-[11px] text-muted-foreground">
          Nothing matches.
        </p>
      ) : null}
      <div id={listId} role="listbox" aria-label={label}>
        {groupOptions(options).map((run, i) => {
          const rows = run.options.map((option) => (
            <OptionRow
              key={option.value}
              option={option}
              selected={option.value === value}
              onPick={onPick}
            />
          ));
          if (run.group === undefined) return <div key={`run-${i}`}>{rows}</div>;
          return (
            <div key={`run-${i}`} role="group" aria-label={run.group}>
              <p
                aria-hidden="true"
                className="px-2 pb-0.5 pt-1.5 text-[10px] font-semibold text-muted-foreground"
              >
                {run.group}
              </p>
              {rows}
            </div>
          );
        })}
      </div>
    </>
  );
}

export default SelectButton;
