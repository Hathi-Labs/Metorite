"use client";

/**
 * The project picker — search a tree, or browse it one space at a time.
 *
 * Owner ask, 2026-10-10: *"The system for selecting the project into which I
 * want to fold my task is difficult to find … Wouldn't it be better with some
 * sort of accordion system or some standard UX for a use case like this?"*
 *
 * The standard answer, and the one this draws:
 *
 * - **A search box first, focused.** Most picks are a name the member already
 *   knows. A word may match the project OR its space and folder, so
 *   "fracktory fdm" finds FDM PRINTS. Each result names its path on a second
 *   line, because a flat result list loses the tree that told two
 *   same-named projects apart.
 * - **A tree whose spaces start closed.** Browsing opens one space at a time.
 *   A folder is an accordion header that opens, never a greyed row with an
 *   excuse beside it. The path to the current pick and to a suggestion starts
 *   open, and a short tree starts fully open (`OPEN_ALL_BELOW`).
 * - **The keyboard of a listbox.** Up and Down move (`lib/cursor.ts`, the
 *   grammar both apps share), Enter picks (or opens a folder) and, with no
 *   cursor while searching, takes the top match. Right opens, Left closes or
 *   climbs to the parent.
 *
 * ## Inline, not a popover
 *
 * It renders where it is put. The Move dialog and Clarify draw it in place,
 * and the capture chip hangs it from `AnchoredPanel`. Inside a `Modal` a
 * portalled panel would sit outside Base UI's focus trap, and a search box
 * that loses focus to the trap is a search box that does not work.
 *
 * Every decision lives in `lib/pickerTree.ts`, which `pickerTree.test.ts`
 * covers. This file draws.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Input from "@/components/ui/Input";
import { accentForSlot } from "@/lib/categorical";
import { stepCursor } from "@/lib/cursor";

import {
  type PickRule,
  type PickerNode,
  ancestorIds,
  initialExpanded,
  matchesTokens,
  pathLabel,
  pickerNodes,
  queryTokens,
  searchRows,
  visibleRows,
} from "../lib/pickerTree";
import { LEVEL_ICONS, type ProjectNode, spaceMarker } from "../lib/tree";

/** A flat row drawn above the tree — "Inbox", an Area. */
export interface PickerLeadRow {
  value: string;
  label: string;
  icon: React.ReactNode;
  /** Drawn quieter, for "leave it loose" rows. */
  muted?: boolean;
  /** Why this row cannot be picked, as a tree row's refusal. Null when it can. */
  refusal?: string | null;
}

/** A group of flat rows above the tree, with an optional heading and footer. */
export interface PickerLeadGroup {
  key: string;
  label?: string;
  rows: readonly PickerLeadRow[];
  /** Drawn under the rows, such as "New Area…". Hidden while searching. */
  footer?: React.ReactNode;
}

export interface ProjectPickerProps {
  /** The company tree, as `GET /projects/nodes` serves it. */
  roots: readonly ProjectNode[];
  /**
   * Why a node may not take what is being placed (`pickerTree.PickRule`).
   * Default: the task rule, where a folder is refused. Keep it stable
   * (`useCallback`): a new rule is a new tree, and re-seeds what is open.
   */
  rule?: PickRule;
  /** A quiet word after a row's name, by id — "where it is now". */
  markers?: Readonly<Record<string, string>>;
  /** The current pick: a node id, a lead row's value, or nothing. */
  value: string | null | undefined;
  onPick: (value: string) => void;
  /** A row marked "suggested". Never a pre-selection (audit 2026-09-24). */
  suggestedId?: string;
  /**
   * A query typed somewhere else — the `#` in the capture box. When set, the
   * picker draws no search box of its own, because the member is typing in
   * another one.
   */
  query?: string | null;
  /** Flat groups above the tree. They filter by the query too. */
  lead?: readonly PickerLeadGroup[];
  /** The heading over the tree. None by default. */
  treeLabel?: string;
  /** What an empty tree says. `null` says nothing (a host with its own rows). */
  emptyTree?: string | null;
  /** Focus the search box on mount. */
  autoFocus?: boolean;
  /**
   * The search box, for a host that aims focus at it. A `Modal` focuses its
   * first tabbable element, which is the close button, so the Move dialog
   * passes this as `initialFocus`.
   */
  inputRef?: React.Ref<HTMLInputElement>;
  /** The scrolling list's height cap. */
  listClass?: string;
  /** For assistive technology: what this list picks. */
  label: string;
  /**
   * Escape in the search box. A popover host closes on it. Inside a `Modal`
   * leave it unset, and the dialog takes Escape as it always does.
   */
  onEscape?: () => void;
}

/** The picker has a cursor and no multi-select, so the sweep stays dormant. */
const NO_SELECTION: ReadonlySet<string> = new Set();

type Item =
  | { kind: "lead"; key: string; row: PickerLeadRow }
  | { kind: "node"; key: string; node: PickerNode; mark: [number, number] | null };

export function ProjectPicker({
  roots,
  rule,
  markers,
  value,
  onPick,
  suggestedId,
  query: outsideQuery,
  lead = [],
  treeLabel,
  emptyTree = "No projects you can file into.",
  autoFocus = false,
  inputRef,
  listClass = "max-h-72",
  label,
  onEscape,
}: ProjectPickerProps) {
  const nodes = useMemo(() => pickerNodes(roots, rule), [roots, rule]);
  const [ownQuery, setOwnQuery] = useState("");
  const external = outsideQuery !== undefined && outsideQuery !== null;
  const query = external ? outsideQuery : ownQuery;
  const tokens = queryTokens(query);
  const searching = tokens.length > 0;

  // Which nodes are open. Seeded per tree from the pick and the suggestion.
  // A member's own opening and closing is theirs after that, so a re-render
  // never undoes it. A pick or suggestion that arrives LATER (Clarify's
  // proposal lands after mount) opens the path to it and closes nothing.
  // Set during render, React's pattern for state derived from a prop.
  const focusKey = `${value ?? ""}|${suggestedId ?? ""}`;
  const [opened, setOpened] = useState(() => ({
    nodes,
    focusKey,
    expanded: initialExpanded(nodes, [value, suggestedId]),
  }));
  if (opened.nodes !== nodes) {
    setOpened({ nodes, focusKey, expanded: initialExpanded(nodes, [value, suggestedId]) });
  } else if (opened.focusKey !== focusKey) {
    setOpened({
      nodes,
      focusKey,
      expanded: new Set([...opened.expanded, ...ancestorIds(nodes, [value, suggestedId])]),
    });
  }
  const expanded = opened.expanded;

  const toggle = (id: string, open?: boolean) =>
    setOpened((was) => {
      const next = new Set(was.expanded);
      if (open ?? !next.has(id)) next.add(id);
      else next.delete(id);
      return { ...was, expanded: next };
    });

  const groups = lead
    .map((g) => ({ ...g, rows: g.rows.filter((r) => matchesTokens(tokens, r.label)) }))
    .filter((g) => !searching || g.rows.length > 0);
  const treeItems: Item[] = searching
    ? searchRows(nodes, query ?? "").map((h) => ({
        kind: "node",
        key: h.node.id,
        node: h.node,
        mark: h.mark,
      }))
    : visibleRows(nodes, expanded).map((n) => ({ kind: "node", key: n.id, node: n, mark: null }));
  const items: Item[] = [
    ...groups.flatMap((g) => g.rows.map((row): Item => ({ kind: "lead", key: row.value, row }))),
    ...treeItems,
  ];

  // The keyboard cursor. It follows the ROW rather than an index into the
  // list, so a row that opens above it does not move the cursor onto a
  // stranger. It belongs to one query: a new word starts it fresh.
  const [held, setHeld] = useState<{ query: string; key: string | null }>({
    query: query ?? "",
    key: null,
  });
  const cursor = held.query === (query ?? "") ? held.key : null;
  const setCursor = (key: string | null) => setHeld({ query: query ?? "", key });
  const at = cursor ? items.findIndex((i) => i.key === cursor) : -1;

  const listId = useId();
  const optionId = (key: string) => `${listId}-${key}`;
  const listRef = useRef<HTMLDivElement>(null);

  // Bring the pick (or the suggestion) into the list's view once it resolves
  // in the tree. The path to it is open, and an open path that ends below the
  // fold is the suggestion nobody sees. A row already in view stays put, so a
  // click never makes the list jump. The LIST scrolls, never the page or the
  // dialog, so this sets `scrollTop` rather than calling `scrollIntoView`.
  const target = [value, suggestedId].find((id) => id && nodes.some((n) => n.id === id)) ?? null;
  useEffect(() => {
    const list = listRef.current;
    const row = target ? document.getElementById(optionId(target)) : null;
    if (!list || !row) return;
    const top = row.offsetTop;
    if (top >= list.scrollTop && top + row.offsetHeight <= list.scrollTop + list.clientHeight) return;
    list.scrollTop = Math.max(0, top - list.clientHeight / 2);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, nodes]);
  useEffect(() => {
    if (!cursor) return;
    document.getElementById(optionId(cursor))?.scrollIntoView({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor]);

  // ⚠️ A refused row says WHY in one place, under the list, and only for the
  // row the member reached for (owner, 2026-10-10). The reason on every row
  // was the wall of text. Hiding refused rows would change the tree's shape,
  // so they stay, muted, with the reason one click or one arrow away.
  const [tried, setTried] = useState<string | null>(null);
  const refusalOf = (item: Item | undefined) =>
    !item
      ? null
      : item.kind === "lead"
        ? item.row.refusal
          ? { name: item.row.label, refusal: item.row.refusal }
          : null
        : item.node.refusal
          ? { name: item.node.name, refusal: item.node.refusal }
          : null;
  const explained =
    refusalOf(at >= 0 ? items[at] : undefined) ??
    refusalOf(items.find((i) => i.key === tried));

  const choose = (item: Item) => {
    const refused = refusalOf(item);
    // A good pick answers the question the last refusal raised, so it goes.
    setTried(refused ? item.key : null);
    if (item.kind === "lead") {
      if (!refused) onPick(item.row.value);
    } else if (item.node.pickable) onPick(item.node.id);
    else if (item.node.hasChildren) toggle(item.node.id);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const current = at >= 0 ? items[at] : null;
    switch (e.key) {
      case "Escape":
        if (!onEscape) return;
        e.preventDefault();
        e.stopPropagation();
        onEscape();
        return;
      case "ArrowDown":
      case "ArrowUp":
      case "Enter": {
        // Enter with no cursor while searching takes the top match, the way
        // a search box that finds one thing should.
        if (e.key === "Enter" && at < 0 && searching && items.length > 0) {
          e.preventDefault();
          choose(items[0]);
          return;
        }
        const step = stepCursor(
          items.map((i) => i.key),
          { cursor: at, anchor: null, selection: NO_SELECTION },
          e.key,
          false,
        );
        if (!step) return;
        e.preventDefault();
        const opened = step.open ? items.find((i) => i.key === step.open) : undefined;
        if (opened) choose(opened);
        else setCursor(items[step.cursor]?.key ?? null);
        return;
      }
      case "ArrowRight":
        if (searching || current?.kind !== "node" || !current.node.hasChildren) return;
        e.preventDefault();
        toggle(current.node.id, true);
        return;
      case "ArrowLeft": {
        if (searching || current?.kind !== "node") return;
        e.preventDefault();
        const n = current.node;
        if (n.hasChildren && expanded.has(n.id)) toggle(n.id, false);
        else if (n.parentId) setCursor(n.parentId);
        return;
      }
    }
  };

  return (
    <div className="flex flex-col gap-1.5">
      {external ? null : (
        <Input
          ref={inputRef}
          inputSize="md"
          icon="Search"
          autoFocus={autoFocus}
          value={ownQuery}
          onChange={(e) => setOwnQuery(e.target.value)}
          // On the search box only. A key on a footer button ("New Area…")
          // is that button's, and must not pick the cursor row.
          onKeyDown={onKeyDown}
          placeholder="Search projects, spaces and folders…"
          aria-label={`Search — ${label}`}
          role="combobox"
          aria-haspopup="tree"
          aria-expanded
          aria-controls={listId}
          aria-activedescendant={cursor ? optionId(cursor) : undefined}
          autoComplete="off"
        />
      )}

      <div
        id={listId}
        ref={listRef}
        role="tree"
        aria-label={label}
        // `relative` makes the list its rows' offset parent, for the scroll above.
        className={`${listClass} relative flex flex-col gap-0.5 overflow-y-auto`}
      >
        {groups.map((g) => (
          <div key={g.key} role="group" aria-label={g.label} className="flex flex-col gap-0.5">
            {g.label ? <GroupLabel>{g.label}</GroupLabel> : null}
            {g.rows.map((row) => (
                <Row
                  key={row.value}
                  id={optionId(row.value)}
                  active={cursor === row.value}
                  selected={value === row.value}
                  muted={row.muted}
                  refused={Boolean(row.refusal)}
                  title={row.refusal ?? undefined}
                  icon={row.icon}
                  marker={markers?.[row.value]}
                  suggested={value !== row.value && suggestedId === row.value}
                  onClick={() => choose({ kind: "lead", key: row.value, row })}
                >
                  <span className="min-w-0 flex-1 truncate">{row.label}</span>
                </Row>
            ))}
            {searching ? null : g.footer}
          </div>
        ))}

        {treeLabel && (!searching || treeItems.length > 0) ? (
          <GroupLabel>{treeLabel}</GroupLabel>
        ) : null}

        {treeItems.map((item) => {
          if (item.kind !== "node") return null;
          const n = item.node;
          const open = expanded.has(n.id);
          return (
            <Row
              key={n.id}
              id={optionId(n.id)}
              active={cursor === n.id}
              selected={value === n.id}
              refused={!n.pickable}
              marker={markers?.[n.id]}
              depth={searching ? 0 : n.depth}
              level={searching ? 1 : n.depth + 1}
              expander={
                searching ? undefined : n.hasChildren ? (
                  <button
                    type="button"
                    tabIndex={-1}
                    aria-label={`${open ? "Close" : "Open"} ${n.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle(n.id);
                    }}
                    className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <Icon name={open ? "ChevronDown" : "ChevronRight"} size={13} />
                  </button>
                ) : (
                  <span aria-hidden className="w-5 shrink-0" />
                )
              }
              ariaExpanded={!searching && n.hasChildren ? open : undefined}
              title={n.refusal ?? undefined}
              icon={<NodeIcon node={n} open={open} />}
              onClick={() => choose(item)}
              suggested={value !== n.id && suggestedId === n.id}
            >
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate">
                  <Marked text={n.name} mark={item.mark} />
                </span>
                {searching && n.path.length > 0 ? (
                  <span className="truncate text-[10px] text-muted-foreground">
                    {pathLabel(n.path)}
                  </span>
                ) : null}
              </span>
            </Row>
          );
        })}

        {searching && items.length === 0 ? (
          <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
            Nothing matches “{query?.trim()}”.
          </p>
        ) : !searching && nodes.length === 0 && emptyTree ? (
          <p className="px-2 py-1.5 text-[11px] text-muted-foreground">{emptyTree}</p>
        ) : null}
      </div>

      {/* `role="status"`, so a keyboard member hears the reason the moment
          the cursor reaches a refused row. */}
      <p role="status" className="min-h-4 px-1 text-[11px] text-muted-foreground">
        {explained ? (
          <>
            <Icon name="Info" className="mr-1 -mt-0.5 inline h-3 w-3" />
            <span className="font-medium text-foreground">{explained.name}</span> cannot take
            it. {explained.refusal}
          </>
        ) : null}
      </p>
    </div>
  );
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    // `aria-hidden`: a tree holds tree items and groups, and the group's
    // `aria-label` already says this to assistive technology.
    <p
      aria-hidden
      className="px-2 pt-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"
    >
      {children}
    </p>
  );
}

/** The glyph for a node's level. A space wears its own marker, as in the sidebar. */
export function NodeIcon({ node, open = false }: { node: PickerNode; open?: boolean }) {
  if (node.level === "space") {
    const marker = spaceMarker(node.node);
    return (
      <Icon
        name={marker.icon}
        className={`h-3.5 w-3.5 shrink-0 ${accentForSlot(marker.slot).text}`}
      />
    );
  }
  if (node.level === "folder") {
    return (
      <Icon
        name={open ? "FolderOpen" : "Folder"}
        className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
      />
    );
  }
  // Not `LEVEL_ICONS.project` ("Kanban"): at 14px its three bare bars read as
  // a stray glyph beside the name. The boxed board reads as a board.
  return (
    <Icon
      name={node.level === "subproject" ? LEVEL_ICONS.subproject : "SquareKanban"}
      className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
    />
  );
}

/** The name, with the part the query hit in the foreground weight. */
function Marked({ text, mark }: { text: string; mark: [number, number] | null }) {
  if (!mark) return <>{text}</>;
  const [from, to] = mark;
  return (
    <>
      {text.slice(0, from)}
      <span className="font-semibold text-foreground">{text.slice(from, to)}</span>
      {text.slice(to)}
    </>
  );
}

function Row({
  id,
  active,
  selected,
  muted,
  refused,
  depth = 0,
  level = 1,
  marker,
  expander,
  ariaExpanded,
  suggested,
  title,
  icon,
  onClick,
  children,
}: {
  id: string;
  active: boolean;
  selected: boolean;
  muted?: boolean;
  /** Not pickable: drawn muted. A folder in the task picker, any refusal in another. */
  refused?: boolean;
  /** A quiet word after the name. */
  marker?: string;
  depth?: number;
  /** `aria-level`, 1-based. The browse depth, or 1 for a flat row. */
  level?: number;
  expander?: React.ReactNode;
  ariaExpanded?: boolean;
  suggested?: boolean;
  title?: string;
  icon: React.ReactNode;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      id={id}
      // A tree item, not an option: a folder OPENS, and `aria-expanded` is
      // a tree item's state. A folder is never `aria-disabled` either. It is
      // the header its projects hang from, and it does something on click.
      role="treeitem"
      aria-level={level}
      aria-selected={selected}
      aria-expanded={ariaExpanded}
      title={title}
      onClick={onClick}
      // A tree's indent, in `rem` so it follows the member's density.
      style={depth ? { paddingLeft: `${0.25 + depth * 1}rem` } : undefined}
      className={[
        "tech-transition flex w-full cursor-pointer items-center gap-1.5 rounded-md px-1 py-1 text-left text-xs",
        selected
          ? "bg-primary/10 text-primary"
          : refused || muted
            ? "text-muted-foreground hover:bg-muted hover:text-foreground"
            : "text-foreground hover:bg-muted",
        active && !selected ? "bg-muted" : "",
        active ? "ring-1 ring-primary/40" : "",
      ].join(" ")}
    >
      {/* A flat row keeps the chevron's column, so its icon lines up with
          the tree's icons below it. */}
      {expander ?? <span aria-hidden className="w-5 shrink-0" />}
      {icon}
      {children}
      {marker ? (
        <span className="shrink-0 text-[10px] text-muted-foreground">{marker}</span>
      ) : null}
      {suggested ? (
        <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-[9px] font-medium uppercase text-muted-foreground">
          suggested
        </span>
      ) : null}
      {selected ? <Icon name="Check" className="h-3.5 w-3.5 shrink-0" /> : null}
    </div>
  );
}

export default ProjectPicker;
