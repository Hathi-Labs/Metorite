// WS-CRM CRM-M1: Settings → Change history (spec crm_conversational.md §8).
// The runner has no DOM, so the card renders to a string, and the wiring of
// the page is a scan of its source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TRY_AGAIN } from "../lib/api";
import {
  CHANGE_HISTORY_EMPTY,
  CHANGE_HISTORY_LOADING,
  changeLines,
  changeState,
  listState,
  moreLine,
  valueText,
  viaLabel,
  whoLabel,
  type ChangeRow,
} from "../lib/changeHistory";
import { SETTINGS_SECTIONS, sectionLabel, visibleSections } from "../lib/settingsSections";
import { ChangeHistoryView } from "./ChangeHistory";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

const RENAME: ChangeRow = {
  id: "5b0d4c64-1b7b-4f10-9a52-8f0f0b7a6a01",
  at: "2026-11-02T09:30:00+00:00",
  actor_email: "asha@fracktal.in",
  actor_name: "Asha Rao",
  via: "ui",
  approved_by: "asha@fracktal.in",
  ladder_level: "team",
  area: "deal_stage",
  area_label: "Deal stage",
  action: "change",
  entity_id: "9a1f0e1e-0000-4000-8000-000000000001",
  summary: 'Renamed the deal stage "Open" to "Qualify"',
  before: { name: "Open" },
  after: { name: "Qualify" },
  mode: "native",
  reversible: true,
  undoes_id: null,
  undone_by: null,
  can_undo: true,
};

const ids = (list: { id: string }[]) => list.map((s) => s.id);

const html = (props: Partial<Parameters<typeof ChangeHistoryView>[0]>) =>
  renderToStaticMarkup(
    createElement(ChangeHistoryView, {
      loading: false,
      error: null,
      onRetry: () => undefined,
      ...props,
    }),
  );

describe("the section", () => {
  it("is the last section, for an admin, in Native and Mirror mode", () => {
    const last = SETTINGS_SECTIONS[SETTINGS_SECTIONS.length - 1];
    expect(last).toEqual({
      id: "change-history",
      label: "Change history",
      icon: "History",
      audience: "admin",
      modes: ["native", "mirror"],
    });
    for (const mode of ["native", "mirror", null] as const) {
      expect(ids(visibleSections({ isAdmin: true, mode })).at(-1)).toBe("change-history");
      expect(ids(visibleSections({ isAdmin: false, mode }))).not.toContain("change-history");
    }
  });

  it("is drawn by the page, in its own branch, and not by PipelineSettings", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(/settingsSection === "change-history" \? \(/);
    expect(page).toContain("<ChangeHistoryCard />");
    expect(read("./PipelineSettings.tsx")).not.toContain("change-history");
    // The card heading is the label of the rail row (CRM-C1).
    expect(html({ loading: true })).toContain(`>${sectionLabel("change-history")}</h2>`);
  });

  it("reads the one route through lib/api.ts and sends no Undo in PR 1", () => {
    const apiSource = read("../lib/api.ts");
    expect(apiSource).toContain("call<ChangeList>(`/change-history?page=");
    expect(apiSource).not.toMatch(/change-history\/[^"`]*undo/);
    expect(read("./ChangeHistory.tsx")).not.toMatch(/>\s*Undo\s*</);
  });
});

describe("the words of one change", () => {
  it("names who, through what and each field before and after", () => {
    expect(whoLabel(RENAME)).toBe("Asha Rao");
    expect(whoLabel({ ...RENAME, actor_name: null })).toBe("asha@fracktal.in");
    expect(viaLabel("ui")).toBe("CRM settings");
    expect(viaLabel("whatsapp")).toBe("WhatsApp");
    expect(viaLabel("chat")).toBe("Chat");
    expect(changeLines(RENAME)).toEqual([
      { key: "name", label: "Name", before: "Open", after: "Qualify" },
    ]);
  });

  it("shows only the name of a created or a removed thing", () => {
    const created: ChangeRow = {
      ...RENAME, action: "create", before: null,
      after: { name: "Demo", type: "open", position: 30, probability: 10 },
    };
    expect(changeLines(created)).toEqual([
      { key: "name", label: "Name", before: "None", after: "Demo" },
    ]);
    const removed: ChangeRow = {
      ...RENAME, area: "pipeline_stage", action: "delete", after: null,
      before: { status_id: "x", position: 2, stage: "Demo" },
    };
    expect(changeLines(removed)).toEqual([
      { key: "stage", label: "Stage", before: "Demo", after: "Removed" },
    ]);
  });

  it("says the access rule, the mode and a win chance in words, and hides ids", () => {
    const access: ChangeRow = {
      ...RENAME, area: "access", before: { access: "admins" }, after: { access: "everyone" },
    };
    expect(changeLines(access)[0]).toMatchObject({
      label: "Who can open the CRM", before: "Admins only", after: "Everyone with CRM access",
    });
    expect(valueText("mode", null)).toBe("Not chosen");
    expect(valueText("mode", "mirror")).toBe("Zoho mirror");
    expect(valueText("probability", 40)).toBe("40%");
    expect(valueText("hidden", true)).toBe("Yes");
    expect(valueText("access_groups", ["group:sales"])).toBe("sales");
    expect(valueText("labels", { contacts: { label: "Sites" } })).toBe("Sites");
    expect(valueText("labels", {})).toBe("The default names");
    const view: ChangeRow = {
      ...RENAME, area: "view", before: { pipeline_id: null }, after: { pipeline_id: "abc" },
    };
    expect(changeLines(view)).toEqual([]);
  });

  it("marks an undone change, an Undo and a change that cannot be undone", () => {
    expect(changeState(RENAME)).toBeNull();
    expect(changeState({ ...RENAME, undone_by: "u" })).toBe("undone");
    expect(changeState({ ...RENAME, undoes_id: "u", reversible: false })).toBe("undo");
    expect(changeState({ ...RENAME, area: "setup", reversible: false })).toBe("final");
  });
});

describe("the three notices and the rows", () => {
  it("tells loading, a failed read and an empty history apart", () => {
    expect(listState({ loading: true, error: null })).toBe("loading");
    expect(listState({ loading: false, error: "x" })).toBe("failed");
    expect(listState({ data: { rows: [], total: 0 }, loading: false, error: null })).toBe("empty");
    expect(listState({ data: { rows: [RENAME], total: 1 }, loading: true, error: "x" })).toBe("rows");
    const loading = html({ loading: true });
    const failed = html({ error: TRY_AGAIN });
    const empty = html({ data: { rows: [], total: 0 } });
    expect(loading).toContain(CHANGE_HISTORY_LOADING);
    expect(failed).toContain(TRY_AGAIN);
    expect(failed).toContain(">Retry</button>");
    expect(failed).toContain('role="alert"');
    expect(empty).toContain(CHANGE_HISTORY_EMPTY);
    expect(new Set([loading, failed, empty]).size).toBe(3);
  });

  it("draws each row with when, who, through what and the fields", () => {
    const rows = html({ data: { rows: [RENAME], total: 1 } });
    expect(rows).toContain("Renamed the deal stage &quot;Open&quot; to &quot;Qualify&quot;");
    expect(rows).toContain("Asha Rao · CRM settings · Deal stage");
    expect(rows).toMatch(/Name:<\/dt>.*Open.*→.*Qualify/);
    expect(rows).not.toContain(RENAME.id);
    expect(rows).not.toContain(RENAME.entity_id as string);
    expect(rows).not.toMatch(/server|gateway|\b(4|5)\d\d\b/i);
  });

  it("says how many it shows when the history holds more", () => {
    expect(moreLine(50, 50)).toBeNull();
    expect(moreLine(50, 120)).toBe("Showing the 50 newest of 120 changes.");
    const more = html({ data: { rows: [RENAME], total: 3 }, onMore: () => undefined });
    expect(more).toContain("Showing the 1 newest of 3 changes.");
    expect(more).toContain(">Show more</button>");
  });
});
