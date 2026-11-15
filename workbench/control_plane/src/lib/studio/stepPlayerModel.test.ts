/**
 * STU-D14a fence: the pure model of the step player.
 *
 * Mutations (each turns a test red): drop the required-check gate in `gate` ("refuses ... without a
 * door call"), add `signed_by` to `signArgs` ("holds result keys only"), and make `resumeAt` ignore
 * `done` ("resumeAt opens the first step that is not done").
 */
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import type { Row } from "@/lib/studio/dataDoor";
import { sampleStepRows, makeSampleStepDoor, CHECK_STEP } from "@/lib/studio/sampleSteps";
import {
  CHECK_FIRST, doneLine, gate, metaOfAnswer, orderSteps, photoSrc, progressLine, refusalText, resumeAt,
  signArgs, signOff, signedLine, updateAction,
} from "@/lib/studio/stepPlayerModel";

const steps = (done: number) => orderSteps(sampleStepRows(done));
const step = (n: number): Row => steps(0)[n - 1];

describe("orderSteps", () => {
  it("sorts by order, and the sample stores steps 3 and 4 swapped", () => {
    expect(sampleStepRows()[2].order).toBe(4);
    expect(steps(0).map((s) => s.order)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });
  it("puts a step with no order last, in its own order", () => {
    const rows = [{ id: "a" }, { id: "b", order: 2 }, { id: "c", order: 1 }, { id: "d" }];
    expect(orderSteps(rows).map((r) => r.id)).toEqual(["c", "b", "a", "d"]);
  });
  it("reads at most 100 steps", () => {
    const rows = Array.from({ length: 150 }, (_, i) => ({ id: `r${i}`, order: i }));
    expect(orderSteps(rows)).toHaveLength(100);
  });
});

describe("resumeAt opens the first step that is not done", () => {
  it.each([[0, 0], [4, 4], [10, 9]])("with %i steps done, the place is %i", (done, at) => {
    expect(resumeAt(steps(done))).toBe(at);
  });
  it("has place 0 for no steps", () => expect(resumeAt([])).toBe(0));
});

describe("the words", () => {
  it("gives Step N of M and N of M done", () => {
    expect(progressLine(0, 10)).toBe("Step 1 of 10");
    expect(progressLine(9, 10)).toBe("Step 10 of 10");
    expect(doneLine(steps(10))).toBe("10 of 10 done");
  });
  it("reads the sign-off line from the row meta", () => {
    expect(signedLine(steps(4)[0])).toBe("Signed by Ravi Menon at 2026-11-14 09:11 UTC");
  });
  it("gives no sign-off line for a step that is not done, or has no meta", () => {
    expect(signedLine(step(1))).toBeNull();
    expect(signedLine({ id: "x", done: true })).toBeNull();
  });
});

describe("the gate", () => {
  it("refuses a step with a required check that is not ticked", () => {
    expect(gate(step(CHECK_STEP), false)).toEqual({ ok: false, message: CHECK_FIRST });
    expect(gate(step(CHECK_STEP), true)).toEqual({ ok: true });
  });
  it("lets a step with no check pass", () => expect(gate(step(1), false)).toEqual({ ok: true }));
  it("refuses without a door call", async () => {
    const run = vi.fn();
    const out = await signOff({ run }, updateAction("step"), step(CHECK_STEP), false);
    expect(out).toEqual({ ok: false, message: CHECK_FIRST });
    expect(run).not.toHaveBeenCalled();
  });
});

describe("the write", () => {
  it("holds result keys only, and no identity key", () => {
    const args = signArgs(step(1), "  Gauge 4.2  ");
    expect(args).toEqual({ id: "st1", data: { done: true, note: "Gauge 4.2" } });
    for (const k of ["signed_by", "signed_at", "updated_by", "updated_at"]) expect(args.data).not.toHaveProperty(k);
  });
  it("leaves out an empty note", () => expect(signArgs(step(1), "  ").data).toEqual({ done: true }));
  it("makes ONE run with the update action", async () => {
    const run = vi.fn().mockResolvedValue({ ok: true, data: { updated_by: "A", updated_at: "2026-11-15T10:00:00Z" } });
    const out = await signOff({ run }, "object:step.update", step(1), false);
    expect(run).toHaveBeenCalledTimes(1);
    expect(run).toHaveBeenCalledWith("object:step.update", { id: "st1", data: { done: true } });
    expect(out).toMatchObject({ ok: true, meta: { updated_by: "A" } });
  });
  it("answers ok:false with the refusal text, and keeps the step", async () => {
    const door = makeSampleStepDoor(sampleStepRows(), 0);
    const out = await signOff(door, "object:step.update", step(2), true);
    expect(out).toEqual({ ok: false, message: "A step is signed after the steps before it." });
  });
  it("stamps the member and the time at the sample store", async () => {
    const door = makeSampleStepDoor(sampleStepRows(), 0);
    const out = await signOff(door, "object:step.update", step(1), false, "ok");
    expect(out.ok && out.meta?.updated_by).toBe("Asha Rao");
    const read = orderSteps(await door.read({ action: "object:step.list" }));
    expect(signedLine(read[0])).toMatch(/^Signed by Asha Rao at \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$/);
  });
  it("drops an identity key that a caller adds to the data", async () => {
    const door = makeSampleStepDoor(sampleStepRows(), 0);
    await door.run("object:step.update", { id: "st1", data: { done: true, updated_by: "x@y.z" } });
    const read = orderSteps(await door.read({ action: "object:step.list" }));
    expect(read[0].updated_by).toBe("Asha Rao");
  });
  it("turns a thrown door into a sentence", async () => {
    const out = await signOff({ run: () => Promise.reject(new Error("x")) }, "a", step(1), false);
    expect(out.ok).toBe(false);
  });
});

describe("refusalText", () => {
  it("prefers the rule message, then a field error, then the message", () => {
    expect(refusalText({ ok: false, message: "m", data: { rule: "r", message: "Rule says no." } })).toBe("Rule says no.");
    expect(refusalText({ ok: false, message: "m", data: { fieldErrors: { done: "Bad." } } })).toBe("Bad.");
    expect(refusalText({ ok: false, message: "m" })).toBe("m");
    expect(refusalText({ ok: false })).toBe("That did not save.");
  });
  it("reads no meta from a bare answer", () => expect(metaOfAnswer({})).toBeNull());
});

describe("the photo", () => {
  const addr = (u: string | null) => () => u;
  it("gives an address only through safeAddress", () => {
    expect(photoSrc(step(1), addr("/office-objects/a.png"))).toBe("/office-objects/a.png");
    expect(photoSrc(step(1), addr("https://evil.example/x.png"))).toBeNull();
    expect(photoSrc(step(1), addr("javascript:alert(1)"))).toBeNull();
    expect(photoSrc(step(1), () => { throw new Error("x"); })).toBeNull();
  });
  it("gives none for a step with no photo, or a door with no address", () => {
    expect(photoSrc(step(7), addr("/office-objects/a.png"))).toBeNull();
    expect(photoSrc(step(1), null)).toBeNull();
  });
});

describe("the files of D14a", () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf-8");
  const files = [
    "./stepPlayerModel.ts", "./sampleSteps.ts", "../../components/studio/StudioStepPlayer.tsx",
  ];
  it.each(files)("%s does not import the real door", (f) => {
    expect(read(f)).not.toMatch(/from\s+["'][^"']*realDoor["']/);
  });
  it.each(files)("%s has no signed_by or signed_at key", (f) => {
    expect(read(f)).not.toMatch(/signed_(?:by|at)/);
  });
  it("writes no identity key in the write args", () => {
    const src = read("./stepPlayerModel.ts");
    const body = src.slice(src.indexOf("export function signArgs"), src.indexOf("/** The update action"));
    expect(body).not.toMatch(/updated_(?:by|at)|signed_(?:by|at)/);
  });
});
