"use client";

/**
 * The node palette (RFC §5.2) — drag a node type onto the canvas, or click to
 * append. Everything here comes from the served catalog (spec D7): agents,
 * integration actions, ready modules — never a hard-coded capability.
 *
 * With many agents/tools the tree alone doesn't scale, so the palette leads
 * with search (`/workflows/catalog/search` — keyword-ranked for now, the same
 * index the Workflow Copilot shortlists from; platform semantic search is
 * BO‑22) and rolls tools up under their integration. Typing searches;
 * clearing returns to the category view.
 */

import AppIcon, { themedIcon } from "@/components/Icon";
import { useEffect, useRef, useState } from "react";
import { searchCatalog } from "../lib/api";
import { FALLBACK_ICON, NODE_ICON } from "../lib/nodeVisuals";
import { NODE_CATEGORY_STYLE } from "../lib/types";
import type { Catalog, NodeType, SearchResult } from "../lib/types";

export type PaletteDrop = {
  nodeType: NodeType;
  label: string;
  config: Record<string, unknown>;
};

type SectionIcon = React.ComponentType<{ className?: string }>;

/** Map one search result onto a draggable drop, joined against the catalog. */
function dropForResult(r: SearchResult, catalog: Catalog | null): PaletteDrop | null {
  if (r.kind === "agent")
    return { nodeType: "agent", label: r.key, config: { agent: r.key, message: "" } };
  if (r.kind === "tool")
    return { nodeType: "tool", label: r.label, config: { action: r.key, args: {} } };
  if (r.kind === "module") {
    const mod = catalog?.modules.find((m) => m.id === r.key && m.status === "ready");
    return mod
      ? { nodeType: "module", label: mod.name, config: { module_id: mod.id, inputs: {} } }
      : null;
  }
  if (r.kind === "node") {
    const type = r.key as NodeType;
    if (type === "condition")
      return { nodeType: type, label: r.label, config: { left: "", op: "equals", right: "" } };
    if (type === "set")
      return { nodeType: type, label: r.label, config: { assignments: {} } };
    if (type === "approval")
      return { nodeType: type, label: r.label, config: { message: "" } };
    if (type === "wait")
      return { nodeType: type, label: r.label, config: { seconds: 3600 } };
    if (type === "output")
      return { nodeType: type, label: r.label, config: { value: "" } };
  }
  return null; // integrations without actions, trigger, etc. — not droppable
}

export default function NodePalette({
  catalog,
  onAdd,
}: {
  catalog: Catalog | null;
  onAdd: (drop: PaletteDrop) => void;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({
    agent: true, tool: true, module: true, logic: true, output: false,
  });
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef<number | null>(null);

  // Debounced semantic search; clearing the box returns to categories.
  // Same lint carve-out as the fetch-on-mount pattern (build/apps/page.tsx).
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    const q = query.trim();
    if (!q) {
      setResults(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    debounceRef.current = window.setTimeout(async () => {
      try {
        const res = await searchCatalog(q);
        setResults(res.results);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [query]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const startDrag = (e: React.DragEvent, drop: PaletteDrop) => {
    e.dataTransfer.setData("application/cc-workflow-node", JSON.stringify(drop));
    e.dataTransfer.effectAllowed = "move";
  };

  /**
   * A palette row carries the same icon plate the canvas card will, so a block
   * is recognisable before and after it is dropped (mockup §.pal-item).
   */
  const item = (
    key: string,
    drop: PaletteDrop | null,
    title: string,
    subtitle: string,
    category: string,
    disabled = false,
    nodeType?: NodeType,
  ) => {
    const style = NODE_CATEGORY_STYLE[category] ?? NODE_CATEGORY_STYLE.logic;
    const dead = disabled || !drop;
    const Icon = NODE_ICON[nodeType ?? drop?.nodeType ?? "tool"] ?? FALLBACK_ICON;
    return (
      <button
        key={key}
        draggable={!dead}
        onDragStart={(e) => drop && startDrag(e, drop)}
        onClick={() => !dead && drop && onAdd(drop)}
        disabled={dead}
        title={dead ? "Not available — configure the integration first" : subtitle}
        className={`w-full text-left rounded-lg border border-transparent px-2 py-1.5 tech-transition group ${
          dead
            ? "opacity-40 cursor-not-allowed"
            : "hover:border-border hover:bg-secondary cursor-grab active:cursor-grabbing"
        }`}
      >
        <div className="flex items-center gap-2">
          <span
            className={`w-6 h-6 shrink-0 rounded-lg border grid place-items-center ${style.tile}`}
          >
            <Icon className="w-3.5 h-3.5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[11px] font-medium text-foreground truncate">
              {title}
            </span>
            <span className="block font-mono text-[9px] text-muted-foreground truncate">
              {subtitle}
            </span>
          </span>
          {!dead && (
            <span className="shrink-0 text-muted-foreground reveal-on-hover tech-transition">
              <AppIcon name="Plus" className="w-3 h-3" />
            </span>
          )}
        </div>
      </button>
    );
  };

  const section = (
    id: string, label: string, Icon: SectionIcon, count: number,
    children: React.ReactNode,
    category = id,
  ) => {
    const style = NODE_CATEGORY_STYLE[category] ?? NODE_CATEGORY_STYLE.logic;
    return (
      <div key={id}>
        <button
          onClick={() => setOpen((o) => ({ ...o, [id]: !o[id] }))}
          className="w-full flex items-center gap-1.5 px-1 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground tech-transition"
        >
          {open[id] ? <AppIcon name="ChevronDown" className="w-3 h-3" /> : <AppIcon name="ChevronRight" className="w-3 h-3" />}
          <span className={`w-1.5 h-1.5 rounded-sm shrink-0 ${style.dot}`} />
          <Icon className="w-3 h-3" />
          {label}
          <span className="ml-auto font-normal normal-case tracking-normal">{count}</span>
        </button>
        {open[id] && <div className="space-y-0.5 mb-2">{children}</div>}
      </div>
    );
  };

  const readyModules = (catalog?.modules ?? []).filter((m) => m.status === "ready");
  const platformTools = (catalog?.tools ?? []).filter((t) => !t.integration);
  const integrations = catalog?.integrations ?? [];

  return (
    <div className="w-52 sm:w-60 shrink-0 overflow-y-auto scrollbar-thin p-2">
      {/* Search — the front door once the catalog is big */}
      <div className="relative mb-2">
        <AppIcon name="Search" className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search agents, tools, modules…"
          className="w-full rounded-lg border border-input bg-background pl-8 pr-7 py-1.5 text-[11px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        />
        {query && (
          <button
            onClick={() => setQuery("")}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            title="Clear search"
          >
            <AppIcon name="X" className="w-3 h-3" />
          </button>
        )}
      </div>

      {results !== null ? (
        <div className="space-y-1">
          <div className="px-1 pb-1 text-[9px] text-muted-foreground">
            {searching
              ? "Searching…"
              : `${results.length} match${results.length === 1 ? "" : "es"}`}
          </div>
          {results.map((r) => {
            const drop = dropForResult(r, catalog);
            const category =
              r.kind === "node"
                ? (r.category || "logic")
                : r.kind === "integration" ? "tool" : r.kind;
            return item(
              `s:${r.kind}:${r.key}`, drop,
              r.label,
              `${r.kind} · ${r.description || "—"}`,
              category,
              r.kind === "integration",
            );
          })}
          {!searching && results.length === 0 && (
            <p className="text-[10px] text-muted-foreground px-1">
              Nothing matches — ask the Copilot; it can create a module for this.
            </p>
          )}
        </div>
      ) : (
        <>
          <div className="flex items-center gap-1.5 px-1 pb-2 text-[10px] text-muted-foreground">
            <AppIcon name="Zap" className="w-3 h-3 text-amber-500" />
            Drag onto the canvas, or click to append
          </div>

          {section(
            "agent", "Agents", themedIcon("Bot"), (catalog?.agents ?? []).length,
            (catalog?.agents ?? []).map((a) =>
              item(
                `agent:${a.name}`,
                { nodeType: "agent", label: a.name, config: { agent: a.name, message: "" } },
                a.name, a.description, "agent",
              ),
            ),
          )}

          {section(
            "tool", "Tools & Integrations", themedIcon("Wrench"),
            (catalog?.tools ?? []).length,
            <>
              {platformTools.length > 0 && (
                <div className="px-1 pt-0.5 text-[8.5px] text-muted-foreground font-semibold">Platform</div>
              )}
              {platformTools.map((t) =>
                item(
                  `tool:${t.action}`,
                  { nodeType: "tool", label: t.label, config: { action: t.action, args: {} } },
                  t.label,
                  t.destructive ? "write — approval-gated" : t.description,
                  "tool",
                ),
              )}
              {integrations.map((integ) => (
                <div key={integ.service}>
                  <div className="px-1 pt-1 text-[8.5px] text-muted-foreground font-semibold flex items-center gap-1">
                    {integ.service}
                    {!integ.available && <span className="opacity-70">· not configured</span>}
                  </div>
                  {integ.actions.length > 0
                    ? integ.actions.map((t) =>
                        item(
                          `tool:${t.action}`,
                          { nodeType: "tool", label: t.label, config: { action: t.action, args: {} } },
                          t.label,
                          t.destructive ? "write — approval-gated" : t.description,
                          "tool",
                          !integ.available,
                        ),
                      )
                    : item(
                        `integration:${integ.service}`, null,
                        integ.service,
                        integ.available ? "no actions yet" : "not configured",
                        "tool", true,
                      )}
                </div>
              ))}
            </>,
          )}

          {section(
            "module", "Modules", themedIcon("Boxes"), readyModules.length,
            readyModules.length === 0 ? (
              <p className="text-[10px] text-muted-foreground px-1">
                No ready modules — build one in the Module Studio, or ask the Copilot.
              </p>
            ) : (
              readyModules.map((m) =>
                item(
                  `module:${m.id}`,
                  { nodeType: "module", label: m.name, config: { module_id: m.id, inputs: {} } },
                  m.name, m.description || "code module", "module",
                ),
              )
            ),
          )}

          {section(
            "logic", "Logic", themedIcon("GitBranch"), 4,
            <>
              {item(
                "logic:condition",
                { nodeType: "condition", label: "Condition",
                  config: { left: "", op: "equals", right: "" } },
                "Condition", "branch on true/false", "logic",
              )}
              {item(
                "logic:set",
                { nodeType: "set", label: "Set variables", config: { assignments: {} } },
                "Set variables", "assign into {{vars.*}}", "logic",
              )}
              {item(
                "logic:approval",
                { nodeType: "approval", label: "Human approval", config: { message: "" } },
                "Human approval", "pause for the approvals inbox", "logic",
              )}
              {item(
                "logic:wait",
                { nodeType: "wait", label: "Wait", config: { seconds: 3600 } },
                "Wait", "pause for a duration, then continue", "logic",
              )}
            </>,
          )}

          {section(
            "output", "Output", themedIcon("LogOut"), 1,
            item(
              "output:output",
              { nodeType: "output", label: "Output", config: { value: "" } },
              "Output", "yield the run's result", "output",
            ),
          )}
        </>
      )}
    </div>
  );
}
