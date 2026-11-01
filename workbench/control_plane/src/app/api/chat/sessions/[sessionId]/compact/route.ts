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
 *   { messages: ChatMessage[], compacted: boolean, removedCount: number,
 *     error?: string }
 *
 * ⚠️ A summary that fails or comes back blank KEEPS every message (task
 * 1128). It answers 502 with `compacted: false` and the messages as sent.
 * Dropping the old messages with no summary lost them for the agent: a blank
 * answer passed the `??` fallback, because `""` is not null.
 */

const LITELLM_BASE_URL =
  process.env.COPILOT_LLM_BASE_URL ??
  process.env.LITELLM_BASE_URL ??
  "http://127.0.0.1:8080/v1";
const LITELLM_KEY =
  process.env.LITELLM_MASTER_KEY ?? "";
// The body sends no `thinking` (task 1128). `deepseek/deepseek-chat` does not
// think, and the gateway's local `/v1` path forwards named fields only, so the
// field would reach no model.
const COMPACT_MODEL = process.env.SUGGESTION_MODEL ?? "deepseek/deepseek-chat";

/** The answer when no summary came back: nothing is removed. */
function keepEverything(messages: StoredMessage[], error: string) {
  return NextResponse.json(
    { messages, compacted: false, removedCount: 0, error },
    { status: 502 },
  );
}

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
      choices?: { message?: { content?: string | null } }[];
    };
    const summary = (data.choices?.[0]?.message?.content ?? "").trim();
    if (!summary) {
      // A blank answer is no summary. Keep the old messages (task 1128).
      console.warn("[compact] summary_empty: kept every message");
      return keepEverything(messages, "summary_empty");
    }

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
    // A failed summary removes nothing either (task 1128). The chat goes on
    // with every message, and the gateway fits the context window itself.
    console.warn(`[compact] summary_failed: kept every message: ${String(err)}`);
    return keepEverything(messages, "summary_failed");
  }
}
