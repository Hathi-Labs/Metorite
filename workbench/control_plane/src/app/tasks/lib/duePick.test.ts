/**
 * A date that a member picks is a day (RX-5, `calendar_events_reminders.md`
 * §5.11).
 *
 * `<input type="date">` gives `2026-11-02`, and `new Date("2026-11-02")` reads
 * that as midnight UTC. `.toISOString()` then adds a time, so the gateway's
 * date seam (`personal.stored_due`) keeps the value as a real time. In Los
 * Angeles that instant is 17:00 on Nov 1, and the task reads overdue on its due
 * day. `dueInstantForDay` stores local noon, the rule of every other picker.
 *
 * A raw `e.target.value` in a due key is the same fault by another road. The
 * gateway takes it for a date, and an old path parsed it as midnight UTC.
 *
 * A grep and not a render. The defect is one expression, and a browser zone
 * that a test sets is not the zone of the run that wrote the row. The walk
 * covers all of `src/`, because the pick that the first version missed sat in
 * the email app, beside a walk of `app/tasks/` alone.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("../../..", import.meta.url));

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(SRC, full).split(sep).join("/"));
      }
    }
  };
  walk(SRC);
  return out.sort();
}

/**
 * A value parsed and written back as an instant, or a picked value parsed. A
 * constant in capitals is a count of milliseconds in a fixture.
 */
const MIDNIGHT_UTC = /new Date\(\s*(?:(?![A-Z_]+\s*\))[\w.]+\s*\)\s*\.toISOString\(\)|e\.target\.value\s*\))/g;

/** A picked value that goes into a due key as it came. */
const RAW_PICK = /\b(?:dueAt|due_at)\s*:\s*e\.target\.value\b(?!\s*\?)/g;

/** Each hit that stays, and why. No pick of a task's due date is on this list. */
const KEPT: Record<string, number> = {
  // A "Defer until" pick says when a task comes back, and no overdue rule reads
  // it. RX-5 leaves it, and §5.11 of the spec names it.
  "app/tasks/components/InboxCard.tsx": 1,
  "app/tasks/components/ItemDetail.tsx": 1,
  // A CRM activity is another object, with its own date rule (§5.11).
  "app/crm/components/Timeline.tsx": 1,
  // A `datetime-local` value holds a real time.
  "app/notes/components/MeetingPrep.tsx": 1,
  // A count of milliseconds from the server.
  "lib/runActivity.ts": 1,
};

function hitsOf(pattern: RegExp): Record<string, number> {
  const found: Record<string, number> = {};
  for (const file of sourceFiles()) {
    const hits = readFileSync(join(SRC, file), "utf8").match(pattern);
    if (hits) found[file] = hits.length;
  }
  return found;
}

describe("a picked date anywhere in the client", () => {
  it("never parses a date input as midnight UTC", () => {
    expect(hitsOf(MIDNIGHT_UTC)).toEqual(KEPT);
  });

  it("never sends a picked date in a due key as it came", () => {
    expect(hitsOf(RAW_PICK)).toEqual({});
  });

  it("sees both of the faults that it fences", () => {
    expect('patch({ dueAt: e.target.value })'.match(RAW_PICK)).toHaveLength(1);
    expect('patch({ due_at: e.target.value, x: 1 })'.match(RAW_PICK)).toHaveLength(1);
    expect('patch({ dueAt: e.target.value ? dueInstantForDay(e.target.value) : "" })'.match(RAW_PICK)).toBeNull();
    expect("new Date(e.target.value).toISOString()".match(MIDNIGHT_UTC)).toHaveLength(1);
    expect("new Date(day).toISOString()".match(MIDNIGHT_UTC)).toHaveLength(1);
    expect("new Date(NOW_MS).toISOString()".match(MIDNIGHT_UTC)).toBeNull();
  });

  it("uses the shared helper at each due and promise pick", () => {
    const uses = (file: string) =>
      (readFileSync(join(SRC, file), "utf8").match(/dueInstantForDay\(/g) ?? []).length;
    expect(uses("app/tasks/components/ItemDetail.tsx")).toBe(2);
    expect(uses("app/tasks/components/ClarifyPanel.tsx")).toBe(1);
    expect(uses("app/tasks/components/QuickCapture.tsx")).toBe(1);
    expect(uses("app/email/components/TaskCaptureModal.tsx")).toBe(1);
    expect(uses("app/projects/components/TaskBody.tsx")).toBe(1);
    expect(uses("app/projects/components/TableView.tsx")).toBe(1);
  });
});
