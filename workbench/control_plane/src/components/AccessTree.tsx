"use client";

/**
 * AccessTree — the project tree of an agent grant (WS-55 EA-17b, spec §10.8).
 *
 * One component for two screens: the consent page draws it now, and "Change
 * access" in the AI agents view (EA-17c) draws it next, with the grant that
 * the agent holds. The decisions live in `lib/accessTree.ts`, and this file
 * only draws them and passes the member's clicks back.
 *
 * - A tick on a project ticks every project beneath it.
 * - An untick of a subproject under a ticked parent leaves it out. The parent
 *   then shows a dash and a count such as "5 of 6", and the child shows the
 *   tag "left out".
 * - The tree grows with the page. It has no scroll box of its own.
 * - Tokens only, `Checkbox`, `Input`, `Badge` and `Button`. The boxes are the
 *   house `Checkbox`, whose dash is its `indeterminate` state.
 */
import { useMemo, useState } from "react";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import {
  NO_SELECTION,
  SEARCH_THRESHOLD,
  analyzeTree,
  parentIds,
  searchMatches,
  selectedCount,
  toggleProject,
  visibleNodes,
  type ProjectTree,
  type Selection,
} from "@/lib/accessTree";

export interface AccessTreeProps {
  tree: ProjectTree;
  selection: Selection;
  onChange: (next: Selection) => void;
  disabled?: boolean;
}

export default function AccessTree({ tree, selection, onChange, disabled }: AccessTreeProps) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const [query, setQuery] = useState("");
  const facts = useMemo(() => analyzeTree(tree, selection), [tree, selection]);
  const matches = useMemo(() => searchMatches(tree, query), [tree, query]);
  const rows = useMemo(() => visibleNodes(tree, expanded, matches), [tree, expanded, matches]);
  const picked = selectedCount(tree, selection);
  const total = tree.nodes.length;

  const fold = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (total === 0) {
    return <p className="rounded-md border border-border p-3 text-xs text-muted-foreground">You see no team projects.</p>;
  }

  return (
    <div className="space-y-2" data-testid="access-tree">
      {total > SEARCH_THRESHOLD && (
        <Input
          icon="Search"
          type="search"
          inputSize="sm"
          placeholder="Find a project"
          aria-label="Find a project"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span data-testid="access-tree-count">
          {picked} of {total} {total === 1 ? "project" : "projects"}
        </span>
        <span className="flex items-center gap-1">
          <Button variant="text" size="none" disabled={disabled} onClick={() => setExpanded(new Set(parentIds(tree)))}>
            Expand all
          </Button>
          <span aria-hidden="true">·</span>
          <Button variant="text" size="none" disabled={disabled} onClick={() => onChange(NO_SELECTION)}>
            Clear
          </Button>
        </span>
      </div>

      <div className="rounded-md border border-border py-1" role="group" aria-label="Projects">
        {rows.length === 0 && <p className="px-3 py-2 text-xs text-muted-foreground">No project matches.</p>}
        <ul>
          {rows.map((node) => {
            const f = facts[node.id];
            const hasChildren = node.children.length > 0;
            const open = matches !== null || expanded.has(node.id);
            return (
              <li
                key={node.id}
                data-testid={`tree-row-${node.name}`}
                className="flex min-h-8 items-center gap-1.5 px-2 text-sm hover:bg-secondary/50"
                style={{ paddingLeft: `${0.5 + node.depth * 1.25}rem` }}
              >
                {hasChildren ? (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    icon={open ? "ChevronDown" : "ChevronRight"}
                    aria-label={`${open ? "Fold" : "Unfold"} ${node.name}`}
                    aria-expanded={open}
                    onClick={() => fold(node.id)}
                  />
                ) : (
                  <span className="inline-block w-6 shrink-0" aria-hidden="true" />
                )}
                <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                  <Checkbox
                    aria-label={node.name}
                    checked={f.state === "on"}
                    indeterminate={f.state === "partial"}
                    disabled={disabled}
                    onChange={() => onChange(toggleProject(tree, selection, node.id))}
                  />
                  <span className={`truncate ${f.reached ? "text-foreground" : "text-muted-foreground"}`}>{node.name}</span>
                </label>
                {f.leftOutMark && (
                  <Badge tone="warning" size="sm">
                    left out
                  </Badge>
                )}
                {hasChildren && (
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground" data-testid="tree-count">
                    {f.inside} of {f.total}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      <p className="border-l-2 border-primary/60 pl-3 text-xs text-muted-foreground">
        Ticking a project ticks everything under it. A subproject added later joins on its own, unless you left it out.
      </p>
    </div>
  );
}
