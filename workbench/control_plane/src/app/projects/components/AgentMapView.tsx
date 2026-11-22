"use client";

/**
 * Agents at work · the Map (WS-MCP SW-103, `agent_coordination.md` §3.12).
 *
 * Reading this as: the same lens as the List, drawn as a tree from left to
 * right: person, then session, then task. Its neighbour is the List beside
 * it, and it reads the same state chips.
 *
 * ⚠️ **Read only.** Nothing drags, connects or edits. A click on a node opens
 * a side sheet with its facts. The sheet opens a task, and nothing else acts.
 *
 * ⚠️ The words of the answer are untrusted. This file draws them as text.
 * Below 640 px the canvas gives way to an outline of nested lists.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useTheme } from "next-themes";

import { ControlLink } from "@/components/ControlLink";
import Icon from "@/components/Icon";
import Modal from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { relativeTime } from "@/lib/taskCard";

import {
  type AgentMap,
  type AgentMapRead,
  CANVAS_LABEL,
  MAP_EMPTY_COPY,
  MAP_ERROR_COPY,
  MAP_TRUNCATED_COPY,
  type MapNode,
  OPEN_TASK_LABEL,
  OUTLINE_LABEL,
  SHOW_QUIET_LABEL,
  agentMapOutline,
  layoutAgentMap,
  runStateChip,
  runStateIcon,
  runStateWord,
  sessionStateChip,
  sessionStateIcon,
  sessionStateWord,
} from "../lib/agentMap";

const CHIP = "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium";

function taskHref(taskId: string): string {
  return `/projects?task=${encodeURIComponent(taskId)}`;
}

/* ── The canvas ─────────────────────────────────────────────────────────── */

interface NodeData extends Record<string, unknown> {
  node: MapNode;
  onPick: (id: string) => void;
}

function StateChip({ node }: { node: MapNode }) {
  if (node.kind === "session" && node.state) {
    return (
      <span className={`${CHIP} ${sessionStateChip(node.state)}`}>
        <Icon name={sessionStateIcon(node.state)} size={12} />
        {sessionStateWord(node.state)}
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

function MapNodeCard({ data }: NodeProps<Node<NodeData>>) {
  const { node, onPick } = data;
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} className="!opacity-0" />
      <button
        type="button"
        data-map-node={node.kind}
        aria-label={`${node.label}. ${node.detail}`.trim()}
        onClick={() => onPick(node.id)}
        style={{ width: node.w, height: node.h }}
        className={`pointer-events-auto flex flex-col justify-center gap-0.5 overflow-hidden rounded-md border border-border bg-card px-3 py-1 text-left text-foreground hover:bg-secondary ${
          node.kind === "quiet" ? "text-xs text-muted-foreground" : ""
        }`}
      >
        {node.kind === "session" ? (
          <>
            <span className="truncate text-sm font-medium">{node.label}</span>
            <span className="flex items-center gap-2">
              <StateChip node={node} />
              {node.detail ? <span className="min-w-0 truncate text-xs text-muted-foreground">{node.detail}</span> : null}
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
      <Handle type="source" position={Position.Right} isConnectable={false} className="!opacity-0" />
    </>
  );
}

const NODE_TYPES = { agentMap: MapNodeCard };

function Canvas({ map, onPick, nowMs }: { map: AgentMap; onPick: (id: string) => void; nowMs: number }) {
  const { resolvedTheme } = useTheme();
  const layout = useMemo(() => layoutAgentMap(map, nowMs), [map, nowMs]);
  const nodes: Node<NodeData>[] = useMemo(
    () =>
      layout.nodes.map((node) => ({
        id: node.id,
        type: "agentMap",
        position: { x: node.x, y: node.y },
        width: node.w,
        height: node.h,
        data: { node, onPick },
        draggable: false,
        connectable: false,
        selectable: false,
        focusable: false,
      })),
    [layout, onPick],
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
        style: edge.kind === "line" ? { strokeDasharray: "4 4" } : undefined,
      })),
    [layout],
  );
  return (
    <div
      role="group"
      aria-label={CANVAS_LABEL}
      data-agent-map-canvas=""
      className="min-h-0 flex-1 basis-96"
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
          colorMode={resolvedTheme === "light" ? "light" : "dark"}
          // The page paints the canvas, so it follows every mode and density.
          style={{ "--xy-background-color": "transparent" } as React.CSSProperties}
          fitView
          minZoom={0.2}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={22} size={1.2} />
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
                      <Icon name={sessionStateIcon(session.state)} size={12} />
                      {session.word}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">{session.client}</p>
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
    return (
      <div className="space-y-3" data-map-facts="session">
        <dl className="space-y-2">
          <Fact term="Person">{session.person}</Fact>
          <Fact term="Client">{session.client}</Fact>
          <Fact term="State">{sessionStateWord(session.state)}</Fact>
          <Fact term="Last active">{relativeTime(session.last_at, nowMs) || "Unknown"}</Fact>
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
}

export function AgentMapView({
  read,
  showQuiet,
  onShowQuiet,
  phone,
  onOpenTask,
  nowMs = Date.now(),
}: AgentMapViewProps) {
  const [pickedId, setPickedId] = useState<string | null>(null);
  const map = read.kind === "ready" ? read.map : null;
  const layout = useMemo(() => (map ? layoutAgentMap(map, nowMs) : null), [map, nowMs]);
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
      <label className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground">
        <Switch checked={showQuiet} onChange={onShowQuiet} label={SHOW_QUIET_LABEL} />
        {SHOW_QUIET_LABEL}
      </label>
      {read.kind === "loading" ? (
        <div className="space-y-2 p-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : read.kind === "ready" && map ? (
        map.sessions.length === 0 ? (
          <p data-agent-map-empty="" className="p-3 text-sm text-muted-foreground">
            {MAP_EMPTY_COPY}
          </p>
        ) : phone ? (
          <Outline map={map} nowMs={nowMs} onOpenTask={open} />
        ) : (
          <Canvas map={map} onPick={setPickedId} nowMs={nowMs} />
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
