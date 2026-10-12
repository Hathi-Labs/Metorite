import { afterEach, describe, expect, it } from "vitest";

import * as api from "./api";
import { listQuery, queryString } from "./filters";
import {
  REMEMBER_KEY,
  readRemembered,
  remember,
  resolvePipeline,
  showPicker,
  stagesOf,
} from "./pipelines";
import type { DealPipeline } from "./types";
import { DEFAULT_VIEW, selectLayout, selectPipeline, selectTab, viewHref, parseView } from "./urlState";

// ── The pipeline picker and its rules (CRM-Q3) ─────────────────────────────

const stage = (id: string, position: number) => ({
  id,
  name: id,
  position,
  type: "open" as const,
  probability: 10,
  forecast_category: null,
});

const pipeline = (id: string, over: Partial<DealPipeline> = {}): DealPipeline => ({
  id,
  name: id.toUpperCase(),
  is_default: false,
  position: 0,
  ext_url: null,
  stages: [stage(`${id}-b`, 2), stage(`${id}-a`, 1)],
  ...over,
});

const ONE = [pipeline("p1", { is_default: true })];
const TWO = [pipeline("p1", { is_default: true, position: 0 }), pipeline("p2", { position: 1 })];

describe("when the picker shows", () => {
  it("hides for no pipeline list, an empty list, and one pipeline", () => {
    expect(showPicker(null)).toBe(false);
    expect(showPicker(undefined)).toBe(false);
    expect(showPicker([])).toBe(false);
    expect(showPicker(ONE)).toBe(false);
  });

  it("shows for two pipelines or more", () => {
    expect(showPicker(TWO)).toBe(true);
    expect(showPicker([...TWO, pipeline("p3")])).toBe(true);
  });
});

describe("which pipeline a request filters by", () => {
  it("sends none with fewer than two pipelines, whatever was remembered", () => {
    expect(resolvePipeline(null, "p2", "p2")).toBeNull();
    expect(resolvePipeline(ONE, "p1", "p1")).toBeNull();
  });

  it("takes the URL first, then the remembered one, then the default", () => {
    expect(resolvePipeline(TWO, "p2", "p1")).toBe("p2");
    expect(resolvePipeline(TWO, null, "p2")).toBe("p2");
    expect(resolvePipeline(TWO, null, null)).toBe("p1");
  });

  it("ignores an id that names no pipeline", () => {
    expect(resolvePipeline(TWO, "gone", "p2")).toBe("p2");
    expect(resolvePipeline(TWO, "gone", "also-gone")).toBe("p1");
  });

  it("lists the stages of a pipeline in their order", () => {
    expect(stagesOf(TWO[0]).map((s) => s.id)).toEqual(["p1-a", "p1-b"]);
  });
});

describe("the remembered pipeline", () => {
  it("round-trips through a store", () => {
    const data = new Map<string, string>();
    const store = {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    };
    remember("p2", store);
    expect(data.get(REMEMBER_KEY)).toBe("p2");
    expect(readRemembered(store)).toBe("p2");
  });

  it("survives a store that throws and a store that is missing", () => {
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(() => remember("p2", broken)).not.toThrow();
    expect(readRemembered(broken)).toBeNull();
    expect(() => remember("p2", null)).not.toThrow();
    expect(readRemembered(null)).toBeNull();
  });
});

describe("the pipeline in the request and the URL", () => {
  it("filters the deals list by pipeline and no other entity", () => {
    const view = { ...DEFAULT_VIEW, pipelineId: "p2" };
    expect(listQuery("deals", view).pipeline_id).toBe("p2");
    expect(listQuery("leads", view).pipeline_id).toBeUndefined();
    expect(listQuery("contacts", view).pipeline_id).toBeUndefined();
    expect(queryString(listQuery("deals", view))).toContain("pipeline_id=p2");
  });

  it("sends no pipeline_id when there is none", () => {
    expect(listQuery("deals", DEFAULT_VIEW).pipeline_id).toBeUndefined();
  });

  it("carries the choice in the URL for Deals only", () => {
    const picked = selectPipeline(DEFAULT_VIEW, "p2");
    expect(viewHref(picked)).toBe("/crm?pipeline=p2");
    expect(parseView("?pipeline=p2").pipelineId).toBe("p2");
    expect(viewHref(selectTab(picked, "leads"))).toBe("/crm?tab=leads");
  });
});

describe("the list or board switch", () => {
  it("flips Deals to a list and back, and writes only the non-default layout", () => {
    const list = selectLayout(DEFAULT_VIEW, "list");
    expect(list.layout).toBe("list");
    expect(viewHref(list)).toBe("/crm?layout=list");
    expect(selectLayout(list, "board")).toMatchObject({ layout: "board" });
    expect(viewHref(selectLayout(list, "board"))).toBe("/crm");
  });

  it("gives Leads a board, and a list by default", () => {
    const leads = selectTab(DEFAULT_VIEW, "leads");
    expect(leads.layout).toBe("list");
    const board = selectLayout(leads, "board");
    expect(viewHref(board)).toBe("/crm?tab=leads&layout=board");
    expect(parseView("tab=leads&layout=board").layout).toBe("board");
  });

  it("does not give Contacts a board", () => {
    const contacts = selectTab(DEFAULT_VIEW, "contacts");
    expect(selectLayout(contacts, "board")).toBe(contacts);
  });

  it("drops a status filter when the board takes over", () => {
    const list = { ...selectTab(DEFAULT_VIEW, "leads"), statusId: "s1" };
    expect(selectLayout(list, "board").statusId).toBeNull();
  });
});

// ── GET /api/crm/pipelines, and its 404 ───────────────────────────────────

type Fetch = typeof globalThis.fetch;

function stub(status: number, body: unknown) {
  const calls: string[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  }) as Fetch;
  return { calls, restore: () => (globalThis.fetch = original) };
}

describe("the pipelines read", () => {
  let restore = () => {};
  afterEach(() => restore());

  it("returns the list on 200", async () => {
    const s = stub(200, TWO);
    restore = s.restore;
    expect(await api.listPipelines()).toEqual(TWO);
    expect(s.calls).toEqual(["/api/crm/pipelines"]);
  });

  it("returns null on 404, so the CRM has one implicit pipeline", async () => {
    const s = stub(404, { detail: "Not Found" });
    restore = s.restore;
    const result = await api.listPipelines();
    expect(result).toBeNull();
    expect(showPicker(result)).toBe(false);
    expect(resolvePipeline(result, null, "p2")).toBeNull();
  });

  it("asks the board for one pipeline, and for none when there is none", async () => {
    const s = stub(200, { lanes: [] });
    restore = s.restore;
    await api.getPipeline(null, "p2");
    await api.getPipeline("vj@x.in", "p2");
    await api.getPipeline();
    expect(s.calls).toEqual([
      "/api/crm/pipeline?pipeline_id=p2",
      "/api/crm/pipeline?owner=vj%40x.in&pipeline_id=p2",
      "/api/crm/pipeline",
    ]);
  });
});
