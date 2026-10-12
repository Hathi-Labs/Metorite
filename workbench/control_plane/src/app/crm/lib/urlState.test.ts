import { describe, expect, it } from "vitest";
import {
  applySort,
  closeRecord,
  DEFAULT_VIEW,
  openRecord,
  parseView,
  selectTab,
  serializeView,
  viewHref,
} from "./urlState";

describe("parseView", () => {
  it("lands on the Deals board with nothing in the URL", () => {
    expect(parseView("")).toEqual(DEFAULT_VIEW);
  });

  it("opens the record sheet from a deep link", () => {
    // The done-when: `?deal=<id>` opens the sheet OVER the list. Live
    // rendering is owner-verified once migration 144 applies; that the URL
    // resolves to the right record and collection is decided here.
    const view = parseView("?deal=abc-123");
    expect(view.record).toEqual({
      param: "deal",
      entity: "deals",
      id: "abc-123",
    });
  });

  it("maps each sheet parameter onto its collection", () => {
    expect(parseView("?lead=1").record?.entity).toBe("leads");
    expect(parseView("?contact=1").record?.entity).toBe("contacts");
    expect(parseView("?organization=1").record?.entity).toBe("organizations");
  });

  it("resolves a hand-edited URL naming two records, in a stated order", () => {
    // Beats showing whichever one URLSearchParams iterated first, and beats
    // showing none — a link that opens nothing looks broken to the sender.
    expect(parseView("?contact=c1&deal=d1").record?.param).toBe("deal");
  });

  it("falls back to Deals for a tab that does not exist", () => {
    expect(parseView("?tab=invoices").tab).toBe("deals");
    expect(parseView("?tab=leads").tab).toBe("leads");
  });

  it("admits the settings tab, which is not a collection", () => {
    // WS-26f f2. The old `isEntity(tab) ? tab : "board"` SWALLOWED it: a
    // `?tab=settings` link landed on the kanban, which is indistinguishable
    // from the page ignoring the click.
    expect(parseView("?tab=settings").tab).toBe("settings");
  });

  it("admits the reports tab on the same grammar", () => {
    // WS-26g. The second non-collection tab, and the reason `isTab` is its own
    // predicate rather than a ternary that only knew about `board`.
    expect(parseView("?tab=reports").tab).toBe("reports");
  });

  it("keeps the record sheet open over the reports tab", () => {
    // `?tab=reports&deal=<id>` is a legitimate link — "this stage is where we
    // are losing them, look at this deal" — and it only works because the tab
    // is URL state rather than a route of its own.
    const view = parseView("?tab=reports&deal=d1");
    expect(view.tab).toBe("reports");
    expect(view.record).toEqual({ param: "deal", entity: "deals", id: "d1" });
  });

  it("reads the list filters", () => {
    const view = parseView("?tab=leads&q=bosch&status=s1&converted=1&owner=vj@x.in");
    expect(view).toMatchObject({
      tab: "leads",
      q: "bosch",
      statusId: "s1",
      includeConverted: true,
      owner: "vj@x.in",
    });
  });

  it("treats an empty parameter as absent, not as a filter on ''", () => {
    expect(parseView("?status=&owner=").statusId).toBeNull();
    expect(parseView("?status=&owner=").owner).toBeNull();
  });
});

describe("serializeView", () => {
  it("writes nothing for the default view", () => {
    // A URL that spells out its defaults is unreadable, and it makes two
    // identical views produce two different history entries.
    expect(serializeView(DEFAULT_VIEW)).toBe("");
    expect(viewHref(DEFAULT_VIEW)).toBe("/crm");
  });

  it("round-trips every field it writes", () => {
    const view = {
      ...DEFAULT_VIEW,
      tab: "leads" as const,
      q: "bosch",
      statusId: "s1",
      includeConverted: true,
      owner: "vj@fracktal.in",
      record: { param: "lead" as const, entity: "leads" as const, id: "l1" },
    };
    expect(parseView(serializeView(view))).toEqual(view);
  });

  it("trims the search box", () => {
    expect(serializeView({ ...DEFAULT_VIEW, q: "   " })).toBe("");
    expect(serializeView({ ...DEFAULT_VIEW, q: " bosch " })).toBe("layout=board&q=bosch");
  });
});

describe("the record sheet is state, not a route", () => {
  it("keeps the list underneath when a record opens", () => {
    // The whole reason it is `?deal=` and not `/crm/deals/<id>`: the reader
    // comes back to the list they were looking at, and Back closes the sheet
    // instead of leaving the app.
    const list = { ...DEFAULT_VIEW, tab: "leads" as const, layout: "list" as const, q: "bosch" };
    const opened = openRecord(list, "lead", "l1");
    expect(opened.tab).toBe("leads");
    expect(opened.q).toBe("bosch");
    expect(viewHref(opened)).toBe("/crm?tab=leads&q=bosch&lead=l1");
  });

  it("closing leaves the list exactly as it was", () => {
    const list = { ...DEFAULT_VIEW, tab: "leads" as const, layout: "list" as const, q: "bosch" };
    expect(closeRecord(openRecord(list, "lead", "l1"))).toEqual(list);
  });
});

describe("selectTab", () => {
  it("drops the filters that do not travel between collections", () => {
    // A status lane from the deal pipeline means nothing on the leads list,
    // and carrying it across would show an empty list with no visible cause.
    const view = {
      ...DEFAULT_VIEW,
      tab: "deals" as const,
      statusId: "s1",
      includeConverted: true,
      q: "bosch",
      record: { param: "deal" as const, entity: "deals" as const, id: "d1" },
    };
    const next = selectTab(view, "contacts");
    expect(next).toMatchObject({
      tab: "contacts",
      statusId: null,
      includeConverted: false,
      record: null,
    });
    // The search box survives: it means the same thing everywhere.
    expect(next.q).toBe("bosch");
  });

  it("is a no-op for the tab already showing", () => {
    const view = { ...DEFAULT_VIEW, tab: "leads" as const, statusId: "s1" };
    expect(selectTab(view, "leads")).toBe(view);
  });

  it("reaches the settings tab and comes back", () => {
    const list = {
      ...DEFAULT_VIEW,
      tab: "deals" as const,
      statusId: "s1",
      record: { param: "deal" as const, entity: "deals" as const, id: "d1" },
    };
    const settings = selectTab(list, "settings");
    expect(settings).toMatchObject({
      tab: "settings",
      statusId: null,
      record: null,
    });
    // …and it is a shareable URL like every other view here.
    expect(viewHref(settings)).toBe("/crm?tab=settings");
    expect(parseView(serializeView(settings))).toEqual(settings);
    expect(selectTab(settings, "deals").tab).toBe("deals");

    // WS-26g's tab round-trips the same way — it is one more thing this page
    // can be showing, not a second app.
    const reports = selectTab(list, "reports");
    expect(reports).toMatchObject({ tab: "reports", statusId: null, record: null });
    expect(viewHref(reports)).toBe("/crm?tab=reports");
    expect(parseView(serializeView(reports))).toEqual(reports);
  });

  it("clears the sort, because the keys are a per-entity allowlist", () => {
    // Carrying `amount` from deals onto leads would not sort oddly — it is a
    // 422 that empties the list.
    const view = {
      ...DEFAULT_VIEW,
      tab: "deals" as const,
      sort: "amount",
      dir: "asc" as const,
    };
    expect(selectTab(view, "leads")).toMatchObject({ sort: null, dir: "desc" });
  });
});

describe("applySort", () => {
  it("starts a new column descending", () => {
    // Every default here is newest / biggest / most recently touched;
    // ascending on the first click opens on the oldest and smallest rows.
    expect(applySort(DEFAULT_VIEW, "amount")).toMatchObject({
      sort: "amount",
      dir: "desc",
    });
  });

  it("flips direction on the column already sorted", () => {
    const once = applySort(DEFAULT_VIEW, "amount");
    const twice = applySort(once, "amount");
    expect(twice.dir).toBe("asc");
    expect(applySort(twice, "amount").dir).toBe("desc");
  });

  it("resets direction when a different column is picked", () => {
    const ascending = applySort(applySort(DEFAULT_VIEW, "amount"), "amount");
    expect(applySort(ascending, "name")).toMatchObject({
      sort: "name",
      dir: "desc",
    });
  });

  it("puts the sort in the URL, so a sorted list is shareable", () => {
    const view = applySort({ ...DEFAULT_VIEW, tab: "deals" }, "amount");
    expect(viewHref(view)).toBe("/crm?layout=board&sort=amount");
    expect(parseView(serializeView(view))).toEqual(view);

    const ascending = applySort(view, "amount");
    expect(viewHref(ascending)).toBe("/crm?layout=board&sort=amount&dir=asc");
    expect(parseView(serializeView(ascending))).toEqual(ascending);
  });

  it("writes no dir without a sort, and reads a nonsense one as desc", () => {
    expect(serializeView({ ...DEFAULT_VIEW, dir: "asc" })).toBe("");
    expect(parseView("?sort=name&dir=sideways").dir).toBe("desc");
  });
});
