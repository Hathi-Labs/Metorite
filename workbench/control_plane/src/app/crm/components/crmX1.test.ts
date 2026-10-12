// CRM-X1, "Find and page" (WS-53, crm_platform.md section 19.5 and 19.10).
// Done-when 7 and 8, the page that `listQuery` sends, "Load more" in the
// store, and the search box. The runner has no DOM, so a component is
// rendered to a string.
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
  listRecords: vi.fn(),
}));

import * as api from "../lib/api";
import { COLUMNS } from "../lib/columns";
import { appendPage, canSortBy, listQuery } from "../lib/filters";
import {
  ANY,
  ME,
  SEARCH_DELAY_MS,
  ownerOptions,
  searchBoxStart,
  searchBoxSync,
  searchBoxWrote,
  searchChanges,
  searchCommitter,
  type CrmOwner,
} from "../lib/filterBar";
import { useCrmStore } from "../lib/store";
import { DEFAULT_VIEW, type CrmView } from "../lib/urlState";
import FilterBar from "./FilterBar";
import RecordList from "./RecordList";

const noop = () => undefined;
const listRecords = vi.mocked(api.listRecords);

const view = (over: Partial<CrmView> = {}): CrmView => ({
  ...DEFAULT_VIEW,
  tab: "contacts",
  layout: "list",
  ...over,
});

const rows = (from: number, to: number) =>
  Array.from({ length: to - from + 1 }, (_, i) => ({ id: `r${from + i}` }));

describe("appendPage (done-when 7)", () => {
  it("adds the next page after the rows on screen, in order", () => {
    expect(appendPage(rows(1, 2), rows(3, 4)).map((r) => r.id)).toEqual([
      "r1",
      "r2",
      "r3",
      "r4",
    ]);
  });

  it("drops a row whose id is already on screen", () => {
    // A record that moved up between two reads shows on both pages.
    const next = [{ id: "r2", name: "moved" }, { id: "r3" }];
    const out = appendPage(rows(1, 2), next);
    expect(out.map((r) => r.id)).toEqual(["r1", "r2", "r3"]);
    // The row on screen stays as it is.
    expect(out[1]).toEqual({ id: "r2" });
  });
});

describe("ownerOptions (done-when 8)", () => {
  const owners: CrmOwner[] = [
    { email: "priya@x.test", name: "Priya", member: true },
    { email: "ravi@x.test", name: null, member: true },
    { email: "ghost@zoho.test", name: null, member: false },
  ];

  it("lists an owner who holds no row on page 1", () => {
    const options = ownerOptions(owners, null);
    expect(options.map((o) => o.value)).toEqual([
      ANY,
      ME,
      "priya@x.test",
      "ravi@x.test",
      "ghost@zoho.test",
    ]);
    expect(options[2].label).toBe("Priya");
    expect(options[4].label).toBe("ghost@zoho.test");
  });

  it("keeps Anyone, Me, and a chosen owner that the list does not hold", () => {
    expect(ownerOptions(owners, "gone@x.test").map((o) => o.value)).toContain("gone@x.test");
    // A chosen owner the list holds in another case is not added twice.
    const again = ownerOptions(owners, "PRIYA@x.test").map((o) => o.value.toLowerCase());
    expect(again.filter((v) => v === "priya@x.test")).toHaveLength(1);
    // "me" is not an owner email.
    expect(ownerOptions(owners, ME).filter((o) => o.value === ME)).toHaveLength(1);
  });

  it("offers Anyone and Me while the list is not read", () => {
    expect(ownerOptions(null, null).map((o) => o.value)).toEqual([ANY, ME]);
  });
});

describe("the page listQuery sends", () => {
  it("asks for the page it is given, at 50 rows", () => {
    expect(listQuery("contacts", view(), 2)).toMatchObject({ page: 2, page_size: 50 });
    expect(listQuery("contacts", view())).toMatchObject({ page: 1, page_size: 50 });
  });
});

describe("the columns (scope items 3 and 4)", () => {
  const keys = (entity: keyof typeof COLUMNS) => COLUMNS[entity].map((c) => c.key);

  it("puts the Stage after the Deal, and Last touched at the end", () => {
    expect(keys("deals").slice(0, 2)).toEqual(["name", "status_name"]);
    expect(keys("deals").at(-1)).toBe("last_activity_at");
    expect(COLUMNS.deals[1].label).toBe("Stage");
  });

  it("gives Leads a Status column", () => {
    expect(COLUMNS.leads.find((c) => c.key === "status_name")?.label).toBe("Status");
  });

  it("offers Last touched, with its sort, on all four lists", () => {
    for (const entity of ["deals", "leads", "contacts", "organizations"] as const) {
      const column = COLUMNS[entity].find((c) => c.key === "last_activity_at");
      expect(column?.label).toBe("Last touched");
      expect(column?.sort).toBe("last_activity_at");
      expect(canSortBy(entity, "last_activity_at")).toBe(true);
    }
  });
});

describe("Load more in the store", () => {
  beforeEach(() => {
    listRecords.mockReset();
    useCrmStore.setState({ rows: [], total: 0, page: 1, loadingMore: false, lastView: null });
  });

  it("asks for the next page of the same view and adds only new rows", async () => {
    listRecords.mockResolvedValueOnce({ rows: rows(1, 50), total: 120 });
    await useCrmStore.getState().loadList("contacts", view({ q: "acme" }));
    expect(useCrmStore.getState().page).toBe(1);

    listRecords.mockResolvedValueOnce({ rows: [{ id: "r50" }, ...rows(51, 99)], total: 120 });
    await useCrmStore.getState().loadMore();

    const [entity, query] = listRecords.mock.calls[1];
    expect(entity).toBe("contacts");
    expect(query).toMatchObject({ page: 2, page_size: 50, q: "acme" });
    const state = useCrmStore.getState();
    expect(state.page).toBe(2);
    expect(state.rows).toHaveLength(99);
    expect(new Set(state.rows.map((r) => r.id)).size).toBe(99);
  });

  it("sets the page back to 1 when the view changes", async () => {
    listRecords.mockResolvedValue({ rows: rows(1, 50), total: 120 });
    await useCrmStore.getState().loadList("contacts", view());
    await useCrmStore.getState().loadMore();
    expect(useCrmStore.getState().page).toBe(2);
    await useCrmStore.getState().loadList("contacts", view({ owner: "me" }));
    expect(useCrmStore.getState().page).toBe(1);
    expect(listRecords.mock.calls.at(-1)?.[1]).toMatchObject({ page: 1 });
  });

  it("drops an answer for an old view", async () => {
    listRecords.mockResolvedValueOnce({ rows: rows(1, 50), total: 120 });
    await useCrmStore.getState().loadList("contacts", view());

    let answer: (value: { rows: { id: string }[]; total: number }) => void = noop;
    listRecords.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)));
    const more = useCrmStore.getState().loadMore();

    // The member changes the view while page 2 is in flight.
    listRecords.mockResolvedValueOnce({ rows: rows(500, 510), total: 11 });
    await useCrmStore.getState().loadList("contacts", view({ q: "new" }));

    answer({ rows: rows(51, 100), total: 120 });
    await more;
    const state = useCrmStore.getState();
    expect(state.rows.map((r) => r.id)).toEqual(rows(500, 510).map((r) => r.id));
    expect(state.total).toBe(11);
    expect(state.page).toBe(1);
  });

  it("drops a slow first page that a newer view overtook", async () => {
    let first: (value: { rows: { id: string }[]; total: number }) => void = noop;
    listRecords.mockImplementationOnce(() => new Promise((resolve) => (first = resolve)));
    const slow = useCrmStore.getState().loadList("contacts", view({ q: "old" }));
    listRecords.mockResolvedValueOnce({ rows: rows(7, 7), total: 1 });
    await useCrmStore.getState().loadList("contacts", view({ q: "new" }));
    first({ rows: rows(1, 50), total: 120 });
    await slow;
    expect(useCrmStore.getState().rows.map((r) => r.id)).toEqual(["r7"]);
  });
});

describe("review round 1: the end of the list, and the depth after a write", () => {
  beforeEach(() => {
    listRecords.mockReset();
    useCrmStore.setState({
      rows: [],
      total: 0,
      page: 1,
      hasMore: true,
      loadingMore: false,
      lastView: null,
    });
  });

  it("stops offering more when a page comes back short, though a row moved", async () => {
    // A sync moved one row between the reads: page 2 repeats r50, and holds 49.
    listRecords.mockResolvedValueOnce({ rows: rows(1, 50), total: 100 });
    await useCrmStore.getState().loadList("contacts", view());
    expect(useCrmStore.getState().hasMore).toBe(true);
    listRecords.mockResolvedValueOnce({ rows: rows(50, 98), total: 100 });
    await useCrmStore.getState().loadMore();
    const state = useCrmStore.getState();
    expect(state.rows).toHaveLength(98);
    expect(state.rows.length).toBeLessThan(state.total);
    expect(state.hasMore).toBe(false);
  });

  it("stops offering more when a page comes back empty", async () => {
    listRecords.mockResolvedValueOnce({ rows: rows(1, 50), total: 101 });
    await useCrmStore.getState().loadList("contacts", view());
    listRecords.mockResolvedValueOnce({ rows: [{ id: "r50" }, ...rows(51, 99)], total: 101 });
    await useCrmStore.getState().loadMore();
    expect(useCrmStore.getState().hasMore).toBe(true);
    listRecords.mockResolvedValueOnce({ rows: [], total: 101 });
    await useCrmStore.getState().loadMore();
    expect(useCrmStore.getState().hasMore).toBe(false);
  });

  it("knows a first page that is short is the whole list", async () => {
    listRecords.mockResolvedValueOnce({ rows: rows(1, 7), total: 8 });
    await useCrmStore.getState().loadList("contacts", view());
    expect(useCrmStore.getState().hasMore).toBe(false);
  });

  it("re-reads as many rows as were on screen after a write", async () => {
    listRecords.mockResolvedValue({ rows: rows(1, 50), total: 300 });
    await useCrmStore.getState().loadList("contacts", view());
    await useCrmStore.getState().loadMore();
    expect(useCrmStore.getState().page).toBe(2);

    listRecords.mockResolvedValueOnce({ rows: rows(1, 100), total: 300 });
    await useCrmStore.getState().refreshCollection();
    expect(listRecords.mock.calls.at(-1)?.[1]).toMatchObject({ page: 1, page_size: 100 });
    const state = useCrmStore.getState();
    expect(state.rows).toHaveLength(100);
    expect(state.page).toBe(2);
    expect(state.hasMore).toBe(true);
  });

  it("caps the re-read at the largest page, and keeps the next page in step", async () => {
    listRecords.mockResolvedValue({ rows: rows(1, 50), total: 300 });
    await useCrmStore.getState().loadList("contacts", view());
    await useCrmStore.getState().loadMore();
    await useCrmStore.getState().loadMore();
    await useCrmStore.getState().loadMore();
    expect(useCrmStore.getState().page).toBe(4);

    listRecords.mockResolvedValueOnce({ rows: rows(1, 100), total: 300 });
    await useCrmStore.getState().refreshCollection();
    expect(listRecords.mock.calls.at(-1)?.[1]).toMatchObject({ page: 1, page_size: 100 });
    expect(useCrmStore.getState().page).toBe(2);
    listRecords.mockResolvedValueOnce({ rows: rows(101, 150), total: 300 });
    await useCrmStore.getState().loadMore();
    expect(listRecords.mock.calls.at(-1)?.[1]).toMatchObject({ page: 3, page_size: 50 });
  });

  it("starts again at page 1 when the view changes", async () => {
    listRecords.mockResolvedValue({ rows: rows(1, 50), total: 300 });
    await useCrmStore.getState().loadList("contacts", view());
    await useCrmStore.getState().loadMore();
    await useCrmStore.getState().loadList("contacts", view({ q: "other" }));
    expect(listRecords.mock.calls.at(-1)?.[1]).toMatchObject({ page: 1, page_size: 50 });
    expect(useCrmStore.getState().page).toBe(1);
  });

  it("hides Load more when the store says the list ended", () => {
    const html = renderToStaticMarkup(
      createElement(RecordList, {
        entity: "deals",
        rows: rows(1, 98),
        total: 100,
        loading: false,
        sort: null,
        direction: "desc",
        onSort: noop,
        onOpen: noop,
        onLoadMore: noop,
        hasMore: false,
      }),
    );
    expect(html).toContain("98 of 100");
    expect(html).not.toContain("Load more");
  });
});

describe("the search box (done-when 9, scope item 1)", () => {
  const bar = (over: Record<string, unknown> = {}) =>
    renderToStaticMarkup(
      createElement(FilterBar, {
        entity: "deals",
        view: view({ tab: "deals" }),
        onChange: noop,
        owners: null,
        fieldDefs: null,
        activeView: null,
        dirty: false,
        canEdit: false,
        isAdmin: false,
        saving: false,
        onSave: async () => undefined,
        onUpdate: async () => undefined,
        onReset: noop,
        ...over,
      }),
    );

  it("draws the box first on a list, marked for the shell", () => {
    const html = bar({ search: true });
    expect(html).toContain('data-page-filter="deals"');
    expect(html.indexOf("data-page-filter")).toBeLessThan(html.indexOf("Owner"));
  });

  it("draws no box on a board, which takes no q", () => {
    expect(bar({ search: false })).not.toContain("data-page-filter");
  });

  it("writes the query once, 300 ms after the last key", () => {
    vi.useFakeTimers();
    try {
      const commit = vi.fn();
      const box = searchCommitter(commit);
      box.type("a");
      vi.advanceTimersByTime(200);
      box.type("ac");
      vi.advanceTimersByTime(200);
      box.type("acme");
      vi.advanceTimersByTime(SEARCH_DELAY_MS - 1);
      expect(commit).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(commit).toHaveBeenCalledTimes(1);
      expect(commit).toHaveBeenCalledWith("acme");
      expect(SEARCH_DELAY_MS).toBe(300);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the search box keeps its text (visual review, 2026-10-12)", () => {
  // Measured in the browser: "printer ", a pause, then "order" gave
  // "printerorder". The URL holds the trimmed query (`serializeView`), so the
  // box took its own write for a write from outside and dropped the space.
  it("keeps a trailing space after its own write comes back trimmed", () => {
    let state = searchBoxStart("");
    state = { ...state, draft: "printer " };
    state = searchBoxWrote(state, "printer ");
    state = searchBoxSync(state, "printer");
    expect(state.draft).toBe("printer ");
  });

  it("writes nothing new for a pause after a space", () => {
    expect(searchChanges("printer ", "printer")).toBe(false);
    expect(searchChanges("printer o", "printer")).toBe(true);
  });

  it("takes a query it did not write: Back, or Clear", () => {
    let state = searchBoxStart("zephyr");
    state = { ...state, draft: "bosch" };
    state = searchBoxWrote(state, "bosch");
    state = searchBoxSync(state, "bosch");
    expect(state.draft).toBe("bosch");
    // Back.
    state = searchBoxSync(state, "zephyr");
    expect(state.draft).toBe("zephyr");
    // Clear.
    state = searchBoxSync(state, "");
    expect(state.draft).toBe("");
  });

  it("keeps a key typed while its own write was on the way", () => {
    let state = searchBoxStart("");
    state = searchBoxWrote({ ...state, draft: "ab" }, "ab");
    state = { ...state, draft: "abc" };
    state = searchBoxSync(state, "ab");
    expect(state.draft).toBe("abc");
  });
});

describe("the list footer (scope item 2)", () => {
  const list = (shown: number, total: number) =>
    renderToStaticMarkup(
      createElement(RecordList, {
        entity: "deals",
        rows: rows(1, shown),
        total,
        loading: false,
        sort: null,
        direction: "desc",
        onSort: noop,
        onOpen: noop,
        onLoadMore: noop,
      }),
    );

  it("reads shown of total, and offers Load more while rows are left", () => {
    const html = list(50, 120);
    expect(html).toContain("50 of 120");
    expect(html).toContain("Load more");
  });

  it("offers no Load more when every row shows", () => {
    expect(list(3, 3)).not.toContain("Load more");
  });

  it("draws the stage by name, and Last touched as a relative time", () => {
    const html = renderToStaticMarkup(
      createElement(RecordList, {
        entity: "deals",
        rows: [
          {
            id: "d1",
            name: "Deal",
            status_id: "s1",
            status_name: "Proposal",
            last_activity_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
          },
        ],
        total: 1,
        loading: false,
        sort: null,
        direction: "desc",
        onSort: noop,
        onOpen: noop,
        statuses: [
          { id: "s1", name: "Proposal", color: "blue", position: 0, type: "open", is_default: false },
        ],
      }),
    );
    expect(html).toContain("Proposal");
    expect(html).toContain("3d ago");
    expect(html).toContain("Last touched");
  });
});
