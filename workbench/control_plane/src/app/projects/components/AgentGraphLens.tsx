"use client";

/**
 * The Agents view · the map (WS-MCP AMR-5, `agent_coordination.md` §3.12.8).
 *
 * Reading this as: everyone's work as one mind map, left to right: the scope,
 * the person, the orchestrator, the agent and its sub-agents. The layout is the
 * pure `layoutGraph`. `@xyflow/react` pans and zooms it (§3.12.4 rule 6), as the
 * map of SW-103 does. Each node folds in place with one chevron.
 *
 * AMR-4 (§3.12.9): an agent opens its sub-agent rows. Under 60 % zoom the cards
 * show their names only.
 *
 * ⚠️ **Read only.** Nothing drags, connects or edits. A click selects a node,
 * and the details open beside the map.
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
import { relativeTime } from "@/lib/taskCard";

import {
  type Graph,
  type GraphEdgeKind,
  type GraphNode,
  type GraphOptions,
  layoutGraph,
  relatedNodes,
} from "../lib/agentGraph";
import {
  type LensModel,
  type LensSession,
  type LensState,
  activeCount,
  cardFact,
  countStates,
  lensEdge,
  lensWord,
  machineWord,
  subWords,
  workLine,
} from "../lib/agentLenses";
import { NAMES_ONLY_BELOW, subTotals } from "../lib/agentScale";
import { EVERYONE } from "./AgentDetails";
import { CARD_FACE, CardFact, CountLine, SessionCard, pressKeys } from "./AgentSessionCard";
import { BucketRow, Square, Squares, TileCounts } from "./AgentSquares";

export const GRAPH_LABEL = "Map of people, orchestrators and agents";
export { EVERYONE };
export const LEGEND_LABEL = "Legend";
/** The squares of a folded person card. The rest read "+N". */
const CARD_SQUARES = 28;

const LINE: Record<GraphEdgeKind, string> = {
  tree: "color-mix(in srgb, var(--muted-foreground) 45%, transparent)",
  dispatch: "color-mix(in srgb, var(--muted-foreground) 70%, transparent)",
};

/** What a node can ask of the map. */
export interface NodeActions {
  onPick: (node: GraphNode) => void;
  /** Folds or unfolds the node in place. */
  onFold: (node: GraphNode) => void;
  onHover: (id: string | null) => void;
  /** AMR-4: opens or closes the sub-agents of a session. */
  onToggleSubs: (sessionId: string) => void;
  /** Opens the "idle or done" node of a person, or closes it. */
  onRest: (person: string) => void;
  onBucket: (person: string, state: LensState, next: number) => void;
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
}

/** "47 agents · 4 active". */
export function agentTotals(sessions: readonly LensSession[]): string {
  const n = sessions.length;
  return `${n} ${n === 1 ? "agent" : "agents"} · ${activeCount(sessions)} active`;
}

const needWord = (n: number): string => (n === 1 ? "1 needs you" : `${n} need you`);

const subagentWord = (n: number): string => `${n} ${n === 1 ? "subagent" : "subagents"}`;

/** "idle or done" for a fold under the Active switch. */
export const restWord = (node: { count: number; open: boolean }): string =>
  `${node.open ? "Hide " : ""}${node.count} idle or done`;

/** The one chevron of a node that folds in place. */
function Chevron({ folded, name, onClick }: { folded: boolean; name: string; onClick: () => void }) {
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      icon={folded ? "ChevronRight" : "ChevronDown"}
      aria-label={`${folded ? "Unfold" : "Fold"} ${name}`}
      aria-expanded={!folded}
      data-agent-chevron=""
      onClick={onClick}
      className="!absolute right-1.5 top-1.5 z-10"
    />
  );
}

function ring(selected: boolean): string {
  return selected ? "ring-2 ring-primary ring-offset-2 ring-offset-background" : "";
}

export function GraphCard({ data }: { data: NodeData }) {
  const { node, selected, nowMs, nameOf, onPick, onFold, subsOpen, namesOnly, onToggleSubs } = data;
  const pick = () => onPick(node);
  const fold = () => onFold(node);
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
        onMap
      />
    );
  }
  if (node.kind === "rest") {
    const word = restWord(node);
    const toggle = () => data.onRest(node.person);
    return (
      <div
        role="button"
        tabIndex={0}
        aria-expanded={node.open}
        aria-label={node.open ? word : `Show ${word}`}
        onClick={toggle}
        onKeyDown={pressKeys(toggle)}
        data-agent-rest={node.person}
        className="flex h-full w-full cursor-pointer items-center justify-between gap-2 rounded-lg border border-border bg-card px-2.5 text-xs text-muted-foreground hover:bg-secondary"
      >
        <span className="truncate">{node.open ? word.replace(/^Hide /, "") : word}</span>
        <span className="shrink-0 font-medium text-primary">{node.open ? "Hide" : "Show"}</span>
      </div>
    );
  }
  if (node.kind === "bucket") {
    const n = node.sessions.length;
    const word = `+${n} ${lensWord(node.state).toLowerCase()}`;
    return (
      <BucketRow
        word={word}
        sessions={node.sessions}
        nowMs={nowMs}
        max={namesOnly ? 0 : n > 24 ? 12 : 16}
        onOpen={() => data.onBucket(node.person, node.state, node.next)}
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
  if (node.kind === "root") {
    const name = node.person ? nameOf(node.person) : EVERYONE;
    const need = countStates(node.sessions).you;
    const subs = subTotals(node.sessions).total;
    return (
      <div className="relative h-full">
        <div
          role="button"
          tabIndex={0}
          aria-pressed={selected}
          aria-label={`${name}. ${agentTotals(node.sessions)}.${need > 0 ? ` ${needWord(need)}.` : ""} ${subagentWord(subs)}.`}
          onClick={pick}
          onKeyDown={pressKeys(pick)}
          data-agent-graph-root=""
          className={`flex h-full w-full cursor-pointer flex-col justify-center gap-0.5 rounded-lg border-2 border-border bg-card px-2.5 text-left shadow-sm hover:shadow-md ${ring(selected)}`}
        >
          <span className="flex min-w-0 items-center gap-1.5 pr-6">
            <Icon name={node.person ? "User" : "Users"} size={15} className="shrink-0 text-muted-foreground" aria-hidden />
            <span className={`truncate font-semibold text-foreground ${namesOnly ? "text-xl" : "text-sm"}`}>{name}</span>
          </span>
          {namesOnly ? null : (
            <>
              <span className="text-[11px] text-muted-foreground">{agentTotals(node.sessions)}</span>
              {need > 0 ? <span className="text-[11px] text-warning">{needWord(need)}</span> : null}
              <span className="text-[11px] text-muted-foreground">{subagentWord(subs)}</span>
            </>
          )}
        </div>
        {node.canFold ? <Chevron folded={node.folded} name={name} onClick={fold} /> : null}
      </div>
    );
  }
  if (node.kind === "person") {
    const name = nameOf(node.person);
    const counts = countStates(node.sessions);
    const subs = subTotals(node.sessions).total;
    return (
      <div className="relative h-full">
        <div
          role="button"
          tabIndex={0}
          aria-pressed={selected}
          aria-label={`${name}. ${agentTotals(node.sessions)}.${counts.you > 0 ? ` ${needWord(counts.you)}.` : ""}`}
          onClick={pick}
          onKeyDown={pressKeys(pick)}
          data-agent-graph-person={node.folded ? "folded" : "open"}
          className={`flex h-full w-full cursor-pointer flex-col justify-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1.5 text-left shadow-sm hover:shadow-md ${ring(selected)}`}
        >
          <span className="flex min-w-0 items-center gap-2 pr-6">
            <PersonAvatar email={node.person} displayName={name} size={28} />
            <span className="flex min-w-0 flex-col">
              <span className={`truncate font-semibold text-foreground ${namesOnly ? "text-xl" : "text-sm"}`}>{name}</span>
              {namesOnly ? null : (
                <span className="truncate text-[11px] text-muted-foreground">{agentTotals(node.sessions)}</span>
              )}
            </span>
          </span>
          {namesOnly ? null : node.folded ? (
            <>
              <TileCounts counts={counts} />
              <span className="min-w-0 overflow-hidden">
                <Squares sessions={node.sessions} nowMs={nowMs} max={CARD_SQUARES} onPick={data.onPickSession} />
              </span>
              <span className="truncate text-[11px] text-muted-foreground">{subagentWord(subs)}</span>
            </>
          ) : (
            <span className="flex min-w-0 gap-2 whitespace-nowrap text-[11px] text-muted-foreground">
              {counts.you > 0 ? <span className="text-warning">{needWord(counts.you)}</span> : null}
              <span className="truncate">{subagentWord(subs)}</span>
            </span>
          )}
        </div>
        {node.canFold ? <Chevron folded={node.folded} name={name} onClick={fold} /> : null}
      </div>
    );
  }
  if (node.kind !== "orch") return null;
  const s = node.session;
  const place = `${machineWord(s)}${s.model ? ` · ${s.model}` : ""}`;
  const fact = cardFact(s, nowMs);
  const folded = `${node.hidden} ${node.hidden === 1 ? "agent" : "agents"} folded`;
  const age = relativeTime(s.lastAt, nowMs);
  return (
    <div className="relative h-full">
      <div data-agent-edge={lensEdge(s.state) ?? undefined} data-agent-graph-orch={s.state} className={`h-full rounded-lg ${ring(selected)}`}>
        <div
          role="button"
          tabIndex={0}
          aria-pressed={selected}
          aria-label={`Orchestrator ${s.name}. ${lensWord(s.state)}.${fact ? ` ${fact}.` : ""} ${workLine(s, nowMs)}. Machine ${place}.${node.folded ? ` ${folded}.` : ""}`}
          onClick={pick}
          onKeyDown={pressKeys(pick)}
          className={CARD_FACE}
        >
          {/* The room at the right is that of the chevron and the sub-agent button. */}
          <span className={`flex min-w-0 items-center gap-1.5 ${s.subs.total > 0 ? "pr-14" : "pr-7"}`}>
            <Icon name="Bot" size={13} className="shrink-0 text-muted-foreground" aria-hidden />
            <span className={`min-w-0 truncate font-semibold ${namesOnly ? "text-xl" : "text-sm"}`}>{s.name}</span>
            {age && !namesOnly ? <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{age}</span> : null}
          </span>
          {namesOnly ? null : (
            <>
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {node.folded ? folded : workLine(s, nowMs)}
                </span>
                {s.host ? (
                  <span className="max-w-[50%] shrink-0 truncate rounded bg-secondary px-1 text-[11px] text-muted-foreground" title={place}>
                    {s.host}
                  </span>
                ) : null}
              </span>
              <CountLine counts={countStates(node.members)} />
              <span className="flex h-5 min-w-0 items-center" data-agent-card-foot="">
                <CardFact session={s} nowMs={nowMs} />
              </span>
            </>
          )}
        </div>
      </div>
      {node.canFold ? <Chevron folded={node.folded} name={s.name} onClick={fold} /> : null}
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
          className={`!absolute top-1.5 z-10 ${node.canFold ? "right-8" : "right-1.5"}`}
        />
      ) : null}
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
/** The order of the tab stops: column by column, from the top, as the map reads. */
export function readingOrder<N extends { x: number; y: number }>(nodes: readonly N[]): N[] {
  return [...nodes].sort((a, b) => a.x - b.x || a.y - b.y);
}
/** The move of a node to its new place (decision G4). */
export const NODE_MOVE = "transition-transform duration-200 ease-out motion-reduce:transition-none";
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
  const latest = useRef(graph);
  useEffect(() => {
    latest.current = graph;
  }, [graph]);
  // Only a new selection pans. A refresh never moves the view (decision G4).
  useEffect(() => {
    if (selectedId === null) return undefined;
    // The wait lets the dock take its width first.
    const timer = window.setTimeout(() => {
      const node = latest.current.nodes.find((n) => n.id === selectedId);
      if (!node) return;
      const view = getViewport();
      const next = panToShow(node, view, box.current?.getBoundingClientRect().width ?? 0);
      if (next !== null) void setViewport({ ...view, x: next });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [box, selectedId, getViewport, setViewport]);
  return null;
}

/**
 * The sub-agent column opens to the right of the cards. A click that opens it
 * pans it into view, once. A refresh never moves the view (decision G4).
 */
function ShowSubs({ box, graph, sessionId }: { box: React.RefObject<HTMLDivElement | null>; graph: Graph; sessionId: string | null }) {
  const { getViewport, setViewport } = useReactFlow();
  const done = useRef<string | null>(null);
  useEffect(() => {
    if (sessionId === null) {
      done.current = null;
      return undefined;
    }
    if (done.current === sessionId) return undefined;
    const column = graph.nodes.find((n) => "sessionId" in n && n.sessionId === sessionId);
    if (!column) return undefined;
    const timer = window.setTimeout(() => {
      done.current = sessionId;
      const view = getViewport();
      const next = panToShow(column, view, box.current?.getBoundingClientRect().width ?? 0);
      if (next !== null) void setViewport({ ...view, x: next });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [box, graph, sessionId, getViewport, setViewport]);
  return null;
}

function Controls({ legend, onLegend }: { legend: boolean; onLegend: () => void }) {
  const flow = useReactFlow();
  return (
    <Panel position="bottom-left">
      <div
        role="group"
        aria-label="Map controls"
        className="flex items-center gap-0.5 rounded-xl border border-border bg-card p-1 shadow-md"
      >
        <Button variant="ghost" size="sm" icon="Scan" onClick={() => void flow.fitView({ padding: 0.06, maxZoom: 1 })}>
          Fit
        </Button>
        <Button variant="ghost" size="icon-sm" icon="Minus" aria-label="Zoom out" onClick={() => void flow.zoomOut()} />
        <Button variant="ghost" size="icon-sm" icon="Plus" aria-label="Zoom in" onClick={() => void flow.zoomIn()} />
        <Button variant="ghost" size="sm" icon="Info" selected={legend} aria-expanded={legend} onClick={onLegend}>
          {LEGEND_LABEL}
        </Button>
      </div>
    </Panel>
  );
}

const LEGEND_EDGES: ReadonlyArray<[LensState, string]> = [
  ["you", "warning"],
  ["failed", "destructive"],
  ["working", "live"],
  ["waiting", "steady"],
  ["idle", "breathing"],
];

export function Legend() {
  return (
    <Panel position="bottom-left" className="!mb-16">
      <dl
        aria-label={LEGEND_LABEL}
        className="grid grid-cols-[1.75rem_auto] items-center gap-x-2 gap-y-1 rounded-xl border border-border bg-card px-3 py-2 text-[11px] text-muted-foreground shadow-md"
      >
        {LEGEND_EDGES.map(([state, edge]) => (
          <div key={state} className="contents">
            <dt data-agent-edge={edge} aria-hidden className="h-4 w-6 !rounded">
              <span className="relative z-[1] block h-full w-full rounded-sm bg-card" />
            </dt>
            <dd>{lensWord(state)}</dd>
          </div>
        ))}
        <div className="contents">
          <dt aria-hidden className="flex justify-center">
            <Square state="done" />
          </dt>
          <dd>{lensWord("done")}</dd>
        </div>
        <div className="contents">
          <dt aria-hidden>
            <svg width="28" height="8">
              <path d="M1,4 H27" fill="none" stroke={LINE.dispatch} strokeWidth={1.5} />
            </svg>
          </dt>
          <dd>Dispatched</dd>
        </div>
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
  /** What the map shows: the scope, the switch, the find and the folds. */
  options: Omit<GraphOptions, "unit" | "subs">;
  selectedId: string | null;
  nowMs: number;
  /** The one session whose sub-agents show. */
  subs?: GraphOptions["subs"];
  actions: Omit<NodeActions, "onHover" | "onPickId">;
  focus?: GraphFocus | null;
}

export function AgentGraphLens({ model, options, selectedId, nowMs, subs = null, actions, focus = null }: AgentGraphLensProps) {
  const { resolvedTheme } = useTheme();
  const unit = useRootScale();
  const box = useRef<HTMLDivElement | null>(null);
  const [legend, setLegend] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const [namesOnly, setNamesOnly] = useState(false);
  const graph = useMemo(() => layoutGraph(model, { ...options, unit, subs }), [model, options, unit, subs]);
  const { onPick, onFold, onRest, onBucket, onToggleSubs, onPickSession } = actions;
  const pickId = useCallback(
    (sessionId: string) => {
      const session = model.byId.get(sessionId);
      if (session) onPickSession(session);
    },
    [model, onPickSession],
  );
  const lit = useMemo(
    () => (hover && graph.nodes.some((n) => n.id === hover) ? relatedNodes(graph, hover) : null),
    [graph, hover],
  );
  const nodes: Node<NodeData>[] = useMemo(
    () =>
      readingOrder(graph.nodes).map((node) => ({
        id: node.id,
        type: "agentGraph",
        position: { x: node.x, y: node.y },
        width: node.w,
        height: node.h,
        // Decision G4: a move takes 200 ms, and none with reduced motion.
        className: NODE_MOVE,
        data: {
          node,
          selected: selectedId === node.id,
          dim: lit !== null && !lit.has(node.id),
          nowMs,
          nameOf: options.nameOf,
          onPick,
          onFold,
          onHover: setHover,
          subsOpen: (node.kind === "session" || node.kind === "orch") && subs?.sessionId === node.session.id,
          namesOnly,
          onToggleSubs,
          onRest,
          onBucket,
          onPickSession,
          onPickId: pickId,
        },
        draggable: false,
        connectable: false,
        selectable: false,
        focusable: false,
      })),
    [graph, selectedId, lit, nowMs, options.nameOf, onPick, onFold, subs, namesOnly, onToggleSubs, onRest, onBucket, onPickSession, pickId],
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
      data-agent-graph=""
      data-agent-names-only={namesOnly ? "" : undefined}
      className="min-h-0 flex-1"
      style={{ minHeight: 360 }}
      onKeyDown={(e) => {
        // Escape closes the legend first, and keeps the details open.
        if (e.key === "Escape" && legend) {
          e.preventDefault();
          setLegend(false);
        }
      }}
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
          <StartView box={box} graph={graph} resetKey={`${options.person}|${options.mode}|${options.find.trim()}|${unit}`} />
          <ZoomWatch onChange={setNamesOnly} />
          <CenterOn graph={graph} focus={focus} />
          <KeepSelected box={box} graph={graph} selectedId={selectedId} />
          <ShowSubs box={box} graph={graph} sessionId={subs?.sessionId ?? null} />
          {legend ? <Legend /> : null}
          <Controls legend={legend} onLegend={() => setLegend((v) => !v)} />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}

export default AgentGraphLens;
