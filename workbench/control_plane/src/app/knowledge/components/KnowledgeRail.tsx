"use client";

/**
 * The Knowledge rail (WS-61 KB-2a and KB-3b, `knowledge_base.md` §11.1).
 *
 * Two entries today, Search (KB-3b) and Spaces (KB-2a). Files, Sources and
 * Review join them with KB-2c, KB-7a and KB-5, and each is ABSENT until
 * then, never a stub. Search sits first, above the tree, so it stays in one
 * place while a space opens. Under Spaces, each space the member can open is
 * a row, and the open space shows its page tree. Every row is a `RailRow`
 * (AGENTS.md rule 12), as the Projects tree and the CRM rail draw theirs.
 *
 * ⚠️ Props only, no context. On a phone the rail draws inside the shell's
 * drawer, which renders above the Knowledge layout, so a context read here
 * would find nothing. The page owns where the rail goes. Fence:
 * `src/lib/railRows.test.ts`.
 */

import { useMemo, useState } from "react";

import Icon from "@/components/Icon";
import RailRow from "@/components/ui/RailRow";
import { SkeletonTree } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";

import { listPages, pagesKey } from "../lib/api";
import { pageIcon, spaceIcon } from "../lib/labels";
import { KNOWLEDGE_HOME, KNOWLEDGE_SEARCH, pageHref, spaceHref } from "../lib/links";
import { ancestorIds, buildTree, type TreeNode } from "../lib/tree";
import type { PageSummary, Space } from "../lib/types";

const ICON = "h-3.5 w-3.5 shrink-0";

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
}

export default function KnowledgeRail({
  spaces,
  selectedSpaceId,
  selectedPageId,
  atHome,
  atSearch,
  onNavigate,
}: KnowledgeRailProps) {
  return (
    <div className="flex flex-col gap-0.5">
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
        />
      ))}
    </div>
  );
}

function SpaceBranch({
  space,
  selected,
  selectedPageId,
  onNavigate,
}: {
  space: Space;
  selected: boolean;
  selectedPageId: string | null;
  onNavigate: (href: string) => void;
}) {
  const [open, setOpen] = useState(selected);
  // A space that becomes the one on screen opens. Set during render, the
  // adjust-state-on-a-prop-change pattern, so no frame shows it closed.
  const [wasSelected, setWasSelected] = useState(selected);
  if (selected !== wasSelected) {
    setWasSelected(selected);
    if (selected) setOpen(true);
  }

  const pages = useCachedResource<PageSummary[]>(open ? pagesKey(space.id) : null, () =>
    listPages(space.id),
  );
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
        ? tree.map((node) => (
            <PageBranch
              key={node.page.id}
              node={node}
              depth={2}
              folds={folds}
              onToggle={toggleFold}
              selectedPageId={selectedPageId}
              onNavigate={onNavigate}
            />
          ))
        : null}
    </>
  );
}

function PageBranch({
  node,
  depth,
  folds,
  onToggle,
  selectedPageId,
  onNavigate,
}: {
  node: TreeNode;
  depth: number;
  folds: Set<string>;
  onToggle: (id: string) => void;
  selectedPageId: string | null;
  onNavigate: (href: string) => void;
}) {
  const { page, children } = node;
  const expanded = folds.has(page.id);
  const selected = page.id === selectedPageId;
  return (
    <>
      <RailRow
        label={page.title}
        depth={depth}
        guides
        tree
        selected={selected}
        icon={<Icon name={pageIcon(page)} className={ICON} />}
        meta={page.state === "draft" ? <span className="text-[10px]">Draft</span> : null}
        expand={children.length ? { expanded, onToggle: () => onToggle(page.id) } : undefined}
        buttonProps={{ "aria-current": selected ? "page" : undefined }}
        onSelect={() => {
          if (children.length && !expanded) onToggle(page.id);
          onNavigate(pageHref(page.id));
        }}
      />
      {expanded
        ? children.map((child) => (
            <PageBranch
              key={child.page.id}
              node={child}
              depth={depth + 1}
              folds={folds}
              onToggle={onToggle}
              selectedPageId={selectedPageId}
              onNavigate={onNavigate}
            />
          ))
        : null}
    </>
  );
}
