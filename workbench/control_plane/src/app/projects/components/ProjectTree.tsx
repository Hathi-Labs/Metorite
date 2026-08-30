"use client";

/**
 * Projects · the tree sidebar.
 *
 * Departments, projects and subprojects are one self-FK (D-PM-2), so this is
 * one recursive list rather than three panes — the indentation IS the
 * hierarchy.
 *
 * Rendered twice by the page and identically both times: the collapsible rail
 * on desktop and the shell drawer's sheet on a phone (WS-27ag). The selected
 * row wears the house active token, `bg-primary/10 text-primary` — the same one
 * the sidebar, the tabs and every other nav in the tree use.
 */
import { ContextMenu } from "@/components/ContextMenu";
import Icon, { themedIcon } from "@/components/Icon";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import { PROJECT_STATES, projectStateAccent } from "@/lib/statusAccent";
import { useState } from "react";

import type { ProjectRow } from "../lib/api";
import { type ProjectMenuHandlers, projectMenuItems } from "../lib/projectMenu";
import { accentForSlot } from "@/lib/categorical";

import {
  childCreationOptions,
  effectiveState,
  hasRunState,
  type NodeKind,
  type NodeLevel,
  nodeKind,
  nodeLevel,
  spaceMarker,
} from "../lib/tree";

/**
 * The run-state indicator (WS-27bg, owner-directed: *"each project should show
 * one colored indicator in front of it"*).
 *
 * It REPLACES the generic folder glyph rather than sitting beside it. In a tree
 * where every row is a project, "this is a project" is not information — the
 * icon slot was carrying none, and the state is the fact worth the space.
 *
 * Hue AND glyph, never hue alone (D-PM-27): amber and green are the pair most
 * commonly confused, and this is a dense list read at a glance.
 *
 * An INHERITED state draws at lower emphasis and says so in its tooltip. A node
 * paused because somebody paused *it* and a node paused because its department
 * is paused are different facts; a reader who cannot tell them apart goes
 * looking for the pause on the wrong row.
 */
function StateDot({
  state,
  inherited,
  projectName,
  level,
}: {
  state: string;
  inherited: boolean;
  projectName: string;
  /** 'project' or 'subproject' — the two levels that own a run state. */
  level: NodeLevel;
}) {
  const visual = PROJECT_STATES[state];
  const accent = projectStateAccent(state);
  const label = visual?.label ?? state;
  // A subproject draws the same glyph one step smaller. Indentation alone
  // stops being readable once a folder sits between the two (owner
  // directive 2026-08-31), and the STATE must stay the information — so the
  // level changes the size, never the hue.
  const size = level === "subproject" ? "h-3 w-3" : "h-3.5 w-3.5";
  return (
    <Icon
      name={visual?.icon ?? "Circle"}
      className={`${size} shrink-0 ${accent.text} ${
        inherited ? "opacity-50" : ""
      }`}
      aria-label={
        inherited
          ? `${level}, ${label} — inherited from a parent project`
          : `${level}, ${label} — ${projectName}`
      }
    />
  );
}

/**
 * The marker for a node that is NOT a project or subproject — a space or a
 * folder (owner directive 2026-08-31: *"I cannot tell any difference
 * between a project, a space, a folder or a subproject"*).
 *
 * The four levels now read apart at a glance, and each marker carries the
 * fact its level actually has:
 *
 *   • **space** — its own icon, in its own ramp slot. A space is a place,
 *     and a place is recognised by its sign. Larger, because it is the row
 *     you scan the sidebar for.
 *   • **folder** — a folder glyph, muted. Folders are furniture: they group,
 *     they hold no work, they have no state.
 *   • **project / subproject** — `StateDot`, because for a row that DOES
 *     work the run state is the fact worth the slot, not its kind. They part
 *     on indentation and on dot size.
 */
function LevelGlyph({
  level,
  node,
}: {
  level: NodeLevel;
  node: ProjectRow;
}) {
  if (level === "space") {
    const marker = spaceMarker(node);
    return (
      <Icon
        name={marker.icon}
        className={`h-4 w-4 shrink-0 ${accentForSlot(marker.slot).text}`}
        aria-label={`Space — ${node.name}`}
      />
    );
  }
  return (
    <Icon
      name="Folder"
      className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
      aria-label={`Folder — ${node.name}`}
    />
  );
}

interface Props {
  roots: ProjectRow[];
  selectedId: string | null;
  onSelect: (project: ProjectRow) => void;
  /**
   * Start creating a child of `kind` under this node. What each row offers
   * comes from `childCreationOptions` — the grammar's UI half (migration
   * 193). Omitted = read-only tree.
   */
  onAddChild?: (parent: ProjectRow, kind: NodeKind) => void;
  /** Open Space Settings for a space (migration 194). */
  onOpenSettings?: (space: ProjectRow) => void;
  /** WS-27bg — right-click actions. Omitted = a read-only tree, no menu. */
  actions?: ProjectMenuHandlers;
}

function Node({
  node,
  depth,
  parentGen,
  selectedId,
  onSelect,
  onAddChild,
  onOpenSettings,
  inheritedState,
  actions,
}: {
  node: ProjectRow;
  depth: number;
  /**
   * PROJECT generations above this node (folders count nothing): 0 at a
   * root, so a space is generation 1, a project 2, a subproject 3 — the
   * numbers `childCreationOptions` keys on.
   */
  parentGen: number;
  selectedId: string | null;
  onSelect: (project: ProjectRow) => void;
  onAddChild?: (parent: ProjectRow, kind: NodeKind) => void;
  onOpenSettings?: (space: ProjectRow) => void;
  /** The effective state of this node's PARENT; absent at a root. */
  inheritedState?: string | null;
  /** Omitted = a read-only tree, and no menu is offered at all. */
  actions?: ProjectMenuHandlers;
}) {
  const [open, setOpen] = useState(depth < 1);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [addMenu, setAddMenu] = useState<{ x: number; y: number } | null>(null);
  // WS-27bg slice 2 remainder — the rename field, inline on the row.
  //
  // Inline rather than a dialog, and the choice is the TagManager idiom (§9.11
  // `components/TagManager.tsx:155`) rather than a preference: renaming a node
  // in a tree wants the surrounding rows visible, because the name's job is to
  // be distinguishable from its siblings. A modal hides exactly the context
  // that makes a good name obvious.
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(node.name);
  const children = node.children ?? [];
  const isSelected = node.id === selectedId;
  const kind = nodeKind(node);
  const gen = kind === "folder" ? parentGen : parentGen + 1;
  const level = nodeLevel(kind, gen);
  // Derived on the way down, never written (D-PM-26): this node's effective
  // state is what its own column says OR the more restrictive thing an
  // ancestor says, and the same value is what its children inherit. A FOLDER
  // is transparent here too: its own `status` column is meaningless
  // (migration 193), so the ancestor state passes straight through it.
  // A space and a folder have no run state of their own (owner directive
  // 2026-08-31 / migration 194), so the ancestor's state passes straight
  // through them to the projects below.
  const run = hasRunState(level)
    ? effectiveState(node, inheritedState)
    : { state: inheritedState ?? "active", inherited: Boolean(inheritedState) };
  const addOptions = onAddChild ? childCreationOptions(kind, gen) : [];

  return (
    <li>
      <div
        className={`flex items-center gap-1 rounded-md px-2 py-1.5 text-sm ${
          isSelected
            ? "bg-primary/10 text-primary"
            : "text-foreground hover:bg-muted"
        }`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
        onContextMenu={
          actions
            ? (e) => {
                e.preventDefault();
                setMenu({ x: e.clientX, y: e.clientY });
              }
            : undefined
        }
      >
        {children.length > 0 ? (
          <button
            type="button"
            aria-label={open ? `Collapse ${node.name}` : `Expand ${node.name}`}
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 rounded p-0.5 hover:bg-background/60"
          >
            {open ? (
              <Icon name="ChevronDown" className="h-3.5 w-3.5" />
            ) : (
              <Icon name="ChevronRight" className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="w-[18px] shrink-0" />
        )}
        {renaming && actions ? (
          <form
            className="flex min-w-0 flex-1 items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              const next = draft.trim();
              // An empty field and an unchanged one are both "never mind",
              // not errors: the row is already named, so there is nothing to
              // report and nothing to write.
              if (next && next !== node.name) actions.onRename(node, next);
              setRenaming(false);
            }}
          >
            {hasRunState(level) ? (
              <StateDot
                state={run.state}
                inherited={run.inherited}
                projectName={node.name}
                level={level}
              />
            ) : (
              <LevelGlyph level={level} node={node} />
            )}
            <Input
              autoFocus
              inputSize="sm"
              value={draft}
              aria-label={`Rename ${node.name}`}
              onChange={(e) => setDraft(e.target.value)}
              // Deliberately NO `onBlur` cancel. Clicking Save blurs the field
              // first, so a blur-cancel closes the form before the click can
              // submit it — the button would be dead and only Enter would
              // work. Escape and Save are the two ways out, both explicit.
              onKeyDown={(e) => {
                if (e.key !== "Escape") return;
                // The same rule TagManager records: the substrate binds
                // Escape on `document`, above React's root container, so an
                // unstopped key would cancel the rename AND dismiss whatever
                // surface the tree is drawn inside — the drawer, on a phone.
                e.stopPropagation();
                setRenaming(false);
              }}
            />
            <Button type="submit" size="sm">
              Save
            </Button>
          </form>
        ) : (
          <button
            type="button"
            onClick={() => onSelect(node)}
            className="flex min-w-0 flex-1 items-center gap-2 text-left"
          >
            {hasRunState(level) ? (
              <StateDot
                state={run.state}
                inherited={run.inherited}
                projectName={node.name}
                level={level}
              />
            ) : (
              <LevelGlyph level={level} node={node} />
            )}
            {/* A space is the row people scan the sidebar FOR, so it is the
                one level that carries weight. Everything below reads as its
                contents rather than as more siblings. */}
            <span
              className={`truncate ${level === "space" ? "font-medium" : ""}`}
            >
              {node.name}
            </span>
            {/* ⚠️ The "CU" (imported-from-ClickUp) provenance badge was removed
                2026-08-24 (D52). `clickup_id` still EXISTS on the row — D52.3
                keeps the column under R6 — but nothing writes it any more, so a
                badge would only ever mark rows imported before the retirement.
                It goes with the column, in the release that drops it. */}
          </button>
        )}
        {onAddChild && !renaming && addOptions.length > 0 ? (
          // A child is created HERE rather than from a dialog that asks
          // "which parent?" — the answer is already on screen, and asking for
          // it again is how a tree with fifty nodes gets mis-parented rows.
          // One option acts on click; two open a menu; a subproject offers
          // nothing at all, because it is the grammar's floor (migration 193).
          <button
            type="button"
            aria-label={`${addOptions[0].label} under ${node.name}`}
            title={addOptions.map((o) => o.label).join(" / ")}
            onClick={(e) => {
              if (addOptions.length === 1) {
                onAddChild(node, addOptions[0].kind);
                return;
              }
              const rect = e.currentTarget.getBoundingClientRect();
              setAddMenu({ x: rect.left, y: rect.bottom + 2 });
            }}
            className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-background/60"
          >
            <Icon name="Plus" className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
      {addMenu && onAddChild ? (
        <ContextMenu
          x={addMenu.x}
          y={addMenu.y}
          items={addOptions.map((option) => ({
            kind: "item" as const,
            label: option.label,
            icon: themedIcon(option.kind === "folder" ? "Folder" : "Plus"),
            onSelect: () => onAddChild(node, option.kind),
          }))}
          onClose={() => setAddMenu(null)}
        />
      ) : null}
      {menu && actions ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={projectMenuItems(
            node,
            actions,
            {
              onBeginRename: () => {
                // Seed from the row, not from whatever the last cancelled
                // edit left behind: reopening the field must offer the
                // CURRENT name.
                setDraft(node.name);
                setRenaming(true);
              },
              onOpenSettings: onOpenSettings
                ? () => onOpenSettings(node)
                : undefined,
            },
            level
          ).map((entry) =>
            entry.kind === "item"
              ? {
                  ...entry,
                  icon: entry.icon ? themedIcon(entry.icon) : undefined,
                }
              : entry
          )}
          onClose={() => setMenu(null)}
        />
      ) : null}
      {open && children.length > 0 ? (
        <ul>
          {children.map((child) => (
            <Node
              key={child.id}
              node={child}
              depth={depth + 1}
              parentGen={gen}
              selectedId={selectedId}
              onSelect={onSelect}
              onAddChild={onAddChild}
              onOpenSettings={onOpenSettings}
              inheritedState={run.state}
              actions={actions}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function ProjectTree({
  roots,
  selectedId,
  onSelect,
  onAddChild,
  onOpenSettings,
  actions,
}: Props) {
  if (roots.length === 0) {
    return (
      <div className="px-2 py-6">
        <p className="text-sm text-muted-foreground">
          No spaces yet. Create one with the + above.
        </p>
      </div>
    );
  }
  return (
    <ul className="space-y-0.5">
      {roots.map((root) => (
        <Node
          key={root.id}
          node={root}
          depth={0}
          parentGen={0}
          selectedId={selectedId}
          onSelect={onSelect}
          onAddChild={onAddChild}
          onOpenSettings={onOpenSettings}
          actions={actions}
        />
      ))}
    </ul>
  );
}
