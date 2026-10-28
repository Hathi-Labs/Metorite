// CRM-C3: stages and statuses an admin can shape, in both modes
// (spec crm_platform.md §20.6). The runner has no DOM, so each component is
// rendered to a string, and a wiring rule is a scan of the source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { accentForHue } from "@/lib/statusAccent";

import * as api from "../lib/api";
import { orderLanes, stageAccent, toLeadLanes, type BoardLane } from "../lib/board";
import type { CrmMode } from "../lib/connect";
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
import KanbanBoard from "./KanbanBoard";
import PipelineSettings, {
  MirrorReasonGrid,
  MirrorStageForm,
  NativeStageForm,
  StageList,
} from "./PipelineSettings";

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

// ── Done when 13: a hidden lane shows only with records, last on the board ──

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

  it("sits at the end of the board when it holds records", () => {
    // D130: the old rule put it at the end of its group, before Won.
    const lanes = [lane(HIDDEN, 2), lane(OPEN, 1), lane(WON, 0), lane(GONE, 1)];
    expect(visibleLanes(orderLanes(lanes, "native"), true).map((l) => l.status.id)).toEqual([
      "Open",
      "Won",
      "Hidden",
      "Gone",
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

  it("keeps the Leads board in Zoho order with no overlay: no groups by type", () => {
    // Fracktal's Lead Status list, in Zoho order. A lost or won status stays
    // where Zoho puts it. Since D130 the deal board has no type group either.
    const zoho = [
      status("Not Contacted", { position: 0, probability: null }),
      status("Junk Lead", { position: 1, type: "lost", probability: null }),
      status("Contacted", { position: 2, probability: null }),
      status("Lost Lead", { position: 3, type: "lost", probability: null }),
      status("Not Qualified", { position: 4, type: "lost", probability: null }),
      status("Pre-Qualified", { position: 5, type: "won", probability: null }),
      status("Contact in Future", { position: 6, probability: null }),
    ];
    const shuffled = [zoho[4], zoho[0], zoho[6], zoho[2], zoho[5], zoho[1], zoho[3]];
    expect(toLeadLanes(shuffled, {}).map((l) => l.status.id)).toEqual(
      zoho.map((s) => s.id)
    );
  });
});

// ── Done when 18: the Mirror editor and both resets ──────────────────────
//
// CRM-U11 (D130) rebuilt the editor: one line per row, and an Edit dialog.
// The row "Reset to Zoho" became "Use Zoho's value" in the dialog, and
// "Reset order to Zoho" became the "Same as Zoho" choice of "Column order".

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
      createElement(StageList, {
        kind: "deal",
        mode: "mirror",
        statuses,
        saving: false,
        onSave: noop,
        onOrder: noop,
        onResetOrder: noop,
      })
    );
  /** The Edit dialog of one Mirror stage. `Modal` portals, so the form renders alone. */
  const dialog = (stage: Status) =>
    renderToStaticMarkup(
      createElement(MirrorStageForm, {
        kind: "deal",
        status: stage,
        saving: false,
        onSave: noop,
        onCancel: () => undefined,
      })
    );
  /** The markup of the one button whose text holds *label*, in *html*. */
  const button = (html: string, label: string): string => {
    const found = html.split("<button").find((part) => part.split("</button>")[0].includes(label));
    expect(found, label).toBeDefined();
    return found!.split("</button>")[0];
  };

  it("shows the win chance as text on each row, and the Zoho value beside the Metorite value in Edit", () => {
    const html = grid([qualify, removed]);
    expect(html).toContain(">33%<");
    expect(html).toContain("Removed in Zoho");
    expect(html).toContain('draggable="true"');
    expect(html).toContain('aria-label="Edit Qualify"');
    // One line per row: no input on a row, no source field, no delete.
    expect(html).not.toContain("<input");
    expect(html).not.toMatch(/aria-label="(Delete|Remove)/);
    expect(dialog(qualify)).toContain("Zoho: 10%");
    expect(dialog(qualify)).toContain('value="33"');
    expect(dialog(removed)).toContain("Zoho: 40%");
    expect(dialog(removed)).toContain('placeholder="40"');
  });

  it("offers \"Use Zoho's value\" with an overlay, and shows \"Custom\" after an admin order", () => {
    expect(button(dialog(qualify), "Use Zoho")).not.toContain('disabled=""');
    const plain = status("Plain", { source_probability: 10 });
    expect(button(dialog(plain), "Use Zoho")).toContain('disabled=""');
    expect(button(grid([qualify]), "Custom")).toContain('aria-selected="true"');
    expect(button(grid([plain]), "Same as Zoho")).toContain('aria-selected="true"');
  });

  it("resets the probability and the colour, and keeps hide and the rot threshold", () => {
    expect(resetToSource("deal")).toEqual({ ui_probability: null, ui_color: null });
    // A lead status carries a win chance too since migration 288 (CRM-U6).
    expect(resetToSource("lead")).toEqual({ ui_probability: null, ui_color: null });
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
          onSaveLostReason: noop,
          onRemoveLostReason: noop,
          onReorderLostReasons: async () => undefined,
        })
      );
    const admin = draw(true);
    expect(admin).toContain("Same as Zoho");
    expect(admin).toContain('aria-label="Edit Qualify"');
    const member = draw(false);
    expect(member).not.toContain("Same as Zoho");
    expect(member).not.toContain('draggable="true"');
  });
});

// ── Native mode adds hide (item 15) ──────────────────────────────────────

describe("the Native grid", () => {
  it("hides a stage in its Edit dialog, and a lost reason with its box", () => {
    // CRM-U11: the stage row lost its "hidden" box. The Edit dialog holds
    // "Show this column on the board", and a hidden stage shows a tag.
    const html = renderToStaticMarkup(
      createElement(PipelineSettings, {
        dealStatuses: [status("Open", { type: "open" as StatusType })],
        leadStatuses: [],
        lostReasons: [{ id: "r1", label: "Price", position: 0 }],
        saving: false,
        onSaveStatus: noop,
        onRemoveStatus: noop,
        onSaveLostReason: noop,
        onRemoveLostReason: noop,
        onReorderLostReasons: async () => undefined,
      })
    );
    expect(html.match(/>hidden</g)?.length).toBe(1);
    // The Native grid keeps its own source fields: no Zoho column.
    expect(html).not.toContain("Zoho ");
    const form = renderToStaticMarkup(
      createElement(NativeStageForm, {
        kind: "deal",
        status: status("Open", { hidden: true }),
        saving: false,
        onSave: noop,
        onAdd: noop,
        onRemove: noop,
        onCancel: () => undefined,
      })
    );
    expect(form).toContain("Show this column on the board");
    expect(form).not.toMatch(/type="checkbox"[^>]*checked=""[^>]*>[^<]*Show this column/);
    const list = renderToStaticMarkup(
      createElement(StageList, {
        kind: "deal",
        mode: "native",
        statuses: [status("Open"), status("Parked", { hidden: true, position: 10 })],
        saving: false,
        onSave: noop,
        onOrder: noop,
      })
    );
    expect(list).toContain(">Hidden<");
  });
});

// ── Done when 14 and 13: the lane colour and the "Hidden" mark on the board ──

describe("a Mirror lane on the board", () => {
  const lane = (status: Status, count = 0) =>
    ({ status, rows: [], count, amount: 0, weighted: 0 }) as unknown as BoardLane;
  const board = (lanes: BoardLane[], mode: CrmMode) =>
    renderToStaticMarkup(
      createElement(KanbanBoard, {
        lanes,
        loading: false,
        mode,
        onMove: () => undefined,
        onOpen: () => undefined,
      })
    );

  it("draws a set ui_color in that colour, and a won lane with none in green", () => {
    const violet = accentForHue("violet");
    const green = accentForHue("green");
    // Every mirrored row is gray at the source (crm_platform.md §20.1 fact 6).
    const proposal = status("Proposal", { color: "gray", ui_color: "violet" });
    const won = status("Closed Won", { color: "gray", type: "won" });
    expect(stageAccent(proposal, "mirror")).toBe(violet);
    expect(stageAccent(won, "mirror")).toBe(green);
    const html = board([lane(proposal, 1), lane(won, 1)], "mirror");
    expect(html).toContain(violet.chip);
    expect(html).toContain(green.chip);
    // With no ui_color, the type rule: an open stage with no keyword is gray.
    expect(stageAccent(status("Plain", { color: "gray" }), "mirror")).toBe(
      accentForHue("gray")
    );
    // A Native board keeps its stored colour, and never reads ui_color.
    expect(stageAccent({ ...proposal, color: "red" }, "native")).toBe(accentForHue("red"));
  });

  it("marks a hidden lane, and a lane that left Zoho, with the Hidden word", () => {
    const html = board(
      [lane(status("Open"), 1), lane(status("Parked", { hidden: true }), 2)],
      "mirror"
    );
    expect(html.match(/>Hidden</g)?.length).toBe(1);
    const gone = board(
      [lane(status("Old", { source_gone_at: "2026-10-13T00:00:00Z" }), 1)],
      "native"
    );
    expect(gone).toContain(">Hidden<");
  });
});
