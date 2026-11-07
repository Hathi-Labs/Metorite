// WS-CRM CRM-M3a: "New pipeline" on the Native Deal stages card (spec
// crm_conversational.md §8 CRM-M3). The runner has no DOM, so the preview
// renders to a string, and the wiring of the card and the page is a scan of
// their source. The gateway plans the pipeline, so these tests hold the
// request and the words, never a copy of the plan.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as dataCache from "@/lib/dataCache";
import { createPipeline, previewPipeline } from "../lib/api";
import {
  blankRow,
  defaultSource,
  draftProblem,
  emptyDraft,
  moveRow,
  pipelineBody,
  removeRow,
  type NewPipelineDraft,
  type PipelinePlan,
} from "../lib/newPipeline";
import type { DealPipeline } from "../lib/types";
import { PlanPreview } from "./NewPipelineDialog";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

const SALES: DealPipeline = {
  id: "8a0f0e1e-0000-4000-8000-000000000001",
  name: "Sales pipeline",
  is_default: true,
  position: 0,
  stages: [],
};
const RENEWALS: DealPipeline = { ...SALES, id: "8a0f0e1e-0000-4000-8000-000000000002", name: "Renewals", is_default: false, position: 1 };

const PLAN: PipelinePlan = {
  name: "AMC contracts",
  kind: "deal",
  copy_of: null,
  stages: [
    { id: null, name: "Site visit", type: "open", source: "new" },
    { id: "9a1f0e1e-0000-4000-8000-000000000002", name: "Proposal", type: "open", source: "existing" },
    { id: "9a1f0e1e-0000-4000-8000-000000000003", name: "Won", type: "won", source: "added" },
    { id: "9a1f0e1e-0000-4000-8000-000000000004", name: "Lost", type: "lost", source: "added" },
  ],
};

function draft(over: Partial<NewPipelineDraft>): NewPipelineDraft {
  return { name: "AMC contracts", start: "empty", copyFrom: null, stages: [blankRow()], ...over };
}

function row(name: string, type: "open" | "won" | "lost" = "open") {
  return { ...blankRow(), name, type };
}

describe("the draft of a new pipeline", () => {
  it("starts as a copy of the default pipeline, or as a list with no pipeline", () => {
    expect(emptyDraft([RENEWALS, SALES])).toMatchObject({ start: "copy", copyFrom: SALES.id });
    expect(defaultSource([RENEWALS, { ...SALES, is_default: false, position: 5 }])).toBe(RENEWALS.id);
    expect(emptyDraft(null)).toMatchObject({ start: "empty", copyFrom: null });
    expect(emptyDraft([]).stages).toHaveLength(1);
  });

  it("sends a copy by its id, and a list by its names in order", () => {
    expect(pipelineBody(draft({ start: "copy", copyFrom: SALES.id, name: "  Renewals " }))).toEqual({
      name: "Renewals",
      copy_from: SALES.id,
    });
    expect(
      pipelineBody(draft({ stages: [row(" Site visit "), row(""), row("Signed", "won"), row("Gone", "lost")] }))
    ).toEqual({
      name: "AMC contracts",
      // An open row sends no type: a name that the CRM has keeps the type of
      // that stage. A blank row is a row still to fill, never a stage.
      stages: [{ name: "Site visit" }, { name: "Signed", type: "won" }, { name: "Gone", type: "lost" }],
    });
  });

  it("sends nothing while there is nothing to plan", () => {
    expect(pipelineBody(draft({ name: " " , stages: [row("A")] }))).toBeNull();
    expect(pipelineBody(draft({ start: "copy", copyFrom: null }))).toBeNull();
    expect(pipelineBody(draft({ stages: [row(""), row("  ")] }))).toBeNull();
  });

  it("says what is wrong in words, field by field", () => {
    expect(draftProblem(draft({ name: "" , stages: [row("A")] }))).toBe("Give the pipeline a name.");
    expect(draftProblem(draft({ name: "x".repeat(121), stages: [row("A")] }))).toMatch(/at most 120/);
    expect(draftProblem(draft({ start: "copy", copyFrom: null }))).toBe("Pick the pipeline to copy.");
    expect(draftProblem(draft({ stages: [row("")] }))).toBe("Add at least one stage.");
    expect(draftProblem(draft({ stages: [row("Proposal"), row(" proposal ")] }))).toMatch(/twice/);
    expect(draftProblem(draft({ stages: Array.from({ length: 51 }, (_, i) => row(`S${i}`)) }))).toMatch(
      /at most 50 stages/
    );
    expect(draftProblem(draft({ stages: [row("Site visit")] }))).toBeNull();
  });

  it("moves a row with its arrows and keeps one row in the list", () => {
    const rows = [row("A"), row("B"), row("C")];
    expect(moveRow(rows, 2, 0).map((r) => r.name)).toEqual(["C", "A", "B"]);
    expect(moveRow(rows, 0, -1).map((r) => r.name)).toEqual(["A", "B", "C"]);
    const left = removeRow([rows[0]], rows[0].key);
    expect(left).toHaveLength(1);
    expect(left[0].name).toBe("");
  });
});

describe("the two calls", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reads the preview with a GET, and the stages as one JSON value", async () => {
    const seen: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        seen.push({ url, init });
        return new Response(JSON.stringify(PLAN), { status: 200 });
      })
    );
    await previewPipeline({ name: "AMC contracts", stages: [{ name: "Site visit" }] });
    expect(seen[0].init?.method ?? "GET").toBe("GET");
    const url = new URL(seen[0].url, "http://app.test");
    expect(url.pathname).toBe("/api/crm/pipelines/preview");
    expect(url.searchParams.get("name")).toBe("AMC contracts");
    expect(JSON.parse(url.searchParams.get("stages") ?? "")).toEqual([{ name: "Site visit" }]);
  });

  it("posts the create and wakes every CRM read, the pipelines too, also on a refusal", async () => {
    const woke = vi.spyOn(dataCache, "invalidate");
    const sent: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        sent.push(init ?? {});
        return new Response(JSON.stringify({ detail: { code: "name_taken", message: "Taken." } }), {
          status: 409,
        });
      })
    );
    await expect(createPipeline({ name: "AMC contracts", copy_from: SALES.id })).rejects.toMatchObject({
      status: 409,
      code: "name_taken",
      message: "Taken.",
    });
    expect(sent[0].method).toBe("POST");
    expect(JSON.parse(String(sent[0].body))).toEqual({ name: "AMC contracts", copy_from: SALES.id });
    expect(woke).toHaveBeenCalledWith("crm:");
  });
});

describe("the preview", () => {
  const html = (preview: Parameters<typeof PlanPreview>[0]["preview"]) =>
    renderToStaticMarkup(createElement(PlanPreview, { preview }));

  it("lists the plan in order, and marks the new and the added stages", () => {
    const out = html({ state: "ready", plan: PLAN });
    const at = ["Site visit", "Proposal", "Won", "Lost"].map((name) => out.indexOf(`>${name}<`));
    expect(at.every((i) => i > 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(out).toContain(">New<");
    expect(out).toContain(">Added<");
    expect(out).toContain("4 stages");
    expect(out).toContain("the CRM adds its own stage, so that a deal can close");
    expect(out).toContain('role="status"');
  });

  it("names a copy, and says each other state in words", () => {
    expect(html({ state: "ready", plan: { ...PLAN, copy_of: "Sales pipeline" } })).toContain(
      "A copy of Sales pipeline"
    );
    expect(html({ state: "idle" })).toContain("The preview shows here when the pipeline has a name and a stage.");
    expect(html({ state: "loading", plan: null })).toContain("Checking the stages…");
    expect(html({ state: "refused", message: 'A pipeline named "AMC" exists already.' })).toContain(
      "A pipeline named &quot;AMC&quot; exists already."
    );
  });
});

describe("the wiring", () => {
  const settings = read("./PipelineSettings.tsx");
  const page = read("../page.tsx");
  const dialog = read("./NewPipelineDialog.tsx");

  it("shows New pipeline to an admin of a Native CRM on the flag only", () => {
    expect(settings).toMatch(/pipelineCreate && !readOnly \?/);
    expect(page).toContain("pipelineCreate={ctx.isAdmin && pipelineCreate}");
    expect(page).toContain('usePipelineCreate(ctx.isAdmin && ctx.mode === "native")');
    // On both shapes of the Native card: one pipeline, and several.
    expect(settings.match(/action=\{newPipeline\}/g)).toHaveLength(2);
  });

  it("is one shared Modal, and the create goes through the store", () => {
    expect(dialog).toContain('import Modal from "@/components/ui/Modal"');
    expect(dialog).not.toMatch(/<(input|select|textarea)\b/);
    expect(settings).toContain("useCrmStore((state) => state.createPipeline)");
  });
});
