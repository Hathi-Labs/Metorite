import { deactivateOperator, updateOperator } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

// PATCH /api/operator/operators/{id} → Console PATCH /operators/{id}.
// Changes a role or a status. Admin only.
export async function PATCH(
  request: Request,
  ctx: Ctx,
): Promise<Response> {
  const { id } = await ctx.params;
  const body = await readJsonBody(request);
  return proxyToConsole((d) => updateOperator(id, body, d));
}

// DELETE /api/operator/operators/{id} → Console DELETE /operators/{id}.
//
// ⚠️ **It deactivates. It never deletes the row** (D63 — deactivation seals).
// The row stays so the person's `control_audit` history stays readable, and
// removing it would orphan the audit trail that naming them was for. The verb
// is DELETE because that is what an operator means by it.
export async function DELETE(
  _request: Request,
  ctx: Ctx,
): Promise<Response> {
  const { id } = await ctx.params;
  return proxyToConsole((d) => deactivateOperator(id, d));
}
