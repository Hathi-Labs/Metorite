// CRM-Z10b: the dialog "Switch to Metorite", the switch button of the card,
// and "Finish closing Zoho". The runner has no DOM, so each part is rendered
// to a string. The steps and the calls are pure, in `lib/cutoverSwitch.ts`.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CutoverPreview } from "../lib/cutover";
import {
  SWITCH_LABEL,
  SWITCH_WORD,
  initialSwitch,
  switchReducer,
  type SwitchState,
} from "../lib/cutoverSwitch";
import { CutoverCardView, FinishClosingView } from "./CutoverCard";
import { CutoverBody } from "./CutoverDialog";

const noop = () => undefined;

function preview(over: Partial<CutoverPreview> = {}): CutoverPreview {
  return {
    mode: "mirror",
    sync: { last_good_at: "2026-10-28T01:00:00Z", age_seconds: 60, running: false },
    records: [
      {
        key: "deals", label: "Projects", zoho: 550, zoho_age_seconds: 60,
        metorite: { from_zoho: 547, native: 3 }, parts: [], state: "complete", failed: 0, refused: 0,
      },
      {
        key: "module:Products", label: "Products", zoho: null, zoho_age_seconds: null,
        metorite: { from_zoho: 10, native: 0 }, parts: [], state: "held", failed: 0, refused: 0,
      },
    ],
    other: [],
    unmatched_owners: [{ name: "Asha Rao", records: 12 }],
    unmatched_owner_names: 1,
    labels_kept: [],
    not_complete: ["Products"],
    stays_in_zoho: ["Attachments"],
    incomplete: [{ key: "module:Products", label: "Products", reason: "held" }],
    can_switch: true,
    pipelines_in_another_order: 2,
    ...over,
  };
}

function at(step: SwitchState["step"], over: Partial<SwitchState> = {}): SwitchState {
  return { ...initialSwitch(preview()), step, ...over };
}

function draw(state: SwitchState, p: CutoverPreview = preview()): string {
  return renderToStaticMarkup(createElement(CutoverBody, {
    state, preview: p, onEvent: noop, onSwitch: noop, onCancel: noop, onClose: noop,
  }));
}

/** The markup of the one button whose text holds *label*. */
function buttonWith(html: string, label: string): string {
  const found = html.split("<button").find((part) => part.split("</button>")[0].includes(label));
  expect(found, label).toBeDefined();
  return found!.split("</button>")[0];
}

describe("CRM-Z10b the dialog", () => {
  it("opens with one sentence, and the counts behind a fold", () => {
    const html = draw(at("intro"));
    expect(html).toContain("Stop using Zoho now.");
    expect(html).toContain("Show the counts");
    expect(html).toContain("1. Stop Zoho");
    expect(html).toContain("2. Leave out");
    expect(html).toContain("3. Confirm");
  });

  it("keeps the switch button disabled until the text is exactly SWITCH", () => {
    for (const typed of ["", "switch", "SWITC", " SWITCH"]) {
      expect(buttonWith(draw(at("confirm", { typed })), SWITCH_LABEL), typed).toMatch(/ disabled=""/);
    }
    expect(buttonWith(draw(at("confirm", { typed: SWITCH_WORD })), SWITCH_LABEL)).not.toMatch(
      / disabled=""/,
    );
    expect(draw(at("confirm"))).toContain("Type SWITCH to confirm.");
    expect(draw(at("confirm"))).toContain("You cannot undo this.");
  });

  it("offers a Leave out tick for a module that is not complete", () => {
    const html = draw(at("leave"));
    expect(html).toContain("Leave out Products");
    expect(html).toContain("Held back from the sync");
    expect(html).toMatch(/<input[^>]*type="checkbox"/);
    // Next waits for each tick.
    expect(buttonWith(html, "Next")).toMatch(/ disabled=""/);
    const ticked = switchReducer(at("leave"), { type: "tick", key: "module:Products", on: true });
    expect(buttonWith(draw(ticked), "Next")).not.toMatch(/ disabled=""/);
  });

  it("says what runs while it polls, with no way out", () => {
    const html = draw(at("progress", { busy: true, phase: "sync" }));
    expect(html).toContain("Running the final sync from Zoho");
    expect(html).toMatch(/role="status"/);
    expect(buttonWith(html, "Cancel")).toMatch(/ disabled=""/);
    expect(draw(at("progress", { busy: true, phase: "switch" }))).toContain("Switching to Metorite");
  });

  it("shows the revoked outcome when it is done", () => {
    const html = draw(at("done", {
      result: { status: "switched", revoked: "not_confirmed", zoho_connection: "disconnected" },
    }));
    expect(html).toContain("Metorite is your CRM now.");
    expect(html).toContain("did not confirm");
    expect(buttonWith(html, "Close")).not.toMatch(/ disabled=""/);
  });

  it("puts a failure next to the word, and keeps the word", () => {
    const html = draw(at("confirm", { typed: SWITCH_WORD, error: "A sync runs now." }));
    expect(html).toMatch(/role="alert"[^>]*>A sync runs now\./);
    expect(html).toContain(`value="${SWITCH_WORD}"`);
    expect(html).toMatch(/aria-invalid="true"/);
  });

  it("uses the shared primitives only", () => {
    const source = readFileSync(new URL("./CutoverDialog.tsx", import.meta.url), "utf8");
    expect(source).not.toMatch(/<(button|input|select|textarea)\b/);
    expect(source).toContain('from "@/components/ui/Modal"');
    expect(source).not.toMatch(/\b(confirm|alert)\(/);
  });
});

describe("CRM-Z10b the card", () => {
  it("shows the switch button only when the org may switch", () => {
    const on = renderToStaticMarkup(createElement(CutoverCardView, { preview: preview(), onSwitch: noop }));
    expect(buttonWith(on, SWITCH_LABEL)).toBeTruthy();
    expect(on).toContain("2 pipelines list stages in another order.");
    const off = renderToStaticMarkup(createElement(CutoverCardView, {
      preview: preview({ can_switch: false }), onSwitch: noop,
    }));
    expect(off).not.toContain(SWITCH_LABEL);
    expect(off).toContain("The switch is not on for this organization yet.");
  });

  it("offers Finish closing Zoho, and then says it is closed", () => {
    const pending = renderToStaticMarkup(createElement(FinishClosingView, {
      busy: false, error: null, done: null, onFinish: noop,
    }));
    expect(buttonWith(pending, "Finish closing Zoho")).not.toMatch(/ disabled=""/);
    const closed = renderToStaticMarkup(createElement(FinishClosingView, {
      busy: false, error: null, done: ["Zoho is disconnected."], onFinish: noop,
    }));
    expect(closed).not.toContain("<button");
    expect(closed).toContain("Zoho is disconnected.");
  });
});
