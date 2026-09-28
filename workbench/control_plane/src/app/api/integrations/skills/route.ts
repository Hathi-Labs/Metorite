/**
 * GET /api/integrations/skills
 *
 * Proxies to GET /integrations/skills on the FastAPI gateway (WS-23 S1).
 * Returns the read-only skill-family catalog with measured token costs plus
 * the per-agent family matrix for the Integrations → Skills tab.
 */

import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export interface SkillFamily {
  slug: string;
  label: string;
  description: string;
  tools: string[];
  tool_count: number;
  /** Part of the guaranteed core floor — always injected, never toggleable. */
  core: boolean;
  /** Whether a config.json tool_scope narrows this family today. */
  scope_governed: boolean;
  /** Tools exist only at run time (per-grant app_<slug>_<action>). */
  dynamic: boolean;
  tool_pattern?: string;
  /** Measured: marginal addendum + tool JSON schemas, in tokens. */
  token_cost: number;
}

export interface SkillAgentRow {
  name: string;
  description: string;
  tool_scope_declared: boolean;
  /** Effective families: declared scope ∩ enabled families (WS-23 S2). */
  families: string[];
  /** Families an admin explicitly toggled off (agent_skill_setting rows). */
  disabled_families: string[];
  /** No tool_scope declared and nothing disabled — fail-open, gets everything. */
  all_families: boolean;
}

export interface SkillsCatalog {
  registry_hash: string;
  families: SkillFamily[];
  /** Full unscoped addendum + every static tool schema (WS-12 baseline). */
  total_injected_tokens: number;
  agents: SkillAgentRow[];
}

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/integrations/skills`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(15_000),
    });
    if (res.ok) {
      return NextResponse.json(await res.json());
    }
    const text = await res.text().catch(() => "");
    return NextResponse.json(
      { error: `Gateway ${res.status}: ${text}` },
      { status: res.status }
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
