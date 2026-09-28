// Workflows app — shared types (spec: project-docs/specs/workflows_app.md).
// The graph shapes mirror the gateway's edit-model (React-Flow-native JSON
// persisted verbatim — docs/workflow-editor/README.md §4).

export type WorkflowStatus = "draft" | "published" | "disabled";
export type RunStatus =
  | "queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled";
export type TriggerKind = "manual" | "api" | "schedule" | "webhook" | "event";
export type NodeType =
  | "trigger" | "agent" | "tool" | "module" | "condition" | "set"
  | "approval" | "wait" | "output";

export type WorkflowNodeData = {
  label: string;
  config: Record<string, unknown>;
  /** Declared outputs → {{…}} autocomplete (typed, optional). */
  outputs?: Record<string, string>;
};

export type WorkflowGraphNode = {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  data: WorkflowNodeData;
};

export type WorkflowGraphEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  label?: string;
};

export type WorkflowGraph = {
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
};

export type TriggerSpec = {
  id?: string;
  kind: TriggerKind;
  config: Record<string, unknown>;
  enabled: boolean;
  last_fired_at?: string | null;
};

export type WorkflowSummary = {
  id: string;
  name: string;
  description: string;
  owner_email: string;
  status: WorkflowStatus;
  latest_version: number | null;
  created_at: string | null;
  updated_at: string | null;
  last_run_status?: RunStatus | null;
  last_run_at?: string | null;
  trigger_count?: number;
  /**
   * Why the workflow is off — set by whoever hit Disable, or by the
   * auto-disable policy after repeated unattended failures (spec R2).
   * Only meaningful when `status === "disabled"`.
   */
  disabled_reason?: string | null;
  disabled_at?: string | null;
};

export type WorkflowDetail = WorkflowSummary & {
  graph: WorkflowGraph;
  variables: Record<string, unknown>;
  hook_token?: string;
  /**
   * Absolute URL an external system posts to, as named by the GATEWAY
   * (`public_api_base_url`) — never assembled from the browser's origin, which
   * is the control plane and proxies JSON in a way that breaks HMAC.
   * Empty when the gateway's public origin is unconfigured.
   */
  hook_url?: string;
  /** The path alone, always present — shown when `hook_url` is empty. */
  hook_path?: string;
  triggers: TriggerSpec[];
  versions: { version: number; published_by: string; published_at: string | null }[];
};

export type GraphIssue = {
  code: string;
  message: string;
  node_id?: string;
  edge_id?: string;
};

export type RunSummary = {
  id: string;
  version: number;
  trigger_kind: string;
  status: RunStatus;
  error: string | null;
  started_by: string;
  started_at: string | null;
  finished_at: string | null;
};

export type NodeResult = {
  status: "ok" | "error" | "skipped" | "waiting" | "pending";
  output?: unknown;
  error?: string;
  duration_ms?: number;
};

export type RunDetail = RunSummary & {
  workflow_id: string;
  workflow_name: string;
  trigger_payload: Record<string, unknown>;
  node_results: Record<string, NodeResult>;
  output: unknown;
};

export type RunEvent = {
  event: "run" | "node" | "error";
  node_id?: string;
  status?: string;
  output?: unknown;
  error?: string;
  duration_ms?: number;
  ts?: number;
};

export type CatalogAgent = {
  name: string;
  description: string;
  tags: string[];
  integrations: string[];
};

/** One declared argument of a tool action, parsed server-side. */
export type ToolArg = {
  name: string;
  type: "string" | "number" | "boolean" | "object" | "array";
  required: boolean;
  description: string;
};

export type CatalogTool = {
  action: string;
  label: string;
  description: string;
  integration: string | null;
  /** Raw declaration, e.g. `{ "headers": "object?|Extra headers" }`. */
  args_schema: Record<string, string>;
  /**
   * The same contract parsed by the gateway. Render fields from THIS — the
   * browser must never re-implement the `type?|description` mini-language, or
   * the editor and the publish gate will disagree about what is required.
   */
  args?: ToolArg[];
  read_only: boolean;
  destructive: boolean;
  open_world: boolean;
};

export type CatalogIntegration = {
  service: string;
  available: boolean;
  unavailable_reason?: string | null;
  actions: CatalogTool[];
};

export type ModuleSummary = {
  id: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  status: "draft" | "ready" | "disabled";
  updated_at: string | null;
};

export type ModuleDetail = ModuleSummary & {
  language: string;
  code: string;
  provenance: Record<string, unknown>;
  created_by: string;
  created_at: string | null;
};

export type Catalog = {
  node_types: { type: NodeType; category: string; label: string; description: string }[];
  condition_ops: string[];
  agents: CatalogAgent[];
  integrations: CatalogIntegration[];
  tools: CatalogTool[];
  modules: ModuleSummary[];
};

export type GeneratedModule = {
  name: string;
  description: string;
  code: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  provenance: Record<string, unknown>;
};

/**
 * Palette/canvas category colour encoding (RFC §5.2 — functional, fixed).
 *
 * `tile` is the icon plate carried by every surface that renders a block
 * (palette row, canvas card, inspector header); `bar` is the card's top accent
 * strip, which is what makes a category readable across a zoomed-out graph.
 * Written out in full because Tailwind only keeps classes it can see.
 */
export const NODE_CATEGORY_STYLE: Record<
  string,
  {
    chip: string;
    border: string;
    dot: string;
    tile: string;
    text: string;
    bar: string;
  }
> = {
  trigger: {
    chip: "bg-cat-12/10 text-cat-12 border-cat-12/20",
    border: "border-cat-12/40",
    dot: "bg-cat-12",
    tile: "bg-cat-12/12 text-cat-12 border-cat-12/30",
    text: "text-cat-12",
    bar: "bg-cat-12",
  },
  agent: {
    chip: "bg-violet-500/10 text-violet-500 border-violet-500/20",
    border: "border-violet-500/40",
    dot: "bg-violet-500",
    tile: "bg-violet-500/12 text-violet-500 border-violet-500/30",
    text: "text-violet-500",
    bar: "bg-violet-500",
  },
  tool: {
    chip: "bg-teal-500/10 text-teal-500 border-teal-500/20",
    border: "border-teal-500/40",
    dot: "bg-teal-500",
    tile: "bg-teal-500/12 text-teal-500 border-teal-500/30",
    text: "text-teal-500",
    bar: "bg-teal-500",
  },
  module: {
    chip: "bg-sky-500/10 text-sky-500 border-sky-500/20",
    border: "border-sky-500/40",
    dot: "bg-sky-500",
    tile: "bg-sky-500/12 text-sky-500 border-sky-500/30",
    text: "text-sky-500",
    bar: "bg-sky-500",
  },
  logic: {
    chip: "bg-slate-500/10 text-slate-400 border-slate-500/20",
    border: "border-slate-500/40",
    dot: "bg-slate-400",
    tile: "bg-slate-500/12 text-slate-400 border-slate-500/30",
    text: "text-slate-400",
    bar: "bg-slate-400",
  },
  output: {
    chip: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
    border: "border-emerald-500/40",
    dot: "bg-emerald-500",
    tile: "bg-emerald-500/12 text-emerald-500 border-emerald-500/30",
    text: "text-emerald-500",
    bar: "bg-emerald-500",
  },
};

export function categoryForType(type: NodeType): string {
  if (
    type === "condition" ||
    type === "set" ||
    type === "approval" ||
    type === "wait"
  )
    return "logic";
  return type;
}

// ── Catalog search + Workflow Copilot ───────────────────────────────────────
// Search is keyword-ranked for now; platform-wide semantic search is BO‑22
// (FOUNDATION_BUILDOUT_CHECKLIST.md) and will swap in behind this same shape.

export type SearchResult = {
  kind: "agent" | "tool" | "integration" | "module" | "node";
  key: string;
  label: string;
  description: string;
  category: string;
  score: number;
};

export type SearchResponse = {
  query: string;
  results: SearchResult[];
};

export type CopilotResponse = {
  reply: string;
  graph: WorkflowGraph | null;
  created_modules: { id: string; name: string }[];
  issues: GraphIssue[];
  problems: string[];
  shortlist: SearchResult[];
};
