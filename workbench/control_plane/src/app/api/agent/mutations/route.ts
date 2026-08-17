/**
 * GET /api/agent/mutations
 *
 * Proxies GET /agent/mutations from the FastAPI gateway — the self-mutation
 * HITL queue (auto-fix PRs opened by Self_Mutation_Node). Returns [] when the
 * gateway is unavailable so the inbox UI never breaks.
 */

import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export interface MutationEntry {
  /** "pending_commit" (commit-gate HITL row) or "audit_event" (legacy sandbox event) */
  type?: "pending_commit" | "audit_event";
  /** UUID of the pending_commit row (only present for type === "pending_commit") */
  id?: string;
  agent: string;
  at: string;
  run_id?: string;
  /** Commit SHA (only for pending_commit rows) */
  commit_sha?: string;
  /** Commit message (only for pending_commit rows) */
  commit_message?: string;
  /** PR URL — set for an audit_event that opened a PR, OR a native-MAF
   *  pending_commit approved into a Metorite monorepo PR. */
  pr_url?: string;
  /** "push" (own-remote agent) or "monorepo_pr" (native MAF → Metorite PR). */
  mutation_mode?: "push" | "monorepo_pr";
  /** Monorepo path the PR edits, e.g. "apps/agents/agent-task-manager". */
  target_path?: string;
  /** Git branch name */
  branch?: string;
  error_type?: string;
  test_summary?: string;
  reviewed_by?: string;
  reviewed_at?: string;
  /** Relative gateway URLs for HITL actions (pending_commit rows only) */
  approve_url?: string;
  reject_url?: string;
  diff_url?: string;
  status:
    | "pending"      // pending_commit awaiting review
    | "approved"     // pending_commit approved and pushed
    | "rejected"     // pending_commit rejected
    | "eval_failed"  // pending_commit: commit staged but tests failed — awaiting Push Anyway / Reject / Re-mutate
    | "pr_open"      // audit_event: PR was opened
    | "failed"       // audit_event: sandbox failed
    | "started"      // audit_event: mutation started
    | "commit_pending"; // audit_event: commit staged
  /** Number of ancestor commits auto-approved as part of this approve (cascade). */
  cascade_approved?: number;
}

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await fetch(`${GATEWAY_URL}/agent/mutations`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(4_000),
    });
    if (res.ok) {
      const rows = (await res.json()) as MutationEntry[];
      return NextResponse.json(rows);
    }
  } catch (_e) {
    // Gateway unavailable — return empty queue
  }
  return NextResponse.json([]);
}
