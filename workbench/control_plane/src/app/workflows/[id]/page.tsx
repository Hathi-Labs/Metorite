"use client";

/**
 * /workflows/[id] — the visual workflow editor (RFC §5: three panes + run
 * console; spec F2–F6). React Flow canvas; palette + inspector are served
 * from the gateway catalog; Test ▸ runs the draft and streams per-node
 * status onto the canvas over SSE.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import {
  Suspense,
  use,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useTheme } from "next-themes";
import { useAccess } from "@/components/AccessProvider";
import { hasCapability } from "@/lib/access";
import CopilotPanel from "../components/CopilotPanel";
import NodeInspector from "../components/NodeInspector";
import NodePalette, { type PaletteDrop } from "../components/NodePalette";
import RunConsole from "../components/RunConsole";
import TriggerPanel from "../components/TriggerPanel";
import WorkflowEdge from "../components/WorkflowEdge";
import WorkflowNode, { type CCNodeData } from "../components/WorkflowNode";
import {
  ApiError,
  disableWorkflow,
  enableWorkflow,
  getCatalog,
  getWorkflow,
  publishWorkflow,
  rollbackWorkflow,
  runWorkflow,
  updateWorkflow,
  validateWorkflow,
} from "../lib/api";
import type {
  Catalog,
  GraphIssue,
  NodeResult,
  NodeType,
  RunEvent,
  TriggerSpec,
  WorkflowDetail,
  WorkflowGraph,
  WorkflowGraphNode,
} from "../lib/types";

const nodeTypes = { ccNode: WorkflowNode };
const edgeTypes = { ccEdge: WorkflowEdge };

const TRIGGER_KIND_HINTS = [
  { kind: "manual" as const, hint: "Run button / POST /workflows/{id}/run" },
  { kind: "webhook" as const, hint: "per-workflow tokened URL (+ optional HMAC)" },
  { kind: "schedule" as const, hint: "cron expression, UTC" },
  { kind: "event" as const, hint: "platform events (Zoho/Gmail changes)" },
];

/** Edit-model graph → React Flow state (used on load and on copilot apply). */
function flowFromGraph(
  graph: WorkflowGraph,
  catalog: Catalog | null,
): { nodes: Node[]; edges: Edge[] } {
  const graphNodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const graphEdges = Array.isArray(graph?.edges) ? graph.edges : [];
  return {
    nodes: graphNodes.map((n) => ({
      id: n.id,
      type: "ccNode",
      position: n.position ?? { x: 0, y: 0 },
      data: {
        label: n.data?.label ?? n.id,
        nodeType: n.type,
        config: n.data?.config ?? {},
        ...describeNode(n.type, n.data?.config ?? {}, catalog),
      },
    })),
    // Branch labels and colour are derived at render time (displayEdges), so
    // nothing about presentation is baked into the persisted graph.
    edges: graphEdges.map((e) => ({
      id: e.id ?? `e_${e.source}_${e.sourceHandle ?? "out"}_${e.target}`,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? undefined,
    })),
  };
}

/** "2 hours" / "30 seconds" — the wait node's card summary. */
function humanDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const units: [number, string][] = [
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
    [1, "second"],
  ];
  for (const [secs, name] of units) {
    if (seconds >= secs) {
      const n = Math.round((seconds / secs) * 10) / 10;
      return `${n} ${name}${n === 1 ? "" : "s"}`;
    }
  }
  return `${seconds}s`;
}

/** Single-line clip for card text — the card clamps, this stops giant strings. */
function clip(value: unknown, max = 160): string {
  const s = String(value ?? "").replace(/\s+/g, " ").trim();
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/**
 * What a card says about itself (RFC §5: "legible at a glance"). Every node
 * type answers the same three questions from its own config:
 *
 *   summary — what this step will do, in words
 *   detail  — the capability it is bound to (agent, action, expression)
 *   badges  — flags worth seeing before you select it, e.g. a destructive write
 *
 * Unconfigured nodes say so rather than rendering blank: a card that reads
 * "no agent selected yet" is the same signal the validator will give at publish.
 */
function describeNode(
  type: NodeType,
  config: Record<string, unknown>,
  catalog: Catalog | null,
): { summary: string; detail: string; badges: string[] } {
  const none = { summary: "", detail: "", badges: [] as string[] };

  if (type === "trigger") {
    return {
      summary: "Entry point — the payload arrives downstream as {{trigger.*}}.",
      detail: "",
      badges: [],
    };
  }

  if (type === "agent") {
    const agent = String(config.agent ?? "");
    const known = catalog?.agents.find((a) => a.name === agent);
    const message = clip(config.message);
    const model = String(config.model ?? "");
    return {
      summary:
        message ||
        clip(known?.description) ||
        (agent ? "Runs this agent with no instruction yet." : "No agent selected yet."),
      detail: agent,
      badges: model ? [model] : [],
    };
  }

  if (type === "tool") {
    const action = String(config.action ?? "");
    const tool = catalog?.tools.find((t) => t.action === action);
    const badges: string[] = [];
    if (tool?.integration) badges.push(tool.integration);
    if (tool?.destructive) badges.push("write");
    else if (tool?.read_only) badges.push("read-only");
    return {
      summary:
        clip(tool?.description) ||
        (action ? "Calls this action." : "No action selected yet."),
      detail: action,
      badges,
    };
  }

  if (type === "module") {
    const mod = catalog?.modules.find((m) => m.id === config.module_id);
    return {
      summary:
        clip(mod?.description) ||
        (mod ? "Runs this code module." : "No module selected yet."),
      detail: mod?.name ?? "",
      badges: mod ? ["code"] : [],
    };
  }

  if (type === "condition") {
    const expr = `${config.left ?? ""} ${config.op ?? ""} ${config.right ?? ""}`
      .replace(/\s+/g, " ")
      .trim();
    return {
      summary: expr
        ? "Takes the yes branch when this holds, the no branch otherwise."
        : "No test configured yet.",
      detail: expr,
      badges: [],
    };
  }

  if (type === "set") {
    const keys = Object.keys((config.assignments as object) ?? {});
    return {
      summary: keys.length
        ? `Assigns ${keys.length} variable${keys.length === 1 ? "" : "s"} into {{vars.*}}.`
        : "No assignments yet.",
      detail: keys.join(", "),
      badges: [],
    };
  }

  if (type === "approval") {
    return {
      summary:
        clip(config.message) ||
        "Pauses the run and waits in the Approvals inbox.",
      detail: "",
      badges: ["pauses run"],
    };
  }

  if (type === "wait") {
    const seconds = Number(config.seconds);
    const human = humanDuration(seconds);
    return {
      summary: human ? `Pauses for ${human}, then continues.` : "No duration set yet.",
      detail: human,
      badges: seconds > 60 ? ["parks the run"] : [],
    };
  }

  if (type === "output") {
    return {
      summary: "Yields the run's result and ends this path.",
      detail: clip(config.value, 60),
      badges: [],
    };
  }

  return none;
}

function EditorInner({ id }: { id: string }) {
  const router = useRouter();
  const { resolvedTheme } = useTheme();
  const { screenToFlowPosition } = useReactFlow();
  // Publishing arms live triggers, so it needs its own authority (spec Q3).
  // Drafting, validating, and Test runs stay open to the workflows feature.
  const { access } = useAccess();
  const canPublish = hasCapability(access, "workflows:publish");

  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [triggers, setTriggers] = useState<TriggerSpec[]>([]);
  const [name, setName] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [issues, setIssues] = useState<GraphIssue[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [showTriggers, setShowTriggers] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [showTestPayload, setShowTestPayload] = useState(false);
  const [testPayload, setTestPayload] = useState('{\n  "body": "example"\n}');
  const [runEvents, setRunEvents] = useState<RunEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [consoleCollapsed, setConsoleCollapsed] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [leftTab, setLeftTab] = useState<"palette" | "copilot">("palette");
  const [undoGraph, setUndoGraph] = useState<WorkflowGraph | null>(null);
  /** Edge armed by its "+": the next block picked is spliced into it. */
  const [insertEdgeId, setInsertEdgeId] = useState<string | null>(null);
  /** Text handed from the canvas prompt bar to the Copilot, once. */
  const [copilotSeed, setCopilotSeed] = useState<{ text: string; n: number } | null>(
    null,
  );
  const [describe, setDescribe] = useState("");
  const counter = useRef(1);
  const esRef = useRef<EventSource | null>(null);

  // ── Load ────────────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [wf, cat] = await Promise.all([getWorkflow(id), getCatalog()]);
        if (cancelled) return;
        setDetail(wf);
        setCatalog(cat);
        setName(wf.name);
        setTriggers(wf.triggers);
        const g = wf.graph ?? { nodes: [], edges: [] };
        const graphNodes: WorkflowGraphNode[] = Array.isArray(g.nodes)
          ? g.nodes
          : [];
        if (graphNodes.length === 0) {
          // Every workflow starts from its trigger (RFC §5.2).
          setNodes([
            {
              id: "trigger",
              type: "ccNode",
              position: { x: 120, y: 60 },
              data: {
                label: "Trigger",
                nodeType: "trigger",
                config: { kind: "manual" },
                ...describeNode("trigger", { kind: "manual" }, cat),
              } satisfies CCNodeData & { config: Record<string, unknown> },
            },
          ]);
          setDirty(true);
        } else {
          const flow = flowFromGraph(g, cat);
          setNodes(flow.nodes);
          setEdges(flow.edges);
        }
      } catch (e) {
        if (!cancelled)
          setLoadError(String(e instanceof Error ? e.message : e));
      }
    })();
    return () => {
      cancelled = true;
      esRef.current?.close();
    };
  }, [id]);

  // ── Graph serialization ─────────────────────────────────────────────────
  const toGraph = useCallback((): WorkflowGraph => {
    return {
      nodes: nodes.map((n) => {
        const d = n.data as CCNodeData & { config: Record<string, unknown> };
        return {
          id: n.id,
          type: d.nodeType,
          position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
          data: { label: d.label, config: d.config ?? {} },
        };
      }),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: (e.sourceHandle as string) ?? null,
      })),
    };
  }, [nodes, edges]);

  const save = useCallback(async (): Promise<boolean> => {
    setSaving(true);
    setNotice(null);
    try {
      const wf = await updateWorkflow(id, {
        name: name.trim() || "Untitled workflow",
        graph: toGraph(),
        triggers,
      });
      setDetail(wf);
      setDirty(false);
      return true;
    } catch (e) {
      setNotice(String(e instanceof Error ? e.message : e));
      return false;
    } finally {
      setSaving(false);
    }
  }, [id, name, toGraph, triggers]);

  // ── Canvas callbacks ────────────────────────────────────────────────────
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((ns) => applyNodeChanges(changes, ns));
    if (changes.some((c) => c.type !== "select" && c.type !== "dimensions"))
      setDirty(true);
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((es) => applyEdgeChanges(changes, es));
    if (changes.some((c) => c.type !== "select")) setDirty(true);
  }, []);

  const onConnect = useCallback((conn: Connection) => {
    setEdges((es) =>
      addEdge(
        {
          ...conn,
          id: `e_${conn.source}_${conn.sourceHandle ?? "out"}_${conn.target}`,
        },
        es,
      ),
    );
    setDirty(true);
  }, []);

  /**
   * Add a block. Three ways in, one implementation: dropped at a point, armed
   * on an edge (spliced between its two ends, the old edge replaced), or
   * appended below the graph when neither applies.
   */
  const addNode = useCallback(
    (drop: PaletteDrop, position?: { x: number; y: number }) => {
      const nid = `${drop.nodeType}_${counter.current++}${Date.now().toString(36).slice(-3)}`;
      const splice = insertEdgeId
        ? (edges.find((e) => e.id === insertEdgeId) ?? null)
        : null;
      const source = splice ? nodes.find((n) => n.id === splice.source) : null;
      const target = splice ? nodes.find((n) => n.id === splice.target) : null;

      let where = position;
      if (!where && source && target) {
        where = {
          x: Math.round((source.position.x + target.position.x) / 2),
          y: Math.round((source.position.y + target.position.y) / 2),
        };
      }
      if (!where) {
        const maxY = nodes.reduce((acc, n) => Math.max(acc, n.position.y), 0);
        where = { x: 140, y: maxY + 160 };
      }

      setNodes((ns) => [
        ...ns,
        {
          id: nid,
          type: "ccNode",
          position: where,
          data: {
            label: drop.label,
            nodeType: drop.nodeType,
            config: drop.config,
            ...describeNode(drop.nodeType, drop.config, catalog),
          },
        },
      ]);
      if (splice) {
        setEdges((es) => [
          ...es.filter((e) => e.id !== splice.id),
          {
            id: `e_${splice.source}_${splice.sourceHandle ?? "out"}_${nid}`,
            source: splice.source,
            target: nid,
            sourceHandle: (splice.sourceHandle as string) ?? undefined,
          },
          { id: `e_${nid}_out_${splice.target}`, source: nid, target: splice.target },
        ]);
        setInsertEdgeId(null);
      }
      setSelectedId(nid);
      setDirty(true);
    },
    [catalog, edges, nodes, insertEdgeId],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData("application/cc-workflow-node");
      if (!raw) return;
      try {
        const drop = JSON.parse(raw) as PaletteDrop;
        addNode(drop, screenToFlowPosition({ x: e.clientX, y: e.clientY }));
      } catch {
        /* malformed drag payload — ignore */
      }
    },
    [addNode, screenToFlowPosition],
  );

  const selected = useMemo(() => {
    const n = nodes.find((x) => x.id === selectedId);
    if (!n) return null;
    const d = n.data as CCNodeData & { config: Record<string, unknown> };
    return {
      id: n.id,
      type: d.nodeType,
      position: n.position,
      data: { label: d.label, config: d.config ?? {} },
    } as WorkflowGraphNode;
  }, [nodes, selectedId]);

  const upstreamIds = useMemo(() => {
    if (!selectedId) return [];
    const parents = new Map<string, string[]>();
    for (const e of edges) {
      parents.set(e.target, [...(parents.get(e.target) ?? []), e.source]);
    }
    const seen = new Set<string>();
    const stack = [...(parents.get(selectedId) ?? [])];
    while (stack.length) {
      const cur = stack.pop()!;
      if (seen.has(cur)) continue;
      seen.add(cur);
      stack.push(...(parents.get(cur) ?? []));
    }
    seen.delete("trigger");
    return [...seen];
  }, [edges, selectedId]);

  /**
   * The trigger card reads from the Triggers panel, not from its own config —
   * "Schedule · 0 9 * * 1-5" on the card is the thing makers keep re-opening
   * the panel to check. Derived at render so editing a trigger updates it.
   */
  const triggerFacts = useMemo(() => {
    const live = triggers.filter((t) => t.enabled);
    const detail = live.length
      ? live
          .map((t) =>
            t.kind === "schedule" && t.config?.cron
              ? `${t.kind} · ${String(t.config.cron)}`
              : t.kind,
          )
          .join("  ·  ")
      : "manual";
    return {
      detail,
      badges: live.length === 0 ? ["run manually"] : [],
    };
  }, [triggers]);

  const displayNodes = useMemo(
    () =>
      nodes.map((n) =>
        (n.data as CCNodeData).nodeType === "trigger"
          ? { ...n, data: { ...n.data, ...triggerFacts } }
          : n,
      ),
    [nodes, triggerFacts],
  );

  const armInsert = useCallback(
    (edgeId: string) =>
      setInsertEdgeId((cur) => {
        if (cur !== edgeId) setLeftTab("palette");
        return cur === edgeId ? null : edgeId;
      }),
    [],
  );

  /**
   * Edge presentation (mockup §.edge-path): the branch a condition took is
   * labelled and coloured, and the wire the run is flowing through lights up —
   * during a Test run you should be able to follow the path without reading the
   * console. None of this is persisted; `edges` stays the edit-model.
   */
  const displayEdges = useMemo(() => {
    const statusOf = new Map(
      nodes.map((n) => [n.id, (n.data as CCNodeData).runStatus]),
    );
    return edges.map((e) => {
      const branch =
        e.sourceHandle === "true" || e.sourceHandle === "false"
          ? (e.sourceHandle as "true" | "false")
          : null;
      const from = statusOf.get(e.source);
      const to = statusOf.get(e.target);
      const hot =
        from === "ok" &&
        (to === "running" || to === "waiting" || to === "ok" || to === "error");
      const stroke = hot
        ? "var(--primary)"
        : branch === "true"
          ? "var(--success)"
          : "var(--muted-foreground)";
      return {
        ...e,
        type: "ccEdge",
        animated: hot && to !== "ok",
        style: {
          stroke,
          strokeWidth: hot ? 2.2 : 1.6,
          strokeOpacity: hot ? 1 : branch === "true" ? 0.75 : 0.5,
          strokeDasharray: branch === "false" ? "5 4" : undefined,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color: stroke,
        },
        data: {
          branch,
          hot,
          onInsert: armInsert,
          insertArmed: insertEdgeId === e.id,
        },
      } as Edge;
    });
  }, [edges, nodes, armInsert, insertEdgeId]);

  const patchSelected = useCallback(
    (patch: Record<string, unknown>) => {
      if (!selectedId) return;
      setNodes((ns) =>
        ns.map((n) => {
          if (n.id !== selectedId) return n;
          const d = n.data as CCNodeData & { config: Record<string, unknown> };
          const config = { ...d.config, ...patch };
          return {
            ...n,
            data: { ...d, config, ...describeNode(d.nodeType, config, catalog) },
          };
        }),
      );
      setDirty(true);
    },
    [selectedId, catalog],
  );

  const relabelSelected = useCallback(
    (label: string) => {
      if (!selectedId) return;
      setNodes((ns) =>
        ns.map((n) =>
          n.id === selectedId ? { ...n, data: { ...n.data, label } } : n,
        ),
      );
      setDirty(true);
    },
    [selectedId],
  );

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    setNodes((ns) => ns.filter((n) => n.id !== selectedId));
    setEdges((es) =>
      es.filter((e) => e.source !== selectedId && e.target !== selectedId),
    );
    setSelectedId(null);
    setDirty(true);
  }, [selectedId]);

  // ── Copilot: apply / undo / catalog refresh ─────────────────────────────
  const applyCopilotGraph = useCallback(
    (graph: WorkflowGraph) => {
      setUndoGraph(toGraph()); // snapshot for one-click undo
      const flow = flowFromGraph(graph, catalog);
      setNodes(flow.nodes);
      setEdges(flow.edges);
      setSelectedId(null);
      setDirty(true);
    },
    [toGraph, catalog],
  );

  const undoCopilot = useCallback(() => {
    if (!undoGraph) return;
    const flow = flowFromGraph(undoGraph, catalog);
    setNodes(flow.nodes);
    setEdges(flow.edges);
    setUndoGraph(null);
    setDirty(true);
  }, [undoGraph, catalog]);

  const refreshCatalog = useCallback(async () => {
    try {
      setCatalog(await getCatalog());
    } catch {
      /* palette keeps the last good catalog */
    }
  }, []);

  // ── Actions ─────────────────────────────────────────────────────────────
  const applyIssues = useCallback((next: GraphIssue[]) => {
    setIssues(next);
    setNodes((ns) =>
      ns.map((n) => ({
        ...n,
        data: {
          ...n.data,
          issueCount: next.filter((i) => i.node_id === n.id).length,
        },
      })),
    );
  }, []);

  const onValidate = useCallback(async () => {
    setBusy("validate");
    setNotice(null);
    try {
      if (!(await save())) return;
      const res = await validateWorkflow(id);
      applyIssues(res.issues);
      setNotice(res.ok ? "Graph is valid." : `${res.issues.length} issue(s) found.`);
    } catch (e) {
      setNotice(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }, [id, save, applyIssues]);

  const setNodeRunStatus = useCallback(
    (
      nodeId: string,
      status: CCNodeData["runStatus"],
      durationMs?: number,
    ) => {
      setNodes((ns) =>
        ns.map((n) =>
          n.id === nodeId
            ? {
                ...n,
                data: {
                  ...n.data,
                  runStatus: status,
                  // The card keeps the last known duration while the node is
                  // running again; a finished event overwrites it.
                  durationMs:
                    typeof durationMs === "number"
                      ? durationMs
                      : status === "running"
                        ? undefined
                        : (n.data as CCNodeData).durationMs,
                },
              }
            : n,
        ),
      );
    },
    [],
  );

  // History drill-in (spec F9): paint a recorded run's node_results onto the
  // canvas (null clears). Nodes added since that run simply stay unpainted.
  const paintRunResults = useCallback(
    (results: Record<string, NodeResult> | null) => {
      setNodes((ns) =>
        ns.map((n) => ({
          ...n,
          data: {
            ...n.data,
            runStatus: results
              ? (results[n.id]?.status as CCNodeData["runStatus"])
              : undefined,
            durationMs: results ? results[n.id]?.duration_ms : undefined,
          },
        })),
      );
    },
    [],
  );

  const onTest = useCallback(async () => {
    setBusy("test");
    setNotice(null);
    setRunEvents([]);
    setConsoleCollapsed(false);
    setNodes((ns) =>
      ns.map((n) => ({
        ...n,
        data: { ...n.data, runStatus: undefined, durationMs: undefined },
      })),
    );
    try {
      if (!(await save())) return;
      let payload: Record<string, unknown> = {};
      try {
        payload = JSON.parse(testPayload || "{}");
      } catch {
        setNotice("Test payload is not valid JSON");
        return;
      }
      const res = await runWorkflow(id, payload, true);
      setRunning(true);
      const es = new EventSource(`/api/workflows/runs/${res.run_id}/stream`);
      esRef.current = es;
      es.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data) as RunEvent;
          setRunEvents((prev) => [...prev, event]);
          if (event.event === "node" && event.node_id) {
            setNodeRunStatus(
              event.node_id,
              event.status as CCNodeData["runStatus"],
              event.duration_ms,
            );
          }
          if (event.event === "run" && event.status !== "running") {
            setRunning(false);
            es.close();
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      es.onerror = () => {
        setRunning(false);
        es.close();
      };
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      if (err && err.issues().length) {
        applyIssues(err.issues());
        setNotice("Fix the validation issues, then test again.");
      } else {
        setNotice(String(e instanceof Error ? e.message : e));
      }
      setRunning(false);
    } finally {
      setBusy(null);
    }
  }, [id, save, testPayload, applyIssues, setNodeRunStatus]);

  // Rollback = republish an old version (spec F6). The draft canvas is left
  // alone — only the LIVE version changes; detail refreshes for the badge.
  const onRollback = useCallback(
    async (version: number) => {
      setBusy("rollback");
      setNotice(null);
      try {
        const res = await rollbackWorkflow(id, version);
        const wf = await getWorkflow(id);
        setDetail(wf);
        setShowVersions(false);
        setNotice(
          `Rolled back — v${res.version} is live (a copy of v${res.rolled_back_to})` +
            (res.warnings.length
              ? ` · ${res.warnings.length} catalog warning(s); validate the draft.`
              : "."),
        );
      } catch (e) {
        setNotice(String(e instanceof Error ? e.message : e));
      } finally {
        setBusy(null);
      }
    },
    [id],
  );

  // Take offline — the deliberate half of spec R2's disabled state. Keeps the
  // published version intact; only the triggers stop.
  const onDisable = useCallback(async () => {
    setBusy("disable");
    setNotice(null);
    try {
      await disableWorkflow(id);
      const wf = await getWorkflow(id);
      setDetail(wf);
      setShowVersions(false);
      setNotice("Taken offline — no trigger fires until you re-enable it.");
    } catch (e) {
      setNotice(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }, [id]);

  // Re-enable = put the existing live version back on its triggers (spec R2).
  // Distinct from Publish on purpose: when the auto-disable policy trips on a
  // transient outage, the graph is fine and a new version would be noise.
  const onEnable = useCallback(async () => {
    setBusy("enable");
    setNotice(null);
    try {
      const res = await enableWorkflow(id);
      setDetail((d) =>
        d
          ? { ...d, status: "published", disabled_reason: null, disabled_at: null }
          : d,
      );
      setNotice(
        res.already_live
          ? "Already live."
          : `Re-enabled — v${res.version} is live again.`,
      );
    } catch (e) {
      setNotice(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }, [id]);

  const onPublish = useCallback(async () => {
    setBusy("publish");
    setNotice(null);
    try {
      if (!(await save())) return;
      const res = await publishWorkflow(id);
      applyIssues([]);
      setDetail((d) =>
        d ? { ...d, status: "published", latest_version: res.version } : d,
      );
      setNotice(`Published v${res.version} — triggers are live.`);
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      if (err && err.issues().length) {
        applyIssues(err.issues());
        setNotice("Publish blocked — fix the validation issues.");
      } else {
        setNotice(String(e instanceof Error ? e.message : e));
      }
    } finally {
      setBusy(null);
    }
  }, [id, save, applyIssues]);

  // ── Render ──────────────────────────────────────────────────────────────
  if (loadError) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <p className="text-sm text-destructive">{loadError}</p>
        <button
          onClick={() => router.push("/workflows")}
          className="text-xs text-muted-foreground hover:text-foreground underline"
        >
          Back to workflows
        </button>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="flex items-center justify-center h-full">
        <Icon name="Loader2" className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const published = detail.status === "published";

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden relative">
      {/* Topbar */}
      <div className="flex items-center gap-2 px-3 sm:px-4 py-2 border-b border-border shrink-0">
        <Link
          href="/workflows"
          className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary tech-transition"
        >
          <Icon name="ArrowLeft" className="w-4 h-4" />
        </Link>
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setDirty(true);
          }}
          className="bg-transparent text-sm font-semibold text-foreground focus:outline-none focus:ring-1 focus:ring-ring rounded-md px-1.5 py-0.5 min-w-0 w-48 sm:w-64"
        />
        <div className="relative shrink-0">
          <button
            onClick={() =>
              detail.versions.length > 0 && setShowVersions((s) => !s)
            }
            disabled={detail.versions.length === 0}
            title={
              detail.versions.length > 0
                ? "Version history — roll back to an earlier version"
                : undefined
            }
            className={`text-[10px] px-2 py-0.5 rounded-full border ${
              published
                ? "bg-success/10 text-success border-success/20"
                : "bg-warning/10 text-warning border-warning/20"
            } ${detail.versions.length > 0 ? "hover:ring-1 hover:ring-ring cursor-pointer" : "cursor-default"}`}
          >
            {detail.status}
            {detail.latest_version ? ` · v${detail.latest_version}` : ""}
          </button>
          {showVersions && (
            <div className="absolute left-0 top-7 z-30 w-80 rounded-xl border border-border bg-popover shadow-lg p-2">
              <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide px-1.5 pb-1">
                Versions — rollback republishes a copy
              </div>
              <div className="max-h-64 overflow-y-auto scrollbar-thin">
                {[...detail.versions]
                  .sort((a, b) => b.version - a.version)
                  .map((v) => (
                    <div
                      key={v.version}
                      className="flex items-center gap-2 px-1.5 py-1 text-[11px] rounded-md hover:bg-secondary/50"
                    >
                      <span className="text-foreground font-medium">
                        v{v.version}
                      </span>
                      <span className="text-muted-foreground truncate">
                        {v.published_by}
                      </span>
                      <span className="ml-auto text-muted-foreground shrink-0">
                        {v.published_at
                          ? new Date(v.published_at).toLocaleString()
                          : ""}
                      </span>
                      {v.version === detail.latest_version && published ? (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-success/10 text-success border border-success/20 shrink-0">
                          live
                        </span>
                      ) : (
                        <button
                          onClick={() => onRollback(v.version)}
                          disabled={busy !== null || !canPublish}
                          title={
                            canPublish
                              ? undefined
                              : "Rolling back changes the live version — needs workflows:publish."
                          }
                          className="text-[10px] px-1.5 py-0.5 rounded-md border border-border text-foreground hover:bg-secondary tech-transition disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
                        >
                          {busy === "rollback" ? (
                            <Icon name="Loader2" className="w-3 h-3 animate-spin" />
                          ) : (
                            "Roll back"
                          )}
                        </button>
                      )}
                    </div>
                  ))}
              </div>
              {published && (
                <div className="border-t border-border mt-1.5 pt-1.5">
                  <button
                    onClick={onDisable}
                    disabled={busy !== null || !canPublish}
                    title={
                      canPublish
                        ? "Stop every trigger without deleting anything"
                        : "Disabling changes what runs live — needs workflows:publish."
                    }
                    className="w-full flex items-center gap-1.5 px-1.5 py-1 text-[11px] rounded-md text-muted-foreground hover:text-destructive hover:bg-secondary/50 tech-transition disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {busy === "disable" ? (
                      <Icon name="Loader2" className="w-3 h-3 animate-spin" />
                    ) : (
                      <Icon name="Power" className="w-3 h-3" />
                    )}
                    Take offline — stops all triggers, keeps the version
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
        {/* What fires this workflow, said in words — the mockup's trigger chip */}
        <button
          onClick={() => setShowTriggers((s) => !s)}
          title="Configure triggers"
          className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border border-amber-500/30 bg-amber-500/10 text-amber-500 hover:bg-amber-500/15 tech-transition shrink-0 max-w-56"
        >
          <Icon name="Zap" className="w-3 h-3 shrink-0" />
          <span className="truncate">
            {triggerFacts.detail === "manual"
              ? "Trigger: run manually"
              : `Trigger: ${triggerFacts.detail}`}
          </span>
        </button>

        <div className="ml-auto flex items-center gap-1.5 shrink-0">
          {notice && (
            <span className="hidden md:inline text-[11px] text-muted-foreground max-w-64 truncate">
              {notice}
            </span>
          )}
          <button
            onClick={save}
            disabled={saving || !dirty}
            className="rounded-lg border border-border px-2.5 py-1.5 text-xs text-foreground hover:bg-secondary tech-transition flex items-center gap-1 disabled:opacity-50"
          >
            {saving ? (
              <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Icon name="Save" className="w-3.5 h-3.5" />
            )}
            {dirty ? "Save" : "Saved"}
          </button>
          <button
            onClick={onValidate}
            disabled={busy !== null}
            className="rounded-lg border border-border px-2.5 py-1.5 text-xs text-foreground hover:bg-secondary tech-transition flex items-center gap-1 disabled:opacity-50"
          >
            {busy === "validate" ? (
              <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Icon name="ShieldCheck" className="w-3.5 h-3.5" />
            )}
            Validate
          </button>
          <div className="flex items-center">
            <button
              onClick={onTest}
              disabled={busy !== null || running}
              className="rounded-l-lg border border-border px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-secondary tech-transition flex items-center gap-1 disabled:opacity-50"
            >
              {busy === "test" || running ? (
                <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Icon name="Play" className="w-3.5 h-3.5" />
              )}
              Test
            </button>
            <button
              onClick={() => setShowTestPayload((s) => !s)}
              className="rounded-r-lg border border-l-0 border-border px-1 py-1.5 text-muted-foreground hover:bg-secondary tech-transition"
              title="Sample trigger payload"
            >
              <Icon name="ChevronDown" className="w-3.5 h-3.5" />
            </button>
          </div>
          <Button size="none" layout="flex items-center" onClick={onPublish} disabled={busy !== null || !canPublish} title={
              canPublish
                ? undefined
                : "Publishing needs the workflows:publish permission — ask an admin. You can still edit and Test this draft."
            } className="px-2.5 py-1.5 text-xs gap-1">
            {busy === "publish" ? (
              <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" />
            ) : published ? (
              <Icon name="BadgeCheck" className="w-3.5 h-3.5" />
            ) : (
              <Icon name="Rocket" className="w-3.5 h-3.5" />
            )}
            Publish
          </Button>
        </div>
      </div>

      {detail.status === "disabled" && (
        <div className="flex items-center gap-2 px-3 sm:px-4 py-2 border-b border-warning/20 bg-warning/10 shrink-0">
          <Icon name="AlertTriangle" className="w-4 h-4 text-warning shrink-0" />
          <p className="text-xs text-warning min-w-0">
            <span className="font-medium">Not live — no trigger fires. </span>
            {detail.disabled_reason ??
              "This workflow is disabled. Re-enable it to put its published version back on its triggers."}
          </p>
          <button
            onClick={onEnable}
            disabled={busy !== null || !canPublish || !detail.latest_version}
            title={
              !detail.latest_version
                ? "Never published — use Publish instead."
                : canPublish
                  ? "Put the published version back on its triggers"
                  : "Re-enabling needs the workflows:publish permission — ask an admin."
            }
            className="ml-auto shrink-0 rounded-lg border border-warning/30 px-2.5 py-1.5 text-xs font-medium text-warning hover:bg-warning/15 tech-transition flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {busy === "enable" ? (
              <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Icon name="Power" className="w-3.5 h-3.5" />
            )}
            Re-enable
          </button>
        </div>
      )}

      {showTriggers && (
        <TriggerPanel
          triggers={triggers}
          hookUrl={detail.hook_url}
          hookPath={detail.hook_path}
          published={published}
          onChange={(next) => {
            setTriggers(next);
            setDirty(true);
          }}
          onClose={() => setShowTriggers(false)}
        />
      )}

      {showTestPayload && (
        <div className="absolute right-3 top-12 z-20 w-80 rounded-xl border border-border bg-popover shadow-lg p-3">
          <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide mb-1">
            Sample trigger payload (JSON) — {"{{trigger.*}}"}
          </div>
          <textarea
            value={testPayload}
            onChange={(e) => setTestPayload(e.target.value)}
            rows={6}
            spellCheck={false}
            className="w-full rounded-lg border border-input bg-background px-2.5 py-1.5 text-[11px] font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      )}

      {/* Panes */}
      <div className="flex-1 min-h-0 flex">
        <div className="flex flex-col shrink-0 border-r border-border">
          <div className="flex border-b border-border">
            <button
              onClick={() => setLeftTab("palette")}
              className={`flex-1 text-[11px] px-3 py-2 tech-transition ${
                leftTab === "palette"
                  ? "text-primary font-semibold border-b-2 border-primary -mb-px"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              Palette
            </button>
            <button
              onClick={() => setLeftTab("copilot")}
              className={`flex-1 text-[11px] px-3 py-2 tech-transition inline-flex items-center justify-center gap-1 ${
                leftTab === "copilot"
                  ? "text-primary font-semibold border-b-2 border-primary -mb-px"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon name="Sparkles" className="w-3 h-3" /> Copilot
            </button>
          </div>
          {insertEdgeId && leftTab === "palette" && (
            <div className="flex items-center gap-1.5 px-2 py-1.5 border-b border-primary/30 bg-primary/10 text-[10px] text-primary">
              <span className="min-w-0 truncate">
                Inserting into a connection — pick a block
              </span>
              <button
                onClick={() => setInsertEdgeId(null)}
                className="ml-auto shrink-0 underline hover:no-underline"
              >
                cancel
              </button>
            </div>
          )}
          <div className="flex-1 min-h-0 flex">
            {leftTab === "palette" ? (
              <NodePalette catalog={catalog} onAdd={(drop) => addNode(drop)} />
            ) : (
              <CopilotPanel
                workflowId={id}
                seed={copilotSeed}
                onApplyGraph={applyCopilotGraph}
                onUndo={undoCopilot}
                canUndo={undoGraph !== null}
                onModulesCreated={refreshCatalog}
              />
            )}
          </div>
        </div>
        <div
          className="flex-1 min-w-0 relative"
          onDragOver={(e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
          }}
          onDrop={onDrop}
        >
          <ReactFlow
            nodes={displayNodes}
            edges={displayEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onSelectionChange={({ nodes: sel }) =>
              setSelectedId(sel.length === 1 ? sel[0].id : null)
            }
            colorMode={resolvedTheme === "light" ? "light" : "dark"}
            fitView
            proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{ type: "ccEdge" }}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
            <Controls showInteractive={false} />
          </ReactFlow>

          <div className="absolute right-3 top-3 z-10 pointer-events-none rounded-lg border border-border bg-background/80 backdrop-blur-sm px-2 py-1 text-[10px] text-muted-foreground">
            Drag to move · scroll to zoom · <span className="text-foreground">+</span> on a
            wire inserts a block
          </div>

          {/* Describe → generate → refine (RFC §5.4), on the canvas where the
              blank-canvas problem actually is. Hands off to the Copilot pane so
              there is one place graphs get applied, undone and explained. */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const text = describe.trim();
              if (!text) return;
              setLeftTab("copilot");
              setCopilotSeed((s) => ({ text, n: (s?.n ?? 0) + 1 }));
              setDescribe("");
            }}
            className="absolute left-1/2 -translate-x-1/2 bottom-4 z-10 w-[min(560px,84%)] flex items-center gap-2 rounded-xl border border-border bg-card/95 backdrop-blur-sm shadow-lg pl-3 pr-2 py-2"
          >
            <Icon name="Sparkles" className="w-4 h-4 text-primary shrink-0" />
            <input
              value={describe}
              onChange={(e) => setDescribe(e.target.value)}
              placeholder="Describe an automation to generate… e.g. “When a lead emails, log it to Zoho and draft a reply”"
              className="flex-1 min-w-0 bg-transparent text-xs text-foreground placeholder:text-muted-foreground focus:outline-none"
            />
            <Button size="none" layout="" type="submit" disabled={!describe.trim()} className="shrink-0 px-2.5 py-1.5 text-xs">
              Generate
            </Button>
          </form>
        </div>
        <NodeInspector
          node={selected}
          catalog={catalog}
          upstreamIds={upstreamIds}
          issues={issues}
          triggerKinds={TRIGGER_KIND_HINTS}
          onConfig={patchSelected}
          onLabel={relabelSelected}
          onDelete={deleteSelected}
        />
      </div>

      <RunConsole
        workflowId={id}
        events={runEvents}
        running={running}
        collapsed={consoleCollapsed}
        onToggle={() => setConsoleCollapsed((c) => !c)}
        onPaintRun={paintRunResults}
      />
    </div>
  );
}

export default function WorkflowEditorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-full">
          <Icon name="Loader2" className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <ReactFlowProvider>
        <EditorInner id={id} />
      </ReactFlowProvider>
    </Suspense>
  );
}
