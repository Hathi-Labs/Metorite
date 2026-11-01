import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch, requireIdentity } from "@/lib/gateway";

// Resolves the signed-in member, so it is never statically evaluated.
export const dynamic = "force-dynamic";

/**
 * POST /api/chat/suggestions — generate contextual follow-up suggestions.
 *
 * Takes the last user/assistant exchange and asks a fast, cheap model for
 * three short follow-up prompts the user is likely to want next.
 * Falls back to [] on any failure — the UI then shows its static defaults.
 */

const LITELLM_BASE_URL =
  process.env.COPILOT_LLM_BASE_URL ?? process.env.LITELLM_BASE_URL ?? "http://127.0.0.1:8080/v1";
const LITELLM_KEY =
  process.env.LITELLM_MASTER_KEY ?? "";
const SUGGESTION_MODEL = process.env.SUGGESTION_MODEL ?? "deepseek/deepseek-chat";

/** Ensure the base URL ends with /v1 (env values vary). */
function v1Base(): string {
  const base = LITELLM_BASE_URL.replace(/\/+$/, "");
  return base.endsWith("/v1") ? base : `${base}/v1`;
}

export async function POST(req: NextRequest) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  let body: { userMessage?: string; assistantMessage?: string; agentName?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ suggestions: [] });
  }
  const userMessage = (body.userMessage ?? "").slice(0, 2000);
  const assistantMessage = (body.assistantMessage ?? "").slice(0, 4000);
  if (!assistantMessage.trim()) return NextResponse.json({ suggestions: [] });

  try {
    const res = await gatewayFetch(`${v1Base()}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${LITELLM_KEY}`,
      },
      body: JSON.stringify({
        model: SUGGESTION_MODEL,
        max_tokens: 150,
        temperature: 0.7,
        messages: [
          {
            role: "system",
            content:
              "You generate follow-up prompts for a business chat with an AI " +
              `agent${body.agentName ? ` named "${body.agentName}"` : ""}. ` +
              "Given the last exchange, propose exactly 3 short follow-up " +
              "messages the USER would plausibly send next. Make them " +
              "SPECIFIC to entities, numbers, and open threads in the " +
              "conversation — never generic filler like 'Tell me more'. " +
              "Each under 60 characters, actionable, no numbering. " +
              'Reply ONLY with a JSON array of 3 strings, e.g. ' +
              '["Draft a follow-up email to CMET", "Which deals slip this quarter?", "Update the PO forecast"]',
          },
          {
            role: "user",
            content: `User asked:\n${userMessage}\n\nAgent replied:\n${assistantMessage}`,
          },
        ],
      }),
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return NextResponse.json({ suggestions: [] });
    const data = await res.json();
    const text: string = data?.choices?.[0]?.message?.content ?? "";
    // Extract the first JSON array in the reply.
    const match = text.match(/\[[\s\S]*?\]/);
    if (!match) return NextResponse.json({ suggestions: [] });
    const parsed = JSON.parse(match[0]);
    const suggestions = Array.isArray(parsed)
      ? parsed.filter((s) => typeof s === "string" && s.trim()).slice(0, 3)
      : [];
    return NextResponse.json({ suggestions });
  } catch {
    return NextResponse.json({ suggestions: [] });
  }
}
