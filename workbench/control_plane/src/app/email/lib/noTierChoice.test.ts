/**
 * D-EM-61: no member chooses the model or the tier of an email AI task.
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.16, EM-T15.
 *
 * The owner, 2026-10-09: "It is hard-coded depending on our best process for
 * email. You can remove the settings for the email AI tiers for doing
 * different things." So the email settings draw no model row, the settings
 * type carries no model field, the agent's settings card names none, and the
 * two email chats pass the one tier that our code chooses.
 *
 * A source scan, because the subject is the absence of a control and vitest
 * here runs in node with no DOM. The backend half is
 * `tests/unit/test_email_no_tier_choice.py`.
 *
 * Mutations this file catches (R7):
 * - a model row comes back into SettingsTab -> "no Email source names a
 *   model field" and "the settings tab reads no tier list".
 * - a chat reads the stored chat model again -> "the two email chats pass
 *   the fixed tier".
 * - the client tier drifts from the gateway's -> "one tier on both sides".
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { EMAIL_CHAT_TIER } from "./assistantSettings";

const SRC = fileURLToPath(new URL("../../../", import.meta.url));
const REPO = join(SRC, "..", "..", "..");
const ROOTS = [join(SRC, "app", "email"), join(SRC, "components", "email")];
const THIS_FILE = fileURLToPath(import.meta.url);
const TAB = join(SRC, "app", "email", "components", "automation", "ai-settings", "SettingsTab.tsx");
const read = (p: string) => readFileSync(p, "utf-8");

// Built from parts, so this file does not name the fields it forbids.
const RETIRED = ["draft", "compose", "chat"].map((k) => `${k}_${"model"}`);

function sourcesUnder(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sourcesUnder(full, out);
    else if (/\.(ts|tsx)$/.test(name) && full !== THIS_FILE) out.push(full);
  }
  return out;
}

describe("no email AI tier is a member choice (D-EM-61)", () => {
  it("scans real files", () => {
    const files = ROOTS.flatMap((root) => sourcesUnder(root));
    expect(files.length).toBeGreaterThan(20);
    expect(files).toContain(TAB);
  });

  it("no Email source names a model field", () => {
    const hits = ROOTS.flatMap((root) => sourcesUnder(root)).flatMap((f) => {
      const src = read(f);
      return RETIRED.filter((k) => src.includes(k)).map((k) => `${f}: ${k}`);
    });
    expect(hits).toEqual([]);
  });

  it("the settings tab reads no tier list and draws no tier picker", () => {
    const tab = read(TAB);
    expect(tab).not.toContain("/api/settings/llm");
    expect(tab).not.toContain("tier_name");
    expect(tab).not.toContain("<optgroup");
    expect(tab).not.toContain("useTierRouted");
    expect(tab).not.toContain("visibleModelRows");
    expect(tab).not.toMatch(/tier-(fast|balanced|powerful)/);
  });

  it("the two email chats pass the fixed tier and read no stored one", () => {
    const surfaces: Record<string, RegExp> = {
      "app/email/components/EmailAssistantChat.tsx":
        /governedModelProps\(tier\.covered, EMAIL_CHAT_TIER\)/,
      "app/chat/page.tsx": /governedModelProps\(emailTier\.covered, EMAIL_CHAT_TIER\)/,
    };
    for (const [rel, props] of Object.entries(surfaces)) {
      const src = read(join(SRC, rel));
      expect(src, rel).toMatch(props);
      expect(src, rel).not.toMatch(/\.chat_model\b/);
      expect(src, rel).not.toContain("readsChatModel");
    }
  });

  it("one tier on both sides", () => {
    // The gateway's EMAIL_TASK_TIERS["chat"] serves the uncovered chat route.
    // The client passes the same tier, so the two never disagree.
    const gateway = read(join(
      REPO, "apps", "services", "gateway", "gateway", "routes", "email",
      "automation", "assistant.py",
    ));
    expect(gateway).toContain(`"chat": "${EMAIL_CHAT_TIER}"`);
    expect(EMAIL_CHAT_TIER).toBe("tier-powerful");
  });
});
