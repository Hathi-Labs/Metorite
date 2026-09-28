import { NextRequest, NextResponse } from "next/server";
import { gatewayFetch, requireIdentity } from "@/lib/gateway";

// Resolves the signed-in member, so it is never statically evaluated.
export const dynamic = "force-dynamic";

/**
 * POST /api/chat/sessions/[sessionId]/compact
 *
 * Summarises the conversation history down to a compact system context block,
 * keeping the last `keepLast` (default 6) messages verbatim so the agent
 * retains immediate context.  Mirrors Claude Code's /compact behaviour.
 *
 * Request body:
 *   { messages: ChatMessage[], keepLast?: number }
 *
 * Response:
 *   { messages: ChatMessage[], compacted: boolean, removedCount: number }
 */

const LITELLM_BASE_URL =
  process.env.COPILOT_LLM_BASE_URL ??
  process.env.LITELLM_BASE_URL ??
  "http://127.0.0.1:8080/v1";
const LITELLM_KEY =
  process.env.LITELLM_MASTER_KEY ?? "";
const COMPACT_MODEL = process.env.SUGGESTION_MODEL ?? "deepseek/deepseek-chat";

function v1Base(): string {
  const base = LITELLM_BASE_URL.replace(/\/+$/, "");
  return base.endsWith("/v1") ? base : `${base}/v1`;
}

interface StoredMessage {
  id: string;
  role: string;
  content: string;
  timestamp: number;
  [key: string]: unknown;
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  // Require auth (middleware marks /api/chat as public; no-op in dev).  Prevents
  // unauthenticated callers from running billed LiteLLM completions.
  // params is async in Next.js 15+
  await params; // ensure params are resolved (sessionId not needed server-side)

  let body: { messages?: StoredMessage[]; keepLast?: number };
  try {
    body = (await req.json()) as { messages?: StoredMessage[]; keepLast?: number };
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
  }

  const messages: StoredMessage[] = body.messages ?? [];
  const keepLast = Math.max(1, body.keepLast ?? 6);

  // Nothing to compact — return as-is
  if (messages.length <= keepLast) {
    return NextResponse.json({ messages, compacted: false, removedCount: 0 });
  }

  const toSummarize = messages.slice(0, messages.length - keepLast);
  const toKeep = messages.slice(messages.length - keepLast);

  // Build a plain-text transcript for the summariser (skip system messages)
  const transcript = toSummarize
    .filter((m) => m.role !== "system")
    .map((m) => {
      const label = m.role === "user" ? "User" : "Assistant";
      // Truncate very long messages to avoid sending the full context twice
      const body = (m.content ?? "").slice(0, 8_000);
      return `${label}: ${body}`;
    })
    .join("\n\n");

  if (!transcript.trim()) {
    // Nothing meaningful to summarise — just drop the old system messages
    return NextResponse.json({ messages: toKeep, compacted: true, removedCount: toSummarize.length });
  }

  try {
    const res = await gatewayFetch(`${v1Base()}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${LITELLM_KEY}`,
      },
      body: JSON.stringify({
        model: COMPACT_MODEL,
        max_tokens: 1_200,
        temperature: 0.2,
        messages: [
          {
            role: "system",
            content:
              "You are a conversation summariser. Create a dense, structured summary of the " +
              "conversation excerpt below. Preserve ALL important facts, decisions, context, " +
              "entities (names, IDs, dollar values, dates), open action items, and key conclusions. " +
              "Format as tightly grouped bullet points under short headings. " +
              "This summary will be injected as context at the start of an ongoing AI-agent chat — " +
              "it must be comprehensive enough that the agent can continue seamlessly without the " +
              "original messages. Do NOT include filler or meta-commentary.",
          },
          { role: "user", content: transcript },
        ],
      }),
    });

    if (!res.ok) {
      throw new Error(`LLM responded with ${res.status}`);
    }

    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
    };
    const summary =
      data.choices?.[0]?.message?.content?.trim() ?? "(Summary unavailable)";

    const summaryMessage: StoredMessage = {
      id: `compact-${Date.now()}`,
      role: "system",
      content:
        `[CONTEXT SUMMARY — ${toSummarize.length} earlier message(s) compacted to save context]\n\n` +
        summary,
      timestamp: Date.now(),
    };

    return NextResponse.json({
      messages: [summaryMessage, ...toKeep],
      compacted: true,
      removedCount: toSummarize.length,
    });
  } catch (err) {
    // On LLM failure, still compact — just without a summary (drop old messages).
    // Better to lose the summary than block the user.
    const fallbackMessage: StoredMessage = {
      id: `compact-${Date.now()}`,
      role: "system",
      content: `[CONTEXT COMPACTED — ${toSummarize.length} earlier message(s) removed. Summary generation failed: ${String(err)}]`,
      timestamp: Date.now(),
    };
    return NextResponse.json({
      messages: [fallbackMessage, ...toKeep],
      compacted: true,
      removedCount: toSummarize.length,
    });
  }
}
