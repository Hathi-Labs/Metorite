// CRM-C3: stages and statuses an admin can shape, in both modes
// (spec crm_platform.md §20.6). The runner has no DOM, so each component is
// rendered to a string, and a wiring rule is a scan of the source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import * as api from "../lib/api";
import { orderLanes, toLeadLanes } from "../lib/board";
import { isRetired, pickable, visibleLanes } from "../lib/emptyStages";
import { chipsFor } from "../lib/filters";
import {
  hasOverlay,
  orderAfterDrop,
  overlayPatch,
  resetToSource,
  validateOverlayProbability,
} from "../lib/settings";
import type { LostReason, PipelineLane, Status, StatusType } from "../lib/types";
import { DEFAULT_VIEW } from "../lib/urlState";
import PipelineSettings, { MirrorReasonGrid, MirrorStatusGrid } from "./PipelineSettings";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const noop = async () => true;

function status(id: string, over: Partial<Status> = {}): Status {
  return {
    id,
    name: id,
    color: "gray",
    position: 0,
    type: "open",
    is_default: false,
    probability: 10,
    ...over,
  };
}

const OPEN = status("Open", { position: 0 });
const HIDDEN = status("Hidden", { position: 10, hidden: true });
const GONE = status("Gone", { position: 20, source_gone_at: "2026-10-13T00:00:00Z" });
const WON = status("Won", { position: 30, type: "won" });

// ── Done when 13: no picker offers a hidden row, except the current value ──

describe("the pickers", () => {
  it("offer each row that is not hidden or gone, and the current value", () => {
    const rows = [OPEN, HIDDEN, GONE, WON];
    expect(pickable(rows).map((s) => s.id)).toEqual(["Open", "Won"]);
    expect(pickable(rows, "Hidden").map((s) => s.id)).toEqual(["Open", "Hidden", "Won"]);
    expect(pickable(rows, "Gone").map((s) => s.id)).toEqual(["Open", "Gone", "Won"]);
    expect(isRetired(OPEN)).toBe(false);
    expect(isRetired(HIDDEN) && isRetired(GONE)).toBe(true);
  });

  it("the filter chips drop a hidden stage, unless the view is filtered to it", () => {
    const rows = [OPEN, HIDDEN, WON];
    const ids = (statusId: string | null) =>
      chipsFor("deals", rows, { ...DEFAULT_VIEW, tab: "deals", statusId }).map((c) => c.id);
    expect(ids(null)).toEqual(["all", "Open", "Won"]);
    expect(ids("Hidden")).toEqual(["all", "Open", "Hidden", "Won"]);
  });

  it("the lost dialog never offers a hidden reason", () => {
    const reasons: LostReason[] = [
      { id: "r1", label: "Price", position: 0 },
      { id: "r2", label: "Old reason", position: 1, hidden: true },
    ];
    // The dialog opens only for a deal with no reason, so no current value.
    expect(pickable(reasons).map((r) => r.label)).toEqual(["Price"]);
    // `Modal` portals, so the server render is empty: the wiring is read as
    // source. The list and its empty state both draw `offered`.
    const modal = read("./MoveModal.tsx");
    expect(modal).toContain("const offered = pickable(reasons);");
    expect(modal).toContain("{[...offered]");
    expect(modal).toContain("offered.length === 0");
    expect(modal).not.toContain("{[...reasons]");
  });

  it("the status pill offers a hidden stage only as the current value", () => {
    // The pill draws its menu only when open, and the runner has no DOM, so
    // its wiring is read as source: the menu is `pickable` over the current id.
    const pill = read("./StatusPill.tsx");
    expect(pill).toContain("pickable([...statuses]");
    expect(pill).toContain(", status?.id)");
  });
});

// ── Done when 13: a hidden lane shows only with records, last in its group ──

describe("a hidden lane on a board", () => {
  const lane = (s: Status, count: number): PipelineLane => ({
    status: s,
    rows: [],
    count,
    amount: 0,
    weighted: 0,
    pipeline_position: null,
  });

  it("shows only when it holds records, whatever the empty-stage choice says", () => {
    const lanes = [lane(HIDDEN, 0), lane(OPEN, 0), lane(GONE, 0)];
    expect(visibleLanes(orderLanes(lanes, "mirror"), true).map((l) => l.status.id)).toEqual([
      "Open",
    ]);
    expect(visibleLanes(orderLanes(lanes, "mirror"), false)).toEqual([]);
  });

  it("sits at the end of its group when it holds records", () => {
    const lanes = [lane(HIDDEN, 2), lane(OPEN, 1), lane(WON, 0), lane(GONE, 1)];
    expect(visibleLanes(orderLanes(lanes, "native"), true).map((l) => l.status.id)).toEqual([
      "Open",
      "Hidden",
      "Gone",
      "Won",
    ]);
  });

  it("the Leads board takes the same rule", () => {
    const lead = (id: string, over: Partial<Status> = {}) =>
      status(id, { probability: null, ...over });
    const statuses = [
      lead("Fresh", { position: 0 }),
      lead("Parked", { position: 5, hidden: true }),
      lead("Old", { position: 7, hidden: true }),
      lead("Working", { position: 10 }),
    ];
    const lanes = toLeadLanes(statuses, {
      Parked: { rows: [], total: 3 },
      Fresh: { rows: [], total: 0 },
    });
    expect(lanes.map((l) => l.status.id)).toEqual(["Fresh", "Working", "Parked"]);
  });
});

// ── Done when 18: the Mirror editor and both resets ──────────────────────

describe("the Mirror editor", () => {
  const qualify = status("Qualify", {
    probability: 33,
    source_probability: 10,
    ui_probability: 33,
    ui_color: "violet",
    color: "violet",
    source_color: "gray",
    admin_order: true,
    ui_position: 0,
  });
  const removed = status("Removed", {
    source_probability: 40,
    source_gone_at: "2026-10-13T00:00:00Z",
    admin_order: true,
    ui_position: 10,
  });

  const grid = (statuses: Status[]) =>
    renderToStaticMarkup(
      createElement(MirrorStatusGrid, {
        kind: "deal",
        title: "Deal stages",
        blurb: "Zoho owns the stages.",
        statuses,
        saving: false,
        onSave: noop,
        onOrder: noop,
        onResetOrder: noop,
      })
    );
  /** The markup of the one button whose text holds *label*, in *html*. */
  const button = (html: string, label: string): string => {
    const found = html.split("<button").find((part) => part.split("</button>")[0].includes(label));
    expect(found, label).toBeDefined();
    return found!.split("</button>")[0];
  };

  it("shows the Zoho value beside the Metorite value on each row", () => {
    const html = grid([qualify, removed]);
    expect(html).toContain("Zoho 10%");
    expect(html).toContain("Zoho 40%");
    expect(html).toContain('aria-label="Metorite probability"');
    expect(html).toContain('value="33"');
    expect(html).toContain("Removed in Zoho");
    expect(html).toContain('draggable="true"');
    // No source field is editable: the name is text, and there is no delete.
    expect(html).not.toMatch(/aria-label="(Delete|Remove)/);
  });

  it("offers Reset to Zoho on a row with an overlay, and Reset order after an admin order", () => {
    const withOverlay = grid([qualify]);
    expect(button(withOverlay, "Reset to Zoho")).not.toContain('disabled=""');
    expect(button(withOverlay, "Reset order to Zoho")).not.toContain('disabled=""');
    const plain = grid([status("Plain", { source_probability: 10 })]);
    expect(button(plain, "Reset to Zoho")).toContain('disabled=""');
    expect(button(plain, "Reset order to Zoho")).toContain('disabled=""');
  });

  it("resets the probability and the colour, and keeps hide and the rot threshold", () => {
    expect(resetToSource("deal")).toEqual({ ui_probability: null, ui_color: null });
    // A lead status has no probability, and the gateway refuses that field.
    expect(resetToSource("lead")).toEqual({ ui_color: null });
    expect("hidden" in resetToSource("deal")).toBe(false);
    expect("max_dwell_days" in resetToSource("deal")).toBe(false);
    expect(hasOverlay("deal", qualify as unknown as Record<string, unknown>)).toBe(true);
    expect(hasOverlay("deal", removed as unknown as Record<string, unknown>)).toBe(false);
  });

  it("sends the overlay fields only, and the order route takes every id", () => {
    const before = qualify as unknown as Record<string, unknown>;
    const after = { ...before, ui_probability: 50, hidden: true, name: "Renamed" };
    expect(overlayPatch("deal", before, after)).toEqual({ ui_probability: 50, hidden: true });
    expect(overlayPatch("lead", before, { ...before, ui_color: "red" })).toEqual({
      ui_color: "red",
    });
    expect(orderAfterDrop([qualify, removed, OPEN], 2, 0)).toEqual(["Open", "Qualify", "Removed"]);
    expect(orderAfterDrop([qualify, removed], 1, 1)).toBeNull();
    expect(validateOverlayProbability(null)).toBeNull();
    expect(validateOverlayProbability(101)).not.toBeNull();
  });

  it("calls the order route and the reset route", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(input), init]);
      return {
        ok: true,
        status: 200,
        json: async () => [],
        text: async () => "[]",
      } as Response;
    }) as typeof globalThis.fetch;
    try {
      await api.orderStatuses("deal", ["a", "b"]);
      await api.resetStatusOrder("lead");
    } finally {
      globalThis.fetch = original;
    }
    expect(calls.map(([url, init]) => [url, init?.method, init?.body ?? null])).toEqual([
      ["/api/crm/statuses/deal/order", "PUT", JSON.stringify({ ids: ["a", "b"] })],
      ["/api/crm/statuses/lead/order/reset", "POST", null],
    ]);
  });

  it("lets a Mirror admin hide a lost reason", () => {
    const html = renderToStaticMarkup(
      createElement(MirrorReasonGrid, {
        reasons: [{ id: "r1", label: "Price", position: 0, hidden: true }],
        saving: false,
        onSave: noop,
      })
    );
    expect(html).toContain("Price");
    expect(html).toMatch(/checked=""[^>]*>hidden|type="checkbox"[^>]*checked/);
  });

  it("draws the editor for an admin in Mirror mode, and the lists for a member", () => {
    const draw = (admin: boolean) =>
      renderToStaticMarkup(
        createElement(PipelineSettings, {
          showAdminCards: admin,
          readOnly: true,
          dealStatuses: [qualify],
          leadStatuses: [status("New", { probability: null })],
          lostReasons: [{ id: "r1", label: "Price", position: 0 }],
          saving: false,
          onSaveStatus: noop,
          onRemoveStatus: noop,
          onReorderStatuses: async () => undefined,
          onSaveLostReason: noop,
          onRemoveLostReason: noop,
          onReorderLostReasons: async () => undefined,
        })
      );
    const admin = draw(true);
    expect(admin).toContain("Reset order to Zoho");
    expect(admin).toContain("Zoho 10%");
    const member = draw(false);
    expect(member).not.toContain("Reset order to Zoho");
    expect(member).not.toContain('draggable="true"');
  });
});

// ── Native mode adds hide (item 15) ──────────────────────────────────────

describe("the Native grid", () => {
  it("has a hidden box on each stage and each lost reason", () => {
    const html = renderToStaticMarkup(
      createElement(PipelineSettings, {
        dealStatuses: [status("Open", { type: "open" as StatusType })],
        leadStatuses: [],
        lostReasons: [{ id: "r1", label: "Price", position: 0 }],
        saving: false,
        onSaveStatus: noop,
        onRemoveStatus: noop,
        onReorderStatuses: async () => undefined,
        onSaveLostReason: noop,
        onRemoveLostReason: noop,
        onReorderLostReasons: async () => undefined,
      })
    );
    expect(html.match(/>hidden</g)?.length).toBe(2);
    // The Native grid keeps its own source fields: no Zoho column.
    expect(html).not.toContain("Zoho ");
  });
});
