import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  filterTimeline,
  nextOpenActivities,
  TIMELINE_FILTERS,
  taskDueChips,
  threadStatusLabel,
  timelineBranch,
  timelineKey,
} from "./timeline";
import type { Activity, EmailThread, StatusChange, TimelineEntry } from "./types";

const activity: Activity = {
  id: "act-1",
  type: "note",
  body: "Rang them",
};

const statusChange: StatusChange = {
  id: "sc-1",
  to_status: "Qualified",
  changed_by: "priya@fracktal.in",
};

const emailThread: EmailThread = {
  thread_id: "thread-1",
  account_id: "acct-1",
  message_id: "msg-1",
  subject: "Re: quote",
  status: "NEEDS_REPLY",
};

function entry(over: Partial<TimelineEntry>): TimelineEntry {
  return { kind: "activity", origin: "own", ...over };
}

describe("timelineBranch", () => {
  it("routes each kind to its own renderer", () => {
    expect(timelineBranch(entry({ kind: "activity", activity }))).toBe("activity");
    expect(
      timelineBranch(entry({ kind: "status_change", status_change: statusChange }))
    ).toBe("status_change");
    expect(
      timelineBranch(entry({ kind: "email_thread", email_thread: emailThread }))
    ).toBe("email_thread");
  });

  it("never routes an email thread into the activity branch", () => {
    // The bug this whole module exists for. `ActivityEntry` opens with
    // `entry.activity!` and then reads `activity.type` — an email entry
    // reaching it throws at runtime, and the `!` hides that from tsc. Before
    // the third branch existed the dispatch was binary, so "not a status
    // change" meant "an activity", and this was the shipped behaviour.
    const email = entry({ kind: "email_thread", email_thread: emailThread });
    expect(timelineBranch(email)).not.toBe("activity");
    expect(email.activity ?? null).toBeNull();
  });

  it("refuses an entry whose payload is missing rather than guessing", () => {
    // A kind with no payload means the server changed shape. Rendering it as
    // whatever branch happens to be last is the crash; an empty row reads as
    // data loss on a record somebody is trying to trust.
    expect(timelineBranch(entry({ kind: "email_thread" }))).toBe("unknown");
    expect(timelineBranch(entry({ kind: "activity", activity: null }))).toBe(
      "unknown"
    );
    expect(timelineBranch(entry({ kind: "status_change" }))).toBe("unknown");
  });
});

describe("timelineKey", () => {
  it("keys an email entry by its thread, not by its position", () => {
    // Entries arrive merged from three sources and re-sorted; a positional key
    // makes React reuse the wrong node the moment a newer email lands.
    expect(
      timelineKey(entry({ kind: "email_thread", email_thread: emailThread }), 3)
    ).toBe("email_thread-thread-1");
  });

  it("keeps the existing keys for the two older kinds", () => {
    expect(timelineKey(entry({ kind: "activity", activity }), 0)).toBe(
      "activity-act-1"
    );
    expect(
      timelineKey(entry({ kind: "status_change", status_change: statusChange }), 0)
    ).toBe("status_change-sc-1");
  });

  it("falls back to the index when an entry carries no id at all", () => {
    expect(timelineKey(entry({ kind: "activity" }), 7)).toBe("activity-7");
  });
});

describe("threadStatusLabel", () => {
  it("says the four statuses the way a human would", () => {
    expect(threadStatusLabel("NEEDS_REPLY")).toBe("Needs reply");
    expect(threadStatusLabel("AWAITING")).toBe("Awaiting reply");
    expect(threadStatusLabel("FYI")).toBe("FYI");
    expect(threadStatusLabel("DONE")).toBe("Done");
  });

  it("says nothing about a thread nobody classified", () => {
    // Absent is not the same as unknown: most threads have no status row, and
    // a badge on every one of them is noise the timeline does not need.
    expect(threadStatusLabel(null)).toBeNull();
    expect(threadStatusLabel(undefined)).toBeNull();
    expect(threadStatusLabel("  ")).toBeNull();
  });

  it("shows an unrecognised status verbatim rather than dropping it", () => {
    // The email app owns this vocabulary and may grow it. Hiding a value we do
    // not recognise is how a UI starts disagreeing with its own data.
    expect(threadStatusLabel("SNOOZED")).toBe("SNOOZED");
  });
});

// ── CRM-X4 (spec section 19.8) ────────────────────────────────────────────

const act = (id: string, over: Partial<Activity>): TimelineEntry =>
  entry({ kind: "activity", activity: { id, type: "note", ...over } });

describe("filterTimeline (CRM-X4 item 3)", () => {
  const stream: TimelineEntry[] = [
    act("n1", { type: "note" }),
    act("c1", { type: "call" }),
    act("m1", { type: "meeting" }),
    act("t1", { type: "task" }),
    act("s1", { type: "status_change" }),
    act("x1", { type: "system" }),
    entry({ kind: "email_thread", email_thread: emailThread }),
    entry({ kind: "status_change", status_change: statusChange }),
    // A kind with no payload: no renderer can draw it.
    entry({ kind: "activity" }),
  ];
  const ids = (list: TimelineEntry[]) => list.map((e, i) => timelineKey(e, i));

  it("offers All and the six kinds, in that order", () => {
    expect(TIMELINE_FILTERS.map((f) => f.label)).toEqual([
      "All",
      "Notes",
      "Calls",
      "Meetings",
      "Tasks",
      "Emails",
      "Stage changes",
    ]);
  });

  it("keeps every entry for All, an entry of an unknown kind too", () => {
    expect(filterTimeline(stream, "all")).toEqual(stream);
  });

  it("keeps only the chosen kind", () => {
    expect(ids(filterTimeline(stream, "note"))).toEqual(["activity-n1"]);
    expect(ids(filterTimeline(stream, "call"))).toEqual(["activity-c1"]);
    expect(ids(filterTimeline(stream, "meeting"))).toEqual(["activity-m1"]);
    expect(ids(filterTimeline(stream, "task"))).toEqual(["activity-t1"]);
    expect(ids(filterTimeline(stream, "email"))).toEqual(["email_thread-thread-1"]);
  });

  it("counts a status change from the funnel log and its activity as stage changes", () => {
    expect(ids(filterTimeline(stream, "stage"))).toEqual(["activity-s1", "status_change-sc-1"]);
  });
});

describe("the task due date (CRM-X4 item 4), for a member in UTC+05:30", () => {
  const before = process.env.TZ;
  beforeAll(() => {
    process.env.TZ = "Asia/Kolkata";
  });
  afterAll(() => {
    if (before === undefined) delete process.env.TZ;
    else process.env.TZ = before;
  });
  const now = new Date("2026-10-12T03:30:00Z"); // 09:00 IST, 12 Oct
  const task = (due_at: string | null, completed_at: string | null = null): Activity => ({
    id: "t",
    type: "task",
    subject: "Send the quote",
    due_at,
    completed_at,
  });
  const labels = (a: Activity) => taskDueChips(a, now).map((c) => `${c.label}|${c.tone}`);

  it("shows the due date of a task", () => {
    expect(labels(task("2026-10-15T00:00:00.000Z"))).toEqual(["Due 15 Oct|muted"]);
  });

  it("adds an Overdue chip in the danger tone to an open task due before today", () => {
    expect(labels(task("2026-10-11T00:00:00.000Z"))).toEqual(["Due 11 Oct|muted", "Overdue|danger"]);
  });

  it("does not call a task due today overdue", () => {
    expect(labels(task("2026-10-12T00:00:00.000Z"))).toEqual(["Due 12 Oct|muted"]);
  });

  it("does not call a completed task overdue", () => {
    expect(labels(task("2026-10-01T00:00:00.000Z", "2026-10-02T09:00:00Z"))).toEqual([
      "Due 1 Oct|muted",
    ]);
  });

  it("gives no chip to a task with no due date, nor to an activity that is not a task", () => {
    expect(taskDueChips(task(null), now)).toEqual([]);
    expect(taskDueChips({ id: "c", type: "call", due_at: "2026-10-11T00:00:00.000Z" }, now)).toEqual([]);
  });
});

describe("the next open activities of the Overview tab (CRM-X4 item 2)", () => {
  const now = new Date("2026-10-12T06:00:00Z");
  const stream: TimelineEntry[] = [
    act("done", { type: "task", due_at: "2026-10-01T00:00:00Z", completed_at: "2026-10-02T00:00:00Z" }),
    act("undated", { type: "task", due_at: null }),
    act("past-call", { type: "call", occurred_at: "2026-10-11T06:00:00Z" }),
    act("late", { type: "task", due_at: "2026-10-10T00:00:00Z" }),
    act("meet", { type: "meeting", occurred_at: "2026-10-13T05:00:00Z" }),
    act("soon", { type: "task", due_at: "2026-10-12T00:00:00Z" }),
    act("later", { type: "task", due_at: "2026-10-20T00:00:00Z" }),
    act("note", { type: "note", occurred_at: "2026-10-30T00:00:00Z" }),
  ];

  it("keeps the next three, soonest first, by the rule of section 19.7", () => {
    expect(nextOpenActivities(stream, now).map((e) => e.activity?.id)).toEqual(["late", "soon", "meet"]);
  });

  it("takes a limit, and leaves out the history of the lead", () => {
    const fromLead = { ...act("lead-task", { type: "task", due_at: "2026-10-01T00:00:00Z" }), origin: "lead" as const };
    expect(nextOpenActivities([fromLead, ...stream], now, 5).map((e) => e.activity?.id)).toEqual([
      "late",
      "soon",
      "meet",
      "later",
    ]);
  });
});
