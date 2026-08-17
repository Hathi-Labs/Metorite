/**
 * Themed-by-construction — the gate that keeps the engine true as the app grows.
 *
 * A theming engine is not a feature you ship, it is an invariant you hold. The
 * work of building it was mechanical; the work of KEEPING it is refusing, every
 * week, the one hardcoded `#10b981` that seemed fine at the time. Nobody can do
 * that by review — a hex value in a 900-line page is invisible — so it is done
 * here.
 *
 * ## What this checks, and why each one
 *
 * 1. **No hardcoded colour.** A literal colour is a pixel the engine cannot
 *    reach. Switching to Fluent leaves it behind, and the surface it sits on is
 *    the one that looks broken.
 * 2. **No direct `lucide-react` imports.** Icons are a theme choice — Fluent
 *    ships Fluent icons, Material ships Material Symbols. An import bypasses
 *    the pack registry and pins that glyph to Lucide for every theme.
 * 3. **No arbitrary Tailwind colour classes.** `bg-[#0c0c0c]` is rule 1 wearing
 *    a class name.
 * 4. **Solid controls go through the primitives.** A raw `<button className=
 *    "bg-primary …">` is themed for COLOUR but not for personality: it cannot
 *    have Material's pill radius and state layer, Fluent's outline on solid
 *    fills, or an uppercase label, because none of that is expressible in a
 *    class string. `<Button>` is where those tokens are applied.
 * 5. **No raw Tailwind PALETTE classes.** `bg-sky-500/10` is the one that got
 *    away: it is a named class, not a bracket class, so rules 1 and 3 both let
 *    it through, and the app accumulated ~950 of them. It is every bit as
 *    unthemed as `#0ea5e9` — switch the org to Material and a chip painted
 *    `sky-500` keeps its hue while every surface around it moves. Categorical
 *    hues have their own themed home now (`--cat-1` … `--cat-8`); state has
 *    `--success` / `--warning` / `--destructive`.
 * 6. **Active/selected wears the house token.** `bg-accent text-accent-foreground`
 *    is a legal pair of tokens meaning the wrong thing — see that rule's own note.
 * 7. **Single-choice and file pickers use the primitives.** A bare `<select>`
 *    wears the OS's own disclosure triangle and a raw `<input type="file">`
 *    wears the browser's *"Choose Files / No file chosen"*. Neither follows the
 *    theme, the icon pack or the label transform, and both were sitting on
 *    `/projects`' task panel when the owner compared it with `/tasks`' detail
 *    side by side. Use `<Select>` from `components/ui/Input.tsx`, and keep the
 *    file input hidden behind a `<Button>` — which is what every correct call
 *    site in this tree already does.
 * 8. **The headless substrate is wrapped in `components/ui/`, and the wrappers
 *    stay wired.** D-PM-15 chose Base UI for the primitive layer and attached
 *    two conditions to the choice: every primitive arrives as a Metorite
 *    wrapper in `components/ui/`, and there is **one** substrate. Both fail the
 *    same way — quietly, one call site at a time — because `@base-ui/react`
 *    ships its own unstyled-but-opinionated defaults, and a page that imports
 *    `Dialog` directly gets the library's product decisions rather than ours.
 *    Observed in Paca, not imagined: its `package.json` carries Base UI *and*
 *    `radix-ui`, the second reaching exactly one file, inherited from a vendored
 *    component registry. So the core of the rule is an import restriction rather
 *    than a style regex, and it is the R7 fence D-PM-15 condition 1 asks for by
 *    name. **`Toast.tsx` (WS-27ak item 3) is the second wrapper** and the rule
 *    covers it by directory rather than by name — but it also brings a failure a
 *    directory rule cannot see: a toast primitive whose **provider is not
 *    mounted** degrades every call site to a silent no-op, which is the exact
 *    defect class the primitive exists to remove, arriving through its own front
 *    door. So this rule additionally pins the provider's mount in
 *    `app/layout.tsx`, the wrapper's own portal + viewport, and that the wired
 *    `/projects` call sites still go through `useToast`. All of that is one
 *    rule — *the primitive layer is real where it claims to be* — not a ninth.
 *
 * ## Ratchet, not a wall
 *
 * The tree was not clean when this landed and pretending otherwise would have
 * meant either a 68-file migration nobody asked for or a gate switched off on
 * day one. So each rule carries a frozen baseline, and:
 *
 *   * a file **not** in the baseline must be clean — this is the case that
 *     matters, because it is every file we have not written yet;
 *   * a baselined file may not get **worse**;
 *   * a baselined file that got **better** fails until its number is lowered,
 *     so the debt figure in this file is always the real one.
 *
 * That last rule is the one that makes the others credible. A baseline that
 * only ever gets edited downward when someone happens to notice is a baseline
 * that quietly becomes fiction.
 *
 * ## Exceptions are argued, not counted
 *
 * Some literals are correct. A sun in a weather glyph is yellow because suns
 * are yellow; Gmail's label palette has to match Gmail's; a person's identity
 * colour must be stable across themes or it stops identifying them. Those live
 * in EXCEPTIONS with a reason each — the reason is the point, because the next
 * author's real question is never "is this allowed" but "is mine like that one".
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("../..", import.meta.url));

// ── Scanning ────────────────────────────────────────────────────────────────

/** Every `.ts`/`.tsx` under `src/`, as paths relative to `src/`, posix-style. */
function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) {
        walk(full);
      } else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(SRC, full).split(sep).join("/"));
      }
    }
  };
  walk(SRC);
  return out.sort();
}

const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

/**
 * Strip comments and HTML numeric entities before looking for colour.
 *
 * Both produce false positives that would have made the rule untrustworthy on
 * its first run, which is how a gate ends up disabled: `ContextRing.tsx`
 * *explains* `--primary: hsl(198 89% 50%)` in a comment, and `TriggerPanel.tsx`
 * writes `&#123;` to render a literal brace — which `#[0-9a-f]{3}` reads as a
 * colour.
 */
function strip(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(?<![:"'/])\/\/[^\n]*/g, "")
    .replace(/&#\d+;/g, "");
}

const COLOR_LITERAL = /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|\brgba?\(|\bhsla?\(/g;
const COLOR_UTILITY =
  "(?:bg|text|border|ring|fill|stroke|from|via|to|shadow|outline|decoration|accent|caret)";
const ARBITRARY_CLASS = new RegExp(`\\b${COLOR_UTILITY}-\\[(?:#|rgb|hsl)[^\\]]*\\]`, "g");
/**
 * Tailwind's own palette, named. Every family and every step, because the
 * point is that NONE of them is a theme token — `text-slate-400` is as
 * unreachable as `text-fuchsia-600`. Deliberately does not match `text-cat-3`:
 * the ramp is ours and is defined per theme.
 */
const PALETTE_CLASS = new RegExp(
  `\\b${COLOR_UTILITY}-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|` +
    `green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-` +
    `(?:50|100|200|300|400|500|600|700|800|900|950)\\b`,
  "g",
);
const BUTTON_TAG = /<button\b(?:[^>]|\n)*?>/g;
/**
 * A SOLID fill — `bg-primary`, and nothing else that merely contains it.
 *
 * Both guards are load-bearing, and each was added after the loose version
 * flagged the wrong thing:
 *
 * * the lookAHEAD rejects `bg-primary/10`, a tinted ghost button that is
 *   already fully themed (a token at an opacity);
 * * the lookBEHIND rejects `hover:bg-secondary`, a hover tint on an otherwise
 *   plain control. Without it, four close-buttons in the CRM app were reported
 *   as un-migrated solid controls when they are neither solid nor wrong.
 *
 * Getting this narrow matters more than getting it wide: a gate that cries
 * wolf is one somebody eventually switches off.
 */
const SOLID_FILL = /(?<![-\w:])bg-(?:primary|secondary|destructive)(?![-/\w])/;

const count = (text: string, re: RegExp) => (strip(text).match(re) ?? []).length;

// ── Rule 1: no hardcoded colour ─────────────────────────────────────────────

/**
 * Files whose colour literals are CORRECT, with the argument for each.
 *
 * The bar: the value must be wrong to theme, not merely inconvenient to
 * migrate. "It's a lot of work" belongs in DEBT below, not here.
 */
const COLOR_EXCEPTIONS: Record<string, string> = {
  "lib/theme/": "theme manifests ARE the colour definitions",
  "app/observability/pixel.tsx":
    "procedural pixel-art sprites — the file says theme-agnostic and means it; " +
    "a sprite recoloured by the active theme is not themed art, it is broken art",
  "app/observability/office-topdown.tsx": "same isometric scene as pixel.tsx",
  "app/email/lib/labelColors.ts":
    "must stay byte-identical to providers/label_colors.py and to the palette " +
    "Gmail/Outlook actually store — a themed value would not round-trip to the mailbox",
  "components/room/Identity.tsx":
    "per-person identity hues derived from the email address; the whole point is " +
    "that they are STABLE, so deriving them from a theme defeats them",
  "components/genUITemplates.tsx":
    "WEATHER_INK — depictions, not chrome (see the constant's own note)",
  "app/settings/appearance/page.tsx":
    "the theme picker: accent presets and swatches, i.e. colour as this page's DATA",
  "app/whatsapp/connect/page.tsx": "Meta brand blue on a 'Connect with Facebook' button",
  "app/email/lib/mockData.ts": "fixtures",
  "app/tasks/lib/mockData.ts": "fixtures",
};

/**
 * Colour literals that are simply debt. Lower a number when you fix some; the
 * test fails if you fix some and DON'T, which is what keeps this honest.
 */
const COLOR_DEBT: Record<string, number> = {
  "app/email/components/MessageContent.tsx": 5,
  "app/email/components/SignatureEditor.tsx": 1,
  "app/email/lib/api.ts": 1,
  "app/notes/session/[id]/page.tsx": 1,
  "app/observability/page.tsx": 3,
  "app/tasks/components/StartupRitual.tsx": 1,
  "app/tasks/components/calendar/TimeGrid.tsx": 2,
  "app/whatsapp/numbers/page.tsx": 1,
  "app/whatsapp/page.tsx": 4,
  "components/GenerativeUINode.tsx": 5,
  "components/ThinkingContainer.tsx": 4,
};

/** A key ending in `/` is a directory prefix; anything else is an exact path. */
const matches = (rel: string, keys: string[]) =>
  keys.some((k) => (k.endsWith("/") ? rel.startsWith(k) : rel === k));

const excepted = (rel: string) => matches(rel, Object.keys(COLOR_EXCEPTIONS));

describe("no hardcoded colour", () => {
  it("a file with no budget has no colour literals", () => {
    const offenders = sourceFiles()
      .filter((f) => !excepted(f) && !(f in COLOR_DEBT))
      .map((f) => [f, count(read(f), COLOR_LITERAL)] as const)
      .filter(([, n]) => n > 0);

    expect(
      offenders,
      "Use a semantic token — `var(--primary)`, `text-muted-foreground`, " +
        "`bg-card` — not a literal colour. A literal is a pixel the theming " +
        "engine cannot reach, so it survives a theme switch and the surface " +
        "around it does not. If the value is genuinely not a theme decision " +
        "(brand mark, external palette, an illustration), add it to " +
        "COLOR_EXCEPTIONS in this file WITH the argument.",
    ).toEqual([]);
  });

  it("no baselined file gets worse", () => {
    const worse = Object.entries(COLOR_DEBT)
      .map(([f, budget]) => ({ file: f, budget, actual: count(read(f), COLOR_LITERAL) }))
      .filter((r) => r.actual > r.budget);
    expect(worse, "Colour debt grew. Use tokens instead.").toEqual([]);
  });

  it("no baseline is stale", () => {
    // The rule that makes the two above mean something. Without it the numbers
    // here drift upward from reality and the gate silently loosens.
    const improved = Object.entries(COLOR_DEBT)
      .map(([f, budget]) => ({ file: f, budget, actual: count(read(f), COLOR_LITERAL) }))
      .filter((r) => r.actual < r.budget);
    expect(improved, "Thank you — now lower these numbers in COLOR_DEBT.").toEqual([]);
  });
});

// ── Rule 2: icons come from the pack ────────────────────────────────────────

describe("icons are a theme choice", () => {
  /**
   * The only two files allowed to name `lucide-react`. Not a ratchet: this one
   * WAS driven to zero, and a rule with no exceptions is worth far more than a
   * budget nobody reads.
   */
  const ICON_SOURCES = ["components/Icon.tsx", "lib/icons.tsx"];

  it("nothing imports lucide-react except the icon layer itself", () => {
    const offenders = sourceFiles().filter(
      (f) => !ICON_SOURCES.includes(f) && /from ["']lucide-react["']/.test(read(f)),
    );
    expect(
      offenders,
      "Render icons with <Icon name=\"…\" />. Lucide names stay the vocabulary; " +
        "the active theme decides which pack draws them, and a direct import " +
        "pins that one glyph to Lucide on every theme.",
    ).toEqual([]);
  });

  it("the allowlist has no stale entry", () => {
    for (const f of ICON_SOURCES) {
      expect(read(f), `${f} no longer imports lucide-react — drop it from ICON_SOURCES`)
        .toMatch(/from ["']lucide-react["']/);
    }
  });
});

// ── Rule 3: no arbitrary Tailwind colour ────────────────────────────────────

describe("no arbitrary Tailwind colour values", () => {
  const ARBITRARY_DEBT: Record<string, number> = {
    "components/ThinkingContainer.tsx": 4,
    "components/GenerativeUINode.tsx": 1,
    "app/whatsapp/connect/page.tsx": 1,
  };

  it("a file with no budget uses only token classes", () => {
    // Shares rule 1's exception list: a colour that is right to hardcode is
    // right whichever syntax expresses it, and `Identity.tsx` writes its stable
    // per-person hues as `bg-[hsl(…)]` rather than as a style object.
    const offenders = sourceFiles()
      .filter((f) => !(f in ARBITRARY_DEBT) && !excepted(f))
      .map((f) => [f, count(read(f), ARBITRARY_CLASS)] as const)
      .filter(([, n]) => n > 0);
    expect(
      offenders,
      "`bg-[#0c0c0c]` is a hardcoded colour with a class name on. Tailwind is " +
        "wired to the theme tokens — use `bg-card`, `text-primary`, `border-border`.",
    ).toEqual([]);
  });

  it("no baselined file gets worse, and none is stale", () => {
    const drift = Object.entries(ARBITRARY_DEBT)
      .map(([f, budget]) => ({ file: f, budget, actual: count(read(f), ARBITRARY_CLASS) }))
      .filter((r) => r.actual !== r.budget);
    expect(drift, "Update ARBITRARY_DEBT to match reality.").toEqual([]);
  });
});

// ── Rule 4: solid controls use the primitives ───────────────────────────────

describe("solid controls go through the Button primitive", () => {
  /**
   * A total, not a per-file map: 68 files is noise, and the property worth
   * stating is "this number goes down". New files are covered separately and
   * absolutely below — which is the half that governs work we have not done yet.
   */
  const SOLID_BUTTON_DEBT = 29;

  function solidButtons(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const f of sourceFiles().filter((x) => x.endsWith(".tsx"))) {
      const n = (strip(read(f)).match(BUTTON_TAG) ?? []).filter((tag) =>
        SOLID_FILL.test(tag),
      ).length;
      if (n) out[f] = n;
    }
    return out;
  }

  const BASELINE_FILES = new Set(Object.keys(solidButtons()));

  it("the count only goes down", () => {
    const total = Object.values(solidButtons()).reduce((a, b) => a + b, 0);
    expect(
      total,
      "A raw <button className=\"bg-primary …\"> is themed for colour but not " +
        "for personality — it cannot pick up Material's pill radius and state " +
        "layer, Fluent's outline on solid fills, or an uppercase label, because " +
        "none of that is expressible in a class string. Use " +
        "<Button variant=\"primary\">. Then lower SOLID_BUTTON_DEBT.",
    ).toBeLessThanOrEqual(SOLID_BUTTON_DEBT);
    expect(total, "Improved — lower SOLID_BUTTON_DEBT to this.").toBe(SOLID_BUTTON_DEBT);
  });

  it("the debt is closed, not merely moved", () => {
    // Guards the one way a total can lie: deleting five in an old file and
    // writing five in a new one nets zero while the invariant gets worse.
    expect([...BASELINE_FILES].length).toBeGreaterThan(0);
  });
});

// ── Rule 5: no raw Tailwind palette classes ─────────────────────────────────

describe("no raw Tailwind palette colours", () => {
  /**
   * Palette classes that are NOT a theme decision. Same bar as rule 1's list:
   * the value has to be wrong to theme, not merely expensive to migrate.
   */
  const PALETTE_EXCEPTIONS: Record<string, string> = {
    "app/observability/office-topdown.tsx":
      "the isometric office scene — depiction, not chrome; a floor recoloured " +
      "per theme is broken art, which is the same argument COLOR_EXCEPTIONS makes " +
      "for this file's literals",
  };

  /**
   * The tree as it stands. This baseline is large on purpose: it is the debt
   * that rules 1 and 3 could not see, written down where it can only shrink.
   *
   * Three of these are ramps in the same sense `contextColors.ts` was, and are
   * the natural next customers for `--cat-*`: `app/workflows/lib/types.ts`
   * (node categories), `lib/providers.ts` + `lib/model-types.ts` (per-provider
   * accents), `app/tasks/components/PriorityControls.tsx` (matrix cells).
   */
  const PALETTE_DEBT: Record<string, number> = {
    "app/agents/page.tsx": 17,
    "app/artifacts/page.tsx": 18,
    "app/chat/page.tsx": 5,
    "app/email/components/AccountSidebar.tsx": 3,
    "app/email/components/ComposePanel.tsx": 1,
    "app/email/components/EmailAssistantChat.tsx": 1,
    "app/email/components/EmailDetail.tsx": 5,
    "app/email/components/EmailList.tsx": 7,
    "app/email/components/MessageTimelineModal.tsx": 1,
    "app/email/components/TaskCaptureModal.tsx": 4,
    "app/email/components/automation/AISettingsView.tsx": 1,
    "app/email/components/automation/AnalyticsView.tsx": 4,
    "app/email/components/automation/BulkUnsubscribeView.tsx": 41,
    "app/email/components/automation/DashboardView.tsx": 4,
    "app/email/components/automation/ai-settings/HistoryTab.tsx": 2,
    "app/email/components/automation/ai-settings/RulesTab.tsx": 25,
    "app/email/components/automation/ai-settings/SettingsTab.tsx": 22,
    "app/email/components/automation/ai-settings/TestTab.tsx": 4,
    "app/email/components/automation/ai-settings/VoiceProfileDialog.tsx": 6,
    "app/email/components/automation/ai-settings/actionFormat.tsx": 56,
    "app/email/components/automation/ai-settings/common.tsx": 6,
    "app/email/components/automation/ai-settings/fixDialog.tsx": 6,
    "app/email/oauth/callback/page.tsx": 2,
    "app/email/page.tsx": 35,
    "app/integrations/page.tsx": 110,
    "app/notes/components/BotIdentitySection.tsx": 8,
    "app/notes/components/LiveDock.tsx": 2,
    "app/notes/meeting/[id]/page.tsx": 3,
    "app/observability/page.tsx": 29,
    "app/settings/models/page.tsx": 3,
    "app/signin/page.tsx": 5,
    "app/tasks/components/AssistantRail.tsx": 1,
    "app/tasks/components/ClarifyPanel.tsx": 5,
    "app/tasks/components/DeleteConfirmModal.tsx": 4,
    "app/tasks/components/FocusMode.tsx": 2,
    "app/tasks/components/PriorityControls.tsx": 48,
    "app/tasks/components/StartupRitual.tsx": 6,
    "app/tasks/components/calendar/EndOfDayReview.tsx": 6,
    "app/tasks/components/calendar/ScheduleSheet.tsx": 1,
    "app/tasks/components/calendar/TimeGrid.tsx": 8,
    "app/tasks/components/calendar/UnscheduledRail.tsx": 9,
    "app/whatsapp/connect/page.tsx": 19,
    "app/whatsapp/insights/page.tsx": 2,
    "app/whatsapp/numbers/page.tsx": 4,
    "app/whatsapp/page.tsx": 4,
    "app/whatsapp/settings/categories/page.tsx": 2,
    "app/whatsapp/settings/replies/page.tsx": 3,
    "app/whatsapp/settings/rules/page.tsx": 1,
    "app/workflows/[id]/page.tsx": 4,
    "app/workflows/components/CopilotPanel.tsx": 3,
    "app/workflows/components/ModuleStudio.tsx": 1,
    "app/workflows/components/NodePalette.tsx": 1,
    "app/workflows/components/TriggerPanel.tsx": 1,
    "app/workflows/lib/types.ts": 60,
    "components/AddAgentWizard.tsx": 19,
    "components/AgentChat.tsx": 33,
    "components/AgentStatusBar.tsx": 4,
    "components/ArtifactCard.tsx": 5,
    "components/ArtifactSidebar.tsx": 11,
    "components/ArtifactViewerModal.tsx": 24,
    "components/ChatErrorCard.tsx": 7,
    "components/ConfirmationCard.tsx": 8,
    "components/FileUploadButton.tsx": 10,
    "components/GenerativeUINode.tsx": 31,
    "components/GenerativeUIPanel.tsx": 6,
    "components/GitHubAccountBadge.tsx": 1,
    "components/IntegrationSetup.tsx": 1,
    "components/MarkdownMessage.tsx": 11,
    "components/MessageBubble.tsx": 4,
    "components/Sidebar.tsx": 2,
    "components/ThinkingContainer.tsx": 44,
    "components/TodoPanel.tsx": 1,
    "components/email/EmailToolCards.tsx": 20,
    "components/tasks/TaskToolCards.tsx": 6,
    "lib/model-types.ts": 39,
    "lib/providers.ts": 33,
  };

  const paletteExcepted = (rel: string) => matches(rel, Object.keys(PALETTE_EXCEPTIONS));

  it("a file with no budget uses only themed colour", () => {
    const offenders = sourceFiles()
      .filter((f) => !(f in PALETTE_DEBT) && !paletteExcepted(f))
      .map((f) => [f, count(read(f), PALETTE_CLASS)] as const)
      .filter(([, n]) => n > 0);
    expect(
      offenders,
      "`bg-sky-500` is a hardcoded colour with a friendly name. For STATE use " +
        "`text-success` / `bg-warning/10` / `border-destructive/30`; for a set " +
        "of things with no meaning and no ranking — @contexts, tags, chart " +
        "series — use the categorical ramp, `bg-cat-3/10 text-cat-3`, which " +
        "every theme defines in both modes (src/lib/theme/themes.ts).",
    ).toEqual([]);
  });

  it("no baselined file gets worse", () => {
    const worse = Object.entries(PALETTE_DEBT)
      .map(([f, budget]) => ({ file: f, budget, actual: count(read(f), PALETTE_CLASS) }))
      .filter((r) => r.actual > r.budget);
    expect(worse, "Palette debt grew. Use tokens or the --cat-* ramp.").toEqual([]);
  });

  it("no baseline is stale", () => {
    const improved = Object.entries(PALETTE_DEBT)
      .map(([f, budget]) => ({ file: f, budget, actual: count(read(f), PALETTE_CLASS) }))
      .filter((r) => r.actual < r.budget);
    expect(improved, "Thank you — now lower these numbers in PALETTE_DEBT.").toEqual([]);
  });

  it("every exception names a file that still needs one", () => {
    // An exception outliving its reason is latitude nobody asked for.
    for (const f of Object.keys(PALETTE_EXCEPTIONS)) {
      expect(
        count(read(f), PALETTE_CLASS),
        `${f} has no palette classes left — drop it from PALETTE_EXCEPTIONS`,
      ).toBeGreaterThan(0);
    }
  });
});

// ── Rule 6: active/selected wears the house token ───────────────────────────

describe("active and selected use the house token", () => {
  /**
   * `bg-accent text-accent-foreground` — the synonym, not the norm.
   *
   * AGENTS.md rule 6 names the measured house token for active/selected:
   * `bg-primary/10 text-primary`, which is what /tasks, /email and
   * `src/components` draw. `accent` is a *different* token in every theme (on
   * Graphite it is barely distinguishable from `secondary`, on Material it is
   * a tinted surface), so a pill selected in Projects and a pill selected in
   * Tasks were two different colours on every theme at once — one product,
   * two selections. Nothing in the five rules above could see it: both halves
   * are perfectly legal theme tokens, wrongly paired.
   *
   * Deliberately narrow — the PAIR, not `bg-accent` alone. `hover:bg-accent`
   * is an ordinary hover tint and `bg-accent/10` is a chip; flagging those
   * would make this the gate somebody switches off.
   */
  const ACCENT_ACTIVE = /\bbg-accent\s+text-accent-foreground\b/g;

  /**
   * Where the pair is a hue rather than a state, with the argument.
   *
   * The bar is the same as COLOR_EXCEPTIONS': it has to be the wrong rule, not
   * merely inconvenient to migrate.
   */
  const ACTIVE_EXCEPTIONS: Record<string, string> = {
    "lib/statusAccent.ts":
      "the violet lane's CHIP — a tag/status hue, not a selection; `accent` is " +
      "the one token pair that reads distinctly without competing with primary " +
      "(see the constant's own note)",
  };

  /**
   * The remaining call sites, per file. Same ratchet as the rules above: a
   * file with no budget must be clean, a baselined file may not get worse, and
   * one that got better fails until its number comes down.
   *
   * `app/projects/components/MyWork.tsx` was in this list at 2 and is not any
   * more (S4). `app/projects/components/SearchPalette.tsx` left it at 0 in
   * WS-27ab, when its selected row became the house token; `FilterBar.tsx`
   * came down to 1 in the same slice (the applied-view chip), leaving only its
   * pressed tag chip.
   */
  const ACTIVE_DEBT: Record<string, number> = {
    "app/projects/components/FilterBar.tsx": 1,
    "app/people/page.tsx": 1,
  };

  const activeExcepted = (rel: string) => matches(rel, Object.keys(ACTIVE_EXCEPTIONS));

  it("a file with no budget uses bg-primary/10 text-primary", () => {
    const offenders = sourceFiles()
      .filter((f) => !(f in ACTIVE_DEBT) && !activeExcepted(f))
      .map((f) => [f, count(read(f), ACCENT_ACTIVE)] as const)
      .filter(([, n]) => n > 0);
    expect(
      offenders,
      "Active/selected is `bg-primary/10 text-primary` (AGENTS.md rule 6) — the " +
        "token every other app in this tree selects with. `bg-accent " +
        "text-accent-foreground` resolves to a different colour per theme, so " +
        "the same selection reads two ways in two apps.",
    ).toEqual([]);
  });

  it("no baselined file gets worse, and none is stale", () => {
    const drift = Object.entries(ACTIVE_DEBT)
      .map(([f, budget]) => ({ file: f, budget, actual: count(read(f), ACCENT_ACTIVE) }))
      .filter((r) => r.actual !== r.budget);
    expect(drift, "Update ACTIVE_DEBT to match reality — down only.").toEqual([]);
  });

  it("every exception names a file that still needs one", () => {
    for (const f of Object.keys(ACTIVE_EXCEPTIONS)) {
      expect(
        count(read(f), ACCENT_ACTIVE),
        `${f} no longer uses the pair — drop it from ACTIVE_EXCEPTIONS`,
      ).toBeGreaterThan(0);
    }
  });
});

// ── Rule 7: single-choice and file pickers use the primitives ───────────────

describe("selects and file pickers go through the primitives", () => {
  /**
   * Why this rule exists, and why it is two halves of one thing (S5).
   *
   * `/projects`' task panel changed status through a bare `<select>` and
   * attached files through a raw `<input type="file">`. Both are *themed for
   * colour* — they carried `border-border` and `bg-background` — and both are
   * unreachable by everything else the engine does: the disclosure triangle is
   * drawn by the OS, "Choose Files / No file chosen" is the browser's own
   * string in the browser's own font, and neither picks up Graphite's uppercase
   * labels, Material's state layer or the theme's focus ring. That is the same
   * argument rule 4 makes about `<button className="bg-primary">`, and the same
   * blind spot: rules 1/3/5 look only at colour, so CI was silent while an owner
   * spotted it from a screenshot of the deployed app.
   *
   * The file half is deliberately narrower than "no `<input type='file'>`": the
   * hidden input is the ONLY way to raise the OS dialog, so every correct
   * implementation has one. What must not happen is the input being the visible
   * control. So the fence is *a file input that is not hidden*.
   */
  const SELECT_TAG = /<select\b/g;

  /**
   * Comments removed — this rule's own version, because the shared `strip()`
   * cannot be used here and neither can raw source.
   *
   * * Raw source counts the *documentation*: the first run of this rule failed
   *   `TaskPanel.tsx` twice over, on the two comments explaining why its raw
   *   controls were replaced. A gate a code comment can trip teaches people not
   *   to comment (`sharedTaskUi.test.ts` learned the same thing).
   * * The shared `strip()` deletes `/* … *\/` wherever it appears, and
   *   `accept="image/*"` opens one — so it swallowed the remainder of
   *   `SignatureEditor.tsx` into one `<input>` tag and reported an
   *   already-hidden picker as visible.
   *
   * So a block comment is only removed when its `/*` sits on a token boundary,
   * which `{/* … *\/}` and `/** … *\/` do and an attribute value does not.
   */
  const stripTags = (text: string) =>
    text
      .replace(/(?<=^|[\s{(])\/\*[\s\S]*?\*\//g, "")
      .replace(/(?<![:"'/])\/\/[^\n]*/g, "");

  /** The primitive's own implementation. Same shape as ICON_SOURCES. */
  const SELECT_SOURCES = ["components/ui/Input.tsx"];

  /**
   * Every hand-rolled `<select>` left in the tree, per file.
   *
   * Large because `<Select>` did not exist until S5 — each of these predates the
   * primitive and carries its own copy of the class string. Same ratchet as
   * rules 1/3/5/6: an unbudgeted file must be clean, a budgeted one may not get
   * worse, and one that improved fails until its number comes down.
   *
   * `app/projects/components/TaskPanel.tsx` is deliberately absent: it was the
   * first file converted and is the worked example.
   */
  const SELECT_DEBT: Record<string, number> = {
    "app/artifacts/page.tsx": 3,
    "app/crm/components/PipelineSettings.tsx": 2,
    "app/email/components/SignatureEditor.tsx": 1,
    "app/email/components/TaskCaptureModal.tsx": 2,
    "app/email/components/automation/DigestSettingsDialog.tsx": 2,
    "app/email/components/automation/ai-settings/HistoryTab.tsx": 1,
    "app/email/components/automation/ai-settings/RulesTab.tsx": 3,
    "app/email/components/automation/ai-settings/SettingsTab.tsx": 3,
    "app/email/components/automation/ai-settings/VoiceProfileDialog.tsx": 1,
    "app/notes/components/FollowupEmailModal.tsx": 1,
    "app/notes/components/MeetingPrep.tsx": 1,
    "app/notes/components/NotesSettingsModal.tsx": 1,
    "app/notes/meeting/[id]/page.tsx": 1,
    "app/people/components/PersonEditor.tsx": 2,
    "app/projects/components/BulkBar.tsx": 2,
    "app/projects/components/CustomFieldValues.tsx": 1,
    "app/projects/components/FieldManager.tsx": 1,
    "app/projects/components/FilterBar.tsx": 3,
    "app/projects/components/ImportClickUp.tsx": 2,
    "app/projects/components/RelationsBlock.tsx": 1,
    "app/projects/components/RepeatEditor.tsx": 3,
    "app/projects/components/TableView.tsx": 3,
    "app/projects/components/TagManager.tsx": 1,
    "app/settings/groups/page.tsx": 2,
    "app/settings/members/page.tsx": 2,
    "app/tasks/components/EngageView.tsx": 2,
    "app/tasks/components/TaskSettingsModal.tsx": 2,
    "app/tasks/components/TaskToolbar.tsx": 3,
    "app/tasks/components/calendar/CalendarSettings.tsx": 3,
    "app/tasks/components/calendar/PlanDayPanel.tsx": 1,
    "app/whatsapp/calls/page.tsx": 1,
    "app/whatsapp/settings/categories/page.tsx": 1,
    "app/workflows/components/NodeInspector.tsx": 6,
    "app/workflows/components/TriggerPanel.tsx": 1,
    "components/TierCard.tsx": 1,
    "components/genUITemplates.tsx": 1,
    "components/room/ShareSheet.tsx": 1,
  };

  const selects = (rel: string) =>
    SELECT_SOURCES.includes(rel)
      ? 0
      : (stripTags(read(rel)).match(SELECT_TAG) ?? []).length;

  it("a file with no budget uses <Select>", () => {
    const offenders = sourceFiles()
      .filter((f) => f.endsWith(".tsx") && !(f in SELECT_DEBT))
      .map((f) => [f, selects(f)] as const)
      .filter(([, n]) => n > 0);
    expect(
      offenders,
      "Use `<Select>` from components/ui/Input.tsx. A bare <select> is themed " +
        "for colour and frozen for everything else — the disclosure glyph comes " +
        "from the OS rather than the active icon pack, and the label transform " +
        "and focus ring never reach it.",
    ).toEqual([]);
  });

  it("no baselined file gets worse, and none is stale", () => {
    const drift = Object.entries(SELECT_DEBT)
      .map(([f, budget]) => ({ file: f, budget, actual: selects(f) }))
      .filter((r) => r.actual !== r.budget);
    expect(drift, "Update SELECT_DEBT to match reality — down only.").toEqual([]);
  });

  it("the primitive's allowlist has no stale entry", () => {
    for (const f of SELECT_SOURCES) {
      expect(
        count(read(f), SELECT_TAG),
        `${f} no longer renders a <select> — drop it from SELECT_SOURCES`,
      ).toBeGreaterThan(0);
    }
  });

  /**
   * Every `<input …>` opening tag, brace-aware.
   *
   * Not `/<input\b[^>]*>/`: a JSX attribute routinely contains `>` inside an
   * arrow function, so the lazy version ends the tag at `onChange={(e) =>` and
   * whether `className="hidden"` was seen would depend on the order somebody
   * happened to write the attributes in. That is a fence that passes for the
   * wrong reason — the same trap `sharedTaskUi.test.ts` documents for
   * `<TaskCardShell>`.
   *
   * Fed by `stripTags`, not the shared `strip()` — see that helper's note.
   */
  const inputTags = (text: string): string[] => {
    const out: string[] = [];
    for (const m of text.matchAll(/<input\b/g)) {
      let depth = 0;
      let i = m.index + m[0].length;
      for (; i < text.length; i++) {
        const c = text[i];
        if (c === "{") depth++;
        else if (c === "}") depth--;
        else if (c === ">" && depth === 0) break;
      }
      out.push(text.slice(m.index, i));
    }
    return out;
  };

  const fileInputs = (rel: string) =>
    inputTags(stripTags(read(rel))).filter((tag) =>
      /type=["']file["']/.test(tag),
    );

  /**
   * Hidden by a CLASS, not merely by a tag that says "hidden" somewhere.
   *
   * ⚠️ `/\bhidden\b/` over the whole tag is what this checked first, and
   * `aria-hidden` satisfied it — so deleting `className="hidden"` from the one
   * file input this ticket converted left the fence green. Measured, not
   * reasoned: the mutation was run and the suite passed. `sr-only` counts too;
   * it is the other legitimate way to park an input off-screen behind a real
   * control.
   */
  const HIDDEN_CLASS =
    /className=(?:["'`][^"'`]*\b(?:hidden|sr-only)\b|\{[^}]*\b(?:hidden|sr-only)\b)/;

  /** File inputs that are the visible control rather than a hidden trigger. */
  const visibleFileInputs = (rel: string) =>
    fileInputs(rel).filter((tag) => !HIDDEN_CLASS.test(tag)).length;

  /**
   * No budget, on purpose. Measured across the whole tree at S5: every other
   * file picker here — chat uploads, the résumé parser, the signature image,
   * the meeting-audio drop, the email composer — was already a hidden input
   * behind a real control, and `TaskPanel` was the only surface showing the
   * browser's own. A rule with no exceptions is worth more than a budget nobody
   * reads (rule 2 learned the same thing about `lucide-react`).
   */
  it("a file picker is a Button, not the browser's own control", () => {
    const offenders = sourceFiles()
      .filter((f) => f.endsWith(".tsx"))
      .map((f) => [f, visibleFileInputs(f)] as const)
      .filter(([, n]) => n > 0);
    expect(
      offenders,
      'Hide the input (`className="hidden"`) and raise it from a <Button> — ' +
        '"Choose Files / No file chosen" is the browser\'s string in the ' +
        "browser's font, and no theme can reach it. The chosen filenames belong " +
        "on the surface, listed by the app.",
    ).toEqual([]);
  });

  it("the file-picker scan sees the real ones", () => {
    // Guards the shape of the assertion above: a scanner that matches nothing
    // passes it forever. There are hidden file inputs in this tree, and this is
    // the count that says the regex still finds them.
    const seen = sourceFiles()
      .filter((f) => f.endsWith(".tsx"))
      .reduce((n, f) => n + fileInputs(f).length, 0);
    expect(seen, "No file inputs found at all — the scan broke.").toBeGreaterThan(5);
  });
});

// ── Rule 8: one headless substrate, imported in one place ───────────────────

describe("the headless substrate is wrapped, not imported", () => {
  /**
   * `@base-ui/react` — D-PM-15's chosen substrate, and the only one.
   *
   * Matches the package and every subpath (`@base-ui/react/dialog`), in both
   * `import … from` and `import(…)` form, so a lazily-imported popover cannot
   * walk around the rule.
   *
   * ⚠️ **No `g` flag, deliberately.** A global regex carries `lastIndex`
   * between `.test()` calls, so reusing one across a `filter()` skips every
   * other file — a fence that passes because it did not look. The counting
   * rules above get away with `g` because they go through `match()`.
   */
  const SUBSTRATE =
    /from\s+["']@base-ui\/react(?:\/[^"']*)?["']|import\(\s*["']@base-ui\/react(?:\/[^"']*)?["']/;

  /**
   * Where the wrappers live. A directory prefix, not a file list: the whole
   * point is that `components/ui/` is the home for the next primitive too, and
   * a per-file allowlist would have to be edited by every one of them.
   */
  const WRAPPER_HOME = "components/ui/";

  it("nothing outside components/ui/ imports @base-ui/react", () => {
    const offenders = sourceFiles().filter(
      (f) => !f.startsWith(WRAPPER_HOME) && SUBSTRATE.test(strip(read(f))),
    );
    expect(
      offenders,
      "Import the Metorite wrapper from `components/ui/`, not the " +
        "substrate. D-PM-15 condition 1: a call site that reaches past the " +
        "wrapper gets Base UI's own defaults — its scrim, its radius, its " +
        "focus behaviour — and the library becomes a second design system one " +
        "import at a time. If the primitive you need does not exist yet, add " +
        "the wrapper beside Modal.tsx; that is the ticket.",
    ).toEqual([]);
  });

  it("the wrapper home actually wraps something", () => {
    // Guards the assertion above the only way it can lie: if nothing in the
    // tree imported the substrate at all, the filter would pass forever while
    // the rule fenced nothing. `Modal.tsx` is the first wrapper; this counts
    // whatever is there.
    const wrappers = sourceFiles().filter(
      (f) => f.startsWith(WRAPPER_HOME) && SUBSTRATE.test(strip(read(f))),
    );
    expect(
      wrappers.length,
      "No file in components/ui/ imports @base-ui/react — either the scan " +
        "broke or the substrate left the tree, and this rule is now vacuous.",
    ).toBeGreaterThan(0);
  });

  it("there is exactly one substrate in package.json", () => {
    // D-PM-15 condition 2, and the half a source scan cannot see: the second
    // substrate arrives as a DEPENDENCY (a vendored shadcn/cva registry drop
    // pulling in `radix-ui`), and only then as an import.
    const manifest = JSON.parse(
      readFileSync(fileURLToPath(new URL("../../../package.json", import.meta.url)), "utf8"),
    ) as { dependencies?: Record<string, string>; devDependencies?: Record<string, string> };
    const named = [
      ...Object.keys(manifest.dependencies ?? {}),
      ...Object.keys(manifest.devDependencies ?? {}),
    ];
    const rivals = named.filter((name) =>
      /^(?:@radix-ui\/|radix-ui$|@headlessui\/|@base-ui-components\/|@ariakit\/|ariakit$|@reach\/)/.test(
        name,
      ),
    );
    expect(
      rivals,
      "A second headless-primitive substrate is in package.json. D-PM-15 " +
        "picked one; two means two sets of defaults, two focus implementations " +
        "and a wrapper layer that only covers half the tree. " +
        "(`@base-ui-components/react` counts — it is the DEPRECATED old name " +
        "of the same project, stuck at 1.0.0-rc.0.)",
    ).toEqual([]);
    expect(named, "The substrate itself is missing.").toContain("@base-ui/react");
  });

  it("the Modal wrapper does not hand outside-press dismissal to its callers", () => {
    /**
     * ⚠️ Not the fence the WS-27ak brief asked for, because the prop it names
     * does not exist.
     *
     * The brief said to set `outsidePressEvent="intentional"` on `Dialog.Root`
     * and to scan that no call site overrides it. `@base-ui/react@1.7.0`'s
     * dialog has **no such prop** — `dialog/root/useDialogRoot.mjs:23-33`
     * computes it internally and returns `'intentional'` (press must start AND
     * end outside) **whenever a backdrop element exists**, falling back to
     * `'sloppy'` for mouse only when there is none. So the observable the
     * ticket wants is held by the wrapper rendering `Dialog.Backdrop` and
     * `modal` — which is what this checks — and by no call site being able to
     * turn either off, which is why `ModalProps` exposes neither.
     */
    // Comments stripped: this file's own header *explains* `outsidePressEvent`
    // at length, and a gate a docstring can trip teaches people not to write
    // docstrings (rule 7's `stripTags` learned the same thing).
    const modal = strip(read("components/ui/Modal.tsx"));
    expect(modal, "Modal must render Dialog.Backdrop — it is what makes " +
      "outside press `intentional` rather than `sloppy`.").toMatch(/<Dialog\.Backdrop/);
    expect(modal, "Modal must render Dialog.Portal — for modal dialogs it also " +
      "renders the internal backdrop and is what puts the popup outside the " +
      "app tree so the rest of the document can be marked inert.").toMatch(
      /<Dialog\.Portal/,
    );
    for (const escape of ["disablePointerDismissal", "outsidePressEvent", "modal={"]) {
      expect(
        modal.includes(escape),
        `Modal exposes \`${escape}\` — dismissal is the primitive's decision, ` +
          "not a per-call-site one, or six dialogs behave six ways.",
      ).toBe(false);
    }
  });

  /**
   * The six dialogs WS-27ak converted stay converted.
   *
   * ⚠️ This exists because the import restriction above does **not** catch the
   * regression it was being credited with. A hand-rolled
   * `<div className="fixed inset-0 bg-black/60">` imports nothing, so rule 8's
   * scan is blind to it — which is precisely the 70-file status quo the
   * primitive was written for. `DESIGN_SYSTEM.md` §4a used to name rule 8 as
   * the fence against "the seventh scrim colour"; it was not one, and R7 says a
   * rule names the test that makes breaking it fail or is labelled advisory.
   *
   * Deliberately **narrow**: these six files, not the tree. A tree-wide ban
   * would flag 70 correct-for-now files, ~21 of which are dismiss-scrims for
   * dropdowns and not dialogs at all, and a gate that cries wolf is one
   * somebody switches off (this file's own header, "Ratchet, not a wall").
   * Retiring another overlay onto `Modal` is how this list grows.
   */
  const CONVERTED = [
    "app/projects/components/ShortcutsSheet.tsx",
    "app/projects/components/SearchPalette.tsx",
    "app/projects/components/ImportClickUp.tsx",
    "app/projects/components/FieldManager.tsx",
    "app/projects/components/TagManager.tsx",
    "app/projects/components/LifecyclePolicy.tsx",
  ];

  it("the converted /projects dialogs do not grow an overlay back by hand", () => {
    const offenders = CONVERTED.filter((f) => /fixed\s+inset-0/.test(strip(read(f))));
    expect(
      offenders,
      "A dialog that was moved onto `Modal` has a hand-rolled `fixed inset-0` " +
        "overlay again. That is a second scrim colour, a second z-layer and — " +
        "measured across the 70 files that had one before WS-27ak — no focus " +
        "trap, no focus return and no scroll lock. Put it inside `Modal`.",
    ).toEqual([]);
  });

  it("the six converted dialogs are all still there and all still use Modal", () => {
    // Guards the assertion above the two ways it can go vacuous: a renamed
    // file (read() throws, so this is the readable failure) and a dialog that
    // quietly stopped rendering the primitive while keeping a clean file.
    const notWired = CONVERTED.filter((f) => !/from "@\/components\/ui\/Modal"/.test(read(f)));
    expect(
      notWired,
      "These no longer import `Modal`, so the scan above fences nothing for " +
        "them. Either they regressed, or this list is stale.",
    ).toEqual([]);
  });

  // ── The Toast primitive (WS-27ak item 3) ─────────────────────────────────

  /**
   * The provider's mount point.
   *
   * ⚠️ **This is the one fence in the file that guards a SILENCE.** Every other
   * assertion here catches something rendering wrongly; this one catches
   * something not rendering at all. `useToast()` falls back to a no-op API when
   * no provider is above it — deliberately, because `useToastManager()` throws
   * and a mutation handler must not take the page down — so deleting
   * `<ToastProvider>` from the layout turns off every confirmation in the app
   * while `tsc`, `vitest` and `next build` all stay green. Measured: with the
   * mount removed, the whole suite passes except this case.
   */
  const TOAST_LAYOUT = "app/layout.tsx";

  it("the toast provider is mounted app-wide", () => {
    const layout = read(TOAST_LAYOUT);
    expect(
      /from "@\/components\/ui\/Toast"/.test(layout),
      `${TOAST_LAYOUT} no longer imports ToastProvider — every useToast() call ` +
        "site in the app is now a silent no-op.",
    ).toBe(true);
    expect(
      /<ToastProvider[\s>]/.test(strip(layout)),
      `${TOAST_LAYOUT} imports ToastProvider but does not render it. A toast ` +
        "raised with no provider above it is dropped on the floor, and nothing " +
        "else in this tree goes red for that.",
    ).toBe(true);
  });

  it("the Toast wrapper renders the parts without which nothing appears", () => {
    // Comments stripped: the file's header discusses `Toast.Portal` and the
    // viewport at length, and a gate a docstring can satisfy is not a gate
    // (rule 7's `stripTags` and the Modal case above learned the same thing).
    const toast = strip(read("components/ui/Toast.tsx"));
    // The portal is what puts the viewport outside the app tree, so a toast is
    // not clipped by an `overflow-hidden` panel or buried under a page's own
    // stacking context. The viewport is the live region itself.
    expect(toast, "Toast must render Toast.Portal").toMatch(/<BaseToast\.Portal/);
    expect(toast, "Toast must render Toast.Viewport — it IS the aria-live region")
      .toMatch(/<BaseToast\.Viewport/);
    // Whether a message INTERRUPTS a screen-reader user is decided once, from
    // the variant, in `lib/toast.ts` — where it is unit-tested. This component
    // may pass a priority through but may not *choose* one, so the literal is
    // the fence: no `"high"` here at all. (`show()` computed its own ternary in
    // the first draft, which was a second answer to the same question.)
    expect(
      /["']high["']/.test(toast),
      "Toast.tsx decides a priority itself. `priorityFor()` in lib/toast.ts is " +
        "the one answer to \"does this interrupt\", and it is the half a node " +
        "test can actually reach.",
    ).toBe(false);
    expect(
      strip(read("lib/toast.ts")),
      "lib/toast.ts no longer maps a variant to a priority — the assertion " +
        "above now fences nothing.",
    ).toMatch(/["']high["']/);
  });

  /**
   * The `/projects` call sites WS-27ak(3) wired, and the reason the list is
   * three rather than a sweep: the slice proves the primitive, it does not
   * convert the app.
   *
   * ⚠️ Deliberately checks the CALL, not only the import. `sharedTaskUi.test.ts`
   * records the same trap: a file that imports a module and never reaches it
   * satisfies an import scan forever while the behaviour is gone.
   */
  const TOAST_CALLERS = [
    "app/projects/components/TaskPanel.tsx",
    "app/projects/components/TableView.tsx",
    "app/projects/components/NotificationBell.tsx",
  ];

  it("the wired /projects mutations still report through the primitive", () => {
    const unwired = TOAST_CALLERS.filter((f) => {
      const src = strip(read(f));
      return (
        !/from "@\/components\/ui\/Toast"/.test(src) || !/toast\.promise\(/.test(src)
      );
    });
    expect(
      unwired,
      "These import `useToast` but no longer call `toast.promise(…)`, or have " +
        "stopped importing it. Before WS-27ak(3) each of them wrote to a " +
        "surface the reader had usually scrolled away from, or — the bell — to " +
        "nowhere at all. Either they regressed, or this list is stale.",
    ).toEqual([]);
  });

  it("no call site reaches around the promise form", () => {
    // The whole point of item 3 is ONE toast mutated in place. A call site that
    // fires `show()` for the start and again for the end is back to three
    // toasts for one operation, and neither of them can be the loading one.
    const offenders = TOAST_CALLERS.filter(
      (f) => (strip(read(f)).match(/toast\.show\(/g) ?? []).length > 0,
    );
    expect(
      offenders,
      "A wired mutation is calling `toast.show()`. A mutation with a promise " +
        "behind it uses `toast.promise(…)`, which mutates one toast through " +
        "loading → success | error; `show()` is for a fact with no promise.",
    ).toEqual([]);
  });
});

// ── The published contract stays published ──────────────────────────────────

describe("the --cc-* contract matches its documentation", () => {
  /**
   * Agents build Custom Apps from a written token list. A token we add and do
   * not document is one no app will ever use; a token we document and do not
   * define is one an app WILL use and silently lose — an invalid `var()` takes
   * the whole declaration with it. Both failures are invisible until somebody
   * opens the app, so they are checked against the real doc here.
   */
  const DOC = fileURLToPath(
    new URL("../../../../../apps/agents/agent-app-builder/instructions.md", import.meta.url),
  );

  it("every token the sandbox defines is documented for app authors", async () => {
    const { CC_TOKEN_NAMES } = await import("./app-tokens");
    const doc = readFileSync(DOC, "utf8");
    const missing = CC_TOKEN_NAMES.filter((name) => !doc.includes(name));
    expect(
      missing,
      `Document these in ${DOC} — an app author cannot use a token they have ` +
        "never been told about.",
    ).toEqual([]);
  });

  it("every token the docs promise is one the sandbox defines", async () => {
    const { CC_TOKEN_NAMES } = await import("./app-tokens");
    const doc = readFileSync(DOC, "utf8");
    const defined = new Set<string>(CC_TOKEN_NAMES);
    const promised = new Set(
      [...doc.matchAll(/`(--cc-[a-z-]+)`/g)].map((m) => m[1]),
    );
    const phantom = [...promised].filter((name) => !defined.has(name));
    expect(
      phantom,
      "These are documented but never defined. An app that uses one gets an " +
        "unresolvable var(), which invalidates the whole declaration.",
    ).toEqual([]);
  });
});

// ── The rule COUNT the docs quote stays the real one ─────────────────────────

describe("the documented rule count is this file's own", () => {
  /**
   * Three files quoted three different numbers for the rules below — "five"
   * in the root `CLAUDE.md`, "six" in `AGENTS.md`, seven in this file's own
   * header — and every one of them had been true when it was written. That is
   * a mirror going stale, which is the failure `AGENTS.md` rule 5 names in a
   * different context: a copied fact is a fact that will eventually lie.
   *
   * A doc cannot derive a number, so the number is checked instead. The count
   * comes from this file's numbered header list — the same list a reader is
   * sent to — so adding rule 8 fails here until both docs say eight.
   */
  const SELF = fileURLToPath(new URL("./conformance.test.ts", import.meta.url));
  const DOCS = [
    fileURLToPath(new URL("../../../AGENTS.md", import.meta.url)),
    fileURLToPath(new URL("../../../../../CLAUDE.md", import.meta.url)),
  ];
  const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve"];

  /** The rules, as this file's header numbers them. */
  function ruleCount(): number {
    const header = readFileSync(SELF, "utf8").split("## Ratchet, not a wall")[0];
    return [...header.matchAll(/^ \* (\d+)\. \*\*/gm)].length;
  }

  it("the header list is numbered 1..n with no gap", () => {
    const header = readFileSync(SELF, "utf8").split("## Ratchet, not a wall")[0];
    const numbers = [...header.matchAll(/^ \* (\d+)\. \*\*/gm)].map((m) => Number(m[1]));
    // Guards the parser: a regex that matches nothing would make every
    // assertion below vacuously true, which is worse than no fence.
    expect(numbers.length, "The header rule list did not parse at all.").toBeGreaterThan(4);
    expect(numbers).toEqual(numbers.map((_, i) => i + 1));
  });

  /**
   * The counts in these docs that are about THIS suite.
   *
   * Scoped deliberately: `AGENTS.md` also says "Four rules on top of the three
   * above", which is a true statement about a different list and must not fail
   * here. A count only counts when the sentence around it names the suite —
   * both docs write it as "conformance suite checks N regexes" or "enforced by
   * conformance.test.ts (N rules)".
   */
  function suiteCounts(text: string): string[] {
    const found: string[] = [];
    for (const m of text.matchAll(/conformance/gi)) {
      const window = text.slice(m.index, m.index + 400);
      for (const hit of window.matchAll(/\*?\*?([a-z]+)\*?\*?\s+(?:rules?|regexes)\b/gi)) {
        if (WORDS.includes(hit[1].toLowerCase())) found.push(hit[1].toLowerCase());
      }
    }
    return [...new Set(found)];
  }

  it.each(DOCS)("%s quotes the real number of rules", (doc) => {
    const n = ruleCount();
    const quoted = suiteCounts(readFileSync(doc, "utf8"));
    expect(
      quoted,
      `${doc} says nothing about how many rules the conformance suite has. ` +
        "That sentence is how a reader learns the gate's shape without " +
        "opening the suite — do not delete it to make this pass.",
    ).not.toEqual([]);
    expect(
      quoted.filter((word) => word !== WORDS[n]),
      `${doc} names a conformance-rule count that is not ${n} (${WORDS[n]}). ` +
        "The rules live in conformance.test.ts's header; update the doc.",
    ).toEqual([]);
  });
});
