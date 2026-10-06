/**
 * WS-45 S4 (D90): the surfaces that force a model from a `chat_model` setting.
 *
 * Spec: `project-docs/specs/ai_tier_routing.md` §7.1, §8 and ticket S4.
 *
 * For an agent that the platform routes, the email chat, the Tasks rail and
 * the Projects rail stop passing `model` and `lockModel`, and stop reading the
 * `chat_model` setting. The two settings controls hide the chat row. The
 * column stays (R6). With the UI flag off, every surface is as on main.
 *
 * Vitest here runs in node and cannot mount a component. So the rules are pure
 * functions, tested here, and a source scan proves each surface calls them.
 *
 * Mutations this file catches (R7):
 * - `governedModelProps` passes the model for a covered agent -> "covered".
 * - it drops `lockModel` for an uncovered one -> "uncovered".
 * - `readsChatModel` reads before the list lands -> "not before it knows".
 * - `visibleModelRows` drops a background row -> "only the chat row".
 * - a surface passes a bare `lockModel` or `model={chatModel}` again, or reads
 *   `chat_model` ungated -> "each surface calls the rules".
 * - `useTierRouted` fetches with the UI flag off -> "the hook".
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  composerModelPlan,
  governedModelProps,
  readsChatModel,
  tierRoutedFor,
  visibleModelRows,
} from "./tierRouting";

const SRC = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(SRC, rel), "utf-8");

describe("tierRoutedFor", () => {
  it("is the composer's own answer, for every input", () => {
    for (const uiOn of [false, true]) {
      for (const agentsKnown of [false, true]) {
        for (const tier_routed of [undefined, false, true]) {
          const input = { uiOn, agentsKnown, entry: { name: "a", tier_routed } };
          expect(tierRoutedFor(input)).toBe(composerModelPlan(input).covered);
        }
      }
    }
  });

  it("is false with the UI flag off, whatever the gateway says", () => {
    expect(tierRoutedFor({ uiOn: false, agentsKnown: true, entry: { name: "a", tier_routed: true } })).toBe(false);
  });
});

describe("governedModelProps", () => {
  it("uncovered: the setting's model, locked, as today", () => {
    expect(governedModelProps(false, "tier-powerful")).toEqual({ model: "tier-powerful", lockModel: true });
    expect(governedModelProps(false, undefined)).toEqual({ model: undefined, lockModel: true });
  });

  it("uncovered with no lock: the model only (the Projects rail)", () => {
    expect(governedModelProps(false, "tier-fast", false)).toEqual({ model: "tier-fast" });
  });

  it("covered: neither prop", () => {
    expect(governedModelProps(true, "tier-powerful")).toEqual({});
    expect(governedModelProps(true, "tier-powerful", false)).toEqual({});
  });
});

describe("readsChatModel", () => {
  it("reads only once it knows the agent is not covered", () => {
    expect(readsChatModel({ known: true, covered: false })).toBe(true);
    expect(readsChatModel({ known: true, covered: true })).toBe(false);
  });

  it("not before it knows, so a covered setting is never read once", () => {
    expect(readsChatModel({ known: false, covered: false })).toBe(false);
  });
});

describe("visibleModelRows", () => {
  const rows = [{ key: "draft_model" }, { key: "compose_model" }, { key: "chat_model" }];

  it("uncovered: every row, in order", () => {
    expect(visibleModelRows(rows, "chat_model", false)).toEqual(rows);
  });

  it("covered: only the chat row leaves (§1.2: background tiers stay)", () => {
    expect(visibleModelRows(rows, "chat_model", true).map((r) => r.key)).toEqual(["draft_model", "compose_model"]);
  });
});

// ── Each surface calls the rules (source scan) ───────────────────────────────

const SURFACES: Record<string, { agent: string; props: RegExp; reads?: RegExp }> = {
  "app/chat/page.tsx": {
    agent: 'useTierRouted("email-assistant")',
    props: /governedModelProps\(emailTier\.covered, emailChatModel\)/,
    reads: /if \(emailReadsModel\) setEmailChatModel\(s\.chat_model/,
  },
  "app/email/components/EmailAssistantChat.tsx": {
    agent: "useTierRouted(AGENT)",
    props: /\{\.\.\.governedModelProps\(tier\.covered, chatModel\)\}/,
    reads: /if \(readsModel\) setChatModel\(s\.chat_model/,
  },
  "app/tasks/components/AssistantRail.tsx": {
    agent: "useTierRouted(AGENT)",
    props: /\{\.\.\.governedModelProps\(tier\.covered, chatModel\)\}/,
  },
  "app/projects/components/AssistantRail.tsx": {
    agent: "useTierRouted(PROJECTS_AGENT)",
    props: /\{\.\.\.governedModelProps\(tier\.covered, chatModel, false\)\}/,
    reads: /if \(!readsModel\) return;\s+let cancelled = false;\s+fetchTaskSettings\(\)/,
  },
};

describe("each surface calls the rules", () => {
  for (const [rel, want] of Object.entries(SURFACES)) {
    it(rel, () => {
      const src = read(rel);
      expect(src).toContain(want.agent);
      expect(src).toMatch(want.props);
      if (want.reads) expect(src).toMatch(want.reads);
      // The old forced props are gone from the AgentChat element.
      expect(src).not.toMatch(/^\s+lockModel\s*$/m);
      expect(src).not.toMatch(/^\s+lockModel=\{/m);
      expect(src).not.toMatch(/^\s+model=\{(chatModel|emailChatModel)\}/m);
    });
  }

  it("the two settings controls hide the chat row of a covered agent", () => {
    const email = read("app/email/components/automation/ai-settings/SettingsTab.tsx");
    expect(email).toContain('useTierRouted("email-assistant")');
    expect(email).toMatch(/\], "chat_model", tier\.covered\)\.map\(\(cfg\)/);
    const tasks = read("app/tasks/components/TaskSettingsModal.tsx");
    expect(tasks).toContain('useTierRouted("task-manager")');
    expect(tasks).toContain('visibleModelRows(MODEL_FIELDS, "chatModel", tier.covered)');
  });
});

describe("the hook", () => {
  const hook = read("hooks/useTierRouted.ts");

  it("makes no request with the UI flag off, and knows at once", () => {
    expect(hook).toMatch(/useEffect\(\(\) => \{\s+if \(!uiOn\) return;/);
    expect(hook).toContain("const known = !uiOn || entries !== null;");
  });

  it("reads coverage through the one rule, and names no agent", () => {
    expect(hook).toContain("tierRoutedFor({");
    expect(hook).not.toMatch(/"(email|projects|crm|whatsapp)-assistant"|"task-manager"/);
  });

  it("reads coverage from the gateway's agent list only", () => {
    expect(hook.match(/fetch\(/g)?.length).toBe(1);
    expect(hook).toContain('fetch("/api/agent/list")');
  });
});
