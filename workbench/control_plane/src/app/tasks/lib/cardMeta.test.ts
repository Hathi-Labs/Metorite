/**
 * /tasks · the shared-vocabulary chip adapter.
 *
 * What is pinned: the adapter feeds `taskMeta` (never re-implements it), the
 * subtask-count chip rides in the shared reading order under the shared key
 * and icon, and a zero earns no chip — the same silences `taskMeta` keeps.
 */

import { describe, expect, it } from "vitest";

import { taskMeta } from "@/lib/taskCard";

import { taskMetaChips } from "./cardMeta";
import type { MyTask } from "./types";

const NOW = Date.parse("2026-08-09T12:00:00Z");

function item(patch: Partial<MyTask>): MyTask {
  return {
    id: "t1",
    source: "LOCAL",
    title: "A task",
    disposition: "NEXT",
    isMine: true,
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "2026-08-01T00:00:00Z",
    ...patch,
  };
}

describe("taskMetaChips", () => {
  it("earns nothing for a bare task — zeros are silent", () => {
    expect(taskMetaChips(item({}), NOW)).toEqual([]);
  });

  it("speaks the shared vocabulary for due, attachments and estimate", () => {
    const chips = taskMetaChips(
      item({
        dueAt: "2026-08-10T12:00:00Z",
        attachments: [{ kind: "file", name: "a", url: "u" }],
        timeEstimateMins: 90,
      }),
      NOW,
    );
    // Byte-identical to what taskMeta itself would say — the adapter maps
    // fields, it does not re-phrase.
    expect(chips).toEqual(
      taskMeta(
        { dueAt: "2026-08-10T12:00:00Z", attachmentCount: 1, estimateMins: 90 },
        NOW,
      ),
    );
    expect(chips.map((c) => c.key)).toEqual(["due", "attachments", "estimate"]);
  });

  it("escalates overdue exactly as the shared rule does — done is never overdue", () => {
    const late = taskMetaChips(item({ dueAt: "2026-08-01T00:00:00Z" }), NOW);
    expect(late[0]).toMatchObject({ key: "due", tone: "danger", icon: "AlertTriangle" });

    const done = taskMetaChips(
      item({ dueAt: "2026-08-01T00:00:00Z", completedAt: "2026-08-02T00:00:00Z" }),
      NOW,
    );
    expect(done[0]).toMatchObject({ key: "due", tone: "muted", icon: "Clock" });
  });

  it("counts subtasks in the shared slot, key and icon", () => {
    const chips = taskMetaChips(
      item({
        dueAt: "2026-08-10T12:00:00Z",
        subtaskCount: 3,
        attachments: [{ kind: "file", name: "a", url: "u" }],
      }),
      NOW,
    );
    // Shared reading order: due → subtasks → attachments.
    expect(chips.map((c) => c.key)).toEqual(["due", "subtasks", "attachments"]);
    expect(chips[1]).toMatchObject({
      icon: "ListTree",
      label: "3",
      tone: "muted",
      title: "3 subtasks",
    });
  });

  it("leads with the subtask count when there is no due date", () => {
    const chips = taskMetaChips(item({ subtaskCount: 1 }), NOW);
    expect(chips.map((c) => c.key)).toEqual(["subtasks"]);
    expect(chips[0].title).toBe("1 subtask");
  });
});
