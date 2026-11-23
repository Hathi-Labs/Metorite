"use client";

/**
 * Agents at work · the Map (WS-MCP SW-103, `agent_coordination.md` §3.12).
 *
 * Reading this as: the same lens as the List, drawn as a tree from left to
 * right: person, then session, then task. Its neighbour is the List beside
 * it, and it reads the same state chips. SW-106: a session shows its machine
 * and its model, and the switch "Group by machine" puts a machine node
 * between the person and the sessions. SW-109: a dashed arrow runs from a
 * session to each session that it dispatched, and the dispatcher sits to the
 * left of its lanes. The phone outline says "Dispatched by" under a lane.
 * SW-110: a session with sub-agents has a "Show N sub-agents" button. Open,
 * each sub-agent draws to the right of its task, with its state, its own
 * model and its age. The phone outline lists them under their task.
 *
 * ⚠️ **Read only.** Nothing drags, connects or edits. A click on a node opens
 * a side sheet with its facts. The sheet opens a task, and nothing else acts.
 *
 * ⚠️ The words of the answer are untrusted. This file draws them as text.
 * Below 640 px the canvas gives way to an outline of nested lists.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useTheme } from "next-themes";

import { ControlLink } from "@/components/ControlLink";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { relativeTime } from "@/lib/taskCard";

import {
  type AgentMap,
  type AgentMapRead,
  CANVAS_LABEL,
  DISPATCHED_BY_LABEL,
  DISPATCHED_LABEL,
  GROUP_BY_MACHINE_LABEL,
  MAP_EMPTY_COPY,
  MAP_ERROR_COPY,
  MAP_TRUNCATED_COPY,
  type MapNode,
  NODE_H,
  OPEN_TASK_LABEL,
  OUTLINE_LABEL,
  SHOW_QUIET_LABEL,
  agentMapOutline,
  childStateChip,
  childStateIcon,
  childStartedWhen,
  childStateWord,
  childToggleLabel,
  dispatchGraph,
  groupByMachine,
  layoutAgentMap,
  runStateChip,
  runStateIcon,
  runStateWord,
  sessionPlace,
  sessionStateChip,
  sessionStateIcon,
  sessionStateWord,
} from "../lib/agentMap";
import {
  MODEL_ICON,
  NOT_REPORTED,
  type Place,
  hostKindIcon,
  hostKindWord,
  hostSourceSentence,
  hostSourceWord,
  placeSentence,
} from "../lib/agentPlace";

const CHIP = "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium";

function taskHref(taskId: string): string {
  return `/projects?task=${encodeURIComponent(taskId)}`;
}

/* ── The canvas ─────────────────────────────────────────────────────────── */

interface NodeData extends Record<string, unknown> {
  node: MapNode;
  onPick: (id: string) => void;
  /** SW-110. Open or close the sub-agents of a session. */
  onToggle: (sessionId: string) => void;
}

/**
 * SW-110. The control that opens the sub-agents of a session. It is a real
 * button with `aria-expanded`. xyflow turns pointer events off on a node that
 * is not interactive, so the button turns them on again.
 */
export function SubAgentToggle({
  count,
  expanded,
  onToggle,
}: {
  count: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      icon={expanded ? "ChevronDown" : "ChevronRight"}
      aria-expanded={expanded}
      data-map-children-toggle=""
      onClick={onToggle}
      className="pointer-events-auto mt-1 self-start"
    >
      {childToggleLabel(count, expanded)}
    </Button>
  );
}

function StateChip({ node }: { node: MapNode }) {
  if (node.kind === "session" && node.state) {
    return (
      <span className={`${CHIP} ${sessionStateChip(node.state, node.quietWait)}`}>
        <Icon name={sessionStateIcon(node.state, node.quietWait)} size={12} />
        {sessionStateWord(node.state, node.quietWait)}
      </span>
    );
  }
  if (node.kind === "child") {
    const child = { state: node.runState ?? "" };
    return (
      <span className={`${CHIP} ${childStateChip(child)}`}>
        <Icon name={childStateIcon(child)} size={12} />
        {childStateWord(child)}
      </span>
    );
  }
  if (node.kind === "task") {
    const run = { state: node.runState ?? "", quiet: node.quiet === true };
    return (
      <span className={`${CHIP} ${runStateChip(run)}`}>
        <Icon name={runStateIcon(run)} size={12} />
        {runStateWord(run)}
      </span>
    );
  }
  return null;
}

/** The machine and the model of a session, as two short lines (SW-106). */
function PlaceLines({ place }: { place: Place }) {
  return (
    <>
      <span
        data-map-machine=""
        className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground"
        title={hostKindWord(place.kind)}
      >
        <Icon name={hostKindIcon(place.kind)} size={12} className="shrink-0" aria-hidden />
        <span className="min-w-0 truncate">{place.host ?? NOT_REPORTED}</span>
        {place.source ? (
          <span data-map-source={place.source} className="shrink-0">
            {hostSourceWord(place.source)}
          </span>
        ) : null}
      </span>
      <span data-map-model="" className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
        <Icon name={MODEL_ICON} size={12} className="shrink-0" aria-hidden />
        <span className="min-w-0 truncate">{place.model ?? NOT_REPORTED}</span>
      </span>
    </>
  );
}

function nodeLabel(node: MapNode): string {
  const place = node.place;
  if (node.kind === "child") {
    const parts = [`Sub-agent ${node.label}`, childStateWord({ state: node.runState ?? "" })];
    if (node.model) parts.push(`Model ${node.model}`);
    if (node.detail) parts.push(node.detail);
    return `${parts.join(". ")}.`;
  }
  if (node.kind === "session" && place) return `${node.label}. ${node.detail}. ${placeSentence(place)}`.trim();
  if (node.kind === "machine" && place) {
    return `Machine ${node.label}. ${hostKindWord(place.kind)}. ${node.detail}.`;
  }
  return `${node.label}. ${node.detail}`.trim();
}

/** SW-110. The second line of a sub-agent: its own model when it has one, then its age. */
function ChildLine({ model, age }: { model: string | null | undefined; age: string }) {
  if (!model && !age) return null;
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
      {model ? (
        <>
          <Icon name={MODEL_ICON} size={12} className="shrink-0" aria-hidden />
          <span data-map-child-model="" className="min-w-0 truncate">
            {model}
          </span>
        </>
      ) : null}
      {model && age ? <span aria-hidden>{"\u00b7"}</span> : null}
      {age ? <span className="shrink-0">{age}</span> : null}
    </span>
  );
}

function MapNodeCard({ data }: NodeProps<Node<NodeData>>) {
  const { node, onPick, onToggle } = data;
  const toggle = node.kind === "session" && (node.childCount ?? 0) > 0;
  const card = (
    <button
      type="button"
      data-map-node={node.kind}
      aria-label={nodeLabel(node)}
      onClick={() => onPick(node.id)}
      style={{ width: node.w, height: toggle ? NODE_H.session : node.h }}
      className={`pointer-events-auto flex flex-col justify-center gap-0.5 overflow-hidden rounded-md border border-border bg-card px-3 py-1 text-left text-foreground hover:bg-secondary ${
        node.kind === "quiet" ? "text-xs text-muted-foreground" : ""
      }`}
    >
      {node.kind === "session" ? (
        <>
          <span className="truncate text-sm font-medium">{node.label}</span>
          <span className="flex items-center gap-2">
            <StateChip node={node} />
            {node.detail ? (
              <span className="min-w-0 truncate text-xs text-muted-foreground" title={node.detail}>
                {node.detail}
              </span>
            ) : null}
          </span>
          {node.place ? <PlaceLines place={node.place} /> : null}
        </>
      ) : node.kind === "child" ? (
        <>
          <span className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{node.label}</span>
            <StateChip node={node} />
          </span>
          <ChildLine model={node.model} age={node.detail} />
        </>
      ) : node.kind === "machine" && node.place ? (
        <>
          <span className="flex min-w-0 items-center gap-2">
            <Icon name={hostKindIcon(node.place.kind)} size={14} className="shrink-0" aria-hidden />
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{node.label}</span>
          </span>
          <span className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
            <span className="truncate">{node.detail}</span>
            {node.place.source ? (
              <span data-map-source={node.place.source} className="shrink-0">
                {hostSourceWord(node.place.source)}
              </span>
            ) : null}
          </span>
        </>
      ) : (
        <>
          <span className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{node.label}</span>
            <StateChip node={node} />
          </span>
          {node.detail ? <span className="truncate text-xs text-muted-foreground">{node.detail}</span> : null}
        </>
      )}
    </button>
  );
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} className="!opacity-0" />
      {toggle ? (
        <div style={{ width: node.w, height: node.h }} className="flex flex-col">
          {card}
          <SubAgentToggle
            count={node.childCount ?? 0}
            expanded={node.expanded === true}
            onToggle={() => onToggle(node.ref)}
          />
        </div>
      ) : (
        card
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} className="!opacity-0" />
    </>
  );
}

const DISPATCH_LINE = "var(--muted-foreground)";

const NODE_TYPES = { agentMap: MapNodeCard };

/** The fit that every map uses. */
const FIT_OPTIONS = { padding: 0.08, maxZoom: 1 };
const REFIT_MS = 150;

/**
 * Fits the map again when the box settles or changes size, because the first
 * fit runs while the box is still short. It stops once the member moves the map.
 */
function RefitOnResize({ box, moved }: { box: React.RefObject<HTMLDivElement | null>; moved: React.RefObject<boolean> }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let timer: number | undefined;
    const watcher = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (!moved.current) void fitView(FIT_OPTIONS);
      }, REFIT_MS);
    });
    watcher.observe(el);
    return () => {
      window.clearTimeout(timer);
      watcher.disconnect();
    };
  }, [box, moved, fitView]);
  return null;
}

/**
 * The width of the map changes with the data, for example when a refresh adds
 * the first dispatch line. Fit again then, unless the member moved the map.
 * It never remounts the canvas, so a pan and a zoom stay.
 */
function RefitOnWidth({ width, moved }: { width: number; moved: React.RefObject<boolean> }) {
  const { fitView } = useReactFlow();
  const last = useRef(width);
  useEffect(() => {
    if (last.current === width) return;
    last.current = width;
    if (moved.current) return;
    const timer = window.setTimeout(() => void fitView(FIT_OPTIONS), REFIT_MS);
    return () => window.clearTimeout(timer);
  }, [width, moved, fitView]);
  return null;
}

function Canvas({
  map,
  onPick,
  onToggle,
  nowMs,
  byMachine,
  expanded,
}: {
  map: AgentMap;
  onPick: (id: string) => void;
  onToggle: (sessionId: string) => void;
  nowMs: number;
  byMachine: boolean;
  expanded: ReadonlySet<string>;
}) {
  const { resolvedTheme } = useTheme();
  const box = useRef<HTMLDivElement | null>(null);
  const moved = useRef(false);
  const layout = useMemo(
    () => layoutAgentMap(map, nowMs, { groupByMachine: byMachine, expanded }),
    [map, nowMs, byMachine, expanded],
  );
  const width = useMemo(() => layout.nodes.reduce((m, n) => Math.max(m, n.x + n.w), 0), [layout]);
  const nodes: Node<NodeData>[] = useMemo(
    () =>
      layout.nodes.map((node) => ({
        id: node.id,
        type: "agentMap",
        position: { x: node.x, y: node.y },
        width: node.w,
        height: node.h,
        data: { node, onPick, onToggle },
        draggable: false,
        connectable: false,
        selectable: false,
        focusable: false,
      })),
    [layout, onPick, onToggle],
  );
  const edges: Edge[] = useMemo(
    () =>
      layout.edges.map((edge) => ({
        id: edge.id,
        source: edge.from,
        target: edge.to,
        focusable: false,
        selectable: false,
        animated: false,
        // A dispatch line is dashed and points at the lane. It uses the same token as the text.
        ...(edge.kind === "line"
          ? {
              style: { strokeDasharray: "4 4", stroke: DISPATCH_LINE },
              markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: DISPATCH_LINE },
            }
          : {}),
      })),
    [layout],
  );
  return (
    <div
      ref={box}
      role="group"
      aria-label={CANVAS_LABEL}
      data-agent-map-canvas=""
      className="min-h-0 flex-1 basis-96"
      style={{ minHeight: 360 }}
    >
      <ReactFlowProvider>
        <ReactFlow
          // A new key refits the view when the switch changes the layout width.
          key={byMachine ? "by-machine" : "by-person"}
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          colorMode={resolvedTheme === "light" ? "light" : "dark"}
          // The page paints the canvas, so it follows every mode and density.
          style={{ "--xy-background-color": "transparent" } as React.CSSProperties}
          fitView
          fitViewOptions={FIT_OPTIONS}
          // An event means the member moved the map. A fit by code has none.
          onMoveStart={(event) => {
            if (event) moved.current = true;
          }}
          minZoom={0.2}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={22} size={1.2} />
          <RefitOnResize box={box} moved={moved} />
          <RefitOnWidth width={width} moved={moved} />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}

/* ── The outline of a phone ─────────────────────────────────────────────── */

function Outline({
  map,
  nowMs,
  onOpenTask,
}: {
  map: AgentMap;
  nowMs: number;
  onOpenTask: (taskId: string) => void;
}) {
  const outline = useMemo(() => agentMapOutline(map, nowMs), [map, nowMs]);
  return (
    <nav aria-label={OUTLINE_LABEL} data-agent-map-outline="" className="p-3">
      <ul className="space-y-3">
        {outline.map((person) => (
          <li key={person.person} data-outline-person="">
            <span className="text-sm font-semibold text-foreground">{person.person}</span>
            <ul className="ml-3 mt-1 space-y-2 border-l border-border pl-3">
              {person.sessions.map((session) => (
                <li key={session.id} data-outline-session="">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{session.name}</span>
                    <span className={`${CHIP} ${session.chip}`}>
                      <Icon name={sessionStateIcon(session.state, session.quietWait)} size={12} />
                      {session.word}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {session.lastEvent ? `${session.client} \u00b7 ${session.lastEvent}` : session.client}
                  </p>
                  {session.dispatchedBy.length > 0 ? (
                    <p data-outline-dispatched-by="" className="text-xs text-muted-foreground">
                      {DISPATCHED_BY_LABEL} {session.dispatchedBy.join(", ")}
                    </p>
                  ) : null}
                  <div data-outline-place="" className="space-y-0.5">
                    <PlaceLines place={session.place} />
                  </div>
                  {session.tasks.length > 0 || session.quiet ? (
                    <ul className="ml-3 mt-1 space-y-1 border-l border-border pl-3">
                      {session.tasks.map((task) => (
                        <li key={task.runId} data-outline-task="" className="flex flex-wrap items-center gap-2">
                          <ControlLink
                            href={taskHref(task.taskId)}
                            onActivate={() => onOpenTask(task.taskId)}
                            className="min-w-0 truncate text-xs font-medium text-foreground hover:underline"
                            title={`${OPEN_TASK_LABEL}: ${task.title}`}
                          >
                            {task.title}
                          </ControlLink>
                          <span className={`${CHIP} ${task.chip}`}>{task.word}</span>
                          {task.children.length > 0 ? (
                            <ul className="ml-3 w-full space-y-1 border-l border-border pl-3">
                              {task.children.map((child) => (
                                <li
                                  key={child.id}
                                  data-outline-child=""
                                  className="flex flex-wrap items-center gap-2 text-xs"
                                >
                                  <span className="font-medium text-foreground">{child.name}</span>
                                  <span className={`${CHIP} ${child.chip}`}>{child.word}</span>
                                  <ChildLine model={child.model} age={child.age} />
                                </li>
                              ))}
                            </ul>
                          ) : null}
                        </li>
                      ))}
                      {session.quiet ? (
                        <li data-outline-quiet="" className="text-xs text-muted-foreground">
                          {session.quiet}
                        </li>
                      ) : null}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/* ── The side sheet ─────────────────────────────────────────────────────── */

function Fact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <dt className="w-28 shrink-0 text-muted-foreground">{term}</dt>
      <dd className="min-w-0 flex-1 break-words text-foreground">{children}</dd>
    </div>
  );
}

/** Machine, Kind, Place and Model, in the words of §3.12.3 (SW-106). */
function PlaceFacts({ place }: { place: Place }) {
  return (
    <>
      <Fact term="Machine">{place.host ?? NOT_REPORTED}</Fact>
      <Fact term="Kind">{hostKindWord(place.kind)}</Fact>
      <Fact term="Place">{hostSourceSentence(place.source)}</Fact>
      <Fact term="Model">{place.model ?? NOT_REPORTED}</Fact>
    </>
  );
}

function OpenTask({ taskId, onOpenTask }: { taskId: string; onOpenTask: (taskId: string) => void }) {
  return (
    <ControlLink
      href={taskHref(taskId)}
      onActivate={() => onOpenTask(taskId)}
      className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
    >
      <Icon name="ExternalLink" size={14} />
      {OPEN_TASK_LABEL}
    </ControlLink>
  );
}

function NodeFacts({
  node,
  map,
  nowMs,
  onOpenTask,
}: {
  node: MapNode;
  map: AgentMap;
  nowMs: number;
  onOpenTask: (taskId: string) => void;
}) {
  if (node.kind === "person") {
    const sessions = map.sessions.filter((s) => s.person === node.ref);
    return (
      <dl className="space-y-2" data-map-facts="person">
        <Fact term="Person">{node.label}</Fact>
        <Fact term="Sessions">{sessions.length}</Fact>
      </dl>
    );
  }
  if (node.kind === "session") {
    const session = map.sessions.find((s) => s.id === node.ref);
    if (!session) return null;
    const runs = map.runs.filter((r) => r.session_id === session.id);
    const dispatch = dispatchGraph(map);
    const dispatchedBy = (dispatch.dispatchers.get(session.id) ?? []).map(
      (id) => map.sessions.find((s) => s.id === id)?.name ?? "",
    );
    const lanes = dispatch.dispatched.get(session.id)?.length ?? 0;
    return (
      <div className="space-y-3" data-map-facts="session">
        <dl className="space-y-2">
          <Fact term="Person">{session.person}</Fact>
          <Fact term="Client">{session.client}</Fact>
          <PlaceFacts place={sessionPlace(session)} />
          <Fact term="State">{sessionStateWord(session.state, dispatch.waiting.has(session.id))}</Fact>
          <Fact term="Last active">{relativeTime(session.last_at, nowMs) || "Unknown"}</Fact>
          {dispatchedBy.length > 0 ? <Fact term={DISPATCHED_BY_LABEL}>{dispatchedBy.join(", ")}</Fact> : null}
          {lanes > 0 ? <Fact term={DISPATCHED_LABEL}>{lanes === 1 ? "1 session" : `${lanes} sessions`}</Fact> : null}
          <Fact term="Open runs">{session.run_count}</Fact>
          <Fact term="Quiet runs">{session.quiet_count}</Fact>
        </dl>
        {runs.length > 0 ? (
          <ul className="divide-y divide-border">
            {runs.map((run) => (
              <li key={run.run_id} className="flex flex-wrap items-center gap-2 py-1.5">
                <span className="min-w-0 flex-1 truncate text-sm text-foreground">{run.title}</span>
                <span className={`${CHIP} ${runStateChip(run)}`}>{runStateWord(run)}</span>
                <OpenTask taskId={run.task_id} onOpenTask={onOpenTask} />
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  }
  if (node.kind === "machine" && node.place) {
    const group = groupByMachine(map.sessions).find((g) => g.key === node.ref);
    return (
      <dl className="space-y-2" data-map-facts="machine">
        <Fact term="Machine">{node.place.host ?? NOT_REPORTED}</Fact>
        <Fact term="Kind">{hostKindWord(node.place.kind)}</Fact>
        <Fact term="Place">{hostSourceSentence(node.place.source)}</Fact>
        <Fact term="Sessions">{group?.sessions.length ?? 0}</Fact>
      </dl>
    );
  }
  if (node.kind === "child") {
    const child = map.children.find((c) => c.child_id === node.ref);
    const run = map.runs.find((r) => r.run_id === node.runId);
    if (!child || !run) return null;
    return (
      <div className="space-y-3" data-map-facts="child">
        <dl className="space-y-2">
          <Fact term="State">{childStateWord(child)}</Fact>
          {child.model ? <Fact term="Model">{child.model}</Fact> : null}
          {child.age !== null ? <Fact term="Started">{childStartedWhen(child.age, nowMs)}</Fact> : null}
          <Fact term="Task">{run.title}</Fact>
        </dl>
        <OpenTask taskId={run.task_id} onOpenTask={onOpenTask} />
      </div>
    );
  }
  if (node.kind === "task") {
    const run = map.runs.find((r) => r.run_id === node.ref);
    if (!run) return null;
    return (
      <div className="space-y-3" data-map-facts="task">
        <dl className="space-y-2">
          <Fact term="Task">{run.title}</Fact>
          {run.task_number !== null ? <Fact term="Number">{run.task_number}</Fact> : null}
          <Fact term="State">{runStateWord(run)}</Fact>
          <Fact term="Last event">{relativeTime(run.last_at, nowMs) || "Unknown"}</Fact>
        </dl>
        <OpenTask taskId={run.task_id} onOpenTask={onOpenTask} />
      </div>
    );
  }
  return (
    <p className="text-sm text-foreground" data-map-facts="quiet">
      {node.label}. Turn on {SHOW_QUIET_LABEL} to list them.
    </p>
  );
}

/* ── The view ───────────────────────────────────────────────────────────── */

export interface AgentMapViewProps {
  read: AgentMapRead;
  showQuiet: boolean;
  onShowQuiet: (next: boolean) => void;
  /** True below 640 px: the outline takes the place of the canvas. */
  phone: boolean;
  onOpenTask?: (taskId: string) => void;
  nowMs?: number;
  /** SW-112: the empty-state copy. A project map says no agent works on it. */
  emptyCopy?: string;
}

export function AgentMapView({
  read,
  showQuiet,
  onShowQuiet,
  phone,
  onOpenTask,
  nowMs = Date.now(),
  emptyCopy = MAP_EMPTY_COPY,
}: AgentMapViewProps) {
  const [pickedId, setPickedId] = useState<string | null>(null);
  // The switch is local: it is a view of the map, not a setting that persists.
  const [byMachine, setByMachine] = useState(false);
  // SW-110. The sessions whose sub-agents show. Local to the view: no store and no URL.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = useCallback((sessionId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }, []);
  const map = read.kind === "ready" ? read.map : null;
  const layout = useMemo(
    () => (map ? layoutAgentMap(map, nowMs, { groupByMachine: byMachine, expanded }) : null),
    [map, nowMs, byMachine, expanded],
  );
  const picked = layout?.nodes.find((n) => n.id === pickedId) ?? null;
  const open = useCallback(
    (taskId: string) => {
      setPickedId(null);
      if (onOpenTask) onOpenTask(taskId);
      else window.location.assign(taskHref(taskId));
    },
    [onOpenTask],
  );
  useEffect(() => {
    if (pickedId && !picked) setPickedId(null);
  }, [pickedId, picked]);

  return (
    <div data-agent-map="" className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-3 py-2 text-xs text-muted-foreground">
        <label className="flex items-center gap-2">
          <Switch checked={showQuiet} onChange={onShowQuiet} label={SHOW_QUIET_LABEL} />
          {SHOW_QUIET_LABEL}
        </label>
        {phone ? null : (
          <label className="flex items-center gap-2" data-agent-map-group="">
            <Switch checked={byMachine} onChange={setByMachine} label={GROUP_BY_MACHINE_LABEL} />
            {GROUP_BY_MACHINE_LABEL}
          </label>
        )}
      </div>
      {read.kind === "loading" ? (
        <div className="space-y-2 p-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : read.kind === "ready" && map ? (
        map.sessions.length === 0 ? (
          <p data-agent-map-empty="" className="p-3 text-sm text-muted-foreground">
            {emptyCopy}
          </p>
        ) : phone ? (
          <Outline map={map} nowMs={nowMs} onOpenTask={open} />
        ) : (
          <Canvas
            map={map}
            onPick={setPickedId}
            onToggle={toggle}
            nowMs={nowMs}
            byMachine={byMachine}
            expanded={expanded}
          />
        )
      ) : (
        <p role="alert" data-agent-map-error="" className="p-3 text-sm text-foreground">
          {MAP_ERROR_COPY}
        </p>
      )}
      {map?.truncated ? <p className="p-3 text-xs text-muted-foreground">{MAP_TRUNCATED_COPY}</p> : null}
      <Modal
        open={picked !== null}
        onClose={() => setPickedId(null)}
        title={picked ? picked.label : ""}
        placement="end"
        size="md"
      >
        {picked && map ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <NodeFacts node={picked} map={map} nowMs={nowMs} onOpenTask={open} />
          </div>
        ) : null}
      </Modal>
    </div>
  );
}

export default AgentMapView;
