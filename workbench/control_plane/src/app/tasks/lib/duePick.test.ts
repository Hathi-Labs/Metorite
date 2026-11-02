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
 * A grep and not a render. The defect is one expression, and a browser zone
 * that a test sets is not the zone of the run that wrote the row.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const TASKS = fileURLToPath(new URL("..", import.meta.url));

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(TASKS, full).split(sep).join("/"));
      }
    }
  };
  walk(TASKS);
  return out.sort();
}

/** A value parsed and written back as an instant, or a picked value parsed. */
const MIDNIGHT_UTC = /new Date\(\s*(?:[\w.]+\s*\)\s*\.toISOString\(\)|e\.target\.value\s*\))/g;

/**
 * The two "Defer until" picks. A defer date says when a task comes back, and
 * no overdue rule reads it. RX-5 leaves it, and the report of RX-5 names it.
 */
const KEPT: Record<string, number> = {
  "components/InboxCard.tsx": 1,
  "components/ItemDetail.tsx": 1,
};

describe("a picked date in the Tasks app", () => {
  it("never parses a date input as midnight UTC", () => {
    const found: Record<string, number> = {};
    for (const file of sourceFiles()) {
      const hits = readFileSync(join(TASKS, file), "utf8").match(MIDNIGHT_UTC);
      if (hits) found[file] = hits.length;
    }
    expect(found).toEqual(KEPT);
  });

  it("uses the shared helper at each due and promise pick", () => {
    const uses = (file: string) =>
      (readFileSync(join(TASKS, file), "utf8").match(/dueInstantForDay\(/g) ?? []).length;
    expect(uses("components/ItemDetail.tsx")).toBe(2);
    expect(uses("components/ClarifyPanel.tsx")).toBe(1);
    expect(uses("components/QuickCapture.tsx")).toBe(1);
  });
});
