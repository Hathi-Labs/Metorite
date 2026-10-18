"use client";

/**
 * The Knowledge rail (WS-61 KB-2a, KB-3b and KB-2e, `knowledge_base.md`
 * §11.1 and §21.5).
 *
 * Two entries today, Search (KB-3b) and Spaces (KB-2a). Files, Sources and
 * Review join them with KB-2c, KB-7a and KB-5, and each is ABSENT until
 * then, never a stub. Search sits first, above the tree, so it stays in one
 * place while a space opens. Under Spaces, each space the member can open is
 * a row, and the open space shows its page tree. Every row is a `RailRow`
 * (AGENTS.md rule 12), as the Projects tree and the CRM rail draw theirs.
 *
 * The tree (KB-2e). A writer of the space gets, on each page row:
 *
 *   - a `+` and a `…` on hover and on keyboard focus. The `+` opens the New
 *     page form with the row as the parent. The `…` holds Copy link, New
 *     sub-page and, for a manager of the space, Archive. On a phone the row
 *     shows one `…` at all times;
 *   - a drag. A drop on the middle of a row puts the page under it, and a
 *     drop on an edge puts the page above or below it. `lib/treeDrop.ts`
 *     plans the move, and a page cannot drop into its own subtree. A row
 *     that holds children opens when a drag rests on it.
 *
 * A reader of the space gets none of these: the rows are not draggable and
 * carry no actions. ArrowUp and ArrowDown move the focus between the rows on
 * screen (`moveRailFocus`), and ArrowRight and ArrowLeft open and close a
 * row, as `RailRow` does.
 *
 * ⚠️ Props only, no context. On a phone the rail draws inside the shell's
 * drawer, which renders above the Knowledge layout, so a context read here
 * would find nothing. The page owns where the rail goes. Fence:
 * `src/lib/railRows.test.ts`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import Icon, { themedIcon } from "@/components/Icon";
import Button from "@/components/ui/Button";
import RailRow from "@/components/ui/RailRow";
import { moveRailFocus } from "@/components/ui/RailRow";
import { SkeletonTree } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { invalidate, put } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import { KNOWLEDGE_CACHE, KnowledgeError, listPages, movePage, pagesKey } from "../lib/api";
import { pageIcon, spaceIcon } from "../lib/labels";
import { KNOWLEDGE_HOME, KNOWLEDGE_SEARCH, pageHref, spaceHref } from "../lib/links";
import { ancestorIds, buildTree, type TreeNode } from "../lib/tree";
import { applyMove, dropZoneAt, planDrop, targetFor, type PageDropTarget } from "../lib/treeDrop";
import type { PageSummary, Space } from "../lib/types";

const ICON = "h-3.5 w-3.5 shrink-0";

/** How long a drag rests on a closed row before the row opens, in ms. */
export const DRAG_OPEN_MS = 600;

/** The parent a new sub-page names. */
export interface SubPageParent {
  id: string;
  title: string;
  spaceId: string;
}

export interface KnowledgeRailProps {
  spaces: readonly Space[];
  /** The space on screen: `?space=`, or the space of the open page. */
  selectedSpaceId: string | null;
  /** The open page, on `/knowledge/pages/<id>`. */
  selectedPageId: string | null;
  /** True on `/knowledge` with no space chosen. */
  atHome: boolean;
  /** True on `/knowledge/search`. */
  atSearch: boolean;
  /** Go to an address. On a phone it also closes the drawer. */
  onNavigate: (href: string) => void;
  /** Open the New page form under a row. Absent: the rows offer no `+`. */
  onNewSubPage?: (parent: SubPageParent) => void;
  /** Ask to archive a page. The layout asks first. Absent: no Archive. */
  onArchive?: (page: PageSummary) => void;
}

/** True when the member can move and add pages in this space. */
export function canWriteSpace(space: Pick<Space, "level">): boolean {
  return space.level === "write" || space.level === "manage";
}

export default function KnowledgeRail({
  spaces,
  selectedSpaceId,
  selectedPageId,
  atHome,
  atSearch,
  onNavigate,
  onNewSubPage,
  onArchive,
}: KnowledgeRailProps) {
  return (
    <div className="flex flex-col gap-0.5" onKeyDown={moveRailFocus}>
      <RailRow
        label="Search"
        tier="group"
        selected={atSearch}
        icon={<Icon name="Search" className={ICON} />}
        buttonProps={{ "aria-current": atSearch ? "page" : undefined }}
        onSelect={() => onNavigate(KNOWLEDGE_SEARCH)}
      />
      <RailRow
        label="Spaces"
        tier="group"
        selected={atHome}
        icon={<Icon name="LayoutGrid" className={ICON} />}
        buttonProps={{ "aria-current": atHome ? "page" : undefined }}
        onSelect={() => onNavigate(KNOWLEDGE_HOME)}
      />
      {spaces.map((space) => (
        <SpaceBranch
          key={space.id}
          space={space}
          selected={space.id === selectedSpaceId}
          selectedPageId={space.id === selectedSpaceId ? selectedPageId : null}
          onNavigate={onNavigate}
          onNewSubPage={onNewSubPage}
          onArchive={onArchive}
        />
      ))}
    </div>
  );
}

/** The drag of one space's tree. `null` for a reader. */
interface TreeDrag {
  draggingId: string | null;
  over: PageDropTarget | null;
  begin: (id: string) => void;
  /** The target under the pointer, or `null` when it is no legal one. */
  hover: (target: PageDropTarget | null) => void;
  drop: (target: PageDropTarget) => void;
  end: () => void;
  /** True when a drop of the dragged page on `target` would move it. */
  legal: (target: PageDropTarget) => boolean;
}

/** The acts of one row's `+` and `…`. `null` for a reader. */
interface RowActs {
  newSubPage?: (page: PageSummary) => void;
  copyLink: (page: PageSummary) => void;
  archive?: (page: PageSummary) => void;
}

function sameTarget(a: PageDropTarget | null, b: PageDropTarget | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.kind === "onto" && b.kind === "onto") return a.pageId === b.pageId;
  if (a.kind === "between" && b.kind === "between") return a.parentId === b.parentId && a.index === b.index;
  return false;
}

function SpaceBranch({
  space,
  selected,
  selectedPageId,
  onNavigate,
  onNewSubPage,
  onArchive,
}: {
  space: Space;
  selected: boolean;
  selectedPageId: string | null;
  onNavigate: (href: string) => void;
  onNewSubPage?: (parent: SubPageParent) => void;
  onArchive?: (page: PageSummary) => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(selected);
  // A space that becomes the one on screen opens. Set during render, the
  // adjust-state-on-a-prop-change pattern, so no frame shows it closed.
  const [wasSelected, setWasSelected] = useState(selected);
  if (selected !== wasSelected) {
    setWasSelected(selected);
    if (selected) setOpen(true);
  }

  const key = pagesKey(space.id);
  const pages = useCachedResource<PageSummary[]>(open ? key : null, () => listPages(space.id));
  const list = useMemo(() => pages.data ?? [], [pages.data]);
  const tree = useMemo(() => buildTree(list), [list]);

  // The open folds of the tree. The ancestors of the open page are open, so
  // the member sees where the page sits.
  const [folds, setFolds] = useState<Set<string>>(() => new Set());
  const ancestors = useMemo(() => ancestorIds(list, selectedPageId), [list, selectedPageId]);
  const ancestorKey = ancestors.join(",");
  const [seenAncestors, setSeenAncestors] = useState("");
  if (ancestorKey !== seenAncestors) {
    setSeenAncestors(ancestorKey);
    if (ancestors.some((id) => !folds.has(id))) setFolds(new Set([...folds, ...ancestors]));
  }
  const toggleFold = (id: string) =>
    setFolds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // ── The drag (KB-2e) ──────────────────────────────────────────────────────
  const writer = canWriteSpace(space);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [over, setOver] = useState<PageDropTarget | null>(null);
  const openTimer = useRef<{ id: string; timer: ReturnType<typeof setTimeout> } | null>(null);
  const stopOpenTimer = useCallback(() => {
    if (openTimer.current) clearTimeout(openTimer.current.timer);
    openTimer.current = null;
  }, []);
  useEffect(() => stopOpenTimer, [stopOpenTimer]);

  const end = useCallback(() => {
    stopOpenTimer();
    setDraggingId(null);
    setOver(null);
  }, [stopOpenTimer]);

  const hasChildren = useCallback(
    (id: string) => list.some((p) => p.parent_id === id && p.id !== id),
    [list],
  );

  const move = useCallback(
    async (movingId: string, target: PageDropTarget) => {
      const plan = planDrop(list, movingId, target);
      if (!("move" in plan)) return;
      const body = plan.move;
      // Draw the drop at once. The read after the write replaces it.
      put(key, applyMove(list, movingId, body));
      if ("parent_id" in body && body.parent_id) {
        const parent = body.parent_id;
        setFolds((prev) => (prev.has(parent) ? prev : new Set([...prev, parent])));
      }
      try {
        await movePage(movingId, body);
      } catch (err) {
        const reason = err instanceof KnowledgeError ? err.message : String(err);
        toast.show({
          key: `kb-move:${movingId}`,
          variant: "error",
          title: "Could not move the page",
          description: `${reason} The tree shows the order that the server holds.`,
        });
      } finally {
        invalidate(KNOWLEDGE_CACHE);
      }
    },
    [key, list, toast],
  );

  const drag: TreeDrag | null = useMemo(
    () =>
      writer
        ? {
            draggingId,
            over,
            begin: (id) => {
              setDraggingId(id);
              setOver(null);
            },
            hover: (target) => {
              setOver((prev) => (sameTarget(prev, target) ? prev : target));
              // A drag that rests on a closed row with children opens it.
              const id = target?.kind === "onto" ? target.pageId : null;
              if (!id || folds.has(id) || !hasChildren(id)) {
                if (openTimer.current && openTimer.current.id !== id) stopOpenTimer();
                return;
              }
              if (openTimer.current?.id === id) return;
              stopOpenTimer();
              openTimer.current = {
                id,
                timer: setTimeout(() => {
                  openTimer.current = null;
                  setFolds((prev) => new Set([...prev, id]));
                }, DRAG_OPEN_MS),
              };
            },
            drop: (target) => {
              const movingId = draggingId;
              end();
              if (movingId) void move(movingId, target);
            },
            end,
            legal: (target) => draggingId !== null && "move" in planDrop(list, draggingId, target),
          }
        : null,
    [writer, draggingId, over, folds, hasChildren, stopOpenTimer, end, move, list],
  );

  const acts: RowActs | null = useMemo(
    () =>
      writer
        ? {
            newSubPage: onNewSubPage
              ? (page) => onNewSubPage({ id: page.id, title: page.title, spaceId: space.id })
              : undefined,
            copyLink: (page) => {
              void (async () => {
                try {
                  await navigator.clipboard.writeText(new URL(pageHref(page.id), window.location.origin).href);
                  toast.show({ key: `kb-link:${page.id}`, title: "Link copied" });
                } catch {
                  toast.show({
                    key: `kb-link:${page.id}`,
                    variant: "error",
                    title: "Could not copy the link",
                    description: "The browser refused the clipboard. Copy the address from the page instead.",
                  });
                }
              })();
            },
            archive: space.level === "manage" && onArchive ? onArchive : undefined,
          }
        : null,
    [writer, onNewSubPage, onArchive, space.id, space.level, toast],
  );

  const opens = space.page_count > 0 || tree.length > 0;
  return (
    <>
      <RailRow
        label={space.name}
        depth={1}
        guides
        tree
        tier="group"
        selected={selected && !selectedPageId}
        icon={<Icon name={spaceIcon(space)} className={ICON} />}
        meta={
          space.page_count ? (
            <>
              {space.page_count}
              <span className="sr-only"> pages</span>
            </>
          ) : null
        }
        expand={opens ? { expanded: open, onToggle: () => setOpen((v) => !v) } : undefined}
        buttonProps={{ "aria-current": selected && !selectedPageId ? "page" : undefined }}
        onSelect={() => {
          setOpen(true);
          onNavigate(spaceHref(space.id));
        }}
      />
      {open && pages.loading ? <SkeletonTree count={3} /> : null}
      {open
        ? tree.map((node, index) => (
            <PageBranch
              key={node.page.id}
              node={node}
              depth={2}
              parentId={null}
              index={index}
              folds={folds}
              onToggle={toggleFold}
              selectedPageId={selectedPageId}
              onNavigate={onNavigate}
              drag={drag}
              acts={acts}
            />
          ))
        : null}
    </>
  );
}

function PageBranch({
  node,
  depth,
  parentId,
  index,
  folds,
  onToggle,
  selectedPageId,
  onNavigate,
  drag,
  acts,
}: {
  node: TreeNode;
  depth: number;
  /** The parent as drawn, and the row's place among its drawn siblings. */
  parentId: string | null;
  index: number;
  folds: Set<string>;
  onToggle: (id: string) => void;
  selectedPageId: string | null;
  onNavigate: (href: string) => void;
  drag: TreeDrag | null;
  acts: RowActs | null;
}) {
  const { page, children } = node;
  const expanded = folds.has(page.id);
  const selected = page.id === selectedPageId;
  const [menu, setMenu] = useState<{ x: number; y: number; keyboard: boolean } | null>(null);
  const menuButton = useRef<HTMLButtonElement | null>(null);

  const dragging = drag?.draggingId === page.id;
  const over = drag?.over ?? null;
  const litInto = over?.kind === "onto" && over.pageId === page.id;
  // An open row with children draws no line under itself: the gap after it
  // is under its whole branch, and the line there is its next sibling's.
  const open = expanded && children.length > 0;
  const litBefore = over?.kind === "between" && over.parentId === parentId && over.index === index;
  const litAfter =
    !open && over?.kind === "between" && over.parentId === parentId && over.index === index + 1;

  // In rem, as the row's indent is, so it follows the density setting.
  const lineIndent = { left: `calc(${depth}rem + 0.5rem)` };

  const targetAt = (event: React.DragEvent<HTMLElement>): PageDropTarget => {
    const box = event.currentTarget.getBoundingClientRect();
    return targetFor(dropZoneAt(event.clientY - box.top, box.height), page.id, parentId, index, open);
  };

  const dragProps = drag
    ? {
        draggable: true,
        onDragStart: (event: React.DragEvent<HTMLDivElement>) => {
          event.dataTransfer.effectAllowed = "move";
          // Firefox starts no drag with no payload. The drop reads the state.
          event.dataTransfer.setData("text/plain", page.id);
          drag.begin(page.id);
        },
        onDragEnd: () => drag.end(),
        onDragOver: (event: React.DragEvent<HTMLDivElement>) => {
          if (!drag.draggingId) return;
          const target = targetAt(event);
          // A drop the plan refuses is no drop target, so the pointer says no.
          if (!drag.legal(target)) {
            drag.hover(null);
            return;
          }
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          drag.hover(target);
        },
        onDrop: (event: React.DragEvent<HTMLDivElement>) => {
          if (!drag.draggingId) return;
          event.preventDefault();
          const target = targetAt(event);
          if (drag.legal(target)) drag.drop(target);
          else drag.end();
        },
      }
    : {};

  const openMenu = (button: HTMLButtonElement, keyboard: boolean) => {
    menuButton.current = button;
    const rect = button.getBoundingClientRect();
    setMenu({ x: rect.left, y: rect.bottom + 2, keyboard });
  };

  const items: CtxItem[] = acts
    ? [
        {
          kind: "item",
          label: "Copy link",
          icon: themedIcon("Link"),
          onSelect: () => acts.copyLink(page),
        },
        ...(acts.newSubPage
          ? [
              {
                kind: "item" as const,
                label: "New sub-page",
                icon: themedIcon("FilePlus"),
                onSelect: () => acts.newSubPage?.(page),
              },
            ]
          : []),
        ...(acts.archive
          ? [
              { kind: "sep" as const },
              {
                kind: "item" as const,
                label: "Archive",
                icon: themedIcon("Archive"),
                danger: true,
                onSelect: () => acts.archive?.(page),
              },
            ]
          : []),
      ]
    : [];

  const moreButton = (phone: boolean) => (
    <Button
      variant="ghost"
      size={phone ? "none" : "icon-xs"}
      radius="keep"
      className={phone ? "h-10 w-10 rounded" : "rounded"}
      icon="MoreHorizontal"
      aria-label={`Actions for ${page.title}`}
      aria-haspopup="menu"
      aria-expanded={menu !== null}
      title={phone ? undefined : "Actions"}
      onClick={(event) => openMenu(event.currentTarget, event.detail === 0)}
    />
  );

  return (
    <>
      <div className="relative">
        {/* The drop line. Drawn on the row, so it stays in step with the list.
            It starts where this row's icon starts, so it shows the depth of
            the place it means: one `w-4` step per level, after the `px-2`. */}
        {litBefore ? (
          <div
            data-drop-line="before"
            style={lineIndent}
            className="pointer-events-none absolute right-0 top-0 z-10 h-0.5 bg-primary"
          />
        ) : null}
        {litAfter ? (
          <div
            data-drop-line="after"
            style={lineIndent}
            className="pointer-events-none absolute bottom-0 right-0 z-10 h-0.5 bg-primary"
          />
        ) : null}
        <RailRow
          label={page.title}
          depth={depth}
          guides
          tree
          selected={selected && !litInto}
          className={`${dragging ? "opacity-40" : ""} ${litInto ? "ring-1 ring-primary" : ""}`}
          icon={<Icon name={pageIcon(page)} className={ICON} />}
          meta={page.state === "draft" ? <span className="text-[10px]">Draft</span> : null}
          expand={children.length ? { expanded, onToggle: () => onToggle(page.id) } : undefined}
          buttonProps={{ "aria-current": selected ? "page" : undefined }}
          rowProps={{ ...dragProps, "data-page-id": page.id }}
          pinned={menu !== null}
          onSelect={() => {
            if (children.length && !expanded) onToggle(page.id);
            onNavigate(pageHref(page.id));
          }}
          actions={
            acts ? (
              <>
                {moreButton(false)}
                {acts.newSubPage ? (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    radius="keep"
                    className="rounded"
                    icon="Plus"
                    aria-label={`New sub-page under ${page.title}`}
                    title="New sub-page"
                    onClick={() => acts.newSubPage?.(page)}
                  />
                ) : null}
              </>
            ) : undefined
          }
          phoneActions={acts ? moreButton(true) : undefined}
        />
      </div>
      {menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={items}
          autoFocus={menu.keyboard}
          onClose={() => {
            setMenu(null);
            // The focus goes back to the `…`, so the keyboard keeps its place.
            if (menu.keyboard) menuButton.current?.focus();
          }}
        />
      ) : null}
      {expanded
        ? children.map((child, childIndex) => (
            <PageBranch
              key={child.page.id}
              node={child}
              depth={depth + 1}
              parentId={page.id}
              index={childIndex}
              folds={folds}
              onToggle={onToggle}
              selectedPageId={selectedPageId}
              onNavigate={onNavigate}
              drag={drag}
              acts={acts}
            />
          ))
        : null}
    </>
  );
}
