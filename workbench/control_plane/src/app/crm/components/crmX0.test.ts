// CRM-X0: setup, connection and start over (spec crm_platform.md §19.11).
// The runner has no DOM, so each component is rendered to a string, and the
// "only the hub" rule is a scan of the source.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { connectErrorMessage, mirrorStalled, modeLine, type ConnectionInfo } from "../lib/connect";
import {
  ACTIONS,
  CONNECT_FROM_NATIVE,
  SETUP_HREF,
  hubActions,
  hubState,
  type HubState,
  type RecordCounts,
} from "../lib/setup";
import {
  CONFIRM_WORD,
  INITIAL,
  RESET_FAILED,
  canConfirm,
  confirmLabel,
  runStartOver,
  startOverReducer,
  type StartOverEvent,
  type StartOverState,
} from "../lib/startOver";
import { SetupLink, StalledBanner } from "./MirrorBanner";
import { SetupHubView, countsSummary } from "./SetupHub";
import { StartOverBody } from "./StartOverDialog";

const noop = () => undefined;

/** The markup of the one button whose text holds *label*. */
const buttonWith = (html: string, label: string): string => {
  const found = html.split("<button").find((part) => part.split("</button>")[0].includes(label));
  expect(found, label).toBeDefined();
  return found!.split("</button>")[0];
};

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

const COUNTS: RecordCounts = {
  counts: { leads: 1, contacts: 2, organizations: 1, deals: 547, activities: 0, module_records: 0 },
  total: 551,
};

const labels = (state: HubState, mirrorOn = true) => {
  const plan = hubActions(state, mirrorOn);
  return { main: plan.main.map((a) => a.label), other: plan.other.map((a) => a.label) };
};

const drawHub = (mode: "native" | "mirror" | null, connection: ConnectionInfo | null) =>
  renderToStaticMarkup(
    createElement(SetupHubView, {
      state: hubState(mode, connection),
      mode,
      connection,
      sync: null,
      counts: COUNTS,
      busy: { syncing: false, connecting: false },
      onAction: noop,
    }),
  );

// ── Done when 5: the hub's actions for each state ──────────────────────────

describe("the setup hub offers the actions of each state (§19.11 item 1)", () => {
  it("no mode: choose Connect Zoho or Use Metorite", () => {
    expect(hubState(null, conn("disconnected"))).toBe("none");
    expect(labels("none")).toEqual({ main: ["Use Metorite", "Connect Zoho"], other: [] });
    expect(labels("none", false)).toEqual({ main: ["Use Metorite"], other: [] });
  });

  it("Mirror, connected: Sync now, then Disconnect, Switch to Metorite, Start over", () => {
    expect(hubState("mirror", conn("connected"))).toBe("mirror-connected");
    expect(labels("mirror-connected")).toEqual({
      main: ["Sync now"],
      other: ["Disconnect", "Switch to Metorite", "Start over"],
    });
    const html = drawHub("mirror", conn("connected"));
    for (const word of ["Sync now", "Disconnect", "Switch to Metorite", "Start over", "Connected"]) {
      expect(html).toContain(word);
    }
    expect(html).not.toContain("Reconnect Zoho");
  });

  it("Mirror, disconnected: Reconnect Zoho (keeps the records) is the main action", () => {
    for (const status of ["disconnected", "needs_reconnect", null] as const) {
      expect(hubState("mirror", conn(status))).toBe("mirror-disconnected");
    }
    expect(labels("mirror-disconnected")).toEqual({
      main: ["Reconnect Zoho (keeps the records)"],
      other: ["Switch to Metorite", "Start over"],
    });
    // The production state of 2026-10-12: 547 deals and a disconnected Zoho.
    const html = drawHub("mirror", conn("disconnected"));
    expect(html).toContain("Reconnect Zoho (keeps the records)");
    expect(html).toContain("547 deals");
    expect(html).not.toContain("Sync now");
    // The main action is the primary button.
    expect(buttonWith(html, "Reconnect Zoho (keeps the records)")).toContain("bg-primary");
    expect(buttonWith(html, "Start over")).not.toContain("bg-primary");
  });

  it("another Zoho account: the start over for the new account, or the first account again", () => {
    expect(hubState("mirror", conn("needs_reset"))).toBe("mirror-needs-reset");
    expect(labels("mirror-needs-reset")).toEqual({
      main: ["Start over with the new account"],
      other: ["Reconnect Zoho (keeps the records)", "Switch to Metorite", "Start over"],
    });
    expect(ACTIONS["start-over-new-account"].dialog?.then).toBe("mirror");
  });

  it("Native: no main action, then Connect Zoho and Start over", () => {
    expect(hubState("native", null)).toBe("native");
    expect(labels("native")).toEqual({ main: [], other: ["Connect Zoho", "Start over"] });
    expect(labels("native", false)).toEqual({ main: [], other: ["Start over"] });
    // Connect Zoho from Native needs an empty CRM, so it opens the dialog.
    expect(CONNECT_FROM_NATIVE.dialog?.then).toBe("mirror");
  });

  it("each action says in one sentence what it does (§19.11 item 7)", () => {
    for (const action of [...Object.values(ACTIONS), CONNECT_FROM_NATIVE]) {
      expect(action.explain.match(/[.!?]/g), action.id).toHaveLength(1);
      expect(action.explain.endsWith("."), action.id).toBe(true);
    }
    expect(ACTIONS.disconnect.explain).toContain("records stay");
    expect(ACTIONS.disconnect.explain).toContain("stop updating");
    expect(ACTIONS["start-over"].explain).toContain("Every record goes");
    // Each action that removes records opens the dialog. The others do not.
    const removes = ["switch-to-metorite", "start-over", "start-over-new-account"];
    for (const action of Object.values(ACTIONS)) {
      expect(Boolean(action.dialog)).toBe(removes.includes(action.id));
    }
  });

  it("counts the records in one line", () => {
    expect(countsSummary(COUNTS)).toBe(
      "551 in all: 1 leads · 2 contacts · 1 companies · 547 deals",
    );
    expect(countsSummary({ counts: {}, total: 0 })).toBe("No records");
    expect(countsSummary(null)).toBe("—");
  });
});

// ── Done when 5: only the hub offers a change of setup ─────────────────────

const CRM = fileURLToPath(new URL("..", import.meta.url));
const crmSources = (): string[] => {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(CRM, full).split(sep).join("/"));
      }
    }
  };
  walk(CRM);
  return out;
};
const read = (rel: string) => readFileSync(join(CRM, rel), "utf8");

describe("only the hub offers a change of setup", () => {
  it("the scan sees the CRM's files", () => {
    expect(crmSources()).toContain("components/SetupHub.tsx");
    expect(crmSources()).toContain("page.tsx");
  });

  it("no CRM file says Change setup any more", () => {
    for (const file of crmSources()) {
      expect(read(file), file).not.toMatch(/Change (CRM )?setup/i);
    }
  });

  it("only the hub's files name a setup change or call the reset", () => {
    const allowed = new Set([
      "lib/setup.ts",
      "lib/startOver.ts",
      "lib/api.ts",
      "components/SetupHub.tsx",
      "components/StartOverDialog.tsx",
    ]);
    for (const file of crmSources()) {
      if (allowed.has(file)) continue;
      const text = read(file);
      expect(text, file).not.toMatch(/Start over|Switch to Metorite|resetCrm|\/reset\b/);
    }
  });

  it("the U5b card and the old connection card are gone", () => {
    expect(crmSources()).not.toContain("components/SetupPanel.tsx");
    expect(crmSources()).not.toContain("components/ConnectionPanel.tsx");
    expect(read("lib/api.ts")).not.toContain("resetMode");
  });

  it("the banner and the title-bar line link to the hub", () => {
    const banner = renderToStaticMarkup(
      createElement(StalledBanner, { isAdmin: true, status: "disconnected", onOpenSetup: noop }),
    );
    expect(banner).toContain("Open CRM setup");
    expect(banner).toContain("Records stay here, but they are not updating.");
    expect(banner).not.toContain("Connect Zoho again");
    const member = renderToStaticMarkup(
      createElement(StalledBanner, { isAdmin: false, status: "needs_reset", onOpenSetup: noop }),
    );
    expect(member).not.toContain("<button");
    expect(member).toContain("not the one these records came from");
    const link = renderToStaticMarkup(
      createElement(SetupLink, { isAdmin: true, children: "Zoho disconnected" }),
    );
    expect(link).toContain(`href="${SETUP_HREF.replace("&", "&amp;")}"`);
    expect(
      renderToStaticMarkup(createElement(SetupLink, { isAdmin: false, children: "x" })),
    ).not.toContain("<a");
    expect(read("page.tsx")).toContain("onOpenSetup={() => chooseView(\"settings\")}");
  });

  it("shows the banner for each state where the records stop updating", () => {
    expect(mirrorStalled(conn("disconnected"))).toBe(true);
    expect(mirrorStalled(conn("needs_reconnect"))).toBe(true);
    expect(mirrorStalled(conn("needs_reset"))).toBe(true);
    expect(mirrorStalled(conn("connected"))).toBe(false);
    expect(modeLine({ mode: "mirror", connection: conn("needs_reset"), sync: null })).toBe(
      "Zoho disconnected",
    );
  });

  it("names the two new refusals of the Zoho return", () => {
    expect(connectErrorMessage("different_org")).toContain("not the one your records came from");
    expect(connectErrorMessage("different_org")).toContain("CRM setup");
    expect(connectErrorMessage("org_unreadable")).toContain("Nothing changed");
  });
});

// ── Done when 6: the dialog, step by step ──────────────────────────────────

const drawStep = (state: StartOverState) =>
  renderToStaticMarkup(
    createElement(StartOverBody, {
      state,
      explain: ACTIONS["start-over"].explain,
      counts: COUNTS,
      onEvent: noop,
      onDownload: noop,
      onConfirm: noop,
      onCancel: noop,
    }),
  );

const walk = (state: StartOverState, ...events: StartOverEvent[]) =>
  events.reduce(startOverReducer, state);

describe("the start-over dialog walks counts, download, typed confirm, then the call", () => {
  it("step 1 shows the counts and says the change removes them", () => {
    expect(INITIAL.step).toBe("counts");
    const html = drawStep(INITIAL);
    expect(html).toContain("This change removes every record below.");
    expect(html).toContain("547");
    expect(html).toContain("551");
    expect(html).toContain('aria-current="step"');
    expect(html).not.toContain("Download a copy");
  });

  it("step 2 offers the download and marks it done when it starts", () => {
    const at = walk(INITIAL, { type: "next" });
    expect(at.step).toBe("download");
    expect(drawStep(at)).toContain("Download a copy");
    expect(drawStep(at)).toContain("Go on without a copy");
    const done = walk(at, { type: "downloaded" });
    expect(done.downloaded).toBe(true);
    expect(drawStep(done)).toContain("Download started");
    expect(drawStep(done)).not.toContain("Go on without a copy");
  });

  it("step 3 waits for the typed DELETE", () => {
    const at = walk(INITIAL, { type: "next" }, { type: "downloaded" }, { type: "next" });
    expect(at.step).toBe("confirm");
    expect(canConfirm(at)).toBe(false);
    expect(drawStep(at)).toContain("Remove 551 records and continue");
    expect(buttonWith(drawStep(at), "Remove 551 records")).toContain('disabled=""');
    const ready = walk(at, { type: "type", text: CONFIRM_WORD });
    expect(buttonWith(drawStep(ready), "Remove 551 records")).not.toContain('disabled=""');
    expect(canConfirm(walk(at, { type: "type", text: "delete" }))).toBe(false);
    expect(canConfirm(walk(at, { type: "type", text: "DELETE " }))).toBe(false);
    const typed = walk(at, { type: "type", text: CONFIRM_WORD });
    expect(canConfirm(typed)).toBe(true);
    // A submit before the word is a no-op.
    expect(walk(at, { type: "submit" }).busy).toBe(false);
    expect(walk(typed, { type: "submit" }).busy).toBe(true);
    expect(confirmLabel(1)).toBe("Remove 1 record and continue");
    expect(confirmLabel(1500)).toBe("Remove 1,500 records and continue");
  });

  it("warns at step 3 when no copy was taken", () => {
    const at = walk(INITIAL, { type: "next" }, { type: "next" });
    expect(drawStep(at)).toContain("You did not download a copy.");
  });

  it("then makes the call, with the typed word and the change", async () => {
    const reset = vi.fn().mockResolvedValue({ removed: {}, mode: null, consent_url: null });
    const invalidate = vi.fn();
    const go = vi.fn();
    expect(await runStartOver("none", { reset, invalidate, go })).toBeNull();
    expect(reset).toHaveBeenCalledWith({ confirm: "DELETE", then: "none" });
    expect(invalidate).toHaveBeenCalledOnce();
    expect(go).not.toHaveBeenCalled();
  });

  it("follows the Zoho consent after a start over to Zoho", async () => {
    const url = "https://accounts.zoho.in/oauth/v2/auth?state=s";
    const reset = vi.fn().mockResolvedValue({ removed: {}, mode: null, consent_url: url });
    const invalidate = vi.fn();
    const go = vi.fn();
    expect(await runStartOver("mirror", { reset, invalidate, go })).toBeNull();
    expect(reset).toHaveBeenCalledWith({ confirm: "DELETE", then: "mirror" });
    expect(go).toHaveBeenCalledWith(url);
  });

  it("shows a refusal and keeps the dialog open", async () => {
    const reset = vi.fn().mockRejectedValue(new Error("Type DELETE to confirm. Nothing was removed."));
    const failed = await runStartOver("native", { reset, invalidate: noop, go: noop });
    expect(failed).toBe("Type DELETE to confirm. Nothing was removed.");
    const state = walk(
      { ...INITIAL, step: "confirm", typed: CONFIRM_WORD, busy: true },
      { type: "failed", message: failed! },
    );
    expect(state.busy).toBe(false);
    expect(drawStep(state)).toContain('role="alert"');
    const plain = await runStartOver("none", {
      reset: vi.fn().mockRejectedValue(new Error("")),
      invalidate: noop,
      go: noop,
    });
    expect(plain).toBe(RESET_FAILED);
  });

  it("is a Modal, with the shared primitives", () => {
    const dialog = read("components/StartOverDialog.tsx");
    expect(dialog).toContain('from "@/components/ui/Modal"');
    expect(dialog).toContain('from "@/components/ui/Input"');
    expect(dialog).not.toMatch(/fixed inset-0|<input\b|<button\b/);
  });
});
