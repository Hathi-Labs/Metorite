// The timeline on a module record (WS-53 CRM-Q23c, `crm_platform.md`
// section 18.10). The runner has no DOM, so each part is rendered to a string
// and read as text.
//
// Done when:
// 1. `ModuleRecordSheet.tsx` shows the timeline of CRM-Q23a, read only.
// 2. `Timeline.tsx` gets a read-only mode with no composer. `lib/api.ts` and
//    `lib/modules.ts` get the fetch.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import * as api from "../lib/api";
import {
  moduleTabs,
  moduleTimelineKey,
  moduleTimelineState,
  type ModuleRecordPage,
} from "../lib/modules";
import type { TimelineEntry } from "../lib/types";
import { ModuleRecordBody, ModuleTimeline } from "./ModuleRecordSheet";
import Timeline, { READ_ONLY_EMPTY } from "./Timeline";

const noop = () => undefined;

const PAGE: ModuleRecordPage = {
  module: { api_name: "Operator_Associates", label: "Operator Associates", singular_label: "Operator", count: 3 },
  record: {
    id: "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001",
    module: "Operator_Associates",
    name: "Ravi Kumar",
    owner_email: null,
    fields: { Name: "Ravi Kumar" },
    ext_source: "zoho",
    ext_id: "z1",
    ext_url: null,
    ext_created_at: null,
    ext_modified_at: null,
  },
  field_defs: [],
  links: {},
};

const NOTE: TimelineEntry = {
  kind: "activity",
  at: "2026-10-16T09:00:00Z",
  origin: "own",
  activity: {
    id: "a1",
    type: "note",
    body: "Visited the site. The press runs two shifts.",
    occurred_at: "2026-10-16T09:00:00Z",
    created_by: "ravi@dewin.test",
    ext_source: "zoho",
  },
};

const TASK: TimelineEntry = {
  kind: "activity",
  at: "2026-10-15T09:00:00Z",
  origin: "own",
  activity: {
    id: "a2",
    type: "task",
    subject: "Send the quote",
    due_at: "2026-10-20T00:00:00Z",
    completed_at: null,
    ext_source: "zoho",
  },
};

const draw = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(el);

describe("item 2: the fetch of the timeline of a module record", () => {
  it("reads the CRM-Q23a route through the BFF, with the names encoded", async () => {
    const calls: string[] = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return { ok: true, status: 200, text: async () => JSON.stringify({ entries: [NOTE] }) } as Response;
    }) as typeof fetch;
    try {
      const body = await api.getModuleTimeline("Operator_Associates", "id 1");
      expect(body.entries).toHaveLength(1);
    } finally {
      globalThis.fetch = original;
    }
    expect(calls).toEqual(["/api/crm/modules/Operator_Associates/records/id%201/timeline?limit=100"]);
  });

  it("keys the read by module and record, under the CRM prefix that each write wakes", () => {
    expect(moduleTimelineKey("Quotes", "r1")).toBe("crm:module-timeline:Quotes:r1");
  });

  it("tells an answer, a wait and a failure apart", () => {
    expect(moduleTimelineState({ entries: [NOTE] }, null)).toEqual({ kind: "entries", entries: [NOTE] });
    expect(moduleTimelineState({ entries: [] }, null)).toEqual({ kind: "entries", entries: [] });
    expect(moduleTimelineState(undefined, null)).toEqual({ kind: "loading" });
    expect(moduleTimelineState(undefined, "Gateway error 502")).toEqual({ kind: "failed" });
    // A good answer wins over the error of a later re-read.
    expect(moduleTimelineState({ entries: [NOTE] }, "Gateway error 502").kind).toBe("entries");
  });
});

describe("item 2: the read-only mode of the timeline", () => {
  const html = draw(createElement(Timeline, { entries: [NOTE, TASK], readOnly: true }));

  it("draws no composer", () => {
    expect(html).not.toContain("<textarea");
    expect(html).not.toContain("What happened?");
    expect(html).not.toContain(">Log<");
  });

  it("draws each entry, and the filter of the stream", () => {
    expect(html).toContain("Visited the site. The press runs two shifts.");
    expect(html).toContain("Send the quote");
    expect(html).toContain("All");
  });

  it("shows the state of a task with no control", () => {
    expect(html).not.toContain("Complete task");
    expect(html).toContain('aria-label="The task is open"');
  });

  it("says that the record has no activity", () => {
    expect(draw(createElement(Timeline, { entries: [], readOnly: true }))).toContain(READ_ONLY_EMPTY);
  });

  it("keeps the composer and the task control of a known record", () => {
    const full = draw(
      createElement(Timeline, { entries: [TASK], onLog: noop, onToggleTask: noop, saving: false }),
    );
    expect(full).toContain("<textarea");
    expect(full).toContain("Complete task");
  });
});

describe("item 1: the record page of a module shows the timeline, read only", () => {
  it("offers the Timeline tab on each module record", () => {
    expect(moduleTabs(PAGE)).toContain("timeline");
  });

  it("draws the entries on the Timeline tab, with no composer", () => {
    const html = draw(
      createElement(ModuleRecordBody, {
        page: PAGE,
        tab: "timeline",
        onTab: noop,
        onOpenLink: noop,
        timeline: { kind: "entries", entries: [NOTE] },
      }),
    );
    expect(html).toContain("Timeline");
    expect(html).toContain("Visited the site.");
    expect(html).not.toContain("<textarea");
  });

  it("draws a skeleton while the read waits, and a retry after a failure", () => {
    const wait = draw(createElement(ModuleTimeline, { state: { kind: "loading" }, onRetry: noop }));
    expect(wait).toContain('aria-busy="true"');
    const failed = draw(createElement(ModuleTimeline, { state: { kind: "failed" }, onRetry: noop }));
    expect(failed).toContain("The timeline did not load. Try again.");
    expect(failed).toContain("Retry");
  });

  it("reads the timeline only while its tab is on screen", () => {
    const source = readFileSync(new URL("./ModuleRecordSheet.tsx", import.meta.url), "utf-8");
    expect(source).toMatch(/wantsTimeline \? moduleTimelineKey\(module, id\) : null/);
    expect(source).toMatch(/api\.getModuleTimeline\(module, id\)/);
    // No write path reaches a module record.
    expect(source).not.toMatch(/logActivity|completeTask|onLog=/);
  });
});
