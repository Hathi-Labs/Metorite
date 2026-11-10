/**
 * /api/admin/[…path] → gateway /admin/*
 *
 * Member roster, roles, and per-user access overrides. Authorization is the
 * gateway's: every /admin route there carries its own require_permission, so
 * this proxy deliberately does not second-guess who is an admin. Duplicating
 * the check here would just create a second place for it to be wrong.
 */
import { NextRequest } from "next/server";
import { proxyToGateway } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

function upstreamPath(path: string[], req: NextRequest): string {
  const base = `/admin/${path.map(encodeURIComponent).join("/")}`;
  const qs = req.nextUrl.searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

async function forward(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  req: NextRequest,
  params: Promise<{ path: string[] }>
): Promise<Response> {
  const { path } = await params;
  const refused = refuseUnsafePath(path);
  if (refused) return refused;
  const body =
    method === "GET" || method === "DELETE" ? undefined : await req.text();
  try {
    return await proxyToGateway(upstreamPath(path, req), { method, body });
  } catch {
    return new Response(
      JSON.stringify({ detail: "Gateway unreachable." }),
      { status: 502, headers: { "Content-Type": "application/json" } }
    );
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export const GET = (req: NextRequest, ctx: Ctx) => forward("GET", req, ctx.params);
export const POST = (req: NextRequest, ctx: Ctx) => forward("POST", req, ctx.params);
export const PUT = (req: NextRequest, ctx: Ctx) => forward("PUT", req, ctx.params);
export const PATCH = (req: NextRequest, ctx: Ctx) => forward("PATCH", req, ctx.params);
export const DELETE = (req: NextRequest, ctx: Ctx) => forward("DELETE", req, ctx.params);
