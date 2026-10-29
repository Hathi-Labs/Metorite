// CRM-U15: plain everyday screens (crm_platform.md section 20.19).
//
// The runner has no DOM. A component renders to a string, the store runs on a
// mocked api, and a wiring rule is a scan of the source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", () => ({
  CrmError: class CrmError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  getPipeline: vi.fn(),
}));

import * as api from "../lib/api";
import type { BoardLane } from "../lib/board";
import {
  CONVERT_DISABLED_REASON,
  LOCK_TIP,
  MIRROR_LINE,
  NEW_DISABLED_REASON,
  ONLY_IN_METORITE,
} from "../lib/connect";
import { FILTER_GROUPS } from "../lib/filterFields";
import { useCrmStore } from "../lib/store";
import { DEFAULT_VIEW } from "../lib/urlState";
import FieldsPanel from "./FieldsPanel";
import KanbanBoard from "./KanbanBoard";
import LockedValue from "./LockedValue";
import { MirrorNote } from "./MirrorBanner";
import RecordList from "./RecordList";

const noop = () => undefined;
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf-8");
const getPipeline = vi.mocked(api.getPipeline);

const panel = (entity: "leads" | "organizations", record: Record<string, unknown>) =>
  renderToStaticMarkup(
    createElement(FieldsPanel, {
      part: "overview",
      entity,
      record,
      statuses: [],
      dealContacts: [],
      saving: false,
      onPatch: noop,
      onConvert: noop,
      onSetPrimary: noop,
      onDetach: noop,
      onOpenContact: noop,
    }),
  );

// ── Part A: one clear Mirror line ─────────────────────────────────────────

describe("part A: the Mirror line", () => {
  it("says the one sentence, in the words of the spec", () => {
    expect(MIRROR_LINE).toBe(
      "This is a read-only copy of your Zoho. You can add notes, calls and tasks here. Create and edit records in Zoho.",
    );
    expect(renderToStaticMarkup(createElement(MirrorNote))).toContain(MIRROR_LINE);
  });

  it("draws the strip in Mirror mode only, once", () => {
    const page = read("../page.tsx");
    expect(page.match(/<MirrorNote \/>/g)).toHaveLength(1);
    expect(page).toContain('ctx.mode === "mirror" && <MirrorNote />');
  });

  it("draws no lock icon on a Mirror field row, and shows the line on a click", () => {
    const html = panel("organizations", { id: "1", name: "Acme", ext_source: "zoho" });
    expect(html).toContain("Acme");
    expect(html).not.toContain("lucide-lock");
    expect(html).not.toContain(`aria-label="${LOCK_TIP}"`);
    expect(read("./FieldsPanel.tsx")).not.toContain('name="Lock"');
    expect(read("./FieldInput.tsx")).not.toContain('name="Lock"');
    // The line shows after a click (the state is set in the handler).
    expect(read("./LockedValue.tsx")).toContain("onClick={() => setAsked(true)}");
    expect(LOCK_TIP).toBe("Edit this in Zoho.");
    expect(renderToStaticMarkup(createElement(LockedValue, null, "x"))).toContain(`title="${LOCK_TIP}"`);
  });

  it("shows Convert greyed out in Mirror, with the reason", () => {
    const html = panel("leads", { id: "l1", lead_name: "Ann", ext_source: "zoho" });
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Convert<\/button>/);
    expect(html).toContain(`title="${CONVERT_DISABLED_REASON}"`);
    expect(CONVERT_DISABLED_REASON).toBe("Convert leads in Zoho");
  });

  it("shows Convert enabled in Native", () => {
    const html = panel("leads", { id: "l1", lead_name: "Ann", ext_source: null });
    expect(html).toMatch(/<button[^>]*>Convert<\/button>/);
    expect(html).not.toContain('disabled=""');
    expect(html).not.toContain(CONVERT_DISABLED_REASON);
  });

  it("shows New greyed out in Mirror, with the reason, and not removed", () => {
    const page = read("../page.tsx");
    expect(page).toContain("disabled={!mayCreate}");
    expect(page).toContain("title={mayCreate ? undefined : NEW_DISABLED_REASON}");
    expect(NEW_DISABLED_REASON).toBe("Create records in Zoho");
  });

  it("gives a card no grab cursor, and no drag in Mirror", () => {
    expect(read("./KanbanBoard.tsx")).not.toMatch(/cursor-grab/);
    const lanes = [
      {
        status: { id: "s1", name: "Open", color: "blue", position: 0, type: "open", is_default: true },
        rows: [{ id: "d1", name: "Big deal", ext_source: null }],
        count: 1,
        amount: 0,
        weighted: 0,
      },
    ] as unknown as BoardLane[];
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, { lanes, loading: false, readOnly: true, onMove: noop, onOpen: noop }),
    );
    expect(html).toContain('draggable="false"');
    expect(html).not.toContain("cursor-grab");
  });
});

// ── Part B: the edit pencil in Native ─────────────────────────────────────

describe("part B: the pencil", () => {
  it("gives a Native editable row a pencil, and keeps tick and X", () => {
    const html = panel("organizations", { id: "1", name: "Acme", ext_source: null });
    expect(html).toMatch(/aria-label="Edit name"/);
    expect(html).toContain("group-hover/edit:opacity-100");
    expect(html).toContain("focus-visible:opacity-100");
    const src = read("./FieldsPanel.tsx");
    expect(src).toContain('aria-label="Save"');
    expect(src).toContain('aria-label="Cancel"');
  });

  it("adds no save-on-blur", () => {
    expect(read("./FieldsPanel.tsx")).not.toContain("onBlur");
  });
});

// ── Part C: plain words ───────────────────────────────────────────────────

describe("part C: the copy", () => {
  it("rewrites the Convert dialog", () => {
    const src = read("./ConvertModal.tsx");
    expect(src).toContain("Convert this lead into a deal, a contact and a company.");
    expect(src).toContain("No company on the lead, so none is created.");
    expect(src).not.toMatch(/provenance|minting/);
  });

  it("rewrites the board hint", () => {
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, { lanes: [], loading: false, onMove: noop, onOpen: noop }),
    );
    expect(html).toContain("Stages are set in CRM settings.");
    expect(html).not.toContain("data, not code");
  });

  it("says Not in Zoho", () => {
    expect(ONLY_IN_METORITE).toBe("Not in Zoho");
    expect(read("../lib/connect.ts")).not.toContain("Only in Metorite");
  });

  it("names the filter groups Dates, Choices, Numbers, Other", () => {
    expect([...FILTER_GROUPS]).toEqual(["Dates", "Choices", "Numbers", "Other"]);
  });

  it("puts the method of a report behind an info icon", () => {
    const src = read("./Reports.tsx");
    expect(src).toContain("InfoTip");
    expect(src).toContain('blurb="Open deals in each stage, with the weighted amount."');
    expect(src).toContain('blurb="How many deals reached each stage."');
    expect(src).toContain("method=");
    expect(src).not.toMatch(/blurb="[^"]{120,}/);
  });
});

// ── Part D: stale and duplicate controls ──────────────────────────────────

describe("part D: stale controls", () => {
  it("has no dashed end-of-board create tile and no onCreate prop", () => {
    const src = read("./KanbanBoard.tsx");
    expect(src).not.toContain("border-dashed");
    expect(src).not.toMatch(/onCreate/);
    expect(read("../page.tsx")).not.toMatch(/onCreate=\{mayCreate/);
  });

  it("points the hidden-stage hint at Board options", () => {
    const lanes: BoardLane[] = [];
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, { lanes, loading: false, hiddenEmpty: 2, onMove: noop, onOpen: noop }),
    );
    expect(html).toContain("Board options");
    expect(html).not.toContain("in the bar");
    expect(read("./KanbanBoard.tsx")).not.toContain("and its + stay in reach");
  });
});

// ── Part E: loading and error ─────────────────────────────────────────────

describe("part E: loading and error", () => {
  beforeEach(() => {
    getPipeline.mockReset();
    useCrmStore.setState({ lanes: [], lastView: null, moreLane: null, error: null });
  });

  it("shows a skeleton while the first board read runs", () => {
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, { lanes: [], loading: true, onMove: noop, onOpen: noop }),
    );
    expect(html).toContain('aria-busy="true"');
  });

  it("shows a skeleton while the first list read runs", () => {
    const src = read("./RecordList.tsx");
    expect(src).toContain("<SkeletonRows");
    expect(typeof RecordList).toBe("function");
  });

  it("clears the lanes when a board read fails", async () => {
    const view = { ...DEFAULT_VIEW, tab: "deals" as const, layout: "board" as const };
    getPipeline.mockResolvedValueOnce({
      lanes: [
        {
          status: { id: "s1", name: "Open", color: "blue", position: 0, type: "open", is_default: true },
          rows: [{ id: "d1", name: "Old deal" }],
          count: 1,
          amount: 0,
          weighted: 0,
        },
      ],
    } as never);
    await useCrmStore.getState().loadBoard(view);
    expect(useCrmStore.getState().lanes).toHaveLength(1);
    getPipeline.mockRejectedValueOnce(new Error("The board is down"));
    await useCrmStore.getState().loadBoard(view);
    expect(useCrmStore.getState().lanes).toEqual([]);
    expect(useCrmStore.getState().error).toContain("The board is down");
  });

  it("says the board did not load, rather than 'no stage'", () => {
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, { lanes: [], loading: false, failed: true, onMove: noop, onOpen: noop }),
    );
    expect(html).toContain("The board did not load.");
    expect(html).not.toContain("Stages are set");
  });

  it("gives the error banner a Retry that reads again", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(/<Button variant="ghost" size="sm" icon="RefreshCw" onClick=\{reload\}>\s*Retry/);
    // `reload` is the one read of the Refresh button.
    expect(page).toContain("onClick={reload}");
    expect(page).toContain("failed={store.error !== null}");
  });
});
