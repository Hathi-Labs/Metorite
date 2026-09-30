/**
 * WS-27bn R5g: the live Overview, full page.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.7 and §8 R5g. The rules
 * are pure, so each one is pinned here. The vitest environment has no DOM,
 * so no test runs a timer or an effect. The markup half is
 * `components/reportsLiveOverview.test.ts`.
 */
import { describe, expect, it } from "vitest";

import { previewNeeded } from "./reportBuilder";
import {
  BODY_ORDER,
  FILTERS_OPEN_KEY,
  LIVE_INTERVAL_MS,
  LIVE_STALE_MS,
  answerIsCurrent,
  gridSpans,
  intervalRuns,
  overviewFilterCount,
  overviewSummary,
  readFiltersOpen,
  previewRequest,
  refreshOnVisible,
  updatedLine,
  writeFiltersOpen,
} from "./overviewLive";
import { overviewState, withSubject } from "./reportBuilder";

const NOW = Date.parse("2026-09-30T10:00:00Z");

describe("R5g rule 4: when Overview asks the server again", () => {
  it("waits 60 seconds on a return, and 5 minutes on the timer", () => {
    expect(LIVE_STALE_MS).toBe(60_000);
    expect(LIVE_INTERVAL_MS).toBe(5 * 60_000);
  });

  it("refreshes on a return to the tab when the answer is over 60 s old", () => {
    const base = { visible: true, busy: false, now: NOW };
    expect(refreshOnVisible({ ...base, updatedAt: NOW - 61_000 })).toBe(true);
    expect(refreshOnVisible({ ...base, updatedAt: NOW - 60_000 })).toBe(false);
    expect(refreshOnVisible({ ...base, updatedAt: NOW - 5_000 })).toBe(false);
  });

  it("does not refresh a hidden tab, a busy one, or one with no answer yet", () => {
    const old = NOW - 10 * 60_000;
    expect(refreshOnVisible({ visible: false, busy: false, now: NOW, updatedAt: old })).toBe(false);
    expect(refreshOnVisible({ visible: true, busy: true, now: NOW, updatedAt: old })).toBe(false);
    expect(refreshOnVisible({ visible: true, busy: false, now: NOW, updatedAt: null })).toBe(false);
  });

  it("runs the 5-minute timer only while the tab is visible", () => {
    expect(intervalRuns("visible")).toBe(true);
    expect(intervalRuns("hidden")).toBe(false);
    expect(intervalRuns("prerender")).toBe(false);
  });

  it("a refresh keeps the key, so Home still keeps the Overview state", () => {
    const key = JSON.stringify({
      project_id: null,
      config: { weeks: 12, skip_current_week: false, include_subtree: true, sections: ["finished"] },
      blocked: false,
      round: 0,
    });
    const req = previewRequest(key);
    expect(req.key).toBe(key);
    expect(req.project_id).toBeNull();
    expect(req.config.sections).toEqual(["finished"]);
    expect(req.blocked).toBe(false);
    // The key on screen is the key of the choices, so no second preview.
    expect(previewNeeded(req.key, key, false)).toBe(false);
  });

  it("a blocked key asks for nothing", () => {
    const key = JSON.stringify({ project_id: null, config: { sections: [] }, blocked: true, round: 0 });
    expect(previewRequest(key).blocked).toBe(true);
  });

  it("a late answer never overwrites a newer one", () => {
    // The newest request, for the choices on screen: it lands.
    expect(answerIsCurrent({ seq: 3, latestSeq: 3, key: "k1", currentKey: "k1" })).toBe(true);
    // A refresh that started before a later request: it drops.
    expect(answerIsCurrent({ seq: 2, latestSeq: 3, key: "k1", currentKey: "k1" })).toBe(false);
    // A refresh for choices the member has since changed: it drops.
    expect(answerIsCurrent({ seq: 3, latestSeq: 3, key: "k1", currentKey: "k2" })).toBe(false);
  });
});

describe("R5g rule 2: the summary line", () => {
  it("says how long ago the answer came", () => {
    expect(updatedLine(null, NOW)).toBeNull();
    expect(updatedLine(NOW - 20_000, NOW)).toBe("Updated just now");
    expect(updatedLine(NOW - 2 * 60_000, NOW)).toBe("Updated 2 min ago");
    expect(updatedLine(NOW - 90 * 60_000, NOW)).toBe("Updated 1 h ago");
  });

  it("joins the choices and the time with the house separator", () => {
    expect(
      overviewSummary({
        subject: null,
        scope: "Whole organization",
        period: "The last 12 weeks",
        updated: "Updated 2 min ago",
      })
    ).toBe("Whole organization · The last 12 weeks · Updated 2 min ago");
    expect(
      overviewSummary({ subject: "Meera Iyer", scope: "In Printers", period: "Last week", updated: null })
    ).toBe("About Meera Iyer · In Printers · Last week");
  });
});

describe("R5g rule 2: the filter count badge", () => {
  const base = overviewState();

  it("is 0 at the Overview defaults", () => {
    expect(overviewFilterCount(base)).toBe(0);
  });

  it("counts each choice that differs, once", () => {
    const person = withSubject(base, { kind: "person", email: "m@x.io" });
    expect(overviewFilterCount(person)).toBe(1);
    expect(overviewFilterCount({ ...base, projectId: "p1" })).toBe(1);
    expect(overviewFilterCount({ ...base, projectId: "p1", includeSubtree: false })).toBe(1);
    expect(overviewFilterCount({ ...base, includeSubtree: false })).toBe(1);
    expect(overviewFilterCount({ ...base, weeks: 1, skipCurrentWeek: true })).toBe(1);
    expect(overviewFilterCount({ ...base, sections: ["finished"] })).toBe(1);
    expect(
      overviewFilterCount({
        ...person,
        projectId: "p1",
        weeks: 4,
        skipCurrentWeek: true,
        sections: [...base.sections, "pulse"],
      })
    ).toBe(4);
  });

  it("does not count the order of the sections", () => {
    expect(overviewFilterCount({ ...base, sections: [...base.sections].reverse() })).toBe(0);
  });
});

describe("R5g rule 3: Filters remembers its state, and storage may fail", () => {
  function memory(start: Record<string, string> = {}) {
    const data = { ...start };
    return {
      data,
      getItem: (k: string) => (k in data ? data[k] : null),
      setItem: (k: string, v: string) => {
        data[k] = v;
      },
    };
  }

  it("is closed by default", () => {
    expect(readFiltersOpen(() => memory())).toBe(false);
    expect(readFiltersOpen(() => null)).toBe(false);
  });

  it("reads what the member left", () => {
    expect(readFiltersOpen(() => memory({ [FILTERS_OPEN_KEY]: "1" }))).toBe(true);
    expect(readFiltersOpen(() => memory({ [FILTERS_OPEN_KEY]: "0" }))).toBe(false);
  });

  it("reads closed when the storage throws", () => {
    expect(
      readFiltersOpen(() => {
        throw new Error("SecurityError");
      })
    ).toBe(false);
    expect(
      readFiltersOpen(() => ({
        getItem: () => {
          throw new Error("denied");
        },
        setItem: () => undefined,
      }))
    ).toBe(false);
  });

  it("writes the state, and a failed write does not throw", () => {
    const store = memory();
    writeFiltersOpen(() => store, true);
    expect(store.data[FILTERS_OPEN_KEY]).toBe("1");
    writeFiltersOpen(() => store, false);
    expect(store.data[FILTERS_OPEN_KEY]).toBe("0");
    expect(() =>
      writeFiltersOpen(() => {
        throw new Error("SecurityError");
      }, true)
    ).not.toThrow();
    expect(() =>
      writeFiltersOpen(
        () => ({
          getItem: () => null,
          setItem: () => {
            throw new Error("QuotaExceeded");
          },
        }),
        true
      )
    ).not.toThrow();
  });
});

describe("R5g rule 1: the grid leaves no half-empty row", () => {
  it("names the ten sections in the order RenderedBody draws them", () => {
    expect([...BODY_ORDER].sort()).toEqual(
      [
        "capacity",
        "conflicts",
        "finished",
        "hygiene",
        "load",
        "outlook",
        "pulse",
        "rebalance",
        "stuck",
        "throughput",
      ].sort()
    );
  });

  it("each row holds two panels or one wide panel", () => {
    const cases: string[][] = [
      ["finished", "throughput", "outlook", "stuck", "load", "capacity", "conflicts"],
      ["finished"],
      ["finished", "throughput"],
      ["outlook", "load", "capacity"],
      [...BODY_ORDER],
      ["pulse", "conflicts", "rebalance"],
    ];
    for (const keys of cases) {
      const wide = gridSpans(keys);
      let col = 0;
      for (const k of keys) {
        if (wide.has(k)) {
          expect(col, `${k} starts a row in ${keys.join(",")}`).toBe(0);
        } else col = 1 - col;
      }
      expect(col, `the last row of ${keys.join(",")} is full`).toBe(0);
    }
  });

  it("a clear row takes a full row, and closes the row before it", () => {
    const wide = gridSpans(["finished", "stuck", "load", "capacity"], new Set(["stuck"]));
    expect([...wide].sort()).toEqual(["finished", "stuck"]);
    const lone = gridSpans(["finished", "load", "stuck"], new Set(["stuck"]));
    expect([...lone].sort()).toEqual(["stuck"]);
  });

  it("gives the forecast the full width at the Overview defaults", () => {
    const wide = gridSpans(["finished", "throughput", "outlook", "stuck", "load", "capacity", "conflicts"]);
    expect(wide.has("outlook")).toBe(true);
    expect(wide.has("finished")).toBe(false);
    expect(wide.has("throughput")).toBe(false);
  });
});
