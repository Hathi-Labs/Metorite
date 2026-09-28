/**
 * WS-27bn R3d — the Pulse panel's words, held equal to the email's.
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R3d.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PILL_HUE, PILL_LABEL } from "@/app/people/lib/dashboard";
import { HELP_REASON_WORDS, PULSE_STATUS_WORDS } from "@/lib/reportEmail";

import type { PulseRow } from "./api";
import {
  FOCUS_SHOWN,
  HELP_REASON_LABEL,
  ON_LEAVE_HUE,
  focusMore,
  focusShown,
  hasHrHalf,
  helpLine,
  hiddenPeopleLine,
  loadBar,
  pulseRows,
  statusMark,
} from "./pulse";

const row = (over: Partial<PulseRow> = {}): PulseRow => ({
  assignee: "dee@example.test",
  name: "Dee",
  open_tasks: 4,
  overdue: 1,
  blocked_count: 0,
  stale_count: 0,
  focus: [],
  focus_total: 0,
  help_reasons: [],
  needs_help: false,
  ...over,
});

describe("the words are one set", () => {
  it("the email's status words are the People pill labels, and On leave", () => {
    const { on_leave, ...pills } = PULSE_STATUS_WORDS;
    expect(pills).toEqual(PILL_LABEL);
    expect(on_leave).toBe("On leave");
  });

  it("the panel's reasons are the email's reasons", () => {
    expect(HELP_REASON_LABEL).toBe(HELP_REASON_WORDS);
    expect(Object.keys(HELP_REASON_LABEL)).toEqual(["blocked", "stale", "waiting_overdue"]);
  });

  it("the chat card says the same reason words", () => {
    // `views.py` `HELP_REASON_WORDS`, read as text: one owner for the words.
    const views = readFileSync(
      join(__dirname, "..", "..", "..", "..", "..", "..", "apps", "skills",
        "skill-projects", "skill_projects", "views.py"),
      "utf-8"
    );
    for (const [key, words] of Object.entries(HELP_REASON_WORDS)) {
      expect(views).toContain(`"${key}": "${words}"`);
    }
  });

  it("says the hidden line in the chat's words", () => {
    expect(hiddenPeopleLine(2)).toBe("This report hides 2 other people");
    expect(hiddenPeopleLine(1)).toBe("This report hides 1 other person");
    expect(hiddenPeopleLine(2, "view")).toBe("This view hides 2 other people");
    expect(hiddenPeopleLine(0)).toBeNull();
    expect(hiddenPeopleLine(undefined)).toBeNull();
  });
});

describe("one card", () => {
  it("decides the HR half by the row's own keys", () => {
    expect(hasHrHalf(row())).toBe(false);
    expect(statusMark(row())).toBeNull();
    expect(hasHrHalf(row({ status: "idle", pill: "idle" }))).toBe(true);
  });

  it("draws On leave in the neutral hue, never the pill", () => {
    const mark = statusMark(row({ status: "on_leave", pill: "idle" }));
    expect(mark).toMatchObject({ label: "On leave", hue: ON_LEAVE_HUE });
    const pill = statusMark(row({ status: "behind", pill: "behind" }));
    expect(pill).toMatchObject({ label: "Behind", hue: PILL_HUE.behind });
  });

  it("draws committed of working hours only with hours_basis", () => {
    const hours = loadBar(row({
      status: "on_track", pill: "on_track", hours_basis: true,
      working_hours_this_week: 40, committed_hours_this_week: 12,
    }));
    expect(hours).toMatchObject({ kind: "hours", value: 12, of: 40, text: "12 of 40h" });
    const tasks = loadBar(row({ hours_basis: false, working_hours_this_week: 40 }));
    expect(tasks).toMatchObject({ kind: "tasks", value: 1, of: 4, text: "1 of 4 overdue" });
  });

  it("names three focus tasks and counts the rest from focus_total", () => {
    const focus = Array.from({ length: 5 }, (_, i) => ({
      id: `f${i}`, title: `t${i}`, task_number: i, project_name: null,
      due_at: null, in_progress: true,
    }));
    const r = row({ focus, focus_total: 7 });
    expect(focusShown(r)).toHaveLength(FOCUS_SHOWN);
    expect(focusMore(r, FOCUS_SHOWN)).toBe("…and 4 more");
    expect(focusMore(row({ focus_total: 2 }), 2)).toBeNull();
  });

  it("says the reasons in words, and nothing without one", () => {
    expect(helpLine(row())).toBeNull();
    expect(
      helpLine(row({ needs_help: true, help_reasons: ["stale", "waiting_overdue"] }))
    ).toBe("Needs help: Stale, Waiting past its date");
  });

  it("drops a malformed row at the boundary", () => {
    const data = { rows: [row(), null, { name: "no address" }] } as never;
    expect(pulseRows(data)).toHaveLength(1);
    expect(pulseRows(undefined)).toEqual([]);
  });
});
