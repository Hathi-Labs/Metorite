/**
 * Every chat card draws one look (owner report, 2026-10-13).
 *
 * The owner said the question card looked "out of place compared to the
 * other AGUI cards", and asked for every card that drifted from the settled
 * look to be fixed. The settled look is `components/RollupCard.tsx` and its
 * header, `CollapsibleCardHeader` in `components/ui/Collapsible.tsx`. This
 * file holds three rules over the files that draw a card in the chat
 * transcript. A rule here reads source, so each one names what it cannot
 * see.
 *
 * 1. **No emoji, and no text glyph as an icon.** An icon comes from the app
 *    set (`<Icon>`). The question card drew 💬, and the trail drew ▾ and ▲.
 *    Comments do not count. `GLYPH_DEBT` holds the weather template's
 *    depictions, and it may only go down.
 * 2. **A card draws its title row through a shared header**:
 *    `RollupHeader`, `CollapsibleCardHeader`, `TemplateHead` (which is
 *    `CollapsibleCardHeader` inside a chat card) or `ToolCardShell`. A file
 *    with no title row is exempt, with its reason.
 *    ⚠️ ADVISORY beyond the file: a file that uses a seam once can still hold
 *    a second card with its own row. So the Email file, which holds sixteen
 *    cards, is read card by card (rule 2b), and the cards of the other
 *    files are named in the PR inventories.
 * 3. **No raw Tailwind palette class.** The tree-wide rule 5 of
 *    `theme/conformance.test.ts` lets a baselined file keep its debt. A chat
 *    card file has no budget at all.
 * 4. **No hand-rolled control** (follow-up of #945). A chat card's control
 *    is the shared `Button`, `Input`, `Textarea` or `Select`, never a bare
 *    `<button>`, `<input>`, `<textarea>` or `<select>`. `CONTROL_DEBT` holds
 *    the files that still draw one, and each number may only go down.
 *
 * Plus the wiring: every template that draws `TemplateHead` is in
 * `OWN_HEAD_TEMPLATES`, so `MessageBubble` hands it the header and its title
 * draws once.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { OWN_HEAD_TEMPLATES, TEMPLATE_REGISTRY } from "@/components/genUITemplates";

const SRC = fileURLToPath(new URL("..", import.meta.url));
/** Source as LF. A Windows checkout holds CRLF, and a fence must not care. */
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

/** Strip block and line comments, so a rule reads code and markup only. */
function code(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`])\/\/.*$/gm, "$1")
    .replace(/\{\s*\}/g, "");
}

/**
 * The files that draw a card in the chat transcript, and the header seam
 * each one draws through, or why it has no title row.
 */
const CHAT_CARDS: Record<string, { seam: true } | { exempt: string }> = {
  "components/ElicitationCard.tsx": { seam: true },
  "components/ConfirmationCard.tsx": { seam: true },
  "components/ChatErrorCard.tsx": { seam: true },
  "components/ToolCardShell.tsx": { seam: true },
  "components/GenerativeUINode.tsx": { seam: true },
  "components/genUITemplates.tsx": { seam: true },
  "components/GenerativeUIPanel.tsx": { seam: true },
  "components/projects/ProjectToolCards.tsx": { seam: true },
  "components/tasks/TaskToolCards.tsx": { seam: true },
  "components/email/EmailToolCards.tsx": { seam: true },
  "components/ConfirmationQueue.tsx": { exempt: "it draws ConfirmationCard, one at a time, and no row of its own" },
  "components/ArtifactCard.tsx": { exempt: "a file card is one row: its name, its size and its buttons. Nothing folds" },
  "components/crm/CrmEvidence.tsx": { exempt: "a read's body, drawn inside its step of the trail, which is its title" },
  "components/Readout.tsx": { exempt: "the body of a read card, under the card's own header" },
  "components/RecommendedBadge.tsx": { exempt: "a badge inside an option row" },
  "components/TodoPanel.tsx": { exempt: "the plan above the composer, not a card of the transcript" },
};

const SEAM = /<(?:RollupHeader|CollapsibleCardHeader|TemplateHead|ToolCardShell)\b/;

const EMOJI = /\p{Extended_Pictographic}/gu;
/** Text glyphs drawn as a toggle, a tick or a mark. */
const GLYPH = /[▸▾▴▲▼►◄✓✔✕✗★☆]/gu;

/**
 * Glyphs a file may still draw, and why. A number that goes down must be
 * lowered here in the same change.
 */
const GLYPH_DEBT: Record<string, { count: number; why: string }> = {
  "components/genUITemplates.tsx": {
    count: 1,
    why: "the weather card's ❄ is a depiction inside its sky picture, like WEATHER_INK, not chrome. The 💧 and 🌬 are icons now",
  },
  "components/tasks/TaskToolCards.tsx": {
    count: 1,
    why: "it READS the ✓done mark that the tool prints, in a parse. It draws no glyph",
  },
};

const PALETTE =
  /\b(?:bg|text|border|ring|from|to|via|fill|stroke|outline|divide|decoration|shadow|accent|caret)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b/g;

describe("rule 1: a chat card draws no emoji and no text glyph as an icon", () => {
  for (const file of Object.keys(CHAT_CARDS)) {
    it(file, () => {
      const body = code(read(file));
      const found = [...(body.match(EMOJI) ?? []), ...(body.match(GLYPH) ?? [])];
      const allowed = GLYPH_DEBT[file]?.count ?? 0;
      expect(found.length, `Draw an <Icon> from the app set, not ${found.join(" ")}`).toBeLessThanOrEqual(allowed);
      expect(found.length, "Improved: lower GLYPH_DEBT to this").toBe(allowed);
    });
  }
});

describe("rule 2: a chat card draws its title row through a shared header", () => {
  for (const [file, how] of Object.entries(CHAT_CARDS)) {
    it(file, () => {
      const body = code(read(file));
      if ("exempt" in how) {
        expect(how.exempt.length).toBeGreaterThan(10);
        return;
      }
      expect(body, "Draw the title row with RollupHeader or CollapsibleCardHeader").toMatch(SEAM);
    });
  }

  it("the question card and the confirmation card wait, and draw the shared header", () => {
    for (const file of ["components/ElicitationCard.tsx", "components/ConfirmationCard.tsx"]) {
      const body = code(read(file));
      expect(body).toMatch(/<RollupCard[^>]*header="own"/);
      expect(body).toContain("<RollupHeader");
      expect(body).toMatch(/<RollupCard[^>]*\bpending\b/);
    }
  });

  it("the tool card shell has one toggle: the shared header, not a button of its own", () => {
    const body = code(read("components/ToolCardShell.tsx"));
    const shell = body.slice(body.indexOf("export function ToolCardShell"), body.indexOf("export function DismissableCard"));
    expect(shell).toContain("<CollapsibleCardHeader");
    expect(shell).not.toMatch(/name="ChevronDown"/);
  });
});

describe("rule 3: a chat card holds no raw palette class", () => {
  for (const file of Object.keys(CHAT_CARDS)) {
    it(file, () => {
      const found = code(read(file)).match(PALETTE) ?? [];
      expect(found, "Use a token, a status tone or the categorical ramp").toEqual([]);
    });
  }
});

/**
 * The cards of the Email file, each by its function, and how it draws its
 * title row. `CardHead` is the file's one title row: `RollupHeader` with the
 * card's controls beside it. A card that hands its whole draw to another
 * card names that card.
 */
const EMAIL_CARDS: Record<string, "CardHead" | "ToolCardShell" | { delegates: string }> = {
  ReadGroupCard: "ToolCardShell",
  ThreadCard: "ToolCardShell",
  DraftResultCard: "CardHead",
  RuleResultCard: "CardHead",
  SettingsUpdatedCard: "CardHead",
  EmailListCard: "CardHead",
  EmailGroupsCard: "CardHead",
  EmailPreviewCard: "CardHead",
  LabelUpdateCard: "CardHead",
  ManageInboxCard: "CardHead",
  PatternListCard: "ToolCardShell",
  RulesOverviewCard: "ToolCardShell",
  LabelsCard: "ToolCardShell",
  InfoResultCard: "ToolCardShell",
  ActionResultCard: "CardHead",
  NoActionCard: "CardHead",
};

/** The source of one top-level function of a file, to the next one. */
function fnBody(src: string, name: string): string {
  const start = src.search(new RegExp(`\\nfunction ${name}\\(|\\nexport function ${name}\\(`));
  if (start < 0) return "";
  const next = src.slice(start + 10).search(/\n(?:export )?function /);
  return src.slice(start, next < 0 ? undefined : start + 10 + next);
}

describe("rule 2b: each Email card draws the shared header (follow-up of #945)", () => {
  const src = code(read("components/email/EmailToolCards.tsx"));

  it("names every card of the file", () => {
    const cards = [...src.matchAll(/\nfunction ([A-Z]\w*Card)\(/g)].map((m) => m[1]).sort();
    expect(cards).toEqual(Object.keys(EMAIL_CARDS).sort());
  });

  for (const [card, how] of Object.entries(EMAIL_CARDS)) {
    it(card, () => {
      const body = fnBody(src, card);
      expect(body, `${card} moved: point this scan at it`).not.toBe("");
      if (how === "ToolCardShell") {
        expect(body).toContain("<ToolCardShell");
        return;
      }
      if (typeof how === "object") {
        expect(body).toContain(`<${how.delegates}`);
        return;
      }
      // A flow card: one RollupCard with the card's own header, and the
      // title row through CardHead, never a hand-made row.
      expect(body).toMatch(/<RollupCard[^>]*header="own"/);
      expect(body).toContain("<CardHead");
      expect(body).not.toMatch(/text-\[11px\] font-medium text-foreground/);
    });
  }

  it("CardHead is RollupHeader, with the controls beside it", () => {
    const head = fnBody(src, "CardHead");
    expect(head).toContain("<RollupHeader");
    // The controls are a sibling of the header's box, never its child.
    expect(head.indexOf("{children ?")).toBeGreaterThan(head.indexOf("<RollupHeader"));
  });

  it("the draft card waits until it is saved or sent, so it never folds", () => {
    expect(fnBody(src, "DraftResultCard")).toMatch(/<RollupCard[^>]*\bpending=\{!done\}/);
  });
});

/**
 * Raw controls a chat card file may still draw, and why. A number that goes
 * down must be lowered here in the same change.
 */
const CONTROL_DEBT: Record<string, { count: number; why: string }> = {
  "components/genUITemplates.tsx": {
    count: 8,
    why: "the form card's fields and toggle, and the plan card's inline editors. Their own pass",
  },
  "components/projects/ProjectToolCards.tsx": { count: 1, why: "the task row's open button" },
  "components/tasks/TaskToolCards.tsx": { count: 3, why: "the row, Apply plan and Open in My Tasks. The Tasks pass" },
  "components/ArtifactCard.tsx": { count: 1, why: "the file card's open row" },
  "components/TodoPanel.tsx": { count: 1, why: "the plan's fold" },
};

const RAW_CONTROL = /<(?:button|input|textarea|select)\b/g;

describe("rule 4: a chat card draws no hand-rolled control (follow-up of #945)", () => {
  for (const file of Object.keys(CHAT_CARDS)) {
    it(file, () => {
      const found = code(read(file)).match(RAW_CONTROL) ?? [];
      const allowed = CONTROL_DEBT[file]?.count ?? 0;
      expect(found.length, "Use Button, Input, Textarea or Select from components/ui").toBeLessThanOrEqual(allowed);
      expect(found.length, "Improved: lower CONTROL_DEBT to this").toBe(allowed);
    });
  }
});

describe("a template with its own head draws it as the card's one header", () => {
  const templates = code(read("components/genUITemplates.tsx"));
  const NAMES: Record<string, string> = {
    timeline: "Timeline",
    taskBoard: "TaskBoard",
    dataGrid: "DataGrid",
    reportCard: "ReportCard",
    planCard: "PlanCard",
    optionPicker: "OptionPicker",
    // The follow-up of #945: the rest of the catalog.
    weatherCard: "WeatherCard",
    statDashboard: "StatDashboard",
    sparkTrend: "SparkTrend",
    comparison: "Comparison",
    progressTracker: "ProgressTracker",
    recipeCard: "RecipeCard",
    flightStatus: "FlightStatus",
    trainStatus: "TrainStatus",
    formCard: "FormCard",
    barChart: "ChartCard",
    chart: "ChartCard",
  };

  it("names every chat template", () => {
    expect([...OWN_HEAD_TEMPLATES].sort()).toEqual(Object.keys(NAMES).sort());
  });

  it("every template in the registry draws its own head, so no title draws twice", () => {
    // A new template that draws a title of its own, and is not in
    // OWN_HEAD_TEMPLATES, draws it under the fold's header: twice.
    expect(Object.keys(TEMPLATE_REGISTRY).filter((n) => !OWN_HEAD_TEMPLATES.has(n))).toEqual([]);
  });

  for (const [name, fn] of Object.entries(NAMES)) {
    it(`${name} draws TemplateHead and TemplateBody`, () => {
      const start = templates.search(new RegExp(`function ${fn}\\(`));
      expect(start, `${fn} moved: point this scan at it`).toBeGreaterThan(-1);
      const next = templates.slice(start + 10).search(/\nfunction |\nexport function /);
      const body = templates.slice(start, next < 0 ? undefined : start + 10 + next);
      expect(body).toContain("<TemplateHead");
      expect(body).toContain("<TemplateBody>");
    });
  }

  it("MessageBubble hands the header to them", () => {
    expect(read("components/MessageBubble.tsx")).toContain('const header = genUiRootOwnsHead(spec) ? "own" : "frame";');
  });
});

describe("a new question is a new card (review of #945, P1)", () => {
  // The answered card stays mounted, and the next question takes its place.
  // With no key, React kept the first card's picks and typed text, and the
  // answer reads them by header. Node-env vitest renders no state, so this
  // reads the source. The browser twin: `e2e/chat-question-twice.spec.ts`.
  it("each question card in AgentChat is keyed by its request id", () => {
    const chat = code(read("components/AgentChat.tsx"));
    const cards = chat.match(/<ElicitationCard\b[^>]*?\bid=/g) ?? [];
    expect(cards.length).toBe(2);
    expect(chat).toMatch(/<ElicitationCard\s+key=\{elicitation\.requestId \?\? "questions"\}/);
    expect(chat).toMatch(/<ElicitationCard\s+key=\{userInput\.requestId\}/);
  });
});
