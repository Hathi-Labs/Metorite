// The Field map card (WS-53 CRM-Q7a, spec section 18.7).
// The runner has no DOM, so each component is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  NO_FIELDS,
  NO_TARGET,
  STALE_SENTENCE,
  actionsFor,
  columnLabel,
  pickOptions,
  ruleFor,
  ruleLine,
  statusTone,
  waitingCount,
  type FieldMap,
  type FieldMapField,
  type FieldRule,
} from "../lib/fieldMap";
import { FieldMapCardView, type FieldMapHandlers } from "./FieldMapCard";
import PipelineSettings from "./PipelineSettings";

const field = (key: string, targets: string[] = ["industry", "lost_note"]): FieldMapField => ({
  key,
  label: key.replace(/_/g, " "),
  type: "select",
  visible: true,
  targets,
});

const rule = (over: Partial<FieldRule> = {}): FieldRule => ({
  id: "r1",
  entity: "lead",
  field_api_name: "Sector",
  field_type: "select",
  profile_version: 3,
  target_column: "industry",
  status: "approved",
  proposed_by: "admin",
  confidence: null,
  reason: null,
  decided_by: "admin@x.test",
  decided_at: "2026-10-12T10:00:00Z",
  ...over,
});

const leadMap = (): FieldMap => ({
  entity: "lead",
  profile_version: 3,
  fields: [field("Sector"), field("Guess"), field("Changed"), field("Tags", [])],
  rules: [
    rule(),
    rule({
      id: "r2",
      field_api_name: "Guess",
      status: "proposed",
      proposed_by: "ai",
      confidence: 0.62,
      reason: "The labels name an industry.",
      target_column: "lost_note",
    }),
    rule({ id: "r3", field_api_name: "Changed", status: "stale" }),
  ],
  targets: [],
});

const handlers: FieldMapHandlers = {
  busy: null,
  onPick: () => undefined,
  onDecide: () => undefined,
};

const draw = (maps: Partial<Record<"leads" | "contacts" | "organizations" | "deals", FieldMap | null>>) =>
  renderToStaticMarkup(
    createElement(FieldMapCardView, {
      maps: { leads: null, contacts: null, organizations: null, deals: null, ...maps },
      handlers,
    }),
  );

describe("the actions on one field", () => {
  const f = field("Sector");

  it("offers a pick by hand when no rule names the field", () => {
    expect(actionsFor(null, f)).toEqual(["pick"]);
  });

  it("asks for approve or reject on a proposal", () => {
    expect(actionsFor(rule({ status: "proposed" }), f)).toEqual(["approve", "reject"]);
  });

  it("lets an approved rule only be rejected", () => {
    expect(actionsFor(rule(), f)).toEqual(["reject"]);
  });

  it("lets an admin decide again on a rejected or stale rule", () => {
    expect(actionsFor(rule({ status: "rejected" }), f)).toEqual(["approve", "pick"]);
    expect(actionsFor(rule({ status: "stale" }), f)).toEqual(["approve", "reject", "pick"]);
  });

  it("offers no pick for a field that no column can take", () => {
    expect(actionsFor(null, field("Tags", []))).toEqual([]);
    expect(actionsFor(rule({ status: "rejected" }), field("Tags", []))).toEqual(["approve"]);
  });
});

describe("the words of the card", () => {
  it("names a column the way a person reads it", () => {
    expect(columnLabel("no_of_employees")).toBe("No of employees");
    expect(columnLabel(null)).toBe("—");
  });

  it("offers only the columns that the gateway allows", () => {
    expect(pickOptions(field("Sector", ["industry"]))).toEqual([
      { value: "industry", label: "Industry" },
    ]);
  });

  it("says who proposed a rule, and why, with no confidence number (CRM-Q20-2)", () => {
    // Section 18.9.2: no confidence number reaches the screen. The rule
    // still holds `confidence: 0.62`, and the line leaves it out.
    expect(leadMap().rules[1].confidence).toBe(0.62);
    expect(ruleLine(leadMap().rules[1])).toBe(
      "Proposed by the assistant · The labels name an industry.",
    );
    expect(ruleLine(rule())).toBe("Picked by an admin");
  });

  it("counts the proposals and the stale rules", () => {
    expect(waitingCount([leadMap(), null])).toBe(2);
  });

  it("gives each status one tone", () => {
    expect(statusTone("approved")).toBe("success");
    expect(statusTone("stale")).toBe("warning");
    expect(statusTone("proposed")).toBe("primary");
    expect(statusTone("rejected")).toBe("neutral");
  });

  it("finds the rule of a field", () => {
    expect(ruleFor(leadMap(), "Guess")?.id).toBe("r2");
    expect(ruleFor(leadMap(), "Tags")).toBeNull();
  });
});

describe("the Field map card", () => {
  const html = draw({
    leads: leadMap(),
    contacts: { entity: "contact", profile_version: 3, fields: [], rules: [], targets: [] },
  });

  it("lists each entity, and the fields that go to custom", () => {
    for (const label of ["Leads", "Contacts", "Companies", "Deals"]) {
      expect(html).toContain(label);
    }
    expect(html).toContain("Sector");
    expect(html).toContain(NO_FIELDS);
  });

  it("shows each rule with its target and its status", () => {
    expect(html).toContain("Industry");
    expect(html).toContain("Approved");
    expect(html).toContain("Proposed");
    // CRM-Q20-2: no confidence number, in any form.
    expect(html).not.toMatch(/\d+\s*%|\bsure\b|0\.62/);
  });

  it("explains a stale rule", () => {
    expect(html).toContain(STALE_SENTENCE);
  });

  it("draws approve, reject and the pick by hand", () => {
    expect(html).toContain("Approve");
    expect(html).toContain("Reject");
    expect(html).toContain("Pick a target");
    expect(html).toContain(">Map<");
  });

  it("says so when no column can take a field", () => {
    expect(html).toContain(NO_TARGET);
  });
});

describe("the settings page", () => {
  const page = (over: Record<string, unknown>) =>
    renderToStaticMarkup(
      createElement(PipelineSettings, {
        showAdminCards: true,
        dealStatuses: [],
        leadStatuses: [],
        lostReasons: [],
        saving: false,
        onSaveStatus: async () => true,
        onRemoveStatus: async () => true,
        onReorderStatuses: async () => undefined,
        onSaveLostReason: async () => true,
        onRemoveLostReason: async () => true,
        onReorderLostReasons: async () => undefined,
        ...over,
      }),
    );

  it("draws the Field map for an admin in Mirror mode", () => {
    expect(page({ readOnly: true })).toContain("Field map");
  });

  it("hides it in Native mode and from a member", () => {
    expect(page({ readOnly: false })).not.toContain("Field map");
    expect(page({ readOnly: true, showAdminCards: false })).not.toContain("Field map");
  });
});
