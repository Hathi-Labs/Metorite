/**
 * My Tasks · the empty-state copy (continuity P3, item 5).
 *
 * The box is shared (`@/components/EmptyState`). The words are this app's, and
 * the one sentence the owner named — "Inbox zero. Mind like water." — must
 * survive the move. Also fenced: no local empty-state box grows back in the two
 * files that held one, and the loading state is the shared skeleton.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { inboxEmptyCopy, tasksEmptyCopy } from "./emptyState";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

describe("tasksEmptyCopy", () => {
  it("keeps each view's own sentence", () => {
    expect(tasksEmptyCopy("inbox", false).message).toBe("Inbox zero. Mind like water.");
    expect(tasksEmptyCopy("waiting", false).message).toBe("Nothing on your Waiting-For list.");
    expect(tasksEmptyCopy("next", false).message).toBe("No next actions assigned to you.");
    expect(tasksEmptyCopy("done", false).message).toBe("Nothing here yet.");
  });

  it("a filtered list blames the filters and asks for the way out", () => {
    const copy = tasksEmptyCopy("next", true);
    expect(copy).toMatchObject({
      message: "No tasks match your filters.",
      filtered: true,
      tone: "muted",
    });
  });

  it("an honestly empty list is the success tick", () => {
    expect(tasksEmptyCopy("next", false)).toMatchObject({ tone: "success", filtered: false });
  });
});

describe("inboxEmptyCopy", () => {
  it("inbox zero keeps its words and counts the session", () => {
    expect(inboxEmptyCopy({ empty: true, processed: 0 })).toMatchObject({
      message: "Inbox zero. Mind like water.",
      hint: "Nothing left to process. Capture the next thing above.",
      tone: "success",
    });
    expect(inboxEmptyCopy({ empty: true, processed: 2 }).hint).toMatch(/processed 2 items/);
    expect(inboxEmptyCopy({ empty: true, processed: 1 }).hint).toMatch(/processed 1 item /);
  });

  it("a filter that hid everything says so", () => {
    expect(inboxEmptyCopy({ empty: false, processed: 0 })).toMatchObject({
      message: "Nothing in the inbox matches this filter.",
      filtered: true,
    });
  });
});

describe("both surfaces draw the shared box and the shared skeleton", () => {
  it.each([["../components/ItemList.tsx"], ["../components/InboxView.tsx"]])(
    "%s",
    (rel) => {
      const src = read(rel);
      expect(src).toMatch(/from "@\/components\/EmptyState"/);
      expect(src).toMatch(/<EmptyState\b/);
      expect(src).toMatch(/<SkeletonRows\b/);
      // The retired local boxes, and the spinner the load drew.
      expect(src).not.toMatch(/function (NoMatchState|EmptyState)\b/);
      expect(src).not.toMatch(/Loading your inbox|>Loading…</);
      expect(src).not.toMatch(/Mind like water/);
    },
  );
});
