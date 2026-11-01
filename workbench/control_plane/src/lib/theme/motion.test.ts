/**
 * Every animation in `globals.css` answers a member who asked for less motion.
 *
 * `DESIGN_SYSTEM.md` §4 holds the rule. An `@keyframes` passes when each rule
 * that runs it either sits inside `@media (prefers-reduced-motion:
 * no-preference)`, or names a selector that the `reduce` block stops. The rest
 * must stand in `MOTION_EXEMPT` with the reason the motion stays.
 *
 * Measured 2026-10-14, when this test was written: `.chat-shimmer-text` swept
 * forever under reduced motion, and nothing stopped it.
 *
 * The second half reads every source file (UI gate, owner 2026-10-27). It
 * holds four motion rules outside `globals.css` with a ratchet:
 *
 *   G1  no `@keyframes` outside `globals.css`
 *   G2  no raw `cubic-bezier(` or `duration-[Nms]`
 *   G3  no motion of a layout property, and no `transition-all`
 *   G4  no `animate-pulse`, `-ping` or `-bounce` without `motion-safe:`
 *
 * `DESIGN_SYSTEM.md` §11 holds the rules. `uiRatchet.ts` holds the ratchet.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { countMatches, holdRatchet, readSource, uiSources } from "./uiRatchet";

const GLOBALS = readFileSync(
  fileURLToPath(new URL("../../app/globals.css", import.meta.url)),
  "utf8",
  // A comment goes, and a string stays: `@source "../**/*.tsx"` holds a `/*`.
).replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|\/\*[\s\S]*?\*\//g, (_m, str) => str ?? "");

/** Motion that stays under reduced motion, and why. Each entry is a decision. */
const MOTION_EXEMPT: Record<string, string> = {
  // A status loop. The dot says a run is live, and a still dot reads as a hang.
  "chat-pulse-dot": "status",
  // A status loop. The three dots say the agent is working.
  "chat-typing-dot": "status",
  // `swapPanelForm` and `morphAskBox` in `lib/panelMotion.ts` start no view
  // transition under reduced motion, so these two never run for that member.
  "panel-swap-out": "gated in panelMotion.ts",
  "panel-swap-in": "gated in panelMotion.ts",
  // `morphAskBox` again: the side panel's picture while the Ask bar's box
  // flies into it.
  "ask-panel-in": "gated in panelMotion.ts",
};

interface Block {
  prelude: string;
  body: string;
  parents: string[];
}

/** Every `{ … }` block, with the preludes of the blocks around it. */
function blocks(css: string): Block[] {
  const out: Block[] = [];
  const stack: { prelude: string; start: number }[] = [];
  let preludeStart = 0;
  for (let i = 0; i < css.length; i++) {
    const c = css[i];
    if (c === "{") {
      stack.push({ prelude: css.slice(preludeStart, i).trim(), start: i + 1 });
      preludeStart = i + 1;
    } else if (c === "}") {
      const top = stack.pop();
      if (!top) throw new Error(`Unbalanced "}" at ${i} in globals.css`);
      out.push({ prelude: top.prelude, body: css.slice(top.start, i), parents: stack.map((s) => s.prelude) });
      preludeStart = i + 1;
    } else if (c === ";") {
      preludeStart = i + 1;
    }
  }
  if (stack.length) throw new Error("Unclosed block in globals.css");
  return out;
}

const ALL = blocks(GLOBALS);
const isReduce = (p: string) => /prefers-reduced-motion:\s*reduce/.test(p);
const isNoPreference = (p: string) => /prefers-reduced-motion:\s*no-preference/.test(p);
const selectors = (prelude: string) => prelude.split(",").map((s) => s.trim()).filter(Boolean);

const KEYFRAMES = ALL.flatMap((b) => {
  const m = /^@keyframes\s+([\w-]+)$/.exec(b.prelude);
  return m ? [m[1]] : [];
});

/**
 * The selectors the `reduce` block stops. Only a rule that sets the animation
 * to `none` counts. The same selector in a rule that sets a colour stops
 * nothing (`.sidebar-beacon` has both, and the review of 2026-10-14 proved it).
 */
const STOPPED = new Set(
  ALL.filter(
    (b) =>
      !b.prelude.startsWith("@") &&
      b.parents.some(isReduce) &&
      /(?<![\w-])animation(?:-name)?\s*:\s*none\b/.test(b.body),
  ).flatMap((b) => selectors(b.prelude)),
);

/** The rules that run `name`, outside the `reduce` block. */
function runners(name: string): Block[] {
  const runs = new RegExp(`animation(?:-name)?\\s*:[^;]*(?<![\\w-])${name}(?![\\w-])`);
  return ALL.filter(
    (b) => !b.prelude.startsWith("@") && !b.parents.some(isReduce) && runs.test(b.body),
  );
}

function unanswered(name: string): string[] {
  return runners(name)
    // Each selector in the list needs its own answer. One stopped selector
    // does not excuse the others.
    .filter((b) => !b.parents.some(isNoPreference) && !selectors(b.prelude).every((s) => STOPPED.has(s)))
    .map((b) => b.prelude);
}

describe("every animation in globals.css answers reduced motion (DESIGN_SYSTEM.md §4)", () => {
  it("finds the keyframes and the reduce block, so the test reads the real file", () => {
    expect(KEYFRAMES.length).toBeGreaterThan(5);
    expect(STOPPED.size).toBeGreaterThan(3);
  });

  it.each(KEYFRAMES.filter((k) => !(k in MOTION_EXEMPT)))("%s stops for a member who asked for less motion", (name) => {
    expect(runners(name).length, `nothing runs @keyframes ${name}. Delete it.`).toBeGreaterThan(0);
    expect(
      unanswered(name),
      `add the selector to the \`prefers-reduced-motion: reduce\` block, or gate it in \`no-preference\``,
    ).toEqual([]);
  });

  it("each exemption names motion that exists and is still unanswered", () => {
    for (const name of Object.keys(MOTION_EXEMPT)) {
      expect(KEYFRAMES, `MOTION_EXEMPT names ${name}, and globals.css has no such keyframes`).toContain(name);
      expect(
        unanswered(name).length,
        `${name} now stops under reduced motion. Delete its MOTION_EXEMPT entry`,
      ).toBeGreaterThan(0);
    }
  });
});

// ── The UI gate: motion outside globals.css (DESIGN_SYSTEM.md §11) ──────────

const SOURCES = uiSources();

/** The count of `re` in each file that `keep` admits. */
function measure(re: RegExp, keep: (file: string) => boolean, text: (file: string) => string = readSource) {
  return Object.fromEntries(SOURCES.filter(keep).map((f) => [f, countMatches(text(f), re)]));
}

/** G1. A keyframe that a component defines escapes the reduced-motion check above. */
const LOCAL_KEYFRAMES = /@keyframes\b/g;

/** Measured 2026-10-27. Each number may only fall. */
const KEYFRAMES_DEBT: Record<string, number> = {
  "app/agents/page.tsx": 1,
  "app/observability/office-topdown.tsx": 8,
  "app/observability/page.tsx": 7,
  "app/observability/pixel.tsx": 7,
  "components/BreathingCharacter.tsx": 1,
  "components/flash.module.css": 1,
  "components/genUITemplates.tsx": 12,
};

/** G2. A curve or a duration written by hand is a second motion vocabulary. */
const RAW_MOTION = /cubic-bezier\(|duration-\[\d/g;

/** Files that may write a curve, and why. Each one is a mirror that a test holds to the tokens. */
const RAW_MOTION_EXEMPT: Record<string, string> = {
  "lib/panelMotion.ts": "the script mirror of --panel-easing. panelMotion.test.ts holds it to globals.css",
  "lib/theme/themes.ts": "the data mirror of globals.css. themes.test.ts holds it",
  "lib/theme/glassTokens.ts": "the sandbox mirror of the glass --motion-easing. glassTokens.test.ts holds it to globals.css",
};

const RAW_MOTION_DEBT: Record<string, number> = {
  "components/genUITemplates.tsx": 1,
};

/**
 * G3. Each frame of a width, a height, a position or a margin lays out the page
 * again. `transition-all` moves them without a name.
 */
const LAYOUT_MOTION = new RegExp(
  [
    String.raw`(?<![\w-])transition-all(?![\w-])`,
    String.raw`(?<![\w-])transition-\[[^\]\s]*(?:width|height|top|left|right|bottom|margin|inset)[^\]\s]*\]`,
    String.raw`transition(?:-property|Property)?\s*:\s*["'\x60]?[^;"'\x60{}\n]*?(?<![\w-])(?:all|width|height|top|left|right|bottom|margin(?:-\w+)?|inset|max-height|max-width|min-height|min-width)(?![\w-])`,
  ].join("|"),
  "g",
);

/**
 * The two panels of `DESIGN_SYSTEM.md` §4. Each one animates `width` on
 * purpose, so that the page beside it moves with it. Keyed by its selector in
 * `globals.css`. Do not add a surface that does not push the page.
 */
const LAYOUT_MOTION_EXEMPT: Record<string, string> = {
  ".sidebar-rail": "the sidebar fold. The page beside the rail reflows with it (navigation_shell.md §3.2)",
  ".assistant-spacer[data-moving]": "the assistant's spacer. The page beside the dock reflows with it, and only while it moves",
};

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const exemptBlock = (selector: string) => new RegExp(`${escapeRe(selector)}\\s*\\{[^}]*\\}`, "g");

/** A file's text, with the two exempt panel rules taken out of `globals.css`. */
function withoutPanelRules(file: string): string {
  const text = readSource(file);
  if (file !== "app/globals.css") return text;
  return Object.keys(LAYOUT_MOTION_EXEMPT).reduce((t, sel) => t.replace(exemptBlock(sel), ""), text);
}

const LAYOUT_MOTION_DEBT: Record<string, number> = {
  "app/agents/page.tsx": 2,
  "app/chat/page.tsx": 1,
  "app/email/components/EmailList.tsx": 1,
  "app/email/components/SearchBar.tsx": 1,
  "app/email/components/automation/AISettingsView.tsx": 1,
  "app/email/components/automation/AnalyticsView.tsx": 1,
  "app/email/components/automation/ai-settings/RulesTab.tsx": 1,
  "app/email/components/automation/ai-settings/VoiceProfileDialog.tsx": 1,
  "app/email/page.tsx": 2,
  // `.tech-transition` and `.cc-control`, the two token utilities.
  "app/globals.css": 2,
  "app/integrations/components/ApiKeysView.tsx": 3,
  "app/memory/page.tsx": 2,
  "app/observability/page.tsx": 1,
  "app/tasks/components/TaskListGrouped.tsx": 1,
  "components/AddAgentWizard.tsx": 1,
  "components/ArtifactSidebar.tsx": 3,
  "components/ContextRing.tsx": 1,
  "components/genUITemplates.tsx": 3,
  "components/ui/Collapsible.tsx": 1,
  "components/ui/ProgressBar.tsx": 1,
  // The two classic knobs that slide by `left` (the email Toggle and the
  // Meeting copilot). They moved here from `email/.../automation/ui.tsx` and
  // `MeetingPrep.tsx` with GL-2, and the classic look keeps them.
  "components/ui/Switch.tsx": 2,
  "lib/theme/sandbox-frame.ts": 1,
};

/** G4. A loop that is not a status must stop for a member who asked for less motion. */
const BARE_LOOP = /(?<!motion-safe:)(?<![\w-])animate-(?:pulse|ping|bounce)(?![\w-])/g;

const BARE_LOOP_DEBT: Record<string, number> = {
  "app/agents/page.tsx": 1,
  "app/artifacts/page.tsx": 1,
  "app/calendar/components/NowNextBar.tsx": 2,
  "app/email/components/automation/AISettingsView.tsx": 1,
  "app/email/components/automation/AnalyticsView.tsx": 4,
  "app/email/components/automation/ai-settings/TestTab.tsx": 1,
  "app/memory/page.tsx": 2,
  "app/notes/components/ActiveBots.tsx": 1,
  "app/notes/components/LiveDock.tsx": 1,
  "app/notes/components/RecordingDock.tsx": 1,
  "app/notes/live/[id]/page.tsx": 1,
  "app/notes/page.tsx": 2,
  "app/notes/session/[id]/page.tsx": 3,
  "app/observability/page.tsx": 2,
  "app/page.tsx": 1,
  "app/tasks/components/FocusMode.tsx": 3,
  "app/workflows/components/WorkflowNode.tsx": 2,
  "app/workflows/page.tsx": 1,
  "components/AgentChat.tsx": 3,
  "components/AppShell.tsx": 2,
  "components/ArtifactViewerModal.tsx": 2,
  "components/ContextRing.tsx": 1,
  "components/SandboxedReact.tsx": 1,
  "components/Sidebar.tsx": 3,
  "components/ThinkingContainer.tsx": 4,
  "components/ui/Skeleton.tsx": 1,
  "lib/streamCaret.ts": 1,
};

describe("the UI gate: motion outside globals.css (DESIGN_SYSTEM.md §11)", () => {
  it("reads the real tree", () => {
    expect(SOURCES).toContain("app/globals.css");
    expect(SOURCES.some((f) => f.endsWith(".tsx"))).toBe(true);
    expect(SOURCES.some((f) => f.endsWith(".test.ts"))).toBe(false);
  });

  holdRatchet({
    rule: "G1 a component defines its own @keyframes",
    fix: "Put the keyframes in globals.css with an answer for reduced motion, or use one that is there.",
    measure: () => measure(LOCAL_KEYFRAMES, (f) => f !== "app/globals.css"),
    budget: KEYFRAMES_DEBT,
  });

  holdRatchet({
    rule: "G2 a raw cubic-bezier( or duration-[Nms]",
    fix: "Use a motion token: tech-transition, or var(--motion-duration) and var(--motion-easing).",
    measure: () =>
      measure(
        RAW_MOTION,
        (f) => f !== "app/globals.css" && !f.startsWith("components/ui/") && !(f in RAW_MOTION_EXEMPT),
      ),
    budget: RAW_MOTION_DEBT,
  });

  it("G2: each exempt mirror still holds a curve", () => {
    for (const file of Object.keys(RAW_MOTION_EXEMPT)) {
      expect(countMatches(readSource(file), RAW_MOTION), `${file} holds no curve now. Delete its entry`).toBeGreaterThan(0);
    }
  });

  holdRatchet({
    rule: "G3 a transition of width, height, a position, a margin, or transition-all",
    fix: "Move transform and opacity, and name the property, as in transition-transform or transition-opacity.",
    measure: () => measure(LAYOUT_MOTION, () => true, withoutPanelRules),
    budget: LAYOUT_MOTION_DEBT,
  });

  it("G3: each exempt panel rule exists and still moves a layout property", () => {
    const css = readSource("app/globals.css");
    for (const selector of Object.keys(LAYOUT_MOTION_EXEMPT)) {
      const block = css.match(exemptBlock(selector));
      expect(block, `globals.css has no ${selector} rule. Delete its LAYOUT_MOTION_EXEMPT entry`).not.toBeNull();
      expect(
        countMatches(block!.join("\n"), LAYOUT_MOTION),
        `${selector} no longer moves a layout property. Delete its entry`,
      ).toBeGreaterThan(0);
    }
  });

  holdRatchet({
    rule: "G4 animate-pulse, animate-ping or animate-bounce without motion-safe:",
    fix: "Write motion-safe:animate-pulse. A skeleton comes from components/ui/Skeleton.tsx.",
    measure: () => measure(BARE_LOOP, () => true),
    budget: BARE_LOOP_DEBT,
  });
});
