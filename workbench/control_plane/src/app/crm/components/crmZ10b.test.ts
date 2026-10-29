// CRM-Z10b: the dialog of the switch that keeps the records, the two
// switches of the card, and "Finish closing Zoho". The runner has no DOM, so
// each part is rendered to a string. The steps and the calls are pure, in
// `lib/cutoverSwitch.ts`.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { clearAll, put } from "@/lib/dataCache";

import { CrmError } from "../lib/api";
import type { ConnectionInfo } from "../lib/connect";
import { switchesInCard, type CutoverPreview, type CutoverStatus } from "../lib/cutover";
import {
  SWITCH_LABEL,
  SWITCH_WORD,
  initialSwitch,
  runSwitch,
  switchReducer,
  type SwitchState,
} from "../lib/cutoverSwitch";
import {
  ACTIONS,
  CONNECT_FROM_NATIVE,
  KEEP_RECORDS_SWITCH,
  hubState,
  type HubState,
} from "../lib/setup";
import CutoverCard, { CutoverCardView, FinishClosingView } from "./CutoverCard";
import { CutoverBody } from "./CutoverDialog";
import { SetupHubView } from "./SetupHub";

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

describe("CRM-Z10b a 409 that names modules", () => {
  it("shows the Leave out step with those modules, with no page reload", async () => {
    // No gaps in the preview, so the dialog starts with no Leave out step.
    const clean = preview({
      incomplete: [], not_complete: [],
      records: [preview().records[0]],
    });
    let s = switchReducer(initialSwitch(clean), { type: "next" });
    expect(s.steps).toEqual(["intro", "confirm"]);
    s = switchReducer(switchReducer(s, { type: "type", text: SWITCH_WORD }), { type: "submit" });
    const refusal = new CrmError(
      "Some modules are not complete. Fix them, or tick them in Leave out.",
      409, "module_incomplete", undefined,
      {
        code: "module_incomplete",
        message: "Some modules are not complete. Fix them, or tick them in Leave out.",
        modules: [
          { key: "module:Quotes", label: "Quotes", reason: "short" },
          { key: "tasks", label: "Tasks", reason: "failed" },
        ],
      },
    );
    const status: CutoverStatus = {
      state: "ready", run_id: "r1", started_at: null, finished_at: null, error: null,
      age_seconds: 0, zoho_connection: "connected",
    };
    const out = await runSwitch([], {
      start: async () => ({ run_id: "r1" }),
      status: async () => status,
      commit: async () => { throw refusal; },
      wait: async () => undefined,
    });
    expect("error" in out && out.gaps?.map((g) => g.key)).toEqual(["module:Quotes", "tasks"]);
    s = switchReducer(s, {
      type: "failed", message: "error" in out ? out.error : "", gaps: "error" in out ? out.gaps : [],
    });
    expect(s.step).toBe("leave");
    expect(s.steps).toEqual(["intro", "leave", "confirm"]);
    const html = draw(s, clean);
    expect(html).toContain("Leave out Quotes");
    expect(html).toContain("Leave out Tasks");
    expect(html).toContain("Metorite could not read every record");
    expect(html).toMatch(/role="alert"[^>]*>Some modules are not complete/);
    // Next waits for both ticks, and the word stays.
    expect(buttonWith(html, "Next")).toMatch(/ disabled=""/);
    s = switchReducer(s, { type: "tick", key: "module:Quotes", on: true });
    s = switchReducer(s, { type: "tick", key: "tasks", on: true });
    expect(buttonWith(draw(s, clean), "Next")).not.toMatch(/ disabled=""/);
    expect(s.typed).toBe(SWITCH_WORD);
  });
});

// ── The two switches (D133 as amended 2026-10-29) ───────────────────────────

const START_EMPTY = ACTIONS["switch-to-metorite"];

const conn = (status: ConnectionInfo["status"]): ConnectionInfo => ({
  connected: status === "connected",
  provider: "zoho",
  owning_app: "crm",
  status,
  location: "in",
  scopes: [],
  connected_by: "dana@x.test",
  last_sync_at: null,
  last_error: null,
  mode: "mirror",
});

/** The setup screen of a Mirror org: the hub, and the cutover card under it. */
function drawScreen(state: HubState, connection: ConnectionInfo, p: CutoverPreview): string {
  const hub = renderToStaticMarkup(createElement(SetupHubView, {
    state, mode: "mirror", connection, sync: null, counts: null,
    busy: { syncing: false, connecting: false }, onAction: noop,
    switchInCard: switchesInCard(p),
  }));
  const card = renderToStaticMarkup(createElement(CutoverCardView, {
    preview: p, onSwitch: noop, onStartEmpty: noop,
  }));
  return hub + card;
}

const timesIn = (html: string, words: string): number => html.split(words).length - 1;

describe("CRM-Z10b the two switches have names that say what happens to the records", () => {
  it("each label is unique, and each dialog title is its label", () => {
    expect(KEEP_RECORDS_SWITCH.label).toBe("Switch to Metorite, keep the Zoho records");
    expect(START_EMPTY.label).toBe("Switch to Metorite, start empty");
    expect(SWITCH_LABEL).toBe(KEEP_RECORDS_SWITCH.label);
    expect(START_EMPTY.dialog?.title).toBe(START_EMPTY.label);
    expect(START_EMPTY.dialog?.then).toBe("native");
    const others = [...Object.values(ACTIONS), CONNECT_FROM_NATIVE];
    for (const action of others) {
      expect(action.label, action.id).not.toBe(KEEP_RECORDS_SWITCH.label);
      if (action !== START_EMPTY) {
        expect(action.label.startsWith("Switch to Metorite"), action.id).toBe(false);
      }
    }
    const labels = Object.values(ACTIONS).map((action) => action.label);
    expect(new Set(labels).size).toBe(labels.length);
    // One plain sentence under each, about the records.
    for (const explain of [KEEP_RECORDS_SWITCH.explain, START_EMPTY.explain]) {
      expect(explain.match(/[.!?]/g)).toHaveLength(1);
      expect(explain).toMatch(/record/);
    }
  });

  it("shows both choices once each when the org may switch, and only start empty when not", () => {
    const states: Array<[HubState, ConnectionInfo]> = [
      ["mirror-connected", conn("connected")],
      ["mirror-disconnected", conn("disconnected")],
      ["mirror-needs-reset", conn("needs_reset")],
    ];
    for (const [state, connection] of states) {
      expect(hubState("mirror", connection)).toBe(state);
      const on = drawScreen(state, connection, preview());
      expect(timesIn(on, KEEP_RECORDS_SWITCH.label), state).toBe(1);
      expect(timesIn(on, START_EMPTY.label), state).toBe(1);
      // Both sit in the card: the keep switch is primary, start empty has the danger tone.
      expect(buttonWith(on, KEEP_RECORDS_SWITCH.label)).toContain("bg-primary");
      expect(buttonWith(on, START_EMPTY.label)).toContain("text-destructive");
      expect(on).toContain(KEEP_RECORDS_SWITCH.explain);
      expect(on).toContain(START_EMPTY.explain);

      const off = drawScreen(state, connection, preview({ can_switch: false }));
      expect(timesIn(off, KEEP_RECORDS_SWITCH.label), state).toBe(0);
      expect(timesIn(off, START_EMPTY.label), state).toBe(1);
      expect(buttonWith(off, START_EMPTY.label)).not.toContain("text-destructive");
    }
  });
});

describe("CRM-Z10b the card", () => {
  afterEach(() => clearAll());

  it("reads the status in Native mode only after the switch (F3)", () => {
    const pending: CutoverStatus = {
      state: "done", run_id: "r1", started_at: null, finished_at: null, error: null,
      age_seconds: 60, zoho_connection: "connected",
    };
    put("crm:cutover-status", pending);
    put("crm:mode", { mode: "native", cutover_done: true });
    const switched = renderToStaticMarkup(createElement(CutoverCard, {
      mode: "native", onStartEmpty: noop,
    }));
    expect(switched).toContain("Finish closing Zoho");
    // A Native CRM that never switched: the card mounts no status read.
    put("crm:mode", { mode: "native" });
    const never = renderToStaticMarkup(createElement(CutoverCard, {
      mode: "native", onStartEmpty: noop,
    }));
    expect(never).toBe("");
  });

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
