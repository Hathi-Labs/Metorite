import { describe, expect, it } from "vitest";

import type { TaskRow } from "./api";
import {
  matchCandidates,
  mergeCandidates,
  mergeRefusal,
  mergeSummary,
} from "./taskMerge";

function task(over: Partial<TaskRow> = {}): TaskRow {
  return {
    id: "t1",
    project_id: "p1",
    status_id: "s1",
    title: "Fix the bootloader",
    task_number: 7,
    tags: [],
    assignees: [],
    custom_fields: {},
    ...over,
  } as TaskRow;
}

describe("mergeRefusal", () => {
  it("offers an ordinary task in the same project", () => {
    expect(mergeRefusal(task(), [])).toBeNull();
  });

  it("refuses a task that is being merged", () => {
    // The commonest mistake: picking one of the selected tasks as the target.
    expect(mergeRefusal(task({ id: "a" }), ["a"])).toContain("being merged");
  });

  it("refuses a task that is itself a merged stub", () => {
    // ⚠️ The gateway refuses this too. Saying so in the list means the
    // member never spends a click to be told — and a stub has no content
    // to receive, so merging into one is never what somebody meant.
    expect(
      mergeRefusal(task({ merged_into_task_id: "x", archived_at: "2026-09-21" }), []),
    ).toContain("Already merged");
  });

  it("refuses an archived task, and says what to do", () => {
    const why = mergeRefusal(task({ archived_at: "2026-09-21" }), []);
    expect(why).toContain("restore it first");
  });

  it("calls a merged stub MERGED, not archived", () => {
    // Both are true of the same row — the database guarantees it — so the
    // order of these two branches is the whole message. "Archived" here
    // would send somebody to restore a task whose content is elsewhere.
    const stub = task({ merged_into_task_id: "x", archived_at: "2026-09-21" });
    expect(mergeRefusal(stub, [])).not.toContain("restore");
  });
});

describe("mergeCandidates", () => {
  it("puts recent work first, not alphabetical neighbours", () => {
    const rows = [
      task({ id: "a", task_number: 3, title: "Aardvark" }),
      task({ id: "b", task_number: 91, title: "Zebra" }),
    ];
    expect(mergeCandidates(rows, []).map((c) => c.task.id)).toEqual(["b", "a"]);
  });

  it("KEEPS a refused task in the list rather than hiding it", () => {
    // ⚠️ `MoveDialog`'s rule. Hiding the task somebody is looking for reads
    // as the list being broken; showing it greyed with a reason answers the
    // question they actually have.
    const got = mergeCandidates([task({ id: "a" })], ["a"]);
    expect(got).toHaveLength(1);
    expect(got[0].refusal).not.toBeNull();
  });

  it("does not mutate what it was handed", () => {
    const rows = [task({ id: "a", task_number: 1 }), task({ id: "b", task_number: 2 })];
    mergeCandidates(rows, []);
    expect(rows.map((r) => r.id)).toEqual(["a", "b"]);
  });
});

describe("matchCandidates", () => {
  const rows = mergeCandidates(
    [
      task({ id: "a", task_number: 7, title: "Fix the bootloader" }),
      task({ id: "b", task_number: 42, title: "AI credit assignment" }),
    ],
    [],
  );

  it("matches on a word of the title, case-insensitively", () => {
    expect(matchCandidates(rows, "CREDIT").map((c) => c.task.id)).toEqual(["b"]);
  });

  it("matches on the task number", () => {
    expect(matchCandidates(rows, "42").map((c) => c.task.id)).toEqual(["b"]);
  });

  it("tolerates the # somebody types with the number", () => {
    // People paste "#42" because that is how the product prints it.
    expect(matchCandidates(rows, "#42").map((c) => c.task.id)).toEqual(["b"]);
  });

  it("returns everything for an empty or blank query", () => {
    expect(matchCandidates(rows, "")).toHaveLength(2);
    expect(matchCandidates(rows, "   ")).toHaveLength(2);
  });
});

describe("mergeSummary", () => {
  it("asks for a target before one is chosen", () => {
    expect(mergeSummary(null, ["a", "b"])).toContain("Choose the task to keep");
  });

  it("names the SURVIVOR, because that is the decision", () => {
    const got = mergeSummary(task({ task_number: 42, title: "Credits" }), ["a"]);
    expect(got).toContain("#42 Credits");
  });

  it("says archived, not deleted", () => {
    // 🔴 The owner's objection, in the one sentence they read before
    // agreeing. A merge that sounded like a delete would stop people using
    // it, and one that WAS a delete is the design they rejected.
    const got = mergeSummary(task(), ["a", "b"]);
    expect(got).toContain("archived");
    expect(got).not.toContain("delete");
  });

  it("counts one task as one, not as 1 tasks", () => {
    expect(mergeSummary(null, ["a"])).toContain("1 task will");
  });

  it("agrees with the count, because this is the sentence before the click", () => {
    const one = mergeSummary(task(), ["a"]);
    expect(one).toContain("Its comments");
    expect(one).toContain("it is archived");
    const many = mergeSummary(task(), ["a", "b"]);
    expect(many).toContain("Their comments");
    expect(many).toContain("they are archived");
  });
});
