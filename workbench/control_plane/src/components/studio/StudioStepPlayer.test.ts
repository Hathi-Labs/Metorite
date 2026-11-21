/**
 * STU-D14a fence: the step player view.
 *
 * The vitest env has no DOM, so the view draws from props with `renderToStaticMarkup`, as
 * `StudioGallery.test.ts` does. `StepView` is stateless, so each case is one render.
 *
 * Mutations: draw the sign-off form for a viewer ("a viewer draws no sign-off" turns red), and
 * draw an image with an address that `safeAddress` refused ("an address of another site" turns red).
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import StudioStepPlayer, { StepView, type StepViewProps } from "@/components/studio/StudioStepPlayer";
import type { Row } from "@/lib/studio/dataDoor";
import { CHECK_STEP, NO_PHOTO_STEP, makeSampleStepDoor, sampleStepRows } from "@/lib/studio/sampleSteps";
import { CHECK_FIRST, orderSteps } from "@/lib/studio/stepPlayerModel";
import { sampleFileAddress } from "@/lib/studio/sampleDoor";

const steps = (done = 0) => orderSteps(sampleStepRows(done));
const noop = () => undefined;
function view(over: Partial<StepViewProps> = {}) {
  return renderToStaticMarkup(createElement(StepView, {
    steps: steps(), index: 0, canSign: true, address: sampleFileAddress, ticked: false, note: "", formId: "t",
    onBack: noop, onNext: noop, onTick: noop, onNote: noop, onSign: noop, ...over,
  }));
}

describe("one step on each screen", () => {
  it("draws Step 1 of 10 in a progressbar, the title, the instruction and the photo", () => {
    const html = view();
    expect(html).toContain('role="progressbar"');
    expect(html).toContain("Step 1 of 10");
    expect(html).toContain("Clear the work area");
    expect(html).toContain("Switch the machine off");
    expect(html).toMatch(/<img\b[^>]*src="\/office-objects\/[^"]+"/);
    expect(html).toContain("Back");
    expect(html).toContain("Next");
    expect(html).toContain("0 of 10 done");
  });
  it("disables Back on the first step and Next on the last", () => {
    expect(view()).toMatch(/<button[^>]*disabled[^>]*>(?:(?!<\/button>)[\s\S])*Back/);
    const last = view({ index: 9 });
    expect(last).toContain("Step 10 of 10");
    expect(last).toMatch(/<button[^>]*disabled[^>]*>(?:(?!<\/button>)[\s\S])*Next/);
  });
  it("draws the placeholder for a step with no photo", () => {
    const html = view({ index: NO_PHOTO_STEP - 1 });
    expect(html).toContain("data-step-placeholder");
    expect(html).not.toContain("<img");
  });
  it("draws an address of another site as the placeholder", () => {
    const html = view({ address: () => "https://evil.example/x.png" });
    expect(html).not.toContain("<img");
    expect(html).toContain("data-step-placeholder");
  });
  it("draws the required check as a checkbox, and the sign-off button", () => {
    const html = view({ index: CHECK_STEP - 1 });
    expect(html).toContain('type="checkbox"');
    expect(html).toContain("(required)");
    expect(html).toContain("Sign off this step");
  });
  it("draws no checkbox for a step with no check", () => {
    expect(view({ index: 0 })).not.toContain('type="checkbox"');
  });
});

describe("the alert and the refusal", () => {
  it("draws a plain alert for a refused press, and stays on the step", () => {
    const html = view({ index: CHECK_STEP - 1, alert: CHECK_FIRST });
    expect(html).toMatch(/role="alert"[^>]*>[^<]*Tick the check/);
    expect(html).toContain("Step 4 of 10");
  });
  it("shows the text of an ok:false answer on the same step", () => {
    const html = view({ index: 5, alert: "A step is signed after the steps before it." });
    expect(html).toContain("A step is signed after the steps before it.");
    expect(html).toContain("Step 6 of 10");
  });
});

describe("a signed step", () => {
  it("shows done, the name and the time, and no sign-off", () => {
    const html = view({ steps: steps(4), index: 0 });
    expect(html).toContain("Signed by Ravi Menon at 2026-11-14 09:11 UTC");
    expect(html).toContain("Done");
    expect(html).not.toContain("Sign off this step");
  });
  it("shows 10 of 10 done on the last step", () => {
    const html = view({ steps: steps(10), index: 9 });
    expect(html).toContain("10 of 10 done");
    expect(html).toContain("Step 10 of 10");
  });
});

describe("a viewer", () => {
  it("draws no sign-off for a step that is open, and says so", () => {
    const html = view({ canSign: false, index: 5 });
    expect(html).not.toContain("Sign off this step");
    expect(html).not.toContain("<form");
    expect(html).toContain("Not signed off yet.");
  });
  it("still shows each result", () => {
    expect(view({ canSign: false, steps: steps(4), index: 1 })).toContain("Signed by Ravi Menon");
  });
  it("makes no run call, and opens on the first step that is not done", () => {
    const door = makeSampleStepDoor(sampleStepRows(4), 0);
    const run = vi.spyOn(door, "run");
    const html = renderToStaticMarkup(createElement(StudioStepPlayer, {
      rows: sampleStepRows(4), door, object: "step", canSign: false,
    }));
    expect(run).not.toHaveBeenCalled();
    expect(html).toContain("Step 5 of 10");
    expect(html).not.toContain("Sign off this step");
  });
});

describe("a row with a hidden or missing field", () => {
  it("draws empty, and draws no error", () => {
    const bare: Row[] = [{ id: "b1", order: 1 }, { id: "b2", order: 2, done: true }];
    const html = view({ steps: bare, address: sampleFileAddress });
    expect(html).toContain("Step 1 of 2");
    expect(html).toMatch(/data-step-title="" class="[^"]*"><\/h3>/);
    expect(html).not.toContain("data-step-instruction");
    expect(html).not.toMatch(/undefined|NaN|Error/);
  });
  it("draws No steps for an empty run", () => {
    expect(view({ steps: [] })).toContain("No steps");
  });
});

describe("the player opens where the member stopped", () => {
  it.each([[0, "Step 1 of 10"], [4, "Step 5 of 10"], [10, "Step 10 of 10"]])("with %i done, it opens on %s", (done, line) => {
    const html = renderToStaticMarkup(createElement(StudioStepPlayer, {
      rows: sampleStepRows(done), door: makeSampleStepDoor(sampleStepRows(done), 0), object: "step", canSign: true,
    }));
    expect(html).toContain(line);
  });
});
