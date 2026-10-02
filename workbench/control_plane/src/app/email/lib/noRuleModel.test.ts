/**
 * WS-17 EM-T5b-2 item 8: no rules-model picker (D-EM-7).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.8, EM-T5b-2.
 *
 * The rules run on the `decide` tier, and no member can change it. So no
 * Email source names `rule_model`: not the settings card, not the settings
 * type, and not the label of the agent's settings tool card. The other model
 * pickers (draft, compose and chat) stay.
 *
 * A source scan, because the subject is the absence of a control. The
 * backend half is `tests/unit/test_email_assistant_settings.py`.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("../../../", import.meta.url));
const ROOTS = [join(SRC, "app", "email"), join(SRC, "components", "email")];
const THIS_FILE = fileURLToPath(import.meta.url);

function sourcesUnder(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sourcesUnder(full, out);
    else if (/\.(ts|tsx)$/.test(name) && full !== THIS_FILE) out.push(full);
  }
  return out;
}

describe("the rules model is not a member choice (D-EM-7)", () => {
  it("scans real files", () => {
    const files = ROOTS.flatMap((root) => sourcesUnder(root));
    expect(files.length).toBeGreaterThan(20);
    expect(files.some((f) => f.endsWith("SettingsTab.tsx"))).toBe(true);
  });

  it("no Email source names rule_model", () => {
    const hits = ROOTS.flatMap((root) => sourcesUnder(root)).filter((f) =>
      readFileSync(f, "utf-8").includes("rule_model"),
    );
    expect(hits).toEqual([]);
  });

  it("the other model pickers stay", () => {
    const tab = readFileSync(
      join(SRC, "app", "email", "components", "automation", "ai-settings", "SettingsTab.tsx"),
      "utf-8",
    );
    for (const key of ["draft_model", "compose_model", "chat_model"]) {
      expect(tab).toContain(`key: "${key}" as const`);
    }
  });
});
