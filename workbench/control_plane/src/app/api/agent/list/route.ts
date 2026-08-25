/**
 * GET /api/agent/list
 *
 * Proxies GET /agent from the FastAPI gateway to return the available agent
 * registry for the Control Plane picker. Returns a static fallback list if
 * the gateway is unavailable so the UI never breaks.
 */

import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { resolve } from "path";
import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

// Shown when the gateway is down so the picker still renders.
const STATIC_FALLBACK: AgentEntry[] = [
  { name: "task-manager",  description: "Task management (GTD)",              tags: ["tasks"],    status: "live", agent_runtime: "maf" },
  { name: "sales",         description: "Zoho CRM sales pipeline",              tags: ["sales"],    status: "live", agent_runtime: "maf" },
  { name: "triage",        description: "Email / WhatsApp triage + routing",    tags: ["triage"],   status: "live", agent_runtime: "maf" },
  { name: "delivery",      description: "Project delivery monitoring",          tags: ["delivery"], status: "live", agent_runtime: "maf" },
  { name: "reconciler",    description: "Nightly source-of-truth diff",         tags: ["ops"],      status: "live", agent_runtime: "maf" },
  { name: "strategy",      description: "Weekly digest + planning synthesis",   tags: ["strategy"], status: "live", agent_runtime: "maf" },
];

/** Read agents.json from the repo root so dynamic/Copilot agents survive gateway downtime. */
function readDynamicAgentsFallback(): AgentEntry[] {
  try {
    // workbench/control_plane is 2 levels below the repo root
    const jsonPath = resolve(process.cwd(), "../../apps/services/gateway/agents.json");
    const raw = readFileSync(jsonPath, "utf-8");
    const parsed = JSON.parse(raw) as AgentEntry[];
    return Array.isArray(parsed) ? parsed : [];
  } catch (_e) {
    return [];
  }
}

export interface AgentEntry {
  name: string;
  /** User-set friendly alias shown in the UI. Falls back to `name` when empty.
   *  A pure display overlay — `name` stays the key for runs/localStorage/DB. */
  display_name?: string;
  description: string;
  tags: string[];
  status: string;
  /** How the agent is executed: "github-copilot" | "maf" | "langgraph" */
  agent_runtime?: string;
  integrations?: string[];
  optional_integrations?: string[];
  repo_name?: string;
  repo_url?: string;
  local_path?: string;
  dynamic?: boolean;
  /** Number of commits the local clone is behind origin (0 = up-to-date) */
  behind_by?: number;
  /** Dependency-install health for this agent's clone. */
  dep_status?: {
    ok: boolean;
    error?: string;
    /** apt/system packages a failed build needs (e.g. ["build-essential"]). */
    needs_system_packages?: string[];
    has_requirements?: boolean;
    pyproject_dep_count?: number;
  };
}

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await fetch(`${GATEWAY_URL}/agent`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(4_000),
    });
    if (res.ok) {
      const agents = (await res.json()) as AgentEntry[];
      return NextResponse.json(agents);
    }
  } catch (_e) {
    // Gateway unavailable — fall through to filesystem fallback
  }
  // Merge static built-ins with whatever is in agents.json so GitHub Copilot
  // agents survive gateway downtime (they're in agents.json, not the static list).
  const dynamic = readDynamicAgentsFallback();
  const dynamicNames = new Set(dynamic.map((a) => a.name));
  const merged = [
    ...STATIC_FALLBACK.filter((a) => !dynamicNames.has(a.name)),
    ...dynamic,
  ];
  return NextResponse.json(merged);
}
