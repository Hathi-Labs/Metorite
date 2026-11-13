import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TimelineColumns, TimelineHeader } from "./TimelineChrome";
import { dayCells, dayPx, monthCells, weekCells } from "./geometry";
import { rangeForSpans } from "./scale";

const TODAY = "2026-08-10";

const draw = (zoom: "week" | "month" | "quarter") => {
  const range = rangeForSpans([{ from: "2026-08-03", to: "2026-08-30" }], TODAY, zoom);
  const days = dayCells(range, TODAY);
  const header = renderToStaticMarkup(
    createElement(TimelineHeader, {
      zoom,
      months: monthCells(range),
      days,
      weeks: weekCells(range, TODAY),
      heightPx: 52,
      labelInsetPx: 348,
    }),
  );
  const columns = renderToStaticMarkup(
    createElement(TimelineColumns, {
      range,
      days,
      todayPx: dayPx(TODAY, range),
      heightPx: 400,
    }),
  );
  return { header, columns };
};

describe("TimelineChrome", () => {
  it("draws the month tier and day cells at the week zoom", () => {
    const { header } = draw("week");
    expect(header).toContain("Aug 2026");
    expect(header).toContain("left:348px");
    expect(header).toContain("flex-col");
    expect(header).toContain("font-semibold text-primary");
  });
  it("draws week cells at the month zoom", () => {
    expect(draw("month").header).not.toContain("flex-col");
  });
  it("shades weekends at wide zooms and draws week rules at quarter zoom", () => {
    expect(draw("month").columns).toContain("bg-muted/40");
    const quarter = draw("quarter").columns;
    expect(quarter).not.toContain("bg-muted/40");
    expect(quarter).toContain("w-px bg-border/40");
  });
  it("draws the today line with its title", () => {
    expect(draw("month").columns).toContain('title="Today"');
  });
});
