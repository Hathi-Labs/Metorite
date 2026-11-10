// The deals board: a search box and "Show more" in a lane.
// The runner has no DOM, so a component renders to a string and the store is
// driven with a mocked api.
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
import { boardSearchQuery } from "../lib/filters";
import { useCrmStore } from "../lib/store";
import type { CardSort } from "../lib/nextActivity";
import type { Deal, PipelineLane, Status } from "../lib/types";
import { DEFAULT_VIEW, parseView, serializeView, type CrmView } from "../lib/urlState";
import FilterBar from "./FilterBar";
import KanbanBoard, { showMoreLabel } from "./KanbanBoard";

const noop = () => undefined;
const getPipeline = vi.mocked(api.getPipeline);
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf-8");

const stage: Status = {
  id: "s1",
  name: "Proposal",
  color: "blue",
  position: 0,
  type: "open",
  is_default: true,
};
const deal = (n: number): Deal =>
  ({ id: `d${n}`, name: `Deal ${n}`, status_id: "s1", owner_email: "a@x.in" }) as Deal;
const deals = (from: number, to: number) =>
  Array.from({ length: to - from + 1 }, (_, i) => deal(from + i));
const lane = (rows: Deal[], count: number): PipelineLane => ({
  status: stage,
  rows,
  count,
  amount: 1000,
  weighted: 500,
});

const boardView = (over: Partial<CrmView> & { cardSort?: CardSort } = {}): CrmView => ({
  ...DEFAULT_VIEW,
  tab: "deals",
  layout: "board",
  ...over,
});

describe("the search box of the deals board", () => {
  const bar = (tab: "deals" | "leads") =>
    renderToStaticMarkup(
      createElement(FilterBar, {
        entity: tab,
        view: boardView({ tab }),
        onChange: noop,
        owners: null,
        fieldDefs: null,
        search: tab === "deals",
      }),
    );

  it("is the shared search box, with the list's data-page-filter hook", () => {
    expect(bar("deals")).toContain("data-page-filter");
    expect(bar("leads")).not.toContain("data-page-filter");
  });

  it("is drawn by the page for the deals board, and is the one SearchBox", () => {
    const page = read("../page.tsx");
    expect(page).toContain('search={!boardOn || view.tab === "deals"}');
    // No second search box: the board uses FilterBar's.
    expect(read("./KanbanBoard.tsx")).not.toContain("SearchBox");
  });

  it("writes ?q= into the board URL and reads it back", () => {
    const href = serializeView(boardView({ q: "acme" }));
    expect(href).toContain("q=acme");
    expect(href).toContain("layout=board");
    expect(parseView(new URLSearchParams(href)).q).toBe("acme");
  });

  it("sends ?q= to the board read, and nothing when the box is empty", () => {
    expect(boardSearchQuery(boardView({ q: "  acme " }))).toEqual({ q: "acme" });
    expect(boardSearchQuery(boardView({ q: "   " }))).toEqual({});
  });

  it("says plainly that nothing matches, above the empty lanes", () => {
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, {
        lanes: [lane([], 0)],
        loading: false,
        onMove: noop,
        onOpen: noop,
        emptyNote: "Nothing matches this view.",
      }),
    );
    expect(html).toContain("Nothing matches this view.");
  });
});

describe("Show more in a lane", () => {
  const draw = (l: PipelineLane, over: Record<string, unknown> = {}) =>
    renderToStaticMarkup(
      createElement(KanbanBoard, {
        lanes: [l],
        loading: false,
        onMove: noop,
        onOpen: noop,
        onMore: noop,
        ...over,
      }),
    );

  it("shows 'Show more (N more)' only when the count is larger than the cards", () => {
    expect(draw(lane(deals(1, 50), 120))).toContain("Show more (70 more)");
    expect(draw(lane(deals(1, 3), 3))).not.toContain("Show more");
    expect(showMoreLabel(1)).toBe("Show more (1 more)");
  });

  it("draws no button where the caller gives no onMore (the leads board)", () => {
    expect(draw(lane(deals(1, 50), 120), { onMore: undefined })).not.toContain("Show more");
  });

  it("uses the shared Button, with no hand-rolled control", () => {
    const html = draw(lane(deals(1, 50), 120));
    expect(html).toMatch(/<button[^>]*>[^<]*(<[^>]+>)*Show more/);
    expect(read("./KanbanBoard.tsx")).toContain('import Button from "@/components/ui/Button"');
    expect(read("./KanbanBoard.tsx")).not.toMatch(/<button[^>]*Show more/);
  });

  it("wires the deals board to the store, and the leads board to nothing", () => {
    expect(read("../page.tsx")).toContain(
      'onMore={view.tab === "deals" ? (id) => void store.loadLaneMore(id) : undefined}',
    );
  });
});

describe("loadLaneMore", () => {
  beforeEach(() => {
    getPipeline.mockReset();
    useCrmStore.setState({ lanes: [], lastView: null, moreLane: null, error: null });
  });

  it("asks for the next offset of that lane with the board's filters, and appends", async () => {
    const view = boardView({ q: "acme", owner: "me", cardSort: "amount" });
    getPipeline.mockResolvedValueOnce({ lanes: [lane(deals(1, 50), 120)] });
    await useCrmStore.getState().loadBoard(view);
    getPipeline.mockResolvedValueOnce({ lanes: [lane(deals(51, 100), 120)] });
    await useCrmStore.getState().loadLaneMore("s1");

    expect(getPipeline).toHaveBeenLastCalledWith("me", view.pipelineId, {
      q: "acme",
      card_sort: "amount",
      lane_status_id: "s1",
      lane_offset: 50,
    });
    const got = useCrmStore.getState().lanes[0];
    expect(got.rows.map((r) => r.id)).toEqual(deals(1, 100).map((r) => r.id));
    expect(got.count).toBe(120);
    expect(got.amount).toBe(1000);
    expect(useCrmStore.getState().moreLane).toBeNull();
  });

  it("drops a card that arrives twice", async () => {
    getPipeline.mockResolvedValueOnce({ lanes: [lane(deals(1, 50), 120)] });
    await useCrmStore.getState().loadBoard(boardView());
    getPipeline.mockResolvedValueOnce({ lanes: [lane(deals(50, 99), 120)] });
    await useCrmStore.getState().loadLaneMore("s1");
    expect(useCrmStore.getState().lanes[0].rows).toHaveLength(99);
  });

  it("reads nothing when the lane already holds every card", async () => {
    getPipeline.mockResolvedValueOnce({ lanes: [lane(deals(1, 3), 3)] });
    await useCrmStore.getState().loadBoard(boardView());
    getPipeline.mockClear();
    await useCrmStore.getState().loadLaneMore("s1");
    expect(getPipeline).not.toHaveBeenCalled();
  });
});
