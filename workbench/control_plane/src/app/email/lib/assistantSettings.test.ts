// WS-17 EM-T7 — the "Auto draft replies" toggle draws OFF for a new mailbox.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.2 D-EM-6 and §10.4
// EM-T7. vitest here runs in the node environment, so it cannot render
// `SettingsTab.tsx`. Two halves hold the done-when:
//
// - the DECISION is `autoDraftRepliesOn`, run here over the body that the
//   gateway answers for a mailbox with no settings row. That body is the shared
//   fixture, and `tests/unit/test_email_auto_draft_defaults.py` proves the real
//   GET handler answers it;
// - the WIRING is a source scan of the tab with comments stripped, so a comment
//   cannot satisfy it.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { autoDraftRepliesOn } from "./assistantSettings";

type NewMailboxFixture = {
  get_without_settings_row: { draft_replies: boolean };
};

const FIXTURE: NewMailboxFixture = JSON.parse(
  readFileSync(
    fileURLToPath(
      // …/email/lib → …/email → …/app → …/src → control_plane → workbench → repo
      new URL(
        "../../../../../../tests/fixtures/email_new_mailbox_settings.json",
        import.meta.url,
      ),
    ),
    "utf-8",
  ),
);

const SETTINGS_TAB = join(
  __dirname, "..", "components", "automation", "ai-settings", "SettingsTab.tsx",
);

/** Source with `//` and block comments removed, so prose cannot pass a scan. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

describe("a new mailbox (D-EM-6)", () => {
  it("reads the fixture the gateway test also reads", () => {
    // A fixture that went missing would leave this file asserting nothing
    // while the pytest half stayed green, so its absence must be loud here.
    expect(FIXTURE.get_without_settings_row).toBeDefined();
    expect("draft_replies" in FIXTURE.get_without_settings_row).toBe(true);
  });

  it("draws the Auto draft replies toggle OFF", () => {
    expect(autoDraftRepliesOn(FIXTURE.get_without_settings_row)).toBe(false);
  });

  it("draws OFF when the body has no draft_replies field", () => {
    expect(autoDraftRepliesOn({})).toBe(false);
    expect(autoDraftRepliesOn(null)).toBe(false);
    expect(autoDraftRepliesOn(undefined)).toBe(false);
  });

  it("draws ON only when the member turned it on", () => {
    expect(autoDraftRepliesOn({ draft_replies: true })).toBe(true);
    expect(autoDraftRepliesOn({ draft_replies: false })).toBe(false);
  });
});

describe("SettingsTab wiring", () => {
  const src = codeOnly(readFileSync(SETTINGS_TAB, { encoding: "utf-8" }));

  it("draws the toggle through autoDraftRepliesOn", () => {
    expect(src).toMatch(/enabled=\{autoDraftRepliesOn\(s\)\}/);
  });

  it("has no second reading of draft_replies for the toggle", () => {
    // `enabled={s.draft_replies}` would bypass the rule above.
    expect(src).not.toMatch(/enabled=\{\s*s\.draft_replies\s*\}/);
  });

  it("still saves the member's choice", () => {
    expect(src).toMatch(/persistPatch\(\{\s*draft_replies:\s*v\s*\}\)/);
  });
});

const RULES_TAB = join(
  __dirname, "..", "components", "automation", "ai-settings", "RulesTab.tsx",
);

describe("RulesTab preset copy (D-EM-6)", () => {
  // The gateway owns the presets (`rules.py::_PRESET_RULES`), and
  // `test_email_presets.py` pins that no preset drafts by default. This copy
  // only names the presets for "Add defaults". It must not show a draft
  // action that the gateway no longer installs.
  const src = codeOnly(readFileSync(RULES_TAB, { encoding: "utf-8" }));
  const start = src.indexOf("const PRESET_RULES");
  const block = src.slice(start, src.indexOf("\n];", start));

  it("finds the preset list", () => {
    expect(start).toBeGreaterThan(-1);
    expect(block).toMatch(/name:\s*"Needs Reply"/);
  });

  it("lists no DRAFT_EMAIL action on any preset", () => {
    expect(block).not.toMatch(/DRAFT_EMAIL/);
  });
});
