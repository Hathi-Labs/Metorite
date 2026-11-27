"use client";

/**
 * The Agents view · the Graph lens (WS-MCP AMR-1, `agent_coordination.md` §5.12).
 *
 * Reading this as: the board cards of the Board lens, laid in four columns, so
 * the lines can say who dispatched whom and what each session waits on. The
 * layout is the pure `layoutGraph`. `@xyflow/react` pans and zooms it
 * (§3.12.4 rule 6), as the map of SW-103 does.
 *
 * AMR-4 (§3.12.9): one orchestrator of a large fleet draws bounded, with one
 * bucket for each state of the rest. A session opens its sub-agent rows. Under
 * 60 % zoom the cards show their names only.
 *
 * ⚠️ **Read only.** Nothing drags, connects or edits. A click selects a node,
 * and the details open beside the graph.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useTheme } from "next-themes";

import Icon from "@/components/Icon";
import { PersonAvatar } from "@/components/room/Identity";
import Button from "@/components/ui/Button";

import {
  type Graph,
  type GraphEdgeKind,
  type GraphNode,
  type OpenSubs,
  blockSentence,
  blockWord,
  layoutGraph,
  layoutOrchestrator,
  relatedNodes,
} from "../lib/agentGraph";
import {
  type LensFilters,
  type LensGroup,
  type LensModel,
  type LensSession,
  type LensState,
  activeCount,
  countStates,
  foldWord,
  lensEdge,
  lensWord,
  machineWord,
  subWords,
} from "../lib/agentLenses";
import { type BucketWindows, NAMES_ONLY_BELOW, bucketWord } from "../lib/agentScale";
import { BucketRow } from "./AgentBoardList";
import { CARD_FACE, CountLine, FoldRow, SessionCard, pressKeys } from "./AgentSessionCard";
import { Square } from "./AgentSquares";

export const GRAPH_LABEL = "Graph of people, orchestrators and sessions";

const LINE: Record<GraphEdgeKind, string> = {
  tree: "color-mix(in srgb, var(--muted-foreground) 45%, transparent)",
  dispatch: "color-mix(in srgb, var(--muted-foreground) 70%, transparent)",
  you: "var(--warning)",
  wait: "color-mix(in srgb, var(--primary) 75%, transparent)",
};

/** What a node can ask of the graph. */
interface NodeActions {
  onPick: (node: GraphNode) => void;
  onFold: (sessionId: string) => void;
  onHover: (id: string | null) => void;
  /** AMR-4: opens or closes the sub-agents of a session. */
  onToggleSubs: (sessionId: string) => void;
  onBucket: (state: LensState, rest: number) => void;
  onPickSession: (session: LensSession) => void;
  /** Opens the details of a session by its id. */
  onPickId: (sessionId: string) => void;
}

interface NodeData extends Record<string, unknown>, NodeActions {
  node: GraphNode;
  selected: boolean;
  dim: boolean;
  nowMs: number;
  nameOf: (email: string) => string;
  subsOpen: boolean;
  namesOnly: boolean;
  /** False for one orchestrator: its sessions already fold into buckets. */
  canFold: boolean;
}

function ring(selected: boolean): string {
  return selected ? "ring-2 ring-primary ring-offset-2 ring-offset-background" : "";
}

export function GraphCard({ data }: { data: NodeData }) {
  const { node, selected, nowMs, nameOf, onPick, onFold, subsOpen, namesOnly, onToggleSubs } = data;
  const pick = () => onPick(node);
  if (node.kind === "session") {
    return (
      <SessionCard
        session={node.session}
        nowMs={nowMs}
        selected={selected}
        onOpen={pick}
        subsOpen={subsOpen}
        onToggleSubs={() => onToggleSubs(node.session.id)}
        namesOnly={namesOnly}
      />
    );
  }
  if (node.kind === "fold") return <FoldRow word={foldWord(node.folded)} onShow={pick} />;
  if (node.kind === "bucket") {
    const n = node.sessions.length;
    return (
      <BucketRow
        word={bucketWord(node, lensWord(node.state), node.shown)}
        sessions={node.sessions}
        nowMs={nowMs}
        max={namesOnly ? 0 : n > 24 ? 12 : 16}
        onOpen={() => data.onBucket(node.state, node.rest)}
        onPick={data.onPickSession}
      />
    );
  }
  if (node.kind === "sub" || node.kind === "submore" || node.kind === "subnote") {
    const open = () => data.onPickId(node.sessionId);
    return (
      <div
        role="button"
        tabIndex={0}
        onClick={open}
        onKeyDown={pressKeys(open)}
        data-agent-graph-sub={node.kind}
        className="flex h-full w-full min-w-0 cursor-pointer items-center gap-1.5 rounded-md border border-border bg-card px-2 text-xs text-foreground shadow-sm hover:bg-secondary"
      >
        {node.kind === "sub" ? (
          <>
            <Square state={node.row.state} />
            <span className="min-w-0 truncate font-medium">{node.row.name}</span>
            {namesOnly ? null : <span className="shrink-0 text-muted-foreground">{lensWord(node.row.state)}</span>}
          </>
        ) : node.kind === "submore" ? (
          <span className="truncate text-primary">+{node.count} more · open the list</span>
        ) : (
          <span className="truncate text-muted-foreground">
            {node.failed ? "The subagents did not load. Open the session." : "Loading the subagents…"}
          </span>
        )}
      </div>
    );
  }
  if (node.kind === "person") {
    const counts = countStates(node.sessions);
    return (
      <div
        role="button"
        tabIndex={0}
        aria-pressed={selected}
        aria-label={`${nameOf(node.person)}. ${activeCount(node.sessions)} active.`}
        onClick={pick}
        onKeyDown={pressKeys(pick)}
        data-agent-graph-person=""
        className={`flex h-full w-full cursor-pointer items-center gap-2 rounded-lg px-1 text-left hover:bg-secondary ${ring(selected)}`}
      >
        <PersonAvatar email={node.person} displayName={nameOf(node.person)} size={36} />
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-semibold text-foreground">{nameOf(node.person)}</span>
          <span className="flex gap-2 whitespace-nowrap text-[11px] text-muted-foreground">
            <span>{activeCount(node.sessions)} active</span>
            {counts.you > 0 ? <span className="text-warning">{counts.you} need you</span> : null}
          </span>
        </span>
      </div>
    );
  }
  if (node.kind === "orch") {
    const s = node.session;
    const sub = node.folded
      ? `${node.members.length - 1} sessions folded`
      : `Orchestrator · ${machineWord(s)}${s.model ? ` · ${s.model}` : ""}`;
    return (
      <div className="relative h-full">
        <div
          data-agent-edge={lensEdge(s.state) ?? undefined}
          data-agent-graph-orch={s.state}
          className={`h-full rounded-lg ${ring(selected)}`}
        >
          <div
            role="button"
            tabIndex={0}
            aria-pressed={selected}
            aria-label={`Orchestrator ${s.name}. ${sub}.`}
            onClick={pick}
            onKeyDown={pressKeys(pick)}
            className={CARD_FACE}
          >
            <span className={`flex min-w-0 items-center gap-2 ${s.subs.total > 0 ? "pr-12" : "pr-7"}`}>
              <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
                <Icon name="Bot" size={15} aria-hidden />
              </span>
              <span className="flex min-w-0 flex-col">
                <span className={`truncate font-semibold ${namesOnly ? "text-xl" : "text-sm"}`}>{s.name}</span>
                {namesOnly ? null : <span className="truncate text-xs text-muted-foreground">{sub}</span>}
              </span>
            </span>
            {namesOnly ? null : <CountLine counts={countStates(node.members)} />}
          </div>
        </div>
        {data.canFold ? (
          <Button
            variant="ghost"
            size="icon-xs"
            icon={node.folded ? "ChevronRight" : "ChevronDown"}
            aria-label={`${node.folded ? "Unfold" : "Fold"} ${s.name}`}
            aria-expanded={!node.folded}
            onClick={() => onFold(s.id)}
            className="!absolute right-1.5 top-1.5 z-10"
          />
        ) : null}
        {s.subs.total > 0 && !namesOnly ? (
          <Button
            variant="ghost"
            size="icon-xs"
            icon="GitFork"
            selected={subsOpen}
            aria-expanded={subsOpen}
            aria-label={`${subsOpen ? "Hide" : "Show"} ${subWords(s.subs)} of ${s.name}`}
            title={subWords(s.subs)}
            data-agent-sub-toggle={s.id}
            onClick={() => onToggleSubs(s.id)}
            className={`!absolute top-1.5 z-10 ${data.canFold ? "right-8" : "right-1.5"}`}
          />
        ) : null}
      </div>
    );
  }
  const you = node.block === "you";
  const word = blockWord(node.block);
  const sentence = blockSentence(node.block, node.sessions.length);
  return (
    <div
      data-agent-edge={you ? "warning" : "steady"}
      data-agent-graph-block={node.block}
      className={`h-full rounded-lg ${ring(selected)}`}
    >
      <div
        role="button"
        tabIndex={0}
        aria-pressed={selected}
        aria-label={`${word}. ${sentence}.`}
        onClick={pick}
        onKeyDown={pressKeys(pick)}
        className={CARD_FACE}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span
            className={`flex size-7 shrink-0 items-center justify-center rounded-md bg-secondary ${you ? "text-warning" : "text-muted-foreground"}`}
          >
            <Icon name={you ? "MessageCircleQuestion" : "Hourglass"} size={15} aria-hidden />
          </span>
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-sm font-semibold">{word}</span>
            <span className="truncate text-xs text-muted-foreground">{sentence}</span>
          </span>
        </span>
      </div>
    </div>
  );
}

function FlowNode({ data }: NodeProps<Node<NodeData>>) {
  const { node, dim, onHover } = data;
  return (
    <>
      <Handle id="l" type="target" position={Position.Left} isConnectable={false} className="!opacity-0" />
      <div
        data-graph-node={node.id}
        style={{ width: node.w, height: node.h }}
        onMouseEnter={() => onHover(node.id)}
        onMouseLeave={() => onHover(null)}
        onFocus={() => onHover(node.id)}
        onBlur={() => onHover(null)}
        // `nopan` lets a press reach the card. `nodrag` stops a drag from a card.
        className={`nopan nodrag pointer-events-auto transition-opacity motion-reduce:transition-none ${dim ? "opacity-25" : ""}`}
      >
        <GraphCard data={data} />
      </div>
      <Handle id="r" type="source" position={Position.Right} isConnectable={false} className="!opacity-0" />
      {/* The end of a line between two sessions of the same column. */}
      <Handle id="rt" type="target" position={Position.Right} isConnectable={false} className="!opacity-0" />
    </>
  );
}

const NODE_TYPES = { agentGraph: FlowNode };
const MIN_ZOOM = 0.3;
const MAX_ZOOM = 2;
/** The first view never shrinks the cards below this: a tall graph starts at its top. */
const READABLE_ZOOM = 0.85;
const PAD = 24;

/** The first view: readable, centred, and from the top when the graph is tall. */
export function startViewport(
  nodes: ReadonlyArray<{ x: number; y: number; w: number; h: number }>,
  box: { width: number; height: number },
): { x: number; y: number; zoom: number } {
  if (nodes.length === 0 || box.width <= 0 || box.height <= 0) return { x: 0, y: 0, zoom: 1 };
  const x0 = Math.min(...nodes.map((n) => n.x)) - PAD;
  const y0 = Math.min(...nodes.map((n) => n.y)) - PAD;
  const x1 = Math.max(...nodes.map((n) => n.x + n.w)) + PAD;
  const y1 = Math.max(...nodes.map((n) => n.y + n.h)) + PAD;
  const wide = box.width / (x1 - x0);
  const tall = box.height / (y1 - y0);
  const zoom = Math.min(Math.max(Math.min(wide, tall), READABLE_ZOOM), wide, 1);
  const height = (y1 - y0) * zoom;
  return {
    zoom,
    x: (box.width - (x1 - x0) * zoom) / 2 - x0 * zoom,
    y: (height < box.height ? (box.height - height) / 2 : 0) - y0 * zoom,
  };
}

/** Tells the graph when the zoom is so low that a card can show its name only. */
function ZoomWatch({ onChange }: { onChange: (namesOnly: boolean) => void }) {
  const far = useStore((s) => s.transform[2] < NAMES_ONLY_BELOW);
  useEffect(() => onChange(far), [far, onChange]);
  return null;
}

/** A request to centre one node. A new `n` asks again. */
export interface GraphFocus {
  id: string;
  n: number;
}

/** Centres the node that a question or a square asked for, once, when it draws. */
function CenterOn({ graph, focus }: { graph: Graph; focus: GraphFocus | null }) {
  const { getZoom, setCenter } = useReactFlow();
  const done = useRef(0);
  useEffect(() => {
    if (!focus || done.current === focus.n) return undefined;
    const node = graph.nodes.find((n) => n.id === focus.id);
    if (!node) return undefined;
    // After the first view, and after the dock has taken its width.
    const timer = window.setTimeout(() => {
      done.current = focus.n;
      void setCenter(node.x + node.w / 2, node.y + node.h / 2, { zoom: Math.max(getZoom(), READABLE_ZOOM) });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [focus, graph, getZoom, setCenter]);
  return null;
}

/** Sets the first view when the box has a size, and again when the person or the scale changes. */
function StartView({ box, graph, resetKey }: { box: React.RefObject<HTMLDivElement | null>; graph: Graph; resetKey: string }) {
  const { setViewport } = useReactFlow();
  const done = useRef<string | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el || graph.nodes.length === 0 || done.current === resetKey) return;
    const apply = () => {
      const size = el.getBoundingClientRect();
      if (size.width <= 0 || size.height <= 0) return;
      done.current = resetKey;
      void setViewport(startViewport(graph.nodes, size));
    };
    const timer = window.setTimeout(apply, 60);
    return () => window.clearTimeout(timer);
  }, [box, graph, resetKey, setViewport]);
  return null;
}

/**
 * The dock narrows the graph when it opens. If the selected node is then past
 * the right edge, the graph pans it into view. It moves nothing else.
 */
export function panToShow(
  node: { x: number; w: number },
  view: { x: number; zoom: number },
  width: number,
): number | null {
  const right = (node.x + node.w) * view.zoom + view.x;
  return width > 0 && right > width - PAD ? view.x - (right - width + PAD) : null;
}

function KeepSelected({ box, graph, selectedId }: { box: React.RefObject<HTMLDivElement | null>; graph: Graph; selectedId: string | null }) {
  const { getViewport, setViewport } = useReactFlow();
  const node = graph.nodes.find((n) => n.id === selectedId) ?? null;
  const x = node?.x;
  const w = node?.w;
  useEffect(() => {
    if (x === undefined || w === undefined) return;
    // The wait lets the dock take its width first.
    const timer = window.setTimeout(() => {
      const view = getViewport();
      const next = panToShow({ x, w }, view, box.current?.getBoundingClientRect().width ?? 0);
      if (next !== null) void setViewport({ ...view, x: next });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [box, selectedId, x, w, getViewport, setViewport]);
  return null;
}

function Controls({
  waits,
  onWaits,
  allFolded,
  canFold,
  onFoldAll,
}: {
  waits: boolean;
  onWaits: () => void;
  allFolded: boolean;
  canFold: boolean;
  onFoldAll: () => void;
}) {
  const flow = useReactFlow();
  return (
    <Panel position="bottom-left">
      <div
        role="group"
        aria-label="Graph controls"
        className="flex items-center gap-0.5 rounded-xl border border-border bg-card p-1 shadow-md"
      >
        <Button variant="ghost" size="sm" icon="Scan" onClick={() => void flow.fitView({ padding: 0.06, maxZoom: 1 })}>
          Fit
        </Button>
        <Button variant="ghost" size="icon-sm" icon="Minus" aria-label="Zoom out" onClick={() => void flow.zoomOut()} />
        <Button variant="ghost" size="icon-sm" icon="Plus" aria-label="Zoom in" onClick={() => void flow.zoomIn()} />
        <Button variant="ghost" size="sm" icon="GitBranch" selected={waits} onClick={onWaits}>
          Waits on
        </Button>
        {canFold ? (
          <Button variant="ghost" size="sm" icon={allFolded ? "UnfoldVertical" : "FoldVertical"} onClick={onFoldAll}>
            {allFolded ? "Unfold all" : "Fold all"}
          </Button>
        ) : null}
      </div>
    </Panel>
  );
}

const LEGEND_EDGES: ReadonlyArray<[string, string]> = [
  ["live", "Working"],
  ["steady", "Waiting"],
  ["warning", "Needs you"],
  ["destructive", "Failed"],
  ["breathing", "Idle"],
];
const LEGEND_LINES: ReadonlyArray<[GraphEdgeKind, string]> = [
  ["dispatch", "Dispatched"],
  ["you", "Waits on you"],
  ["wait", "Waits on something else"],
];

function Legend({ wide = false }: { wide?: boolean }) {
  return (
    // One orchestrator has three columns of cards, so its legend needs a wider window.
    <Panel position="bottom-right" className={wide ? "hidden 2xl:block" : "hidden xl:block"}>
      <dl
        aria-label="Legend"
        className="grid grid-cols-[1.75rem_auto] items-center gap-x-2 gap-y-1 rounded-xl border border-border bg-card px-3 py-2 text-[11px] text-muted-foreground shadow-md"
      >
        {LEGEND_EDGES.map(([edge, word]) => (
          <div key={edge} className="contents">
            <dt data-agent-edge={edge} aria-hidden className="h-4 w-6 !rounded">
              <span className="relative z-[1] block h-full w-full rounded-sm bg-card" />
            </dt>
            <dd>{word}</dd>
          </div>
        ))}
        {LEGEND_LINES.map(([kind, word]) => (
          <div key={kind} className="contents">
            <dt aria-hidden>
              <svg width="28" height="8">
                <path d="M1,4 H27" fill="none" stroke={LINE[kind]} strokeWidth={1.5} />
              </svg>
            </dt>
            <dd>{word}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

/** The root font size over 16: a change of density scales the whole graph. */
function useRootScale(): number {
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const root = document.documentElement;
    const read = () => {
      const size = parseFloat(window.getComputedStyle(root).fontSize);
      setScale(Number.isFinite(size) && size > 0 ? size / 16 : 1);
    };
    read();
    const watch = new MutationObserver(read);
    watch.observe(root, { attributes: true });
    return () => watch.disconnect();
  }, []);
  return scale;
}

export interface AgentGraphLensProps {
  model: LensModel;
  filters: LensFilters;
  opened: ReadonlySet<string>;
  onOpenGroup: (key: string) => void;
  selectedId: string | null;
  onPick: (node: GraphNode) => void;
  nowMs: number;
  nameOf: (email: string) => string;
  /** False while the dock is open: the narrower graph has no room for it. */
  legend?: boolean;
  /** AMR-4: draw this one orchestrator, bounded, in place of the whole map. */
  orchestrator?: { person: string; group: LensGroup } | null;
  windows?: BucketWindows;
  onBucket?: (state: LensState, rest: number) => void;
  /** The one session whose sub-agents show. */
  subs?: OpenSubs | null;
  onToggleSubs?: (sessionId: string) => void;
  focus?: GraphFocus | null;
}

const NOOP = () => {};
const NO_WINDOWS: BucketWindows = {};

export function AgentGraphLens({
  model,
  filters,
  opened,
  onOpenGroup,
  selectedId,
  onPick,
  nowMs,
  nameOf,
  legend = true,
  orchestrator = null,
  windows = NO_WINDOWS,
  onBucket = NOOP,
  subs = null,
  onToggleSubs = NOOP,
  focus = null,
}: AgentGraphLensProps) {
  const { resolvedTheme } = useTheme();
  const unit = useRootScale();
  const box = useRef<HTMLDivElement | null>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const [waits, setWaits] = useState(true);
  const [hover, setHover] = useState<string | null>(null);
  const [namesOnly, setNamesOnly] = useState(false);
  const pin = selectedId?.startsWith("s:") ? selectedId.slice(2) : null;
  const person = orchestrator?.person ?? "";
  const group = orchestrator?.group ?? null;
  const graph = useMemo(
    () =>
      group
        ? layoutOrchestrator(person, group, {
            states: filters.states,
            find: filters.find,
            windows,
            pin,
            subs,
            waits,
            unit,
            nameOf,
          })
        : layoutGraph(model, { filters, collapsed, opened, waits, unit, nameOf, subs }),
    [model, filters, collapsed, opened, waits, unit, nameOf, person, group, windows, pin, subs],
  );
  const orchestrators = useMemo(
    () => model.people.flatMap((p) => p.groups.flatMap((g) => (g.orch ? [g.orch.id] : []))),
    [model],
  );
  const allFolded = orchestrators.length > 0 && orchestrators.every((id) => collapsed.has(id));
  const onFold = useCallback((sessionId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }, []);
  const pick = useCallback(
    (node: GraphNode) => {
      if (node.kind === "fold") onOpenGroup(node.groupKey);
      else onPick(node);
    },
    [onOpenGroup, onPick],
  );
  const pickSession = useCallback((session: LensSession) => onPick({ kind: "session", session, id: `s:${session.id}`, x: 0, y: 0, w: 0, h: 0 }), [onPick]);
  const pickId = useCallback(
    (sessionId: string) => {
      const session = model.byId.get(sessionId);
      if (session) pickSession(session);
    },
    [model, pickSession],
  );
  const lit = useMemo(
    () => (hover && graph.nodes.some((n) => n.id === hover) ? relatedNodes(graph, hover) : null),
    [graph, hover],
  );
  const nodes: Node<NodeData>[] = useMemo(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        type: "agentGraph",
        position: { x: node.x, y: node.y },
        width: node.w,
        height: node.h,
        data: {
          node,
          selected: selectedId === node.id,
          dim: lit !== null && !lit.has(node.id),
          nowMs,
          nameOf,
          onPick: pick,
          onFold,
          onHover: setHover,
          subsOpen: (node.kind === "session" || node.kind === "orch") && subs?.sessionId === node.session.id,
          namesOnly,
          canFold: !group,
          onToggleSubs,
          onBucket,
          onPickSession: pickSession,
          onPickId: pickId,
        },
        draggable: false,
        connectable: false,
        selectable: false,
        focusable: false,
      })),
    [graph, selectedId, lit, nowMs, nameOf, pick, onFold, subs, namesOnly, group, onToggleSubs, onBucket, pickSession, pickId],
  );
  const edges: Edge[] = useMemo(
    () =>
      graph.edges.map((edge) => {
        const on = lit !== null && lit.has(edge.from) && lit.has(edge.to);
        const arrow = edge.kind !== "tree";
        return {
          id: edge.id,
          source: edge.from,
          target: edge.to,
          sourceHandle: "r",
          targetHandle: edge.side ? "rt" : "l",
          focusable: false,
          selectable: false,
          style: {
            stroke: LINE[edge.kind],
            strokeWidth: on ? 2.25 : 1.4,
            opacity: lit !== null && !on ? 0.2 : 1,
          },
          ...(arrow ? { markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: LINE[edge.kind] } } : {}),
        };
      }),
    [graph, lit],
  );
  return (
    <div
      ref={box}
      role="group"
      aria-label={GRAPH_LABEL}
      data-agent-graph={group ? "orchestrator" : ""}
      data-agent-names-only={namesOnly ? "" : undefined}
      className="min-h-0 flex-1"
      style={{ minHeight: 360 }}
    >
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          // The wheel pans. Ctrl and the wheel (or a pinch) zoom.
          panOnScroll
          zoomOnScroll={false}
          zoomOnDoubleClick={false}
          colorMode={resolvedTheme === "light" ? "light" : "dark"}
          style={{ "--xy-background-color": "transparent" } as React.CSSProperties}
          minZoom={MIN_ZOOM}
          maxZoom={MAX_ZOOM}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={20} size={1.2} />
          <StartView box={box} graph={graph} resetKey={`${group?.key ?? filters.person}|${filters.find.trim()}|${unit}`} />
          <ZoomWatch onChange={setNamesOnly} />
          <CenterOn graph={graph} focus={focus} />
          <KeepSelected box={box} graph={graph} selectedId={selectedId} />
          {/* The sub-agent column takes the room of the legend. */}
          {legend && !subs ? <Legend wide={group !== null} /> : null}
          <Controls
            waits={waits}
            onWaits={() => setWaits((v) => !v)}
            canFold={!group && orchestrators.length > 0}
            allFolded={allFolded}
            onFoldAll={() => setCollapsed(new Set(allFolded ? [] : orchestrators))}
          />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}

export default AgentGraphLens;
