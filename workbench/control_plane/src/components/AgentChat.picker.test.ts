/**
 * The model picker leaves the chat, and each answer names its tier.
 *
 * WS-45 S3 (D90). Spec: `project-docs/specs/ai_tier_routing.md` §7, §8, §10
 * and ticket S3. The spec names this file `AgentChat.picker.test.tsx`. Vitest
 * here collects `*.test.ts` only, so it is `.ts`, and it draws with
 * `createElement`.
 *
 * Vitest runs in node, so no effect runs. The composer is drawn to markup
 * with the agent list passed in, which is the state after the list lands.
 * The rules behind it are pure and live in `lib/tierRouting.ts`, and
 * `lib/tierRouting.test.ts` holds them. This file proves that the composer
 * and the answer use them, and that the UI flag off changes nothing.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - the picker draws for a covered agent -> "shows no picker for a covered agent";
 * - the effort selector goes with it -> "keeps the effort selector";
 * - the models fetch runs without the plan's leave -> "fetches the models only for a live picker";
 * - a `cc-model-` key is read in the composer -> "reads no stored choice itself";
 * - the composer sends a model for a covered agent -> "sends no model field";
 * - the UI flag off changes the composer or the answer -> the "flag off" cases;
 * - the details menu misses the label, or shows with the flag off -> the answer cases.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import AgentChat from "@/components/AgentChat";
import { AnswerDetailsBody } from "@/components/AnswerDetails";
import MessageBubble from "@/components/MessageBubble";
import type { AgentEntry } from "@/app/api/agent/list/route";
import type { ChatMessage } from "@/hooks/useAgentChat";

const FLAG = "NEXT_PUBLIC_AI_TIER_ROUTING";
const AGENT = "projects-assistant";

function entry(tier_routed?: boolean): AgentEntry {
  return {
    name: AGENT, description: "", tags: [], status: "live", agent_runtime: "maf",
    ...(tier_routed === undefined ? {} : { tier_routed }),
  };
}

function withFlag<T>(value: string | undefined, draw: () => T): T {
  const before = process.env[FLAG];
  if (value === undefined) delete process.env[FLAG];
  else process.env[FLAG] = value;
  try {
    return draw();
  } finally {
    if (before === undefined) delete process.env[FLAG];
    else process.env[FLAG] = before;
  }
}

afterEach(() => {
  delete process.env[FLAG];
});

/** The composer, drawn after the agent list landed. */
function composer(flag: string | undefined, agents?: AgentEntry[], model?: string): string {
  return withFlag(flag, () =>
    renderToStaticMarkup(createElement(AgentChat, {
      agentName: AGENT,
      sessionId: "session-ws45-s3",
      ...(agents ? { availableAgents: agents } : {}),
      ...(model ? { model } : {}),
    })),
  );
}

/** The picker trigger's label for each model the tests use. */
const PICKER_LABELS = ["auto (SDK picks)", "Tier 3 (powerful)"];
const hasPicker = (html: string) => PICKER_LABELS.some((l) => html.includes(l));

const read = (rel: string) => readFileSync(join(__dirname, rel), "utf-8");
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*/g, "");

describe("the composer, with the UI flag on", () => {
  it("shows no picker for a covered agent (done-when 1)", () => {
    const html = composer("1", [entry(true)]);
    expect(hasPicker(html)).toBe(false);
    expect(html).not.toContain("LiteLLM");
  });

  it("shows no picker, and names no model, when the caller forces one", () => {
    // The Projects rail passes the member's `chat_model` (S4 removes it).
    const html = composer("1", [entry(true)], "tier-powerful");
    expect(hasPicker(html)).toBe(false);
    expect(html).not.toContain("tier-powerful");
  });

  it("keeps the effort selector, with its three labels (done-when 2, Q3)", () => {
    const html = composer("1", [entry(true)]);
    expect(html).toContain("Let the model decide");
    expect(html).toContain(">Auto<");
    const src = read("AgentChat.tsx");
    const modes = /const THINK_MODES[^=]*=\s*\[([\s\S]*?)\];/.exec(src)?.[1] ?? "";
    expect([...modes.matchAll(/label: "([^"]+)"/g)].map((m) => m[1])).toEqual(["Auto", "Thinking", "Max"]);
  });

  it("keeps the picker for an agent the gateway does not cover", () => {
    expect(hasPicker(composer("1", [entry(false)]))).toBe(true);
    expect(hasPicker(composer("1", [entry()]))).toBe(true);
  });

  it("draws no picker before the agent list lands", () => {
    expect(hasPicker(composer("1"))).toBe(false);
  });
});

describe("the composer, with the UI flag off (done-when 5)", () => {
  it("draws the picker, whatever the gateway says", () => {
    expect(hasPicker(composer(undefined))).toBe(true);
    expect(hasPicker(composer(undefined, [entry(true)]))).toBe(true);
  });

  it("is the same markup for a covered agent, an uncovered one, and the flag on with no coverage", () => {
    const off = composer(undefined, [entry(false)]);
    expect(composer(undefined, [entry(true)])).toBe(off);
    expect(composer(undefined, [entry()])).toBe(off);
    expect(composer("1", [entry(false)])).toBe(off);
    expect(composer(undefined, [entry(true)], "tier-powerful")).toBe(
      composer("1", [entry(false)], "tier-powerful"),
    );
  });
});

describe("the composer's wiring", () => {
  const chat = code(read("AgentChat.tsx"));
  const hook = code(read("../hooks/useAgentChat.ts"));

  it("fetches the models only for a live picker (done-when 1)", () => {
    const fetches = chat.match(/fetch\("\/api\/models\/all"\)/g) ?? [];
    expect(fetches).toHaveLength(1);
    const at = chat.indexOf('fetch("/api/models/all")');
    const effect = chat.slice(chat.lastIndexOf("useEffect(", at), at);
    expect(effect).toMatch(/if \(!modelPlan\.fetchModels\b/);
  });

  it("reads no stored choice itself (the §10 source check)", () => {
    expect(read("AgentChat.tsx")).not.toMatch(/cc-model-/);
    expect(read("AgentChat.tsx")).not.toMatch(/localStorage\.(get|set)Item\(\s*MODEL_/);
    // The two reads it makes are behind the plan.
    expect(chat).toMatch(/tierUi \? null : getLastModel\(agentName\)/);
    expect(chat).toMatch(/modelPlan\.showPicker \? getModelUsage\(\) : \{\}/);
    expect(chat).toMatch(/modelMemoryStep\(\{[\s\S]*?remember: modelPlan\.rememberModel,/);
    expect(chat).toMatch(/restoredForRef\.current = step\.restoredFor;/);
  });

  it("sends no model field for a covered agent (done-when 3)", () => {
    expect(chat).toMatch(/model: modelPlan\.sendModel \? currentModel : null/);
    expect(hook.match(/\.\.\.chatModelField\(modelRef\.current\)/g)).toHaveLength(2);
    expect(hook).not.toMatch(/model: modelRef\.current \?\? "auto"/);
    // A covered orchestrator does not take the direct LiteLLM path.
    expect(chat).toMatch(/isOrchestrator && !modelPlan\.covered \? currentRuntime : "copilot"/);
  });

  it("sends the effort as before, so it reaches payload.think_mode (done-when 2)", () => {
    expect(hook.match(/thinkMode: thinkModeRef\.current \?\? "auto"/g)).toHaveLength(2);
    const route = code(read("../app/api/agent/chat/route.ts"));
    expect(route).toMatch(/think_mode: thinkMode \?\? "auto"/);
    // The server still takes `model` (R6 expand/contract, §8).
    expect(route).toMatch(/model: model \?\? undefined/);
  });

  it("forgets a covered agent's stored choice", () => {
    expect(chat).toMatch(/if \(!modelPlan\.covered\) return;\s*forgetModelChoice\(currentAgentName, agents\)/);
  });
});

// ── The answer's details menu (§7.2, Q4) ────────────────────────────────────

function answer(customEvents?: ChatMessage["customEvents"]): ChatMessage {
  return {
    id: "a1",
    role: "assistant",
    content: "Here is the plan.",
    timestamp: new Date("2026-10-06T10:00:00Z").getTime(),
    ...(customEvents ? { customEvents } : {}),
  } as unknown as ChatMessage;
}

const TWO_ROUTES = [
  { name: "ai.route", value: { tier: "tier-balanced", kind: "chat", reason: "default", request: 1 } },
  { name: "ai.route", value: { tier: "tier-powerful", kind: "plan", reason: "tool_hint", request: 2 } },
];

const bubble = (flag: string | undefined, message: ChatMessage) =>
  withFlag(flag, () => renderToStaticMarkup(createElement(MessageBubble, { message, sessionId: "s1" })));

describe("the answer", () => {
  it("has a details menu that names both tiers (done-when 4)", () => {
    const html = bubble("1", answer(TWO_ROUTES));
    expect(html).toContain('aria-label="Answer details"');
    const body = renderToStaticMarkup(createElement(AnswerDetailsBody, { tierLabel: "Balanced, then Powerful" }));
    expect(body).toContain("Balanced, then Powerful");
    expect(read("MessageBubble.tsx")).toMatch(/tierRouteLabel\(message\.customEvents\)/);
  });

  it("shows the menu to every member: no access check gates it", () => {
    const src = code(read("MessageBubble.tsx"));
    const line = src.split("\n").find((l) => l.includes("const tierLabel")) ?? "";
    expect(line).toMatch(/tierRoutingUiOn\(\) \? tierRouteLabel\(message\.customEvents\) : null/);
    expect(read("AnswerDetails.tsx")).not.toMatch(/useAccess|hasCapability|isAdmin/);
  });

  it("has no menu for an answer with no tier", () => {
    expect(bubble("1", answer())).not.toContain("Answer details");
  });

  it("with the UI flag off, is the same markup with or without the events", () => {
    const plain = bubble(undefined, answer());
    expect(bubble(undefined, answer(TWO_ROUTES))).toBe(plain);
    expect(plain).not.toContain("Answer details");
  });

  it("never draws the events as raw data in the Interactive view fold", () => {
    expect(bubble("1", answer(TWO_ROUTES))).not.toContain("Interactive view");
  });
});
