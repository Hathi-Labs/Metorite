/**
 * The client of the approval routes (WS-STU J2b-4b). One thin layer over
 * `/api/studio/*`, the BFF proxy of the gateway `/studio` routes of J2b-1.
 *
 *   GET  /api/studio/approvals?state=pending
 *   POST /api/studio/packages/{id}/versions/{n}/approval/approve
 *   POST /api/studio/packages/{id}/versions/{n}/approval/reject
 *
 * Neither POST takes a body, so a reject sends no reason. It sends no identity
 * and no org. The proxy and the gateway take those from the session. Every
 * call answers a value and never throws. The detail is the server's own
 * sentence, unchanged.
 */
import type { PendingApproval } from "./approvalsModel";

export type ApprovalReply<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; detail: string; code?: string };

/** The server's sentence: `detail` as a string, or `detail.message` of a 409 or 422. */
export function refusalOf(body: unknown, status: number): { detail: string; code?: string } {
  const b = body as { detail?: unknown; error?: unknown } | null;
  const raw = b?.detail ?? b?.error;
  if (typeof raw === "string" && raw) return { detail: raw };
  if (raw && typeof raw === "object") {
    const o = raw as { message?: unknown; code?: unknown };
    if (typeof o.message === "string" && o.message) {
      return { detail: o.message, code: typeof o.code === "string" ? o.code : undefined };
    }
  }
  return { detail: `The request failed (${status}).` };
}

async function send<T>(request: () => Promise<Response>): Promise<ApprovalReply<T>> {
  let res: Response;
  try {
    res = await request();
  } catch {
    return { ok: false, status: 0, detail: "The server did not answer. Try again." };
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) return { ok: false, status: res.status, ...refusalOf(body, res.status) };
  return { ok: true, status: res.status, data: body as T };
}

export interface ApprovalList {
  state: string;
  approvals: PendingApproval[];
  truncated: boolean;
}

export interface ReviewAnswer {
  package_id: string;
  version: number;
  review_status: string;
}

export function listApprovals(): Promise<ApprovalReply<ApprovalList>> {
  return send(() => fetch("/api/studio/approvals?state=pending", { cache: "no-store" }));
}

export function approveVersion(packageId: string, version: number): Promise<ApprovalReply<ReviewAnswer>> {
  return send(() =>
    fetch(`/api/studio/packages/${encodeURIComponent(packageId)}/versions/${version}/approval/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }),
  );
}

export function rejectVersion(packageId: string, version: number): Promise<ApprovalReply<ReviewAnswer>> {
  return send(() =>
    fetch(`/api/studio/packages/${encodeURIComponent(packageId)}/versions/${version}/approval/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }),
  );
}
