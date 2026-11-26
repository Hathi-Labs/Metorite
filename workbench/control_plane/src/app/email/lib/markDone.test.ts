// Task 3995, Mark done (`email_app_master_plan.md` §6.6).
//
// R7 fences named here:
//   * `email-done-key`: the key `d` marks the open mail, behind the same
//     guards as each other key, and `e` stays Archive.
//   * `email-done-store`: the loaded rows change before the answer, one call
//     goes for each conversation, a failed call puts them back, and a move back
//     sends the state of before.
//   * `email-done-undo-stack`: the toast button and Ctrl+Z run one step.
// The menu order has its own fence in `components/markDoneMenu.test.ts`.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  resolveThread: vi.fn(),
  getMarkDoneEnabled: vi.fn(),
  listEmails: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, ...api };
});

import { useEmailStore } from "./emailStore";
import {
  MARK_DONE_KEY, MOVE_BACK_LABEL, doneCategories, leavesView, markDoneEntry, markDoneItemLabel,
  moveBackLabel, movedOn, nextSelection, restoredCategories, threadsOf,
} from "./markDone";
import type { Email } from "./types";

const mail = (id: string, over: Partial<Email> = {}): Email =>
  ({
    id,
    accountId: "a",
    threadId: `t-${id}`,
    categories: [],
    folder: "inbox",
    receivedAt: "2026-11-01T10:00:00Z",
    isRead: true,
    isStarred: false,
    isFlagged: false,
    subject: id,
    from: { name: "N", email: "n@x.test" },
    ...over,
  }) as Email;

function deferred<T>() {
  let done!: (value: T) => void;
  let fail!: (err: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    done = res;
    fail = rej;
  });
  return { promise, done, fail };
}

const rows = () => useEmailStore.getState().emails;
const cats = (id: string) => rows().find((e) => e.id === id)?.categories;

beforeEach(() => {
  for (const f of Object.values(api)) f.mockReset();
  useEmailStore.setState({
    emails: [
      mail("one", { categories: ["Awaiting Reply", "Follow-up", "Client"] }),
      mail("two", { categories: ["Needs Reply"] }),
      mail("three", { categories: ["Follow-up"] }),
    ],
    emailsTotal: 3,
    selectedAccountId: null,
    selectedEmailId: "one",
    selectedEmailOverride: null,
    selectedLabel: null,
    searchFilters: [],
    messagePatches: {},
    error: null,
    markStamp: 0,
    markDoneEnabled: false,
  });
});

// ── The key ─────────────────────────────────────────────────────────────────

describe("the key", () => {
  const page = readFileSync(resolve(__dirname, "../page.tsx"), "utf-8");

  it("claims the undo scope only with Mark done on (D10)", () => {
    expect(page).not.toContain('useUndoScope("email")');
    expect(page).toContain('if (canMarkDone) setUndoScope("email");');
  });
  const at = (needle: string) => {
    const found = page.indexOf(needle);
    expect(found, needle).toBeGreaterThan(-1);
    return found;
  };

  it("is d, after each guard of the one key handler", () => {
    expect(MARK_DONE_KEY).toBe("d");
    const key = at("case MARK_DONE_KEY:");
    for (const guard of [
      "if (typing || e.metaKey || e.ctrlKey || e.altKey) return;",
      "if (composeOpen || showAddModal || noAccounts",
      "if (automationFeature) return;",
      "switch (e.key) {",
    ]) {
      expect(at(guard)).toBeLessThan(key);
    }
    expect(page.slice(key, key + 220)).toContain("em && canMarkDone && !e.repeat");
  });

  it("leaves e as Archive, and no other case takes d", () => {
    const archive = at('case "e":');
    expect(page.slice(archive, archive + 140)).toContain('folder: "archive"');
    expect(page).not.toContain('case "d":');
  });
});

// ── The rules ───────────────────────────────────────────────────────────────

describe("the rules", () => {
  it("takes one conversation for each pair of mailbox and thread", () => {
    expect(
      threadsOf(
        [
          { accountId: "a", threadId: "t" },
          { accountId: "a", threadId: "t" },
          { accountId: "b", threadId: "t" },
          { accountId: "a" },
          { threadId: "u" },
        ],
        "c",
      ),
    ).toEqual([
      { accountId: "a", threadId: "t" },
      { accountId: "b", threadId: "t" },
      { accountId: "c", threadId: "u" },
    ]);
  });

  it("puts Done on the newest row that is not sent, and strips the open labels", () => {
    const got = doneCategories([
      mail("in", { categories: ["Needs Reply", "Client"] }),
      mail("out", {
        folder: "sent", receivedAt: "2026-11-02T10:00:00Z",
        categories: ["Follow-up", "Awaiting Reply"],
      }),
    ]);
    expect(got.get("in")).toEqual(["Client", "Done"]);
    expect(got.get("out")).toEqual([]);
  });

  it("puts Follow-up back on the newest row", () => {
    const got = restoredCategories(
      [
        mail("in", { categories: ["Done"] }),
        mail("out", { folder: "sent", receivedAt: "2026-11-02T10:00:00Z" }),
      ],
      { status: "AWAITING", followUp: true },
    );
    expect(got.get("in")).toEqual(["Awaiting Reply"]);
    expect(got.get("out")).toEqual(["Follow-up"]);
  });

  it("puts no label on a draft, also when the draft is the newest row", () => {
    const thread = [
      mail("in", { categories: ["Follow-up"] }),
      mail("out", { folder: "sent", receivedAt: "2026-11-02T10:00:00Z" }),
      mail("nudge", { folder: "Drafts", receivedAt: "2026-11-03T10:00:00Z" }),
    ];
    const done = doneCategories(thread);
    expect(done.get("in")).toEqual(["Done"]);
    expect(done.get("nudge")).toEqual([]);
    const after = thread.map((m) => ({ ...m, categories: done.get(m.id) ?? [] }));
    const back = restoredCategories(after, { status: "AWAITING", followUp: true });
    expect(back.get("in")).toEqual(["Awaiting Reply"]);
    expect(back.get("out")).toEqual(["Follow-up"]);
    expect(back.get("nudge")).toEqual([]);
  });

  it("names the toast button for what it restores", () => {
    const ref = { accountId: "a", threadId: "t" };
    const fu = { ...ref, previous: { status: "AWAITING" as const, followUp: true } };
    const plain = { ...ref, previous: { status: "NEEDS_REPLY" as const, followUp: false } };
    expect(moveBackLabel([fu, plain])).toBe(MOVE_BACK_LABEL);
    expect(moveBackLabel([plain])).toBe("Undo");
    expect(moveBackLabel([{ ...ref, previous: null }])).toBeNull();
    expect(markDoneItemLabel(false, 1)).toBe("Mark done");
  });

  it("takes a row out of a view only when it loses the label of the view", () => {
    expect(leavesView(["follow-up"], ["Follow-up"], ["Done"])).toBe(true);
    expect(leavesView(["follow-up"], ["Client"], ["Done"])).toBe(false);
    expect(leavesView([], ["Follow-up"], [])).toBe(false);
  });

  it("opens the mail at the place of the one that left", () => {
    const list = [{ id: "a" }, { id: "b" }, { id: "c" }];
    expect(nextSelection(list, new Set(["b"]), "b")).toBe("c");
    expect(nextSelection(list, new Set(["c"]), "c")).toBe("b");
    expect(nextSelection(list, new Set(["a", "b", "c"]), "a")).toBeNull();
  });
});

// ── The store ───────────────────────────────────────────────────────────────

describe("Mark done in the store", () => {
  it("changes every loaded row of the thread before the answer, with one call", async () => {
    useEmailStore.setState({
      emails: [
        mail("one", { threadId: "t", categories: ["Follow-up"] }),
        mail("older", {
          threadId: "t", categories: ["Needs Reply"], receivedAt: "2026-10-01T10:00:00Z",
        }),
        mail("two", { categories: ["Needs Reply"] }),
      ],
    });
    const answer = deferred<{ previous: null }>();
    api.resolveThread.mockReturnValue(answer.promise);
    const run = useEmailStore.getState().markThreadsDone(rows().slice(0, 2));
    expect(cats("one")).toEqual(["Done"]);
    expect(cats("older")).toEqual([]);
    expect(cats("two")).toEqual(["Needs Reply"]);
    answer.done({ previous: null });
    await run;
    expect(api.resolveThread.mock.calls).toEqual([["a", "t"]]);
    expect(useEmailStore.getState().markStamp).toBe(1);
    expect(useEmailStore.getState().messagePatches).toEqual({});
  });

  it("sends one call for each conversation of a bulk act, and no second one in flight", async () => {
    const answer = deferred<{ previous: null }>();
    api.resolveThread.mockReturnValue(answer.promise);
    const first = useEmailStore.getState().markThreadsDone(rows());
    const again = await useEmailStore.getState().markThreadsDone([rows()[0]]);
    expect(again).toEqual({ marked: [], failed: 0 });
    answer.done({ previous: null });
    expect((await first).marked).toHaveLength(3);
    expect(api.resolveThread).toHaveBeenCalledTimes(3);
  });

  it("puts the rows back and shows the error when the call fails", async () => {
    api.resolveThread.mockRejectedValue(new Error("This mailbox needs send access."));
    const out = await useEmailStore.getState().markThreadsDone([rows()[0]]);
    expect(out).toEqual({ marked: [], failed: 1 });
    expect(cats("one")).toEqual(["Awaiting Reply", "Follow-up", "Client"]);
    expect(useEmailStore.getState().error).toBe("This mailbox needs send access.");
    expect(useEmailStore.getState().messagePatches).toEqual({});
  });

  it("takes the row out of the Follow-up view, opens the next, and brings it back", async () => {
    useEmailStore.setState({ searchFilters: [{ kind: "tag", value: "Follow-up" }] });
    useEmailStore.setState({ selectedEmailId: "one" });
    api.resolveThread.mockResolvedValue({
      previous: { status: "AWAITING", followUp: true },
    });
    const out = await useEmailStore.getState().markThreadsDone([rows()[0]]);
    expect(rows().map((e) => e.id)).toEqual(["two", "three"]);
    expect(useEmailStore.getState().emailsTotal).toBe(2);
    expect(useEmailStore.getState().selectedEmailId).toBe("two");

    api.resolveThread.mockClear();
    await useEmailStore.getState().moveThreadsBack(out.marked);
    expect(api.resolveThread.mock.calls).toEqual([
      ["a", "t-one", { done: false, restore: { status: "AWAITING", followUp: true } }],
    ]);
    expect(rows().map((e) => e.id)).toEqual(["one", "two", "three"]);
    expect(cats("one")).toEqual(["Awaiting Reply", "Follow-up", "Client"]);
    expect(useEmailStore.getState().emailsTotal).toBe(3);
  });

  it("does not put a row into another view on a move back", async () => {
    useEmailStore.setState({ selectedLabel: "Follow-up" });
    api.resolveThread.mockResolvedValue({
      previous: { status: "AWAITING", followUp: true },
    });
    const out = await useEmailStore.getState().markThreadsDone([rows()[0]]);
    // The member opened another folder before the toast button.
    useEmailStore.setState({ selectedLabel: null, selectedFolder: "sent", emails: [] });
    await useEmailStore.getState().moveThreadsBack(out.marked);
    expect(rows()).toEqual([]);
  });

  it("puts a row that left the view back when the call fails", async () => {
    useEmailStore.setState({ selectedLabel: "Follow-up" });
    api.resolveThread.mockRejectedValue(new Error("no"));
    await useEmailStore.getState().markThreadsDone([rows()[0]]);
    expect(rows().map((e) => e.id)).toEqual(["one", "two", "three"]);
    expect(useEmailStore.getState().emailsTotal).toBe(3);
    expect(useEmailStore.getState().selectedEmailId).toBe("one");
  });

  it("drops a move back that the gateway refused because the thread changed", async () => {
    api.resolveThread.mockResolvedValue({ previous: { status: "AWAITING", followUp: true } });
    const out = await useEmailStore.getState().markThreadsDone([rows()[0]]);
    const changed = Object.assign(new Error("This conversation changed after you marked it done."), {
      status: 409,
    });
    api.resolveThread.mockRejectedValue(changed);
    // Nothing is left to undo, so the step counts as done and leaves the stack.
    expect(await useEmailStore.getState().moveThreadsBack(out.marked)).toBe(0);
    expect(cats("one")).toEqual(["Client", "Done"]);
    expect(useEmailStore.getState().error).toBe(changed.message);
    expect(movedOn(changed)).toBe(true);
    expect(movedOn(new Error("Gateway error 502"))).toBe(false);
    // Another failure keeps the step, so the member can try again.
    api.resolveThread.mockRejectedValue(Object.assign(new Error("down"), { status: 502 }));
    expect(await useEmailStore.getState().moveThreadsBack(out.marked)).toBe(1);
  });

  it("reads the flag, and a failed read keeps Mark done off", async () => {
    api.getMarkDoneEnabled.mockResolvedValue(true);
    await useEmailStore.getState().fetchMarkDone();
    expect(useEmailStore.getState().markDoneEnabled).toBe(true);
    api.getMarkDoneEnabled.mockRejectedValue(new Error("404"));
    await useEmailStore.getState().fetchMarkDone();
    expect(useEmailStore.getState().markDoneEnabled).toBe(false);
  });
});

describe("the step on the undo stack", () => {
  const marked = {
    accountId: "a", threadId: "t", previous: { status: "AWAITING" as const, followUp: true },
  };

  it("moves back on undo, and restores what a redo found", async () => {
    const found = { ...marked, previous: { status: "NEEDS_REPLY" as const, followUp: false } };
    const moveBack = vi.fn().mockResolvedValue(0);
    const markDone = vi.fn().mockResolvedValue({ marked: [found], failed: 0 });
    const entry = markDoneEntry([marked, { ...marked, threadId: "u", previous: null }], {
      markDone, moveBack,
    });
    await entry.undo();
    expect(moveBack).toHaveBeenLastCalledWith([marked]);
    await entry.redo();
    expect(markDone).toHaveBeenCalledWith([marked]);
    await entry.undo();
    expect(moveBack).toHaveBeenLastCalledWith([found]);
  });

  it("rejects when a write fails, so the stack keeps the step", async () => {
    const entry = markDoneEntry([marked], {
      markDone: vi.fn().mockResolvedValue({ marked: [], failed: 1 }),
      moveBack: vi.fn().mockResolvedValue(1),
    });
    await expect(entry.undo()).rejects.toThrow();
    await expect(entry.redo()).rejects.toThrow();
  });
});
