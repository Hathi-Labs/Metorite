/**
 * STU-D3b fence: the Board moves a card on the real door (`ai_studio.md` §14 D3b).
 *
 * The mover (`boardMove.ts`) runs against `createRealDoor` with a fake `fetch`, so
 * the test reads the wire body and the sentence that the member sees. The D3 tests
 * in `StudioBoard.test.ts` cover the same mover against a fake door.
 *
 * Mutation 1: a drop calls `door.run` twice, or the mover skips the `pending` lock.
 * "one drop makes one update" and "two drops while one is in flight make one call" turn red.
 * Mutation 2: drop the `from === to` guard in `move`. "a drop on the same lane sends
 * nothing" turns red.
 * Mutation 3: send `{id, [groupBy]: option}` with no `data` wrapper, or send the whole
 * row. "sends id and a one-key data" turns red.
 * Mutation 4: skip `placed: back()` in the refusal branch. "a 422 restores the card at
 * its lane and index" turns red.
 * Mutation 5: read the refusal text without `readRefusal`. "a 422 names the rule" and
 * "a 503 says when to try again" turn red.
 * Mutation 6: let `door.read` accept `.update`. The read guard tests of `realDoor.test.ts`
 * turn red.
 * Mutation 7: remove `update` from `objectBoardBlock`. "the live block names the update
 * action of the object" turns red.
 */
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll } from "@/lib/dataCache";
import { buildLanes, parseBoard } from "@/lib/studio/boardModel";
import { createMover } from "@/lib/studio/boardMove";
import { NOT_DECLARED_SENTENCE, createRealDoor } from "@/lib/studio/realDoor";
import { fieldSpecs, objectBoardBlock } from "@/lib/studio/packagePreview";
import { computedKeys } from "@/lib/studio/inlineEdit";

beforeEach(() => clearAll());

const PKG = "123e4567-e89b-12d3-a456-426614174000";
const ACTIONS = [
  { id: "find", uses: "object:visit.list" },
  { id: "change", uses: "object:visit.update" },
];
const OBJECT = {
  id: "o1", name: "visit", label: "Visit",
  rules: [{ name: "t", kind: "formula", target: "total" }],
  fields: [
    { key: "title", type: "text", label: "Title", required: true, many: false, options: null, to: null, hot: false },
    { key: "total", type: "select", label: "Total", required: false, many: false, options: ["x"], to: null, hot: false },
    { key: "status", type: "select", label: "Status", required: false, many: false, options: ["Planned", "On site", "Closed"], to: null, hot: false },
    { key: "owner", type: "text", label: "Owner", required: false, many: false, options: null, to: null, hot: false },
  ],
};

interface Call { url: string; body: Record<string, unknown> }
function wire(answer: () => Response | Promise<Response>) {
  const calls: Call[] = [];
  const impl = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : {} });
    return answer();
  });
  return { calls, door: createRealDoor({ packageId: PKG, actions: ACTIONS, fetchImpl: impl as unknown as typeof fetch }) };
}
const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers });
const ROWS = [
  { id: "a", title: "A", status: "Planned" },
  { id: "b", title: "B", status: "Planned" },
  { id: "c", title: "C", status: "On site" },
];
function config() {
  const node = objectBoardBlock(OBJECT, fieldSpecs(OBJECT), computedKeys(OBJECT.rules), { action: "object:visit.list" });
  const parsed = parseBoard(node?.props.data);
  if (!parsed.ok) throw new Error(parsed.reason);
  return parsed.config;
}

describe("a drop on the real door", () => {
  it("one drop makes one update that sends id and a one-key data", async () => {
    const { door, calls } = wire(() => json(200, { record: { id: "a", data: {} } }));
    const mover = createMover(door, "object:visit.update", "status");
    await mover.move("a", "Planned", "Closed", "Closed");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`/api/studio/apps/${PKG}/actions/change`);
    expect(calls[0].body).toEqual({ id: "a", data: { status: "Closed" } });
    expect(mover.getState().placed).toEqual({ a: "Closed" });
    expect(mover.getState().alert).toBeNull();
  });
  it("a drop on the same lane sends nothing", async () => {
    const { door, calls } = wire(() => json(200, {}));
    await createMover(door, "object:visit.update", "status").move("a", "Planned", "Planned", "Planned");
    expect(calls).toHaveLength(0);
  });
  it("two drops while one is in flight make one call", async () => {
    let open: (r: Response) => void = () => {};
    const { door, calls } = wire(() => new Promise<Response>((r) => { open = r; }));
    const mover = createMover(door, "object:visit.update", "status");
    const first = mover.move("a", "Planned", "Closed", "Closed");
    await mover.move("a", "Planned", "On site", "On site");
    expect(calls).toHaveLength(1);
    expect(mover.getState().placed).toEqual({ a: "Closed" });
    open(json(200, {}));
    await first;
    expect(mover.getState().pending.size).toBe(0);
  });
  it("a 422 restores the card at its lane and index", async () => {
    const { door } = wire(() => json(422, { detail: { code: "field_invalid", message: "Not allowed.", fieldErrors: [] } }));
    const mover = createMover(door, "object:visit.update", "status");
    const cfg = config();
    const before = buildLanes(cfg, ROWS).map((l) => l.rows.map((r) => r.id));
    await mover.move("a", "Planned", "Closed", "Closed");
    const after = buildLanes(cfg, ROWS, { placed: mover.getState().placed }).map((l) => l.rows.map((r) => r.id));
    expect(after).toEqual(before);
    expect(before[0]).toEqual(["a", "b"]);
    expect(mover.getState().alert).toContain("Not allowed.");
  });
  it("a 422 names the rule", async () => {
    const { door } = wire(() => json(422, {
      detail: { code: "rule_refused", rule: "close_needs_done", message: "Rule `close_needs_done` of object `visit`: A visit closes only when it is done." },
    }));
    const mover = createMover(door, "object:visit.update", "status");
    await mover.move("a", "Planned", "Closed", "Closed");
    expect(mover.getState().alert).toBe("Refused by close_needs_done: A visit closes only when it is done.");
    expect(mover.getState().placed).toEqual({});
  });
  it("a 503 says when to try again and restores the card", async () => {
    const { door } = wire(() => json(503, { detail: { code: "store_busy" } }, { "Retry-After": "1" }));
    const mover = createMover(door, "object:visit.update", "status");
    await mover.move("a", "Planned", "Closed", "Closed");
    expect(mover.getState().alert).toBe("This app is busy now. Try again in 1 second.");
    expect(mover.getState().placed).toEqual({});
    expect(mover.getState().pending.size).toBe(0);
  });
  it("a package that does not declare the update refuses with its sentence and no call", async () => {
    const calls: unknown[] = [];
    const door = createRealDoor({
      packageId: PKG, actions: [{ id: "find", uses: "object:visit.list" }],
      fetchImpl: (async (...a: unknown[]) => { calls.push(a); return json(200, {}); }) as unknown as typeof fetch,
    });
    const mover = createMover(door, "object:visit.update", "status");
    await mover.move("a", "Planned", "Closed", "Closed");
    expect(calls).toHaveLength(0);
    expect(mover.getState().alert).toBe(NOT_DECLARED_SENTENCE);
  });
  it("a mount sends no write: the door reads the list and nothing else", async () => {
    const { door, calls } = wire(() => json(200, { records: [{ id: "a", data: { status: "Planned" } }] }));
    expect(await door.read({ action: "object:visit.list" })).toEqual([{ id: "a", status: "Planned" }]);
    expect(calls.map((c) => c.url)).toEqual([`/api/studio/apps/${PKG}/actions/find`]);
  });
});

describe("the Board block of an object", () => {
  const specs = fieldSpecs(OBJECT);
  const computed = computedKeys(OBJECT.rules);
  const src = { action: "object:visit.list" };

  it("groups by the first select field that no rule fills, and names the update action", () => {
    const node = objectBoardBlock(OBJECT, specs, computed, src);
    expect(node?.props.name).toBe("board");
    expect(node?.props.source).toEqual(src);
    expect(node?.props.data).toMatchObject({ groupBy: "status", update: "object:visit.update", titleField: "title" });
  });
  it("the live block names the update action of the object", () => {
    const node = objectBoardBlock({ name: "site", label: null }, specs, computed, { action: "object:site.list" });
    expect((node?.props.data as { update: string }).update).toBe("object:site.update");
  });
  it("gives no block for an object with no select field", () => {
    const text = specs.filter((f) => f.type === "text");
    expect(objectBoardBlock(OBJECT, text, computed, src)).toBeNull();
  });
  it("skips a select field with no options, and a select of many values", () => {
    const f = [
      { key: "a", type: "select" as const },
      { key: "b", type: "select" as const, many: true, options: ["x"] },
      { key: "c", type: "select" as const, options: ["x", "y"] },
    ];
    expect((objectBoardBlock(OBJECT, f, new Set(), src)?.props.data as { groupBy: string }).groupBy).toBe("c");
  });
  it("draws a lane for each option, and the cards sit in the lane of their value", () => {
    const lanes = buildLanes(config(), ROWS);
    expect(lanes.map((l) => [l.title, l.rows.map((r) => r.id)])).toEqual([
      ["Planned", ["a", "b"]], ["On site", ["c"]], ["Closed", []],
    ]);
  });
});

describe("the page source", () => {
  const page = readFileSync(new URL("../../app/studio/package-preview/page.tsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  it("draws the Board on the one door and reads no second source", () => {
    const board = page.slice(page.indexOf('aria-label="Board"'), page.indexOf('aria-label="Form"'));
    expect(board).toContain("<GenerativeUINode spec={board} />");
    expect(board).not.toMatch(/createRealDoor|createMover|door\.run|fetch\(/);
    expect(page).toContain("objectBoardBlock(");
    expect(page.match(/createDoorKeeper\(\)/g)).toHaveLength(1);
  });
  it("says so when the object has no select field", () => {
    expect(page).toContain("has no select field");
  });
});
