import { describe, expect, it } from "vitest";
import {
  applyPatches,
  backfillRan,
  changedFields,
  CLAMPED_PROBABILITY,
  moveItem,
  POSITION_STEP,
  renumber,
  reorder,
  reorderFailureMessage,
  STATUS_TYPE_LABELS,
  validateLostReasonDraft,
  validateStatusDraft,
} from "./settings";
import type {
  ClosedAtBackfill,
  StageMetadataReport,
  StatusType,
} from "./types";

// The admin API has no bulk reorder — it has a per-row PATCH — so what a drop
// SENDS is decided here (WS-26f f2, done-when 5).

function lane(id: string, position: number) {
  return { id, position };
}

const LANES = [
  lane("qualification", 10),
  lane("proposal", 20),
  lane("negotiation", 30),
  lane("won", 40),
];

describe("moveItem", () => {
  it("moves one item and leaves the rest in order", () => {
    expect(moveItem(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
  });

  it("is a no-op for a drop that changes nothing", () => {
    const items = ["a", "b", "c"];
    expect(moveItem(items, 1, 1)).toBe(items);
  });

  it("is a no-op for an index that does not exist", () => {
    // An HTML5 drop can report an index for a row that has since re-rendered,
    // and dropping a card should never blank a grid.
    const items = ["a", "b", "c"];
    expect(moveItem(items, 7, 0)).toBe(items);
    expect(moveItem(items, 0, -1)).toBe(items);
  });
});

describe("renumber", () => {
  it("numbers lanes in tens so one can be inserted between two", () => {
    expect(renumber([lane("a", 3), lane("b", 91)])).toEqual([
      { id: "a", position: POSITION_STEP },
      { id: "b", position: POSITION_STEP * 2 },
    ]);
  });
});

describe("reorder", () => {
  it("patches only the rows that actually moved", () => {
    // Dragging the last lane to the top renumbers everything — that is the
    // point of renumbering — but sending a PATCH for a lane whose number did
    // not change is a write nobody asked for.
    const plan = reorder(LANES, 3, 0);
    expect(plan.items.map((l) => l.id)).toEqual([
      "won",
      "qualification",
      "proposal",
      "negotiation",
    ]);
    expect(plan.patches).toEqual([
      { id: "won", position: 10 },
      { id: "qualification", position: 20 },
      { id: "proposal", position: 30 },
      { id: "negotiation", position: 40 },
    ]);
  });

  it("leaves the lanes above an in-place swap untouched", () => {
    const plan = reorder(LANES, 3, 2);
    expect(plan.patches).toEqual([
      { id: "won", position: 30 },
      { id: "negotiation", position: 40 },
    ]);
  });

  it("sends nothing for a drop onto itself", () => {
    expect(reorder(LANES, 1, 1).patches).toEqual([]);
  });

  it("repairs a list whose positions were never in tens", () => {
    // The Zoho importer appends at max(position)+10 from whatever it found, so
    // a real pipeline is not guaranteed to be tidy. A reorder normalises it.
    const messy = [lane("a", 10), lane("b", 11), lane("c", 12)];
    expect(reorder(messy, 0, 0).patches).toEqual([
      { id: "b", position: 20 },
      { id: "c", position: 30 },
    ]);
  });
});

describe("applyPatches", () => {
  it("issues every patch and reports the failures together", async () => {
    // Stopping at the first refusal leaves the rows already written holding
    // their NEW numbers and the rest their old ones — duplicate positions,
    // which the lanes on screen cannot show and the next drag compounds.
    const sent: string[] = [];
    const failures = await applyPatches(
      [lane("a", 10), lane("b", 20), lane("c", 30)],
      async (patch) => {
        sent.push(patch.id);
        if (patch.id === "b") throw new Error("Status not found");
      }
    );

    expect(sent).toEqual(["a", "b", "c"]);
    expect(failures).toEqual(["Status not found"]);
  });

  it("returns nothing when every patch lands", async () => {
    expect(await applyPatches([lane("a", 10)], async () => {})).toEqual([]);
  });

  it("issues them in order, one at a time", async () => {
    // Serial rather than Promise.all: N writes to one column, and a race is
    // not a better failure mode than a slow loop.
    const order: string[] = [];
    await applyPatches([lane("a", 10), lane("b", 20)], async (patch) => {
      order.push(`start:${patch.id}`);
      await Promise.resolve();
      order.push(`end:${patch.id}`);
    });
    expect(order).toEqual(["start:a", "end:a", "start:b", "end:b"]);
  });

  it("survives a thrown non-Error", async () => {
    const failures = await applyPatches([lane("a", 10)], async () => {
      throw "boom";
    });
    expect(failures).toEqual(["boom"]);
  });
});

describe("reorderFailureMessage", () => {
  it("names the arithmetic and what the reader is now looking at", () => {
    // A bare "request failed" next to a grid that re-read into a third order
    // is how somebody drags again and makes it worse.
    const message = reorderFailureMessage(["Status not found"], 4);
    expect(message).toContain("1 of 4");
    expect(message).toContain("the order shown is the server's");
    expect(message).toContain("Status not found");
  });
});

// ── The close-date section is only printed when it was computed ───────────

describe("backfillRan", () => {
  const counts: ClosedAtBackfill = {
    eligible: 0,
    stamped: 0,
    missing_close_date: 0,
  };
  const report = (over: Partial<StageMetadataReport>) =>
    ({ outcome: "dry_run", closed_at: counts, ...over }) as StageMetadataReport;

  it("is true for the two outcomes that opened the database", () => {
    expect(backfillRan(report({ outcome: "dry_run" }))).toBe(true);
    expect(backfillRan(report({ outcome: "applied" }))).toBe(true);
  });

  it("is false when the gateway left the section absent", () => {
    // null means "not evaluated", never "evaluated and found nothing".
    expect(backfillRan(report({ outcome: "stopped", closed_at: null }))).toBe(
      false
    );
    expect(
      backfillRan(report({ outcome: "unavailable", closed_at: null }))
    ).toBe(false);
  });

  it("is false on a stop even if counts somehow arrive", () => {
    // The second condition, and the reason both are ANDed: a future server
    // that started sending a zeroed section on a stop still cannot make this
    // UI print "0 close dates missing" about data nobody read.
    expect(backfillRan(report({ outcome: "stopped" }))).toBe(false);
    expect(backfillRan(report({ outcome: "unavailable" }))).toBe(false);
  });

  it("is true for a real run that found nothing to stamp", () => {
    // Zero IS a measurement here — it is only a lie when nothing was measured.
    expect(backfillRan(report({ outcome: "applied", closed_at: counts }))).toBe(
      true
    );
  });
});

// ── D-CRM-10, client side (done-when 5: BOTH sides) ───────────────────────

describe("validateStatusDraft", () => {
  const open: StatusType = "open";

  it("mirrors the pinned values the gateway enforces", () => {
    expect(CLAMPED_PROBABILITY).toEqual({ won: 100, lost: 0 });
  });

  it("refuses a nameless status", () => {
    expect(validateStatusDraft({ name: "  ", type: open }, "deal")).toBe(
      "A status needs a name."
    );
  });

  it("refuses a percentage that is not one", () => {
    expect(
      validateStatusDraft({ name: "Pilot", type: open, probability: 140 }, "deal")
    ).toContain("outside 0-100");
    expect(
      validateStatusDraft({ name: "Pilot", type: open, probability: -1 }, "deal")
    ).toContain("outside 0-100");
  });

  it("names the rule when a won stage would not forecast 100", () => {
    // The message has to say what to DO: "422" in a toast is not an answer to
    // "why can I not save this row".
    const message = validateStatusDraft(
      { name: "Delivered", type: "won", probability: 40 },
      "deal"
    );
    expect(message).toContain("always forecasts 100%");
    expect(message).toContain("Set probability to 100");
  });

  it("names the rule when a lost stage would not forecast 0", () => {
    expect(
      validateStatusDraft(
        { name: "Dropped", type: "lost", probability: 25 },
        "deal"
      )
    ).toContain("always forecasts 0%");
  });

  it("judges an omitted probability as the 0 the column would default to", () => {
    // Not "leave it alone": the server lands it at 0, so a won stage saved
    // without one is a contradiction the moment it exists.
    expect(
      validateStatusDraft({ name: "Delivered", type: "won" }, "deal")
    ).toContain("always forecasts 100%");
  });

  it("accepts the terminal stages at their pinned values", () => {
    expect(
      validateStatusDraft(
        { name: "Delivered", type: "won", probability: 100 },
        "deal"
      )
    ).toBeNull();
    expect(
      validateStatusDraft({ name: "Dropped", type: "lost", probability: 0 }, "deal")
    ).toBeNull();
  });

  it("applies the won=100 and lost=0 clamp to a lead status too (CRM-U6)", () => {
    // Migration 285 gave `crm_lead_statuses` a win chance, with the same rule.
    expect(
      validateStatusDraft({ name: "Qualified", type: "won", probability: 40 }, "lead")
    ).toContain("Set probability to 100");
    expect(
      validateStatusDraft({ name: "Qualified", type: "won", probability: 100 }, "lead")
    ).toBeNull();
    expect(
      validateStatusDraft({ name: "Cold", type: "open", probability: 140 }, "lead")
    ).toContain("outside 0-100");
  });

  it("names every stage type in plain words", () => {
    expect(STATUS_TYPE_LABELS).toEqual({
      open: "Open",
      ongoing: "In progress",
      on_hold: "On hold",
      won: "Won",
      lost: "Lost",
    });
  });
});

describe("validateLostReasonDraft", () => {
  it("requires a label", () => {
    expect(validateLostReasonDraft("   ")).toBe("A lost reason needs a label.");
    expect(validateLostReasonDraft("Price")).toBeNull();
  });
});

describe("changedFields", () => {
  it("sends only what moved", () => {
    // `type` and `probability` are judged TOGETHER by the server's clamp, so
    // resending an unchanged one alongside a changed one is the difference
    // between a 422 the editor caused and one the data did.
    expect(
      changedFields(
        { name: "Proposal", type: "ongoing", probability: 50 },
        { name: "Proposal/Price Quote", type: "ongoing", probability: 50 }
      )
    ).toEqual({ name: "Proposal/Price Quote" });
  });

  it("is empty when nothing moved", () => {
    expect(changedFields({ a: 1 }, { a: 1 })).toEqual({});
  });

  it("compares arrays by content, not by identity (WS-26h)", () => {
    // The requirements grid rebuilds `required_fields` on every toggle, so a
    // field switched on and back off leaves a NEW array holding the OLD
    // contents. Identity would report that as a change, and a Save button that
    // never goes quiet is a Save button nobody trusts.
    expect(
      changedFields(
        { required_fields: ["amount", "owner_email"] },
        { required_fields: ["amount", "owner_email"] }
      )
    ).toEqual({});
  });

  it("treats a reordered array as a change", () => {
    // `required_fields` is stored as written and the blocked-move 422 lists it
    // in that order, so the order is data.
    expect(
      changedFields(
        { required_fields: ["amount", "owner_email"] },
        { required_fields: ["owner_email", "amount"] }
      )
    ).toEqual({ required_fields: ["owner_email", "amount"] });
  });

  it("sees a field added to or removed from the set", () => {
    expect(
      changedFields({ required_fields: [] }, { required_fields: ["amount"] })
    ).toEqual({ required_fields: ["amount"] });
    expect(
      changedFields({ required_fields: ["amount"] }, { required_fields: [] })
    ).toEqual({ required_fields: [] });
  });
});

// ── WS-26h · what the grid may write ──────────────────────────────────────

describe("validateStatusDraft · stage discipline", () => {
  const base = { name: "Proposal", type: "ongoing" as StatusType, probability: 50 };

  it("accepts the requirable columns", () => {
    expect(
      validateStatusDraft(
        { ...base, required_fields: ["amount", "expected_close_date"] },
        "deal"
      )
    ).toBeNull();
  });

  it("refuses a name the server's allowlist does not carry", () => {
    // Same rule as the gateway's, not a looser one: a client check that
    // permitted something the server refuses turns a rule into an
    // intermittent bug.
    expect(
      validateStatusDraft({ ...base, required_fields: ["amont"] }, "deal")
    ).toContain("amont");
  });

  it("accepts an empty requirement set — that is how a stage stops asking", () => {
    expect(
      validateStatusDraft({ ...base, required_fields: [] }, "deal")
    ).toBeNull();
  });

  it("accepts a null rot threshold as 'never rots'", () => {
    expect(
      validateStatusDraft({ ...base, max_dwell_days: null }, "deal")
    ).toBeNull();
    expect(validateStatusDraft(base, "deal")).toBeNull();
  });

  it("accepts a threshold inside the column", () => {
    expect(
      validateStatusDraft({ ...base, max_dwell_days: 1 }, "deal")
    ).toBeNull();
    expect(
      validateStatusDraft({ ...base, max_dwell_days: 32767 }, "deal")
    ).toBeNull();
  });

  it.each([0, -1, 32768, 1.5])("refuses %s days", (days) => {
    // 0 would paint every card in the stage amber the moment it arrived;
    // the ceiling is SMALLINT's own.
    expect(
      validateStatusDraft({ ...base, max_dwell_days: days }, "deal")
    ).toBeTruthy();
  });

  it("judges neither column on a lead status", () => {
    // The gateway refuses both outright there (they are deal-only), and the
    // grid never renders them for leads — so a lead draft that somehow
    // carried one must not be refused HERE with a message about a rule that
    // is not the one it broke.
    expect(
      validateStatusDraft(
        { name: "New", type: "open", max_dwell_days: 0 },
        "lead"
      )
    ).toBeNull();
  });
});
