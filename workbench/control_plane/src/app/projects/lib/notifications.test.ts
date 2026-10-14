/**
 * Projects · the notification bell's pure half (WS-27j).
 *
 * The two that matter are at the ends: `badge`, because a bell that always
 * shows a number is a bell people stop reading, and `insertMention`, because a
 * mention token the gateway does not parse is a notification nobody receives —
 * the exact failure this ticket exists to end.
 */

import { describe as suite, expect, it } from "vitest";

import type { NotificationRow } from "./api";
import {
  BADGE_MAX,
  actorLabel,
  afterRead,
  badge,
  describe,
  insertMention,
  linkTo,
  mentionsIn,
  notDeliveredNotice,
  order,
  taskLabel,
  unreadIds,
  unreadSplit,
} from "./notifications";

const row = (over: Partial<NotificationRow> = {}): NotificationRow => ({
  id: "n1",
  kind: "assigned",
  task_id: "t1",
  actor: "priya@fracktal.in",
  created_at: "2026-08-07T10:00:00Z",
  read_at: null,
  task_title: "Fix the extruder",
  task_number: 12,
  ...over,
});

suite("badge", () => {
  it("draws nothing at zero", () => {
    // Not "0". A bell wearing a zero always looks like it wants something.
    expect(badge(0)).toBeNull();
  });

  it("draws nothing for a negative or nonsense count", () => {
    expect(badge(-1)).toBeNull();
    expect(badge(NaN)).toBeNull();
  });

  it("counts up to the cap", () => {
    expect(badge(1)).toBe("1");
    expect(badge(BADGE_MAX)).toBe(String(BADGE_MAX));
  });

  it("gestures past the cap instead of overflowing the dot", () => {
    expect(badge(BADGE_MAX + 1)).toBe(`${BADGE_MAX}+`);
  });
});

suite("unreadSplit", () => {
  it("passes a well-formed split through", () => {
    expect(unreadSplit({ total: 5, mentions: 2 })).toEqual({
      total: 5,
      mentions: 2,
    });
  });

  it("tolerates the pre-WS-27v bare number", () => {
    // A stale gateway behind a fresh UI is a deploy-window reality; a bell
    // that crashes during it notifies nobody about anything.
    expect(unreadSplit(7)).toEqual({ total: 7, mentions: 0 });
  });

  it("clamps mentions to total — a subset cannot outgrow its set", () => {
    expect(unreadSplit({ total: 1, mentions: 9 })).toEqual({
      total: 1,
      mentions: 1,
    });
  });

  it("zeroes nonsense instead of drawing it", () => {
    expect(unreadSplit(null)).toEqual({ total: 0, mentions: 0 });
    expect(unreadSplit({ total: -3, mentions: NaN })).toEqual({
      total: 0,
      mentions: 0,
    });
  });
});

suite("afterRead", () => {
  it("shrinks both counts when the row read was a mention", () => {
    expect(afterRead({ total: 3, mentions: 2 }, "mention")).toEqual({
      total: 2,
      mentions: 1,
    });
  });

  it("shrinks only the total for any other kind", () => {
    expect(afterRead({ total: 3, mentions: 2 }, "comment")).toEqual({
      total: 2,
      mentions: 2,
    });
  });

  it("never goes negative on a double-click", () => {
    expect(afterRead({ total: 0, mentions: 0 }, "mention")).toEqual({
      total: 0,
      mentions: 0,
    });
  });

  it("keeps mentions a subset even from an inconsistent input", () => {
    expect(afterRead({ total: 1, mentions: 1 }, "comment")).toEqual({
      total: 0,
      mentions: 0,
    });
  });
});

suite("insertMention", () => {
  it("writes the address form, because it is the only one parsed", () => {
    // Migration 148 dropped UNIQUE(name): `@Priya` has no answer.
    expect(insertMention("", 0, "priya@fracktal.in").body).toBe(
      "@priya@fracktal.in ",
    );
  });

  it("separates the token from the word before it", () => {
    // `ping@a@b.co` does not match the gateway's pattern at all, so a missing
    // space is a mention that silently reaches nobody.
    const { body } = insertMention("ping", 4, "priya@fracktal.in");
    expect(body).toBe("ping @priya@fracktal.in ");
    expect(mentionsIn(body)).toEqual(["priya@fracktal.in"]);
  });

  it("does not double the space when there already is one", () => {
    expect(insertMention("ping ", 5, "a@b.co").body).toBe("ping @a@b.co ");
  });

  it("does not add a trailing space in front of one", () => {
    // Both surrounding spaces are the body's own; the token adds neither.
    expect(insertMention("x  y", 2, "a@b.co").body).toBe("x @a@b.co y");
  });

  it("leaves the caret after the token, not inside it", () => {
    const { body, caret } = insertMention("hi", 2, "a@b.co");
    expect(body.slice(caret)).toBe("");
    // Typing on would continue the sentence, not corrupt the address.
    expect(mentionsIn(body + "please")).toEqual(["a@b.co"]);
  });

  it("inserts at the caret, not at the end", () => {
    expect(insertMention("a b", 1, "x@y.co").body).toBe("a @x@y.co b");
  });

  it("lowercases, matching how the gateway stores recipients", () => {
    expect(insertMention("", 0, "  Priya@Fracktal.IN ").body).toBe(
      "@priya@fracktal.in ",
    );
  });

  it("is a no-op for a blank address", () => {
    expect(insertMention("hi", 2, "  ")).toEqual({ body: "hi", caret: 2 });
  });

  it("clamps a caret outside the body", () => {
    expect(insertMention("hi", 99, "a@b.co").body).toBe("hi @a@b.co ");
  });
});

suite("mentionsIn", () => {
  it("agrees with the gateway about what counts as a mention", () => {
    expect(mentionsIn("@a@b.co and @Nope and @A@B.CO")).toEqual(["a@b.co"]);
  });

  it("finds nothing in an empty body", () => {
    expect(mentionsIn("")).toEqual([]);
  });
});

suite("describe", () => {
  it("names the actor by their local part, not the whole address", () => {
    expect(describe(row())).toBe("priya assigned you #12 Fix the extruder");
  });

  it("keeps an agent's whole handle", () => {
    // Splitting `agent:builder` at the colon would render every agent as
    // "agent", which identifies nobody.
    expect(actorLabel("agent:builder")).toBe("builder");
  });

  it("has a word for an actor it cannot name", () => {
    expect(actorLabel("")).toBe("somebody");
  });

  it("uses the right verb per kind", () => {
    expect(describe(row({ kind: "mention" }))).toContain("mentioned you on");
    expect(describe(row({ kind: "comment" }))).toContain("commented on");
  });

  it("names an agent's client and asks as the shell bell does", () => {
    // WS-55 EA-12c: the same words as `routes/shell/needs.py` `_sentence`.
    expect(describe(row({ kind: "agent_question", actor: "mcp:claude-code" }))).toBe(
      "Claude Code asked you about #12 Fix the extruder",
    );
  });
});

suite("taskLabel", () => {
  it("drops the number when the task has none", () => {
    expect(taskLabel(row({ task_number: null }))).toBe("Fix the extruder");
  });

  it("still says something when the title is missing", () => {
    // The join can come back thin; "assigned you" with nothing after it reads
    // as a broken row.
    expect(taskLabel(row({ task_title: null, task_number: null }))).toBe("a task");
  });
});

suite("order", () => {
  it("puts unread first, then newest first", () => {
    const rows = [
      row({ id: "read-new", read_at: "2026-08-07T12:00:00Z", created_at: "2026-08-07T11:00:00Z" }),
      row({ id: "unread-old", created_at: "2026-08-01T09:00:00Z" }),
      row({ id: "unread-new", created_at: "2026-08-07T09:00:00Z" }),
    ];
    expect(order(rows).map((r) => r.id)).toEqual([
      "unread-new",
      "unread-old",
      "read-new",
    ]);
  });

  it("does not mutate its input", () => {
    const rows = [row({ id: "a" }), row({ id: "b", created_at: "2026-09-01T00:00:00Z" })];
    order(rows);
    expect(rows.map((r) => r.id)).toEqual(["a", "b"]);
  });
});

suite("unreadIds", () => {
  it("is exactly the rows with no read_at", () => {
    expect(
      unreadIds([row({ id: "a" }), row({ id: "b", read_at: "2026-08-07T00:00:00Z" })]),
    ).toEqual(["a"]);
  });
});

suite("linkTo", () => {
  it("encodes the id it puts in the query", () => {
    expect(linkTo(row({ task_id: "a b&c" }))).toBe("/projects?task=a%20b%26c");
  });
});

suite("notDeliveredNotice", () => {
  it("says nothing when everybody was reached", () => {
    expect(notDeliveredNotice([])).toBeNull();
  });

  it("names who was missed and why", () => {
    // Silence here would leave the author believing they pulled a colleague in.
    const notice = notDeliveredNotice(["outsider@fracktal.in"]);
    expect(notice).toContain("outsider");
    expect(notice).toMatch(/cannot see/);
  });
});
