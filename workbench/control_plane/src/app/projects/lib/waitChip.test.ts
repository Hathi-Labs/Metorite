import { describe, expect, it } from "vitest";

import { type DecisionRow, type TaskDecisions, readTaskDecisions } from "./decisions";
import { shortName, waitChip } from "./waitChip";

const NOW = Date.parse("2026-11-08T10:00:00Z");
const names: Record<string, string> = {
  "priya@x.test": "Priya Nair",
  "sam@x.test": "Sam Roy",
};
const nameOf = (email: string) => names[email] ?? email;

const row = (over: Partial<DecisionRow> = {}): DecisionRow => ({
  id: "d1",
  subject_id: "t1",
  decider: "me@x.test",
  options: [],
  question: "Confirm supplier X?",
  asked_by: "vijay@x.test",
  state: "open",
  created_at: null,
  expires_at: "2026-11-20T10:00:00Z",
  answered_by: null,
  answer_key: null,
  answer_reason: null,
  answered_at: null,
  ...over,
});

const wait = (decider: string) => ({ decider, expires_at: null, source: "person" });
const data = (over: Partial<TaskDecisions>): TaskDecisions => ({
  decisions: [],
  waiting_on: [],
  asks: [],
  ...over,
});

describe("the wait chip", () => {
  it("reads Needs you for the addressee", () => {
    expect(waitChip(data({ decisions: [row()] }), nameOf, NOW)).toEqual({
      kind: "needs_you",
      text: "Needs you",
    });
  });

  it("reads Waiting for Priya to another reader", () => {
    const chip = waitChip(data({ waiting_on: [wait("priya@x.test")] }), nameOf, NOW);
    expect(chip).toEqual({ kind: "waiting", text: "Waiting for Priya" });
  });

  it("reads Waiting for 2 for two asks, and names no one", () => {
    const chip = waitChip(
      data({ waiting_on: [wait("priya@x.test"), wait("sam@x.test")] }),
      nameOf,
      NOW,
    );
    expect(chip?.text).toBe("Waiting for 2");
    expect(chip?.text).not.toMatch(/Priya|Sam/);
  });

  it("draws no chip for none, for no read, or for a row that expired or closed", () => {
    expect(waitChip(data({}), nameOf, NOW)).toBeNull();
    expect(waitChip(null, nameOf, NOW)).toBeNull();
    const old = row({ expires_at: "2026-11-01T10:00:00Z" });
    expect(waitChip(data({ decisions: [old] }), nameOf, NOW)).toBeNull();
    expect(waitChip(data({ decisions: [row({ state: "answered" })] }), nameOf, NOW)).toBeNull();
  });

  it("leads with Needs you when the member is asked and also waits", () => {
    const chip = waitChip(
      data({ decisions: [row()], waiting_on: [wait("priya@x.test")] }),
      nameOf,
      NOW,
    );
    expect(chip?.text).toBe("Needs you");
  });

  it("a withdraw removes the chip: an empty waiting_on reads as none", () => {
    expect(waitChip(data({ asks: [], waiting_on: [] }), nameOf, NOW)).toBeNull();
  });

  it("never puts the question into the chip", () => {
    const chip = waitChip(data({ decisions: [row()] }), nameOf, NOW);
    expect(JSON.stringify(chip)).not.toContain("supplier");
  });

  it("shortens a name to its first word, and an address to its local part", () => {
    expect(shortName("Priya Nair")).toBe("Priya");
    expect(shortName("priya@x.test")).toBe("priya");
    expect(shortName("  ")).toBe("");
  });
});

import { availableCommands, stepSequence, type CommandContext } from "./commands";

describe("the key W", () => {
  const ctx: CommandContext = {
    mode: "board",
    hasProject: true,
    isRoot: false,
    filtered: false,
    panelOpen: true,
    panelMode: "side",
    canToggleRail: true,
    canWaitOnSomeone: true,
  };

  it("runs Wait on someone only while a card is open and the ask is on", () => {
    const hit = (c: CommandContext) =>
      stepSequence([], "w", availableCommands(c)).command?.id;
    expect(hit(ctx)).toBe("panel.waitOnSomeone");
    expect(hit({ ...ctx, canWaitOnSomeone: false })).toBeUndefined();
    expect(hit({ ...ctx, panelOpen: false })).toBeUndefined();
  });

  it("leaves g w to the Studio jump", () => {
    const first = stepSequence([], "g", availableCommands(ctx));
    expect(first.pending).toEqual(["g"]);
    expect(stepSequence(["g"], "w", availableCommands(ctx)).command?.id).toBe("go.w");
  });
});

import { readFileSync } from "node:fs";

describe("the chip needs the person ask", () => {
  it("draws no chip when the read has no asks key (WAITING_ORGS off)", () => {
    const open = { id: "d1", state: "open", expires_at: null } as unknown as TaskDecisions["decisions"][number];
    const flagOff = readTaskDecisions({ decisions: [open], waiting_on: [{ decider: "a@x.test" }] });
    expect(flagOff.asks).toBeNull();
    expect(waitChip(flagOff, (e) => e, 0)).toBeNull();
    const flagOffOthers = readTaskDecisions({ decisions: [], waiting_on: [{ decider: "a@x.test" }] });
    expect(waitChip(flagOffOthers, (e) => e, 0)).toBeNull();
  });
});
