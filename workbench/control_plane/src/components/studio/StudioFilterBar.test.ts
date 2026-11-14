/**
 * STU-AF4 fence: the list bar adds no third filter bar. It takes the search state machine and the
 * timer from `lib/filterModel.ts`, draws with the shared controls, and imports no CRM component.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const read = (p: string) => readFileSync(resolve(__dirname, p), "utf-8");
const bar = read("./StudioFilterBar.tsx");
const frame = read("../../app/studio/package-preview/AppFrame.tsx");
const model = read("../../lib/studio/filterBarModel.ts");

describe("StudioFilterBar", () => {
  it("reuses the search box state machine and searchCommitter of filterModel", () => {
    expect(bar).toContain('from "@/lib/filterModel"');
    for (const name of ["searchBoxStart", "searchBoxSync", "searchBoxWrote", "searchCommitter"]) expect(bar).toContain(name);
    expect(model).toContain('from "@/lib/filterModel"');
  });

  it("defines no second debounce, timer or search state machine", () => {
    for (const src of [bar, model]) {
      expect(src).not.toMatch(/setTimeout|setInterval|debounce|coalesce\(/);
      expect(src).not.toMatch(/function searchBox|export type SearchBoxState/);
    }
  });

  it("draws with the shared controls and imports no CRM code", () => {
    for (const c of ["@/components/ui/SelectButton", "@/components/ui/Input", "@/components/ModeSwitch", "@/components/ui/Button"]) {
      expect(bar).toContain(c);
    }
    for (const src of [bar, model, frame]) expect(src).not.toMatch(/app\/crm|@\/app\/crm|\.\.\/crm/);
  });

  it("is mounted by the app frame only, which the frame flag already guards", () => {
    expect(frame).toContain("StudioFilterBar");
    expect(frame).toContain("toListArgs");
  });

  it("names each end of a range, in text and for assistive technology", () => {
    expect(bar).toContain('placeholder="From"');
    expect(bar).toContain('placeholder="To"');
    expect(bar).toContain("`${chip.label} from`");
    expect(bar).toContain("`${chip.label} to`");
    expect(bar).toMatch(/text-muted-foreground">from</);
  });

  it("shows No match with a Clear action only through showNoMatch", () => {
    expect(frame).toContain("showNoMatch");
    expect(frame).toContain("No match");
    expect(frame).toContain("clearedBar");
  });
});
