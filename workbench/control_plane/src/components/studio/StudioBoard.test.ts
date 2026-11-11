/**
 * STU-D3 fence: the Board block.
 *
 * The vitest env has no DOM and takes `*.test.ts` only. So the view draws
 * from props with `renderToStaticMarkup`, and the move runs as the plain
 * store it is (`boardMove.ts`) against a fake door. The keyboard path runs
 * `keyStep` into the same store, as the view does.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/blocks-preview",
}));

import StudioBoard from "@/components/studio/StudioBoard";
import GenerativeUINode from "@/components/GenerativeUINode";
import { clearAll } from "@/lib/dataCache";
import { StudioDataContext } from "@/lib/studio/dataContext";
import type { ActionResult, StudioDataDoor } from "@/lib/studio/dataDoor";
import { boardBlock, buildLanes, keyStep, parseBoard, type Hold } from "@/lib/studio/boardModel";
import { createMover, refusalText } from "@/lib/studio/boardMove";
import { SAMPLE_FIELDS, SAMPLE_ROWS, makeSampleDoor } from "@/lib/studio/sampleDoor";

beforeEach(() => clearAll());

const FIELDS = SAMPLE_FIELDS.filter((f) => ["title", "status", "owner", "visit_date", "tags", "done"].includes(f.key));
const DATA = { fields: FIELDS, groupBy: "status", titleField: "title", cardFields: ["owner", "visit_date", "tags"], update: "object:visit.update" };

function configOf() {
  const p = parseBoard(DATA);
  if (!p.ok) throw new Error(p.reason);
  return p.config;
}

type Run = (action: string, args?: Record<string, unknown>) => Promise<ActionResult>;
function fakeDoor(run: Run): StudioDataDoor & { run: ReturnType<typeof vi.fn> } {
  return { scope: "t:draft", read: async () => [], run: vi.fn(run) };
}
/** A call that stays open until `settle` runs. */
function pendingDoor() {
  let settle: (r: ActionResult) => void = () => {};
  const door = fakeDoor(() => new Promise<ActionResult>((r) => { settle = r; }));
  return { door, settle: (r: ActionResult) => settle(r) };
}
const ok: ActionResult = { ok: true };

describe("a drop", () => {
  it("makes exactly one update call with the id and the new option", async () => {
    const door = fakeDoor(async () => ok);
    const mover = createMover(door, "object:visit.update", "status");
    await mover.move("v1", "Planned", "Closed", "Closed");
    expect(door.run).toHaveBeenCalledTimes(1);
    expect(door.run).toHaveBeenCalledWith("object:visit.update", { id: "v1", data: { status: "Closed" } });
  });
  it("moves the card at the drop, before the door answers", () => {
    const { door } = pendingDoor();
    const mover = createMover(door, "object:visit.update", "status");
    void mover.move("v1", "Planned", "On site", "On site");
    expect(mover.getState().placed).toEqual({ v1: "On site" });
    expect(mover.getState().pending.has("v1")).toBe(true);
  });
  it("sends null for a drop on the No value lane", async () => {
    const door = fakeDoor(async () => ok);
    await createMover(door, "u", "status").move("v1", "Planned", null, "No value");
    expect(door.run).toHaveBeenCalledWith("u", { id: "v1", data: { status: null } });
  });
  it("makes no call for a drop on the same lane", async () => {
    const door = fakeDoor(async () => ok);
    const mover = createMover(door, "u", "status");
    await mover.move("v1", "Planned", "Planned", "Planned");
    expect(door.run).not.toHaveBeenCalled();
    expect(mover.getState().placed).toEqual({});
  });
  it("makes one call for two drops while the first is pending", async () => {
    const { door, settle } = pendingDoor();
    const mover = createMover(door, "u", "status");
    const first = mover.move("v1", "Planned", "On site", "On site");
    void mover.move("v1", "On site", "Closed", "Closed");
    await Promise.resolve();
    expect(door.run).toHaveBeenCalledTimes(1);
    expect(mover.getState().placed).toEqual({ v1: "On site" });
    settle(ok);
    await first;
    expect(mover.getState().pending.size).toBe(0);
  });
  it("lets a second card move while the first is pending", () => {
    const { door } = pendingDoor();
    const mover = createMover(door, "u", "status");
    void mover.move("v1", "Planned", "On site", "On site");
    void mover.move("v2", "Planned", "Closed", "Closed");
    expect(door.run).toHaveBeenCalledTimes(2);
  });
  it("keeps the card in its new lane after the door accepts, until fresh rows arrive", async () => {
    const door = fakeDoor(async () => ok);
    const mover = createMover(door, "u", "status");
    await mover.move("v1", "Planned", "Closed", "Closed");
    expect(mover.getState().placed).toEqual({ v1: "Closed" });
    expect(mover.getState().alert).toBeNull();
    expect(mover.getState().live).toBe("Moved to Closed");
    mover.settle();
    expect(mover.getState().placed).toEqual({});
  });
});

describe("a refusal", () => {
  const rows = [{ id: "a", title: "A", status: "Planned" }, { id: "b", title: "B", status: "Planned" }];

  it("puts the card back at its lane and index, and shows the door message", async () => {
    const door = fakeDoor(async () => ({ ok: false, message: "That card is locked." }));
    const mover = createMover(door, "u", "status");
    const config = configOf();
    const before = buildLanes(config, rows).map((l) => l.rows.map((r) => r.id));
    await mover.move("a", "Planned", "Closed", "Closed");
    const st = mover.getState();
    expect(st.placed).toEqual({});
    expect(st.alert).toBe("That card is locked.");
    expect(st.live).toBe("Move refused: That card is locked.");
    expect(buildLanes(config, rows, { placed: st.placed }).map((l) => l.rows.map((r) => r.id))).toEqual(before);
    expect(before[0]).toEqual(["a", "b"]);
  });
  it("names the rule of a rule refusal", async () => {
    const door = fakeDoor(async () => ({
      ok: false, message: "That did not save.", data: { rule: "close_needs_done", message: "A visit closes only when it is done." },
    }));
    const mover = createMover(door, "u", "status");
    await mover.move("a", "Planned", "Closed", "Closed");
    expect(mover.getState().alert).toBe("Refused by close_needs_done: A visit closes only when it is done.");
  });
  it("treats a thrown call as a refusal", async () => {
    const door = fakeDoor(async () => { throw new Error("net"); });
    const mover = createMover(door, "u", "status");
    await mover.move("a", "Planned", "Closed", "Closed");
    expect(mover.getState().placed).toEqual({});
    expect(mover.getState().alert).toMatch(/did not work/);
  });
  it("frees the card after a refusal, and clears the alert on the next move", async () => {
    let n = 0;
    const door = fakeDoor(async () => (n++ === 0 ? { ok: false, message: "No." } : ok));
    const mover = createMover(door, "u", "status");
    await mover.move("a", "Planned", "Closed", "Closed");
    expect(mover.getState().alert).toBe("No.");
    await mover.move("a", "Planned", "On site", "On site");
    expect(door.run).toHaveBeenCalledTimes(2);
    expect(mover.getState().alert).toBeNull();
  });
  it("reads a bare message when the data is empty", () => {
    expect(refusalText({ message: "Nope", data: {} })).toBe("Nope");
  });
});

describe("the keyboard path", () => {
  it("Space, Right, Space makes one call", async () => {
    const door = fakeDoor(async () => ok);
    const mover = createMover(door, "u", "status");
    const lanes = buildLanes(configOf(), [{ id: "v1", title: "T", status: "Planned" }]);
    const ids = lanes.map((l) => l.id);
    const card = { id: "v1", lane: ids[0] };
    let hold: Hold | null = null;
    for (const key of [" ", "ArrowRight", " "]) {
      const step = keyStep(hold, key, card, ids);
      hold = step.hold;
      if (step.drop) {
        const to = lanes.find((l) => l.id === step.drop?.to);
        await mover.move(step.drop.id, "Planned", to?.value ?? null, to?.title ?? "");
      }
    }
    expect(door.run).toHaveBeenCalledTimes(1);
    expect(door.run).toHaveBeenCalledWith("u", { id: "v1", data: { status: "On site" } });
  });
  it("Escape makes no call", () => {
    const ids = ["a", "b"];
    const up = keyStep(null, " ", { id: "v1", lane: "a" }, ids);
    expect(keyStep(up.hold, "Escape", { id: "v1", lane: "a" }, ids).drop).toBeUndefined();
  });
});

describe("the view", () => {
  const html = (data: unknown, rows = SAMPLE_ROWS, door: StudioDataDoor | null = null) =>
    renderToStaticMarkup(createElement(StudioBoard, { data, rows, door }));

  it("draws a lane for each option, and a card for each record", () => {
    const out = html(DATA);
    for (const o of ["Planned", "On site", "Closed"]) expect(out).toContain(`aria-label="${o}"`);
    expect(out).toContain("Install the press");
    expect(out).toContain("Calibrate the line");
    expect(out).not.toContain("No value");
  });
  it("gives a non-select groupBy an error state and no lanes", () => {
    const out = html({ ...DATA, groupBy: "title" });
    expect(out).toContain('role="alert"');
    expect(out).toContain("groups by a select field");
    expect(out).not.toContain('role="group"');
  });
  it("gives a missing groupBy an error state", () => {
    expect(html({ ...DATA, groupBy: undefined })).toContain('role="alert"');
  });
  it("shows Show more (n) when a lane holds more than the cap", () => {
    const rows = Array.from({ length: 7 }, (_, i) => ({ id: "r" + i, title: "R" + i, status: "Planned" }));
    const out = html({ ...DATA, maxCards: 5 }, rows);
    expect(out).toContain("Show more (2)");
    expect(out).toContain("R4");
    expect(out).not.toContain("R5");
  });
  it("marks an empty lane", () => {
    expect(html(DATA, [])).toContain("No cards");
  });
  it("draws no drag handle and no Move to button when the board has no update action", () => {
    const out = html({ ...DATA, update: undefined }, SAMPLE_ROWS, makeSampleDoor());
    expect(out).not.toContain('draggable="true"');
    expect(out).not.toContain("to another lane");
  });
  it("draws draggable cards and a Move to button when it can update", () => {
    const out = html(DATA, SAMPLE_ROWS, makeSampleDoor());
    expect(out).toContain('draggable="true"');
    expect(out).toContain("to another lane");
  });
  it("binds through the page door as a template named board", () => {
    const door = makeSampleDoor();
    const node = boardBlock({ ...DATA }, { action: "object:visit.list" });
    const out = renderToStaticMarkup(
      createElement(StudioDataContext.Provider, { value: door }, createElement(GenerativeUINode, { spec: node })),
    );
    expect(out).toContain('aria-busy="true"');
  });
});

describe("the fake door", () => {
  const list = { action: "object:visit.list" };
  it("takes a one-field patch and keeps the other fields", async () => {
    const door = makeSampleDoor(SAMPLE_FIELDS, SAMPLE_ROWS, 0);
    const res = await door.run("object:visit.update", { id: "v3", data: { status: "Planned" } });
    expect(res.ok).toBe(true);
    const row = (await door.read(list)).find((r) => r.id === "v3");
    expect(row?.status).toBe("Planned");
    expect(row?.title).toBe("Train the night shift");
  });
  it("refuses a move into Closed of a visit that is not done, and names the rule", async () => {
    const door = makeSampleDoor(SAMPLE_FIELDS, SAMPLE_ROWS, 0);
    const res = await door.run("object:visit.update", { id: "v1", data: { status: "Closed" } });
    expect(res.ok).toBe(false);
    expect(refusalText(res)).toBe("Refused by close_needs_done: A visit closes only when it is done.");
    const row = (await door.read(list)).find((r) => r.id === "v1");
    expect(row?.status).toBe("Planned");
  });
  it("clears the field on a null", async () => {
    const door = makeSampleDoor(SAMPLE_FIELDS, SAMPLE_ROWS, 0);
    await door.run("object:visit.update", { id: "v3", data: { status: null } });
    const row = (await door.read(list)).find((r) => r.id === "v3");
    expect(row?.status).toBeUndefined();
  });
  it("a patch changes only the fields it sends", async () => {
    const door = makeSampleDoor(SAMPLE_FIELDS, SAMPLE_ROWS, 0);
    const before = (await door.read(list)).find((r) => r.id === "v1");
    const res = await door.run("object:visit.update", { id: "v1", data: { owner: "ravi@example.com" } });
    expect(res.ok).toBe(true);
    const after = (await door.read(list)).find((r) => r.id === "v1");
    expect(after).toEqual({ ...before, owner: "ravi@example.com" });
  });
  it("a null in a patch clears a field that the record held", async () => {
    const door = makeSampleDoor(SAMPLE_FIELDS, SAMPLE_ROWS, 0);
    const res = await door.run("object:visit.update", { id: "v1", data: { owner: null } });
    expect(res.ok).toBe(true);
    const row = (await door.read(list)).find((r) => r.id === "v1");
    expect(row && "owner" in row).toBe(false);
    expect(row?.title).toBe("Install the press");
  });
  it("refuses an unknown id", async () => {
    const door = makeSampleDoor(SAMPLE_FIELDS, SAMPLE_ROWS, 0);
    const res = await door.run("object:visit.update", { id: "zz", data: { status: "Planned" } });
    expect(res.ok).toBe(false);
  });
});


describe("the drag ring", () => {
  const mk = () => createMover(fakeDoor(async () => ok), "u", "status");
  it("shows on the lane under the dragged card, and not when no drag runs", () => {
    const m = mk();
    expect(m.dragOver("opt:Closed")).toBe(false);
    expect(m.getState().over).toBeNull();
    m.dragStart("v1");
    expect(m.dragOver("opt:Closed")).toBe(true);
    expect(m.getState().over).toBe("opt:Closed");
  });
  it("clears on dragEnd", () => {
    const m = mk();
    m.dragStart("v1");
    m.dragOver("opt:Closed");
    m.dragEnd();
    expect(m.getState().over).toBeNull();
    expect(m.getState().dragId).toBeNull();
  });
  it("clears on a drop, and returns the dragged id", () => {
    const m = mk();
    m.dragStart("v1");
    m.dragOver("opt:Closed");
    expect(m.drop()).toBe("v1");
    expect(m.getState().over).toBeNull();
    expect(m.getState().dragId).toBeNull();
  });
  it("moves to the other lane when the card leaves for it", () => {
    const m = mk();
    m.dragStart("v1");
    m.dragOver("opt:Closed");
    m.dragLeave(true);
    expect(m.getState().over).toBeNull();
    m.dragOver("opt:Planned");
    expect(m.getState().over).toBe("opt:Planned");
  });
  it("keeps the ring on a leave with no related target", () => {
    const m = mk();
    m.dragStart("v1");
    m.dragOver("opt:Closed");
    m.dragLeave(false);
    expect(m.getState().over).toBe("opt:Closed");
  });
});

describe("the keyboard hold", () => {
  it("drops the hold when focus leaves the board", () => {
    const m = createMover(fakeDoor(async () => ok), "u", "status");
    m.setHold({ id: "v1", from: "opt:Planned", over: "opt:Closed" });
    expect(m.getState().hold).not.toBeNull();
    m.blur();
    expect(m.getState().hold).toBeNull();
    expect(m.getState().live).toBe("Move cancelled");
  });
  it("blur with no hold changes nothing", () => {
    const m = createMover(fakeDoor(async () => ok), "u", "status");
    m.blur();
    expect(m.getState().live).toBe("");
  });
});

describe("focus follows a moved card", () => {
  it("asks for the card after a keyboard move, as the move starts", () => {
    const { door } = pendingDoor();
    const mover = createMover(door, "u", "status");
    void mover.move("a", "Planned", "On site", "On site", { keyboard: true });
    expect(mover.getState().focusId).toBe("a");
    mover.clearFocus();
    expect(mover.getState().focusId).toBeNull();
  });
  it("asks for the card again when a keyboard move is refused", async () => {
    const door = fakeDoor(async () => ({ ok: false, message: "No." }));
    const mover = createMover(door, "u", "status");
    const p = mover.move("a", "Planned", "On site", "On site", { keyboard: true });
    mover.clearFocus();
    await p;
    expect(mover.getState().focusId).toBe("a");
  });
  it("does not ask for focus on a pointer move or its refusal", async () => {
    const door = fakeDoor(async () => ({ ok: false, message: "No." }));
    const mover = createMover(door, "u", "status");
    await mover.move("a", "Planned", "On site", "On site");
    expect(mover.getState().focusId).toBeNull();
  });
  it("the view focuses the card by data-card-id in a layout effect and clears the request", () => {
    const view = readFileSync(new URL("./StudioBoard.tsx", import.meta.url), "utf8");
    expect(view).toMatch(/useLayoutEffect/);
    expect(view).toContain("data-card-id");
    expect(view).toContain("clearFocus()");
  });
});

describe("one live line", () => {
  it("a Move-to move after Escape reads the move result", async () => {
    const mover = createMover(fakeDoor(async () => ok), "u", "status");
    mover.say("Move cancelled");
    expect(mover.getState().live).toBe("Move cancelled");
    await mover.move("a", "Planned", "Closed", "Closed");
    expect(mover.getState().live).toBe("Moved to Closed");
  });
  it("the view keeps no second live text", () => {
    const view = readFileSync(new URL("./StudioBoard.tsx", import.meta.url), "utf8");
    expect(view).not.toMatch(/setHeard|heard \|\|/);
    expect(view).toContain("{st.live}");
  });
});

describe("a refused move keeps the last accepted lane", () => {
  it("move 1 accepted, move 2 refused: the card stays in lane B", async () => {
    let n = 0;
    const door = fakeDoor(async () => (++n === 1 ? ok : { ok: false, message: "No." }));
    const mover = createMover(door, "u", "status");
    await mover.move("a", "Planned", "On site", "On site");
    expect(mover.getState().placed).toEqual({ a: "On site" });
    await mover.move("a", "On site", "Closed", "Closed");
    expect(mover.getState().placed).toEqual({ a: "On site" });
    expect(mover.getState().alert).toBe("No.");
  });
  it("a thrown second move also keeps lane B", async () => {
    let n = 0;
    const door = fakeDoor(async () => { if (++n === 1) return ok; throw new Error("x"); });
    const mover = createMover(door, "u", "status");
    await mover.move("a", "Planned", "On site", "On site");
    await mover.move("a", "On site", "Closed", "Closed");
    expect(mover.getState().placed).toEqual({ a: "On site" });
  });
});

describe("null rows", () => {
  it("draws a board whose rows hold a null", () => {
    const rows = [null, ...SAMPLE_ROWS] as unknown as typeof SAMPLE_ROWS;
    const out = renderToStaticMarkup(createElement(StudioBoard, { data: DATA, rows, door: null }));
    expect(out).toContain('aria-label="Board"');
  });
  it("the view uses the filtered list for every lookup", () => {
    const view = readFileSync(new URL("./StudioBoard.tsx", import.meta.url), "utf8");
    expect(view).toContain("recordsOf(given)");
  });
});

describe("the source", () => {
  const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
  const view = read("./StudioBoard.tsx");
  it("words a card with displayValue and has no formatter of its own", () => {
    expect(view).toContain("displayValue");
    expect(view).not.toMatch(/Intl\.|toLocale|toFixed|formatCardDate|formatWords/);
    expect(read("../../lib/studio/boardModel.ts")).not.toMatch(/Intl\.|toLocale|toFixed/);
  });
  it("draws lanes and cards with the shared board parts", () => {
    expect(view).toMatch(/from "@\/components\/board\/BoardColumn"/);
    expect(view).toContain("<TaskCardShell");
  });
  it("keeps React out of the pure files", () => {
    for (const f of ["boardModel.ts", "boardMove.ts"]) {
      expect(read("../../lib/studio/" + f), f).not.toMatch(/from "react/);
    }
  });
});
