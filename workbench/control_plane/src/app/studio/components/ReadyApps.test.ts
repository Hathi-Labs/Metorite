// The ready apps section (STU-K1b). No DOM runs here, so the test reads the
// source for the shape: a name and a purpose per row, one Button each that
// sends the id, a disabled state while pending, and the section on the page.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("./ReadyApps.tsx", import.meta.url), "utf-8");
const page = readFileSync(new URL("../page.tsx", import.meta.url), "utf-8");

describe("ReadyApps", () => {
  it("shows the name and the purpose of each template", () => {
    expect(src).toContain("{t.name}");
    expect(src).toContain("{t.purpose}");
  });
  it("sends the template id from a Button 'Use this app', disabled while pending", () => {
    expect(src).toContain("Use this app");
    expect(src).toContain("pick(t.id)");
    expect(src).toContain("startFromTemplate(id)");
    expect(src).toContain("disabled={pending !== null}");
  });
  it("hides with an empty list and shows the error as an alert", () => {
    expect(src).toContain("if (templates.length === 0) return null;");
    expect(src).toContain('role="alert"');
  });
  it("sits in the Studio library page, in the Apps view", () => {
    expect(page).toContain("view.showApps && <ReadyApps");
  });
});
