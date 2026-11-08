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

import {
  AUTO_RUN_AI_LINE, AUTO_RUN_DESCRIPTION, autoDraftRepliesOn, autoRunChoiceBody,
  settingsPatchBody, settingsSaveBody, withoutAutoRunChoice,
} from "./assistantSettings";
import type { AssistantSettings } from "./types";

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

describe("the Auto-run line (WS-17 EM-G11c2, item 6)", () => {
  const src = codeOnly(readFileSync(SETTINGS_TAB, { encoding: "utf-8" }));

  it("holds the words of the spec, word for word", () => {
    expect(AUTO_RUN_AI_LINE).toBe(
      "An AI model reads each new message to choose the rule that matches. "
        + "Each action is one that you set.",
    );
  });

  it("draws the line in the Auto-run card", () => {
    const card = src.slice(
      src.indexOf("Run rules automatically on new mail"),
      src.indexOf(AUTO_RUN_TOGGLE),
    );
    expect(card).toContain("{AUTO_RUN_AI_LINE}");
  });

  it("says that OFF also stops the Reply Zero labels and the sorting sweep (fix round 1, P1)", () => {
    expect(AUTO_RUN_DESCRIPTION).toMatch(/Reply Zero labels/);
    expect(AUTO_RUN_DESCRIPTION).toMatch(/sorting sweep/);
    expect(AUTO_RUN_DESCRIPTION).toMatch(/Turn it off to stop all three/);
    const card = src.slice(
      src.indexOf("Run rules automatically on new mail"),
      src.indexOf(AUTO_RUN_TOGGLE),
    );
    expect(card).toContain("{AUTO_RUN_DESCRIPTION}");
  });
});

/** The Auto-run switch's own save in `SettingsTab.tsx` (task 2305). */
const AUTO_RUN_TOGGLE = "persist({ ...s, auto_run: v }, true)";

const SETTINGS = {
  account_id: "acc-1",
  about: "Founder",
  signature: "Ravi",
  auto_run: false,
  auto_run_chosen: false,
  draft_replies: false,
} as unknown as AssistantSettings;

describe("the Auto-run choice marker (task 2305)", () => {
  const src = codeOnly(readFileSync(SETTINGS_TAB, { encoding: "utf-8" }));

  it("the member's own act sends the marker with the value", () => {
    expect(autoRunChoiceBody(SETTINGS, true)).toEqual({
      ...SETTINGS, auto_run: true, auto_run_choice: true,
    });
    expect(autoRunChoiceBody(SETTINGS, false)).toEqual({
      ...SETTINGS, auto_run: false, auto_run_choice: true,
    });
  });

  it("a save of another field never sends the marker, even from a state that held one", () => {
    const held = { ...SETTINGS, auto_run_choice: true as const };
    const body = settingsPatchBody(held, { signature: "— Ravi" });
    expect(body).toEqual({ ...SETTINGS, signature: "— Ravi" });
    expect("auto_run_choice" in body).toBe(false);
    // A patch that changes auto_run is still no explicit act: no marker.
    expect("auto_run_choice" in settingsPatchBody(SETTINGS, { auto_run: true })).toBe(false);
  });

  it("persist drops the marker unless the caller is the Auto-run switch", () => {
    const held = { ...SETTINGS, auto_run_choice: true as const };
    expect("auto_run_choice" in settingsSaveBody(held, false)).toBe(false);
    expect(settingsSaveBody({ ...SETTINGS, auto_run: true }, true)).toEqual({
      ...SETTINGS, auto_run: true, auto_run_choice: true,
    });
    expect(withoutAutoRunChoice(held)).toEqual(SETTINGS);
  });

  it("the read-only GET field is never the marker", () => {
    const body = settingsPatchBody({ ...SETTINGS, auto_run_chosen: true }, { about: "x" });
    expect(body.auto_run_chosen).toBe(true);
    expect("auto_run_choice" in body).toBe(false);
  });

  it("only the Auto-run switch of the tab passes the act, through persist", () => {
    expect(src).toContain(`onChange={(v) => ${AUTO_RUN_TOGGLE}}`);
    // One caller passes the act, and the tab never names the marker itself.
    expect(src.match(/persist\([^;]*?, true\)/g)).toHaveLength(1);
    expect(src).not.toMatch(/auto_run_choice/);
    expect(src).not.toMatch(/persistPatch\(\{\s*auto_run:/);
  });

  it("persistPatch and persist strip the marker", () => {
    expect(src).toContain(
      "const persistPatch = (p: Partial<AssistantSettings>) => persist(settingsPatchBody(s, p));",
    );
    expect(src).toMatch(
      /const persist = async \(next: AssistantSettingsSave, autoRunChoice = false\) => \{\s*setSettings\(withoutAutoRunChoice\(next\)\);/,
    );
    expect(src).toContain("await saveAssistantSettings(settingsSaveBody(next, autoRunChoice));");
    expect(src).toContain("setSettings(withoutAutoRunChoice(res));");
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
