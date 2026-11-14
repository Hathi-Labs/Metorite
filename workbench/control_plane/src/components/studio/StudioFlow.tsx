"use client";

/**
 * The Process flow block (`ai_studio.md` §5.1, slice STU-D8).
 *
 * A read-only workflow graph with the state of each step of one run. The cards and the wires are
 * the ones of the workflow editor (`WorkflowNode`, `WorkflowEdge`) on `@xyflow/react`, so the
 * block adds no second renderer and the editor has no change. The page draws no drag, no connect
 * and no delete. A card shows its label and its type only: `flowModel.ts` drops the config of a
 * step, and the block never reads the hook token or the hook URL of the workflow answer.
 *
 * Run state comes from the run console's own door. The block reads the workflow, then the latest
 * run (or the run that the node names). A run that is still live opens the SSE stream of the
 * console (`/api/workflows/runs/<id>/stream`) and sets one step for each `node` frame. If the
 * stream is not there or drops, the block reads the run again every few seconds. The renderer
 * owns the loading, error and empty states (§3.4), so a manifest writes none of them.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Background, BackgroundVariant, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useTheme } from "next-themes";

import WorkflowEdge from "@/app/workflows/components/WorkflowEdge";
import WorkflowNode from "@/app/workflows/components/WorkflowNode";
import { ApiError, getRun, getWorkflow, listRuns } from "@/app/workflows/lib/api";
import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  type FlowEdgeView, type FlowNodeView, type FlowRunFacts, type FlowSource, type NodeStates,
  applyRunEvent, flowCanRetry, flowGraph, flowReadError, isLive, paintFlow, runState, runStatusOf,
  runWhen, stateCounts, statesFromRun,
} from "@/lib/studio/flowModel";
import { followRun, type StreamLike } from "@/lib/studio/flowFollow";
import { FLOW_STATES, type FlowState, flowStateChip } from "@/lib/statusAccent";

const nodeTypes = { ccNode: WorkflowNode };
const edgeTypes = { ccEdge: WorkflowEdge };

/** How long the block waits between two reads of a live run that has no stream. */
export const POLL_MS = 3000;

/** One pill: the glyph, the word and, for a count, the number. */
export function FlowChip({ state, count }: { state: FlowState; count?: number }) {
  const v = FLOW_STATES[state];
  return (
    <span
      data-flow-chip={state}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${flowStateChip(state)}`}
    >
      <Icon name={v.icon} className={`h-3 w-3 ${state === "running" ? "animate-spin" : ""}`} />
      {v.word}
      {count !== undefined && <span className="tabular-nums">{count}</span>}
    </span>
  );
}

/** The wire of the editor, as the editor draws it, but a status never reads the accent (`--info`, not `--primary`): lit where the run flows, dashed for the no branch. */
function edgeLook(e: FlowEdgeView): Edge {
  const { branch, hot } = e.data;
  const stroke = hot ? "var(--info)" : branch === "true" ? "var(--success)" : "var(--muted-foreground)";
  return {
    ...e,
    animated: hot,
    style: {
      stroke,
      strokeWidth: hot ? 2.2 : 1.6,
      strokeOpacity: hot ? 1 : branch === "true" ? 0.75 : 0.5,
      strokeDasharray: branch === "false" ? "5 4" : undefined,
    },
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: stroke },
  } as Edge;
}

/** The graph alone: no state of its own, no network. A test and the page both draw it. */
export function FlowGraph({
  nodes, edges, dark = false, height,
}: { nodes: FlowNodeView[]; edges: FlowEdgeView[]; dark?: boolean; height?: number }) {
  // A tall graph gets a tall frame, so a card stays readable. The frame stops at 900 px.
  const frame = height ?? Math.min(900, Math.max(280, Math.max(0, ...nodes.map((n) => n.position.y)) + 200));
  const rfEdges = useMemo(() => edges.map(edgeLook), [edges]);
  return (
    <div
      data-flow-graph=""
      className="w-full overflow-hidden rounded-xl border border-border bg-card"
      style={{ height: frame }}
    >
      <ReactFlow
        nodes={nodes as unknown as Node[]}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        edgesFocusable={false}
        deleteKeyCode={null}
        zoomOnScroll={false}
        preventScrolling={false}
        colorMode={dark ? "dark" : "light"}
        fitView
        fitViewOptions={{ padding: 0.06, maxZoom: 1.1 }}
        minZoom={0.3}
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ type: "ccEdge" }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

/** The loading, error and empty states of the block, in plain words. */
export function FlowStatus({
  phase, error, onRetry,
}: { phase: "loading" | "error" | "empty"; error?: string | null; onRetry?: () => void }) {
  if (phase === "loading") {
    return (
      <div className="space-y-2" aria-busy="true" data-flow-state="loading">
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (phase === "error") {
    return (
      <div className="flex items-center gap-2 text-xs text-destructive" role="alert" data-flow-state="error">
        <span>{error || flowReadError(undefined)}</span>
        {onRetry && <Button type="button" variant="secondary" size="sm" onClick={onRetry}>Try again</Button>}
      </div>
    );
  }
  return (
    <div data-flow-state="empty">
      <EmptyState icon="Workflow" message="This workflow has no steps yet" />
    </div>
  );
}

/** The line above the graph: the run in words, then one pill for each state that a step is in. */
export function FlowSummary({
  title, run, nodes, nowMs,
}: { title?: string; run: FlowRunFacts | null; nodes: FlowNodeView[]; nowMs?: number }) {
  const counts = stateCounts(nodes);
  const when = run ? runWhen(run, nowMs) : "";
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" data-flow-summary="">
      {title && <span className="font-medium text-foreground">{title}</span>}
      {run ? (
        <>
          <FlowChip state={runState(run.status)} />
          {when && <span className="text-xs text-muted-foreground">{when}</span>}
        </>
      ) : (
        <span className="text-xs text-muted-foreground" data-flow-norun="">This workflow has not run yet</span>
      )}
      <span className="ml-auto flex flex-wrap items-center gap-1" aria-label="Steps by state">
        {counts.map((c) => <FlowChip key={c.state} state={c.state} count={c.count} />)}
      </span>
    </div>
  );
}

interface FlowLoad {
  phase: "loading" | "error" | "ready";
  error: string | null;
  title: string;
  graph: { nodes: FlowNodeView[]; edges: FlowEdgeView[] };
  run: FlowRunFacts | null;
  states: NodeStates;
}

const BLANK: FlowLoad = { phase: "loading", error: null, title: "", graph: { nodes: [], edges: [] }, run: null, states: {} };

const faultOf = (e: unknown): string => flowReadError(e instanceof ApiError ? e.status : undefined);

/** Reads the workflow and a run, and follows the run while it is live. */
export function useFlowRun(source: FlowSource, retry: number): FlowLoad {
  const [load, setLoad] = useState<FlowLoad>(BLANK);
  const stopRef = useRef<(() => void) | null>(null);

  const stop = useCallback(() => {
    stopRef.current?.();
    stopRef.current = null;
  }, []);

  useEffect(() => {
    let dead = false;
    setLoad(BLANK);

    const follow = (runId: string) => {
      stopRef.current = followRun(runId, {
        openStream: typeof EventSource === "undefined" ? null : (url) => new EventSource(url) as unknown as StreamLike,
        read: getRun,
        pollMs: POLL_MS,
        onFrame: (ev) => {
          if (dead) return;
          setLoad((l) => {
            const status = runStatusOf(ev);
            return { ...l, states: applyRunEvent(l.states, ev), run: status && l.run ? { ...l.run, status } : l.run };
          });
        },
        onRead: (d) => {
          if (dead) return;
          setLoad((l) => ({ ...l, run: d as FlowRunFacts, states: statesFromRun(d.node_results) }));
        },
      });
    };

    (async () => {
      try {
        const wf = await getWorkflow(source.workflow);
        const graph = flowGraph(wf.graph);
        const title = typeof wf.name === "string" ? wf.name : "";
        let run: FlowRunFacts | null = null;
        let states: NodeStates = {};
        let runId = source.run ?? null;
        if (!runId) runId = (await listRuns(source.workflow, 1))[0]?.id ?? null;
        if (runId) {
          const d = await getRun(runId);
          run = d;
          states = statesFromRun(d.node_results);
          if (isLive(d.status)) follow(runId);
        }
        if (!dead) setLoad({ phase: "ready", error: null, title, graph, run, states });
      } catch (e) {
        if (!dead) setLoad({ ...BLANK, phase: "error", error: faultOf(e) });
      }
    })();

    return () => { dead = true; stop(); };
  }, [source.workflow, source.run, retry, stop]);

  return load;
}

function StudioFlowInner({ source }: { source: FlowSource }) {
  const [retry, setRetry] = useState(0);
  const { resolvedTheme } = useTheme();
  const load = useFlowRun(source, retry);
  const painted = useMemo(() => paintFlow(load.graph, load.states), [load.graph, load.states]);

  if (load.phase === "loading") return <FlowStatus phase="loading" />;
  if (load.phase === "error") return <FlowStatus phase="error" error={load.error} onRetry={flowCanRetry(load.error) ? () => setRetry((n) => n + 1) : undefined} />;
  if (load.graph.nodes.length === 0) return <FlowStatus phase="empty" />;
  return (
    <div className="min-w-0 space-y-2" data-studio-flow="">
      <FlowSummary title={load.title} run={load.run} nodes={painted.nodes} />
      <FlowGraph nodes={painted.nodes} edges={painted.edges} dark={resolvedTheme !== "light"} />
    </div>
  );
}

export const StudioFlow = memo(StudioFlowInner);
export default StudioFlow;
