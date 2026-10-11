import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CrmView } from "./urlState";

// ── The store's reads for the Leads board and the related lists (CRM-Q3) ──
//
// `api` is mocked: these are the store's routing decisions. Which request a
// board load issues, which filter each related list sends, and what a refused
// drop puts back.

vi.mock("./api", () => ({
  CrmError: class CrmError extends Error {},
  listStatuses: vi.fn(async () => []),
  listRecords: vi.fn(async () => ({ rows: [], total: 0 })),
  patchRecord: vi.fn(async () => ({ id: "l1" })),
  getRecord: vi.fn(async () => ({ id: "x" })),
  getTimeline: vi.fn(async () => ({ entries: [] })),
  listDealContacts: vi.fn(async () => ({ rows: [] })),
  getPipeline: vi.fn(async () => ({ lanes: [] })),
}));

import * as api from "./api";
import { useCrmStore } from "./store";

const LEADS_BOARD: CrmView = {
  tab: "leads",
  layout: "board",
  pipelineId: null,
  record: null,
  q: "",
  statusId: null,
  includeConverted: false,
  owner: null,
  sort: null,
  dir: "desc",
};

const statuses = [
  { id: "s1", name: "New", color: "blue", position: 0, type: "open", is_default: true },
  { id: "s2", name: "Contacted", color: "blue", position: 1, type: "ongoing", is_default: false },
];

const lead = (id: string, status: string) => ({ id, lead_name: id, status_id: status });

beforeEach(() => {
  vi.clearAllMocks();
  useCrmStore.setState({ leadLanes: [], leadStatuses: [], error: null, saving: false, lastView: null });
});

describe("the Leads board load", () => {
  it("reads one page of leads for each Lead Status, with the status total as the count", async () => {
    vi.mocked(api.listStatuses).mockResolvedValueOnce(statuses as never);
    vi.mocked(api.listRecords).mockImplementation((async (_e: string, q: { status_id?: string }) =>
      q.status_id === "s1"
        ? { rows: [lead("l1", "s1")], total: 9 }
        : { rows: [], total: 0 }) as never);

    await useCrmStore.getState().loadLeadBoard(LEADS_BOARD);

    const calls = vi.mocked(api.listRecords).mock.calls;
    expect(calls.map((c) => c[0])).toEqual(["leads", "leads"]);
    expect(calls.map((c) => c[1]?.status_id).sort()).toEqual(["s1", "s2"]);
    const lanes = useCrmStore.getState().leadLanes;
    expect(lanes.map((l) => l.status.name)).toEqual(["New", "Contacted"]);
    expect(lanes[0]).toMatchObject({ count: 9 });
    expect(lanes[0].rows).toHaveLength(1);
    expect(lanes[1]).toMatchObject({ count: 0, rows: [] });
  });

  it("scopes the board to an owner when the view has one", async () => {
    useCrmStore.setState({ leadStatuses: statuses as never });
    await useCrmStore.getState().loadLeadBoard({ ...LEADS_BOARD, owner: "vj@x.in" });
    expect(vi.mocked(api.listRecords).mock.calls.every((c) => c[1]?.owner === "vj@x.in")).toBe(true);
  });

  it("re-reads the Leads board, not the deals board, after a write", async () => {
    useCrmStore.setState({ leadStatuses: statuses as never, lastView: LEADS_BOARD });
    await useCrmStore.getState().refreshCollection();
    expect(api.getPipeline).not.toHaveBeenCalled();
    expect(api.listRecords).toHaveBeenCalled();
  });

  it("puts the lead back and says why when the move is refused", async () => {
    useCrmStore.setState({ leadStatuses: statuses as never, lastView: LEADS_BOARD });
    vi.mocked(api.listRecords).mockImplementation((async (_e: string, q: { status_id?: string }) =>
      q.status_id === "s1" ? { rows: [lead("l1", "s1")], total: 1 } : { rows: [], total: 0 }) as never);
    await useCrmStore.getState().loadLeadBoard(LEADS_BOARD);
    const before = useCrmStore.getState().leadLanes;
    vi.mocked(api.patchRecord).mockRejectedValueOnce(new Error("refused"));

    await useCrmStore
      .getState()
      .moveLead({ dealId: "l1", fromStatusId: "s1", toStatusId: "s2" });

    expect(useCrmStore.getState().leadLanes).toBe(before);
    expect(useCrmStore.getState().error).toBe("refused");
  });

  it("moves a lead with a PATCH of status_id on the leads route", async () => {
    useCrmStore.setState({ leadStatuses: statuses as never, lastView: LEADS_BOARD });
    await useCrmStore
      .getState()
      .moveLead({ dealId: "l1", fromStatusId: "s1", toStatusId: "s2" }, { lost_note: "x" });
    expect(api.patchRecord).toHaveBeenCalledWith("leads", "l1", { status_id: "s2", lost_note: "x" });
  });
});

describe("the related lists", () => {
  it("asks for a company's contacts and deals by company_id", async () => {
    await useCrmStore.getState().loadRecord("organizations", "o1");
    const calls = vi.mocked(api.listRecords).mock.calls.map((c) => [c[0], c[1]?.company_id]);
    expect(calls).toContainEqual(["contacts", "o1"]);
    expect(calls).toContainEqual(["deals", "o1"]);
  });

  it("asks for a contact's deals by contact_id and no more", async () => {
    await useCrmStore.getState().loadRecord("contacts", "c1");
    const calls = vi.mocked(api.listRecords).mock.calls;
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("deals");
    expect(calls[0][1]).toMatchObject({ contact_id: "c1" });
  });

  it("reads no related list for a deal or a lead", async () => {
    await useCrmStore.getState().loadRecord("deals", "d1");
    await useCrmStore.getState().loadRecord("leads", "l1");
    expect(api.listRecords).not.toHaveBeenCalled();
  });

  it("holds the rows, and empties them when the next record opens", async () => {
    vi.mocked(api.listRecords).mockResolvedValue({ rows: [{ id: "d9", name: "Bosch" }], total: 1 } as never);
    await useCrmStore.getState().loadRecord("contacts", "c1");
    expect(useCrmStore.getState().related.deals).toHaveLength(1);
    vi.mocked(api.listRecords).mockClear();
    await useCrmStore.getState().loadRecord("leads", "l1");
    expect(useCrmStore.getState().related).toEqual({ contacts: [], deals: [] });
  });

  it("leaves the lists empty and raises no banner when the read fails", async () => {
    vi.mocked(api.listRecords).mockRejectedValue(new Error("boom"));
    await useCrmStore.getState().loadRecord("organizations", "o1");
    expect(useCrmStore.getState().related).toEqual({ contacts: [], deals: [] });
    expect(useCrmStore.getState().error).toBeNull();
  });
});
