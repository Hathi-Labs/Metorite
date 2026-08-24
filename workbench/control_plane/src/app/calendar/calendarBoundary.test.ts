/**
 * D54 fence — the Calendar↔Tasks dependency runs ONE WAY.
 *
 * Board **WS-39 S2** · decision **D54** (`work_plan.md` §3) · owning section
 * `project-docs/specs/calendar_focus_os.md` §10.6.
 *
 * **What this is really defending.** D53 makes one task store serve three
 * lenses, so Calendar sharing Tasks' store is correct and expected — a second
 * copy would re-introduce exactly the sync the whole change removes. The thing
 * that must not happen is the dependency pointing *back*: if Tasks imports from
 * `app/calendar/`, the two apps are one app in two folders, and the split D54
 * asked for exists only in the sidebar.
 *
 * ⚠️ The original acceptance clause (spec §10.6, as first written) said
 * "calendar components no longer import from `src/app/tasks/`". That was
 * written before the coupling was measured and it is **wrong**: satisfying it
 * would mean duplicating a 95 KB store or promoting it days before S3a
 * rewrites it. The clause was corrected to the asymmetric rule this file
 * enforces. Recording the correction here rather than quietly testing
 * something else.
 *
 * Structural, not exemplary: both checks walk every file under the directory
 * they guard, so they fail for a module nobody thought to name here.
 */
import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const APP = join(__dirname, "..");
const CALENDAR = join(APP, "calendar");
const TASKS = join(APP, "tasks");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
    } else if (/\.tsx?$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

/** Every module specifier a file imports or re-exports from. */
function specifiers(file: string): string[] {
  const src = readFileSync(file, "utf8");
  return [...src.matchAll(/from\s+["']([^"']+)["']/g)].map((m) => m[1]);
}

describe("the Calendar app's boundary with Tasks (D54)", () => {
  it("Tasks never imports from the Calendar app", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(TASKS)) {
      for (const spec of specifiers(file)) {
        if (spec.includes("app/calendar") || /(^|\/)\.\.\/calendar(\/|$)/.test(spec)) {
          offenders.push(`${file.replace(APP, "app")} → ${spec}`);
        }
      }
    }
    expect(
      offenders,
      "Tasks must not depend on Calendar — the dependency runs one way (D54). " +
        "If Calendar owns something Tasks needs, promote it to a shared module " +
        "(`app/tasks/lib/` today, `src/lib/` once S3a moves the store) rather " +
        "than importing across the boundary. `fmtClock` is the worked example: " +
        "it moved to `app/tasks/lib/utils.ts` for exactly this reason.",
    ).toEqual([]);
  });

  it("Calendar imports Tasks' LIB and store, never Tasks' views", () => {
    // The allowed direction, narrowed: sharing the store is the point of D53;
    // reaching into another app's *views* is not. The store-driven overlays are
    // the deliberate exception — Calendar raises them (`openFocus`,
    // `openSchedule`, `requestDelete`, `quickDispose`) and must mount them, or
    // a right-click in the calendar answers with silence.
    const ALLOWED_TASK_COMPONENTS = new Set([
      "TaskFocusModal",
      "SchedulePopup",
      "DeleteConfirmModal",
      "UndoToast",
    ]);
    const offenders: string[] = [];
    for (const file of sourceFiles(CALENDAR)) {
      for (const spec of specifiers(file)) {
        const m = spec.match(/app\/tasks\/components\/([A-Za-z]+)/);
        if (m && !ALLOWED_TASK_COMPONENTS.has(m[1])) {
          offenders.push(`${file.replace(APP, "app")} → ${spec}`);
        }
      }
    }
    expect(
      offenders,
      "Calendar may read Tasks' shared lib and store, and may mount the four " +
        "store-driven overlays it raises — but pulling in another of Tasks' " +
        "views means the surfaces are entangled rather than sharing a store. " +
        "Widen ALLOWED_TASK_COMPONENTS only for something Calendar genuinely " +
        "RAISES through the store.",
    ).toEqual([]);
  });

  it("the calendar view is no longer reachable inside the Tasks app", () => {
    // The half-move this catches: a new /calendar route added while /tasks
    // still renders the same surface, leaving two entry points for one thing
    // and a `ViewKey` that quietly still works.
    const tasksPage = readFileSync(join(TASKS, "page.tsx"), "utf8");
    expect(tasksPage).not.toContain("CalendarView");
    expect(tasksPage).not.toContain('selectedView === "calendar"');
  });
});
