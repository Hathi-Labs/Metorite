import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import TriggerPanel from "../components/TriggerPanel";
import {
  addEvent,
  cleanTriggers,
  eventIndexes,
  patchAt,
  removeAt,
  setEventType,
  setFilterKey,
  setSource,
} from "./eventTriggers";
import type { TriggerSpec } from "./types";

const zoho: TriggerSpec = { kind: "event", enabled: true, config: { source: "zoho", event_type: "Contacts.edit" } };
const studio: TriggerSpec = { kind: "event", enabled: true, config: { source: "studio" } };
const sched: TriggerSpec = { kind: "schedule", enabled: true, config: { cron: "0 9 * * *" } };

describe("several event triggers", () => {
  const list = [zoho, sched, studio];

  it("lists every event trigger by place", () => {
    expect(eventIndexes(list)).toEqual([0, 2]);
  });

  it("edits one event and keeps every other trigger", () => {
    const next = setEventType(list, 2, "studio.record.created");
    expect(next).toHaveLength(3);
    expect(next[0]).toEqual(zoho);
    expect(next[1]).toEqual(sched);
    expect(next[2].config.event_type).toBe("studio.record.created");
  });

  it("adds a second event and removes one without touching the rest", () => {
    const added = addEvent(list);
    expect(eventIndexes(added)).toEqual([0, 2, 3]);
    expect(removeAt(added, 0)).toEqual([sched, studio, added[3]]);
    expect(patchAt(list, 0, { enabled: false })[2]).toEqual(studio);
  });
});

describe("Studio filter", () => {
  it("saves only the keys that have a value", () => {
    let l: TriggerSpec[] = [studio];
    l = setFilterKey(l, 0, "package_id", "pkg_1");
    l = setFilterKey(l, 0, "object", "");
    l = setFilterKey(l, 0, "op", "create");
    expect(l[0].config.filter).toEqual({ package_id: "pkg_1", op: "create" });
  });

  it("leaves out an empty filter", () => {
    let l: TriggerSpec[] = [studio];
    l = setFilterKey(l, 0, "op", "update");
    l = setFilterKey(l, 0, "op", "  ");
    expect("filter" in l[0].config).toBe(false);
  });

  it("trims each value on save and drops a blank one", () => {
    const l: TriggerSpec[] = [
      { ...studio, config: { source: "studio", filter: { package_id: " pkg ", object: "   " } } },
      { ...studio, config: { source: "studio", filter: { object: " " } } },
    ];
    const out = cleanTriggers(l);
    expect(out[0].config.filter).toEqual({ package_id: "pkg" });
    expect("filter" in out[1].config).toBe(false);
  });

  it("drops the filter when the source leaves Studio", () => {
    const l: TriggerSpec[] = [
      { ...studio, config: { source: "studio", event_type: "studio.record.created", filter: { op: "create" } } },
    ];
    const out = setSource(l, 0, "zoho");
    expect(out[0].config).toEqual({ source: "zoho" });
  });

  it("leaves a blank Studio event type out", () => {
    const l = setEventType([{ ...studio, config: { source: "studio", event_type: "studio.record.created" } }], 0, "");
    expect("event_type" in l[0].config).toBe(false);
  });
});

describe("TriggerPanel", () => {
  const html = (triggers: TriggerSpec[], error?: string) =>
    renderToStaticMarkup(
      createElement(TriggerPanel, { triggers, published: true, onChange: () => {}, onClose: () => {}, error }),
    );

  it("offers Studio records and does not call it retired", () => {
    const out = html([studio]);
    expect(out).toContain("Studio records");
    expect(out).not.toContain("(retired)");
    expect(out).toContain("Any record event");
  });

  it("draws one row for each event trigger", () => {
    const out = html([zoho, studio, { ...zoho }]);
    expect(out.match(/data-testid="event-trigger"/g)).toHaveLength(3);
  });

  it("still shows a retired source as retired", () => {
    const out = html([{ kind: "event", enabled: true, config: { source: "clickup" } }]);
    expect(out).toContain("(retired) clickup");
  });

  it("shows the refusal of a save in full", () => {
    const msg = "Only the author of this workflow can add or change a Studio record trigger.";
    expect(html([studio], msg)).toContain(msg);
  });
});
