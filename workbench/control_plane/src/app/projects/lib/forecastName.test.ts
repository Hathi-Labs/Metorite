import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The panel a member sees as "Forecast" (spec projects_ai_chat.md §17). The
 * owner renamed it on 2026-09-26, because members read "Outlook" as the mail
 * product. Internal names (the route, the section key `outlook`, OutlookPanel)
 * stay. This test fences every member-facing label and the leave wiring.
 */
const root = resolve(__dirname, "../../../..");
const read = (p: string) => readFileSync(resolve(root, p), "utf8");

// Member-facing text only: a quoted, JSX or heading "Outlook", never an identifier.
const VISIBLE_OUTLOOK = /(["'`>#]\s*Outlook\b|\bOutlook\s*[<"'`:])/;

const SURFACES: Array<[string, string]> = [
  ["src/app/projects/components/ReportsView.tsx", '<Table title="Forecast">'],
  ["src/app/projects/lib/reportBuilder.ts", '{ key: "outlook", label: "Forecast" }'],
  ["src/components/projects/ProjectToolCards.tsx", 'analytics_outlook: { icon: "Telescope", label: "Forecast" }'],
  ["src/lib/reportEmail.ts", "lead: `Forecast: "],
  ["../../apps/skills/skill-projects/skill_projects/reads.py", 'f"Forecast for '],
  ["../../apps/skills/skill-projects/skill_projects/views.py", '"## Forecast"'],
  ["../../apps/skills/skill-projects/skill_projects/views.py", '"title": "Forecast"'],
];

describe("the Forecast name", () => {
  it.each(SURFACES)("%s carries the Forecast label", (file, label) => {
    expect(read(file)).toContain(label);
  });

  it.each([...new Set(SURFACES.map(([f]) => f))])("%s shows a member no \"Outlook\"", (file) => {
    const hits = read(file)
      .split("\n")
      .filter((l) => VISIBLE_OUTLOOK.test(l) && !/mail|Microsoft|provider/i.test(l));
    expect(hits).toEqual([]);
  });

  it("the panel tells a member when leave is not counted", () => {
    expect(read("src/app/projects/components/AnalyticsPanels.tsx")).toContain(
      "capacityLine(data.capacity, data.people?.absences_applied)",
    );
  });
});
