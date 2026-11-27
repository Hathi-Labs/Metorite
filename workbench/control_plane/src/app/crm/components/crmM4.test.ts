// WS-CRM CRM-M4a — the New pipeline dialog in lead mode, and the read-only
// list of the lead pipelines (crm_conversational.md §8 CRM-M4).
//
// One dialog makes both kinds. These tests hold the lead half: its body names
// the kind and the deal pipeline that it feeds, its words name statuses, and
// the Lead statuses card lists the lead pipelines to read.

import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { listLeadPipelines } from "../lib/api";
import {
  blankRow,
  closingType,
  defaultDealName,
  defaultFeedOption,
  defaultFeedWords,
  draftProblem,
  emptyDraft,
  FEED_HELP,
  feedHelp,
  pipelineBody,
  renameRow,
  storedTypes,
  type NewPipelineDraft,
  type PipelinePlan,
} from "../lib/newPipeline";
import { useCrmStore } from "../lib/store";
import type { DealPipeline, Status } from "../lib/types";
import { PlanPreview } from "./NewPipelineDialog";
import { defaultLeadList, feedsLine, LEAD_PIPELINES_NOTE } from "./PipelineSettings";
import { PipelineSections } from "./SettingsCards";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

const SALES: DealPipeline = {
  id: "8a0f0e1e-0000-4000-8000-000000000001",
  name: "Sales pipeline",
  is_default: true,
  position: 0,
  stages: [],
};
const AMC: DealPipeline = { ...SALES, id: "8a0f0e1e-0000-4000-8000-000000000002", name: "AMC contracts", is_default: false, position: 1 };
const WEB: DealPipeline = {
  id: "8a0f0e1e-0000-4000-8000-000000000010",
  name: "Web",
  is_default: false,
  position: 0,
  kind: "lead",
  feeds_pipeline_id: AMC.id,
  stages: [
    { id: "s-new", name: "New", position: 0, type: "open" },
    { id: "s-form", name: "Form filled", position: 1, type: "ongoing" },
  ],
};

const STATUSES: Status[] = [
  { id: "s-new", name: "New", color: "gray", position: 10, type: "open", is_default: true },
  { id: "s-contacted", name: "Contacted", color: "blue", position: 20, type: "ongoing", is_default: false },
  { id: "s-qualified", name: "Qualified", color: "green", position: 40, type: "won", is_default: false },
];

const LEAD_PLAN: PipelinePlan = {
  name: "Trade show",
  kind: "lead",
  copy_of: null,
  feeds: "AMC contracts",
  stages: [
    { id: "s-new", name: "New", type: "open", source: "existing" },
    { id: null, name: "Booth visit", type: "open", source: "new" },
    { id: "s-qualified", name: "Qualified", type: "won", source: "added" },
  ],
};

function draft(over: Partial<NewPipelineDraft>): NewPipelineDraft {
  return { name: "Trade show", start: "empty", copyFrom: null, stages: [blankRow()], ...over };
}

function row(name: string, type: "open" | "won" | "lost" = "open") {
  return { ...blankRow(), name, type };
}

describe("the draft of a lead pipeline", () => {
  it("starts with no feed, which follows the default deal pipeline, and copies a lead pipeline", () => {
    // Repair 1: no pick is NULL, as in the chat. It follows the org's default.
    expect(emptyDraft(null, "lead")).toMatchObject({ start: "empty", copyFrom: null, feeds: null });
    expect(emptyDraft([WEB], "lead")).toMatchObject({ start: "copy", copyFrom: WEB.id });
    expect(pipelineBody(draft({ feeds: null, stages: [row("A")] }), new Map(), "lead")).toEqual({
      name: "Trade show",
      kind: "lead",
      stages: [{ name: "A" }],
    });
    expect(defaultFeedWords(defaultDealName([AMC, SALES]))).toBe(
      "the default deal pipeline (now Sales pipeline)",
    );
    expect(defaultFeedWords(null)).toBe("the default deal pipeline");
    // Repair 2: the picker's default option is short, so it reads whole at
    // 390 px. The name of the default goes on the line under it.
    expect(defaultFeedOption("Sales pipeline")).toEqual({
      value: "",
      label: "The default deal pipeline",
      description: "It is Sales pipeline now.",
    });
    expect(defaultFeedOption(null)).toEqual({ value: "", label: "The default deal pipeline" });
    expect(feedHelp("Sales pipeline", false)).toBe(
      `The default deal pipeline is Sales pipeline now. ${FEED_HELP}`,
    );
    expect(feedHelp("Sales pipeline", true)).toBe(FEED_HELP);
    expect(feedHelp(null, false)).toBe(FEED_HELP);
    // A deal draft names no feed, as before.
    expect(emptyDraft([SALES])).not.toHaveProperty("feeds");
  });

  it("sends the kind, the feed and the statuses; a deal body sends neither", () => {
    const known = storedTypes(STATUSES);
    expect(
      pipelineBody(draft({ feeds: " AMC contracts ", stages: [row("new"), row("Booth visit"), row("Junk", "open")] }), known, "lead"),
    ).toEqual({
      name: "Trade show",
      kind: "lead",
      feeds: "AMC contracts",
      // "new" is a status that the CRM has: it keeps its type. "Junk" set
      // to Open goes as "open", or the route would close it.
      stages: [{ name: "new" }, { name: "Booth visit" }, { name: "Junk", type: "open" }],
    });
    expect(pipelineBody(draft({ start: "copy", copyFrom: WEB.id, feeds: null }), known, "lead")).toEqual({
      name: "Trade show",
      kind: "lead",
      copy_from: WEB.id,
    });
    expect(pipelineBody(draft({ feeds: "AMC contracts", stages: [row("A")] }))).toEqual({
      name: "Trade show",
      stages: [{ name: "A" }],
    });
  });

  it("closes a lead status on the words of the route", () => {
    expect(closingType("Converted", "lead")).toBe("won");
    expect(closingType(" junk ", "lead")).toBe("lost");
    expect(closingType("Converted")).toBeNull();
    expect(closingType("Closed Won", "lead")).toBeNull();
    expect(renameRow(row("Conv"), "Converted", "lead").type).toBe("won");
    expect(renameRow(row("Conv"), "Converted").type).toBe("open");
  });

  it("says what is wrong in the words of a lead pipeline", () => {
    expect(draftProblem(draft({ name: "", stages: [row("A")] }), "lead")).toBe("Give the lead pipeline a name.");
    expect(draftProblem(draft({ stages: [row("")] }), "lead")).toBe("Add at least one status.");
    expect(draftProblem(draft({ start: "copy", copyFrom: null }), "lead")).toBe("Pick the lead pipeline to copy.");
    expect(draftProblem(draft({ stages: Array.from({ length: 51 }, (_, i) => row(`S${i}`)) }), "lead")).toMatch(
      /at most 50 statuses/,
    );
  });
});

describe("the preview in lead mode", () => {
  const html = (preview: Parameters<typeof PlanPreview>[0]["preview"], kind?: "deal" | "lead") =>
    renderToStaticMarkup(createElement(PlanPreview, { preview, kind }));

  it("counts statuses, names the deal pipeline it feeds, and says a lead can close", () => {
    const out = html({ state: "ready", plan: LEAD_PLAN });
    expect(out).toContain("3 statuses");
    expect(out).toContain("Feeds deals into AMC contracts.");
    expect(out).toContain("the CRM adds its own status, so that a lead can close");
    expect(
      html({ state: "ready", plan: { ...LEAD_PLAN, feeds: null, default_deal_pipeline: "Sales pipeline" } }),
    ).toContain("Feeds deals into the default deal pipeline (now Sales pipeline).");
    expect(html({ state: "idle" }, "lead")).toContain(
      "The preview shows here when the lead pipeline has a name and a status.",
    );
    expect(html({ state: "loading", plan: null }, "lead")).toContain("Checking the statuses…");
  });
});

describe("the two calls in lead mode", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reads the lead pipelines with kind=lead, and keeps only lead rows", async () => {
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        seen.push(url);
        // A gateway from before CRM-M4a ignores the query and answers deals.
        return new Response(JSON.stringify([SALES, WEB]), { status: 200 });
      }),
    );
    expect(await listLeadPipelines()).toEqual([WEB]);
    const url = new URL(seen[0], "http://app.test");
    expect(url.pathname).toBe("/api/crm/pipelines");
    expect(url.searchParams.get("kind")).toBe("lead");
  });

  it("opens the one dialog in lead mode with a fresh draft", () => {
    useCrmStore.getState().setNewPipeline({ open: true, kind: "lead", draft: null });
    expect(useCrmStore.getState().newPipeline).toEqual({ open: true, kind: "lead", draft: null });
    useCrmStore.getState().setNewPipeline({ open: false, kind: "deal", draft: null });
  });
});

describe("the Lead statuses card with lead pipelines", () => {
  const card = () =>
    renderToStaticMarkup(
      createElement(PipelineSections, {
        title: "Lead statuses",
        pipelines: [defaultLeadList(STATUSES), WEB, { ...WEB, id: "lp-2", name: "Outbound", position: 1, feeds_pipeline_id: null }],
        fallback: STATUSES,
        note: LEAD_PIPELINES_NOTE,
        lineOf: (p: DealPipeline) => feedsLine(p, [SALES, AMC]),
      }),
    );

  it("lists the default list first, then each lead pipeline with its feed, to read", () => {
    const out = card();
    const at = ["Default list", "Web", "Outbound"].map((name) => out.indexOf(`>${name}<`));
    expect(at.every((i) => i > 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(out).toContain("Feeds deals into AMC contracts.");
    expect(out).toContain("Feeds deals into the default deal pipeline (now Sales pipeline).");
    expect(out).toContain("Form filled");
    // Read only: no control of a stage list.
    expect(out).not.toContain("<input");
    expect(out).not.toMatch(/aria-label="Move /);
  });

  it("says on the card what the default list is and what is coming", () => {
    expect(LEAD_PIPELINES_NOTE).toContain("The default list holds each lead with no pipeline");
    expect(LEAD_PIPELINES_NOTE).toContain("Status editing for each pipeline comes in a later release.");
    expect(feedsLine(defaultLeadList(STATUSES), [SALES])).toBeNull();
  });
});

describe("the wiring in lead mode", () => {
  const settings = read("./PipelineSettings.tsx");
  const page = read("../page.tsx");
  const dialog = read("./NewPipelineDialog.tsx");

  it("puts New pipeline on the Native Lead statuses card, behind the same flag", () => {
    expect(settings).toMatch(/const newLeadPipeline = newPipeline \?/);
    expect(settings.match(/action=\{newLeadPipeline\}/g)).toHaveLength(2);
    expect(settings).toContain('openNew("lead")');
    expect(page).toContain("leadPipelines={leadPipelines}");
  });

  it("draws one dialog for both kinds, with the deal pipelines for the feed", () => {
    expect(settings.match(/<NewPipelineDialog\b/g)).toHaveLength(1);
    expect(settings).toContain("kind={newPipelineKind}");
    expect(settings).toContain("dealPipelines={pipelines}");
    expect(dialog).toContain('label="Feeds deals into"');
    // Repair 1: the first option is the default, and it sends no feed.
    expect(dialog).toContain("defaultFeedOption(defaultDeal),");
    expect(dialog).toContain("{feedHelp(defaultDeal, fed !== null)}");
    expect(dialog).toContain("const draft = stored ?? emptyDraft(pipelines, kind);");
    // Repair 1: the lead sections say "Loading statuses…" while the list loads.
    expect(settings).toContain('emptyLine={vocabularyState === "loading" ? "Loading statuses…" : "No statuses yet."}');
    expect(dialog).not.toMatch(/<(input|select|textarea)\b/);
  });
});
