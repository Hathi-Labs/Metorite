/**
 * The task panel's two streams.
 *
 * ⚠️ `describeActivity` had NO test before 2026-09-21, and it is the function
 * that decides what a member reads about their own work. It was printing
 * `Edited due_at, start_date`. Moving it out of `TaskPanel.tsx` is what made
 * these six lines possible at all.
 */
import { describe, expect, it } from "vitest";

import type { ActivityRow } from "./api";
import {
  ROLLUP_AT,
  describeActivity,
  isComment,
  rollUp,
  threadComments,
} from "./activityStream";

function row(over: Partial<ActivityRow> = {}): ActivityRow {
  return {
    id: "a1",
    type: "comment",
    body: "hello",
    created_by: "ada@fracktal.in",
    created_at: "2026-09-20T09:00:00Z",
    ...over,
  };
}

const edit = (...fields: string[]) =>
  row({
    type: "field_change",
    body: null,
    meta: { changes: fields.map((field) => ({ field, old: null, new: "x" })) },
  });

describe("describeActivity", () => {
  it("names the fields the way a person names them", () => {
    // THE reported line, and what it must read instead.
    expect(describeActivity(edit("due_at", "start_date"))).toBe(
      "Edited Due date, Start date",
    );
  });

  it("uses a custom field's own label", () => {
    const defs = [
      {
        id: "f1",
        project_id: "p1",
        field_key: "customer_po",
        name: "Customer PO",
        field_type: "text" as const,
        options: [],
        required: false,
        position: 0,
      },
    ];
    expect(describeActivity(edit("custom.customer_po"), defs)).toBe(
      "Edited Customer PO",
    );
  });

  it("still says something when the diff is empty", () => {
    // A `field_change` whose meta lost its changes is a corrupt row, and an
    // empty line reads as a rendering bug rather than as bad data.
    expect(describeActivity(row({ type: "field_change", meta: {} }))).toBe(
      "Edited fields",
    );
  });

  it("never prints a raw activity type at a member", () => {
    // ⚠️ The default branch used to return `activity.type`. `ACTIVITY_TYPES`
    // lives in `core.py` and this file mirrors it, so the mirror WILL fall
    // behind — it has to fall behind readably.
    expect(describeActivity(row({ type: "some_new_thing", body: null }))).toBe(
      "Some new thing",
    );
  });

  it("reads an assignment in both directions", () => {
    expect(
      describeActivity(
        row({ type: "assignment", body: null, meta: { added: ["ada@x.com"] } }),
      ),
    ).toBe("assigned ada@x.com");
  });
});

describe("isComment", () => {
  it("cuts the stream where the server cuts it", () => {
    // The gateway's `kind` filter is `type = 'comment'` / `type <> 'comment'`.
    // Two definitions of "a comment" is how a row lands in neither list.
    expect(isComment(row())).toBe(true);
    expect(isComment(row({ type: "field_change" }))).toBe(false);
    expect(isComment(row({ type: "mention" }))).toBe(false);
  });
});

describe("threadComments", () => {
  const root = row({ id: "r1", created_at: "2026-09-20T09:00:00Z" });
  const older = row({ id: "r0", created_at: "2026-09-19T09:00:00Z" });

  it("hangs a reply under the comment it answers", () => {
    const reply = row({ id: "c2", parent_id: "r1" });
    const [thread] = threadComments([root, reply]);
    expect(thread.root.id).toBe("r1");
    expect(thread.replies.map((r) => r.id)).toEqual(["c2"]);
  });

  it("keeps roots newest first and replies oldest first", () => {
    // The list reads newest-first like the rest of the panel; a CONVERSATION
    // reads downwards.
    const late = row({ id: "c3", parent_id: "r1", created_at: "2026-09-20T11:00:00Z" });
    const early = row({ id: "c2", parent_id: "r1", created_at: "2026-09-20T10:00:00Z" });
    const threads = threadComments([root, late, early, older]);
    expect(threads.map((t) => t.root.id)).toEqual(["r1", "r0"]);
    expect(threads[0].replies.map((r) => r.id)).toEqual(["c2", "c3"]);
  });

  it("PROMOTES a reply whose root is not in the page", () => {
    // ⚠️ The case that decides whether a member loses their writing.
    // Two ordinary ways to get here: the root is older than the page
    // boundary, or the root was soft-deleted (migration 208 does not
    // cascade, on purpose). Dropping the row would be silent data loss.
    const orphan = row({ id: "c9", parent_id: "gone" });
    const threads = threadComments([orphan]);
    expect(threads).toHaveLength(1);
    expect(threads[0].root.id).toBe("c9");
  });

  it("leaves system events out of the threads entirely", () => {
    expect(threadComments([row({ type: "field_change" })])).toEqual([]);
  });

  it("does not fall over on a reply to a reply", () => {
    // `add_comment` refuses this, so it should not exist. A client that
    // crashes on impossible data is a client that crashes on a bug in
    // somebody else's release.
    const reply = row({ id: "c2", parent_id: "r1" });
    const deeper = row({ id: "c3", parent_id: "c2" });
    const threads = threadComments([root, reply, deeper]);
    const seen = threads.flatMap((t) => [t.root.id, ...t.replies.map((r) => r.id)]);
    expect(seen.sort()).toEqual(["c2", "c3", "r1"]);
  });

  it("holds a comment exactly once", () => {
    // A row that appeared as both a root and a reply would render twice, and
    // the second copy would look like a double post.
    const rows = [root, row({ id: "c2", parent_id: "r1" }), older];
    const seen = threadComments(rows).flatMap((t) => [
      t.root.id,
      ...t.replies.map((r) => r.id),
    ]);
    expect(new Set(seen).size).toBe(seen.length);
    expect(seen.length).toBe(rows.length);
  });
});

describe("rollUp", () => {
  const ten = Array.from({ length: 10 }, (_, i) => i);

  it("shows five and counts the rest", () => {
    expect(rollUp(ten)).toEqual({ shown: [0, 1, 2, 3, 4], hidden: 5 });
    expect(ROLLUP_AT).toBe(5);
  });

  it("hides nothing when there is nothing to hide", () => {
    expect(rollUp([1, 2])).toEqual({ shown: [1, 2], hidden: 0 });
  });

  it("counts rows the SERVER holds, not only the page", () => {
    // ⚠️ The button says "Show 45 older". Computing that from the page alone
    // promises five when there are fifty.
    expect(rollUp(ten, { total: 50 }).hidden).toBe(45);
  });

  it("reports what is still unfetched once it is expanded", () => {
    // Expanded, everything in hand is drawn — and `hidden` becomes the panel's
    // signal that another page exists.
    expect(rollUp(ten, { expanded: true, total: 50 })).toEqual({
      shown: ten,
      hidden: 40,
    });
  });

  it("never reports a negative count from a stale total", () => {
    // A `total` read before the last page arrived can be SMALLER than the
    // rows in hand. "Show -3 older" is the kind of thing that ships.
    expect(rollUp(ten, { total: 2 }).hidden).toBe(5);
    expect(rollUp(ten, { expanded: true, total: 2 }).hidden).toBe(0);
  });
});
