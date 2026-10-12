// The CRM joins the shell (WS-53 CRM-X2, spec section 19.6 and the notes of
// 19.10).
//
// This file holds the browser half: the three new jobs on the CRM page, the
// Call composer of `log-call`, and the icons of the CRM's Find rows. The
// gateway half is `tests/unit/test_shell_search.py` and
// `tests/unit/test_shell_search_crm_r8.py`. The two job lists are one list
// through `tests/unit/test_shell_intent.py::TestOneJobList`.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";
import { FIND_ICONS } from "@/lib/shell/CommandBar";
import { JOBS } from "@/lib/shell/registry";
import {
  CRM_JOB_IDS,
  LOG_CALL_HINT,
  MAX_WAIT_FRAMES,
  SETTLE_FRAMES,
  FOCUS_ASK_MS,
  afterDialogsClose,
  findRecordPlan,
  focusAskFor,
  logCallPlan,
  otherDialogOpen,
} from "../lib/shellJobs";
import { openRecord, parseView, selectRecordTab, viewHref } from "../lib/urlState";
import Timeline from "./Timeline";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

/**
 * The view section 19.10 names, `/crm?tab=deals&layout=list`. Deals is the
 * landing tab, so the address leaves `tab=deals` out.
 */
const DEALS_LIST = viewHref(parseView("tab=deals&layout=list"));

// ── The jobs ───────────────────────────────────────────────────────────────

describe("the CRM jobs in the bar (item 9)", () => {
  it("are four, all of the CRM pane, each opening its own `?do=`", () => {
    const crm = JOBS.filter((j) => j.app === "/crm").map((j) => [j.id, j.href]);
    expect(crm).toEqual(CRM_JOB_IDS.map((id) => [id, `/crm?do=${id}`]));
  });

  it("each draws a known icon", () => {
    for (const j of JOBS.filter((j) => j.app === "/crm")) {
      expect(isKnownIcon(j.icon), `${j.id}: ${j.icon}`).toBe(true);
    }
  });
});

describe("log-call", () => {
  it("with a record open, opens its Timeline with the composer on Call", () => {
    const before = openRecord(parseView("tab=deals&layout=list&q=bosch"), "deal", "d-1");
    const plan = logCallPlan(before);
    expect(plan.view).toEqual(selectRecordTab(before, "timeline"));
    expect(viewHref(plan.view)).toContain("deal=d-1");
    expect(plan).toMatchObject({ composeCall: true, focusSearch: false, hint: null });
  });

  it("with no record, opens the Deals LIST, focuses its search, and says why", () => {
    const plan = logCallPlan(null);
    // Section 19.10: Deals open as a board, and a board has no search box.
    expect(viewHref(plan.view)).toBe(DEALS_LIST);
    expect(plan).toMatchObject({ composeCall: false, focusSearch: true, hint: LOG_CALL_HINT });
    expect(LOG_CALL_HINT).toBe("Open the record to log the call on");
  });

  it("from the Deals board or another list, still lands on the Deals list", () => {
    expect(viewHref(logCallPlan(parseView("tab=deals")).view)).toBe(DEALS_LIST);
    const fromContacts = logCallPlan(parseView("tab=contacts&q=priya")).view;
    expect(fromContacts.tab).toBe("deals");
    expect(fromContacts.layout).toBe("list");
    expect(fromContacts.record).toBeNull();
  });
});

describe("find-record", () => {
  it("opens the list of the collection on screen and focuses its search", () => {
    const plan = findRecordPlan(parseView("tab=leads&layout=board"));
    expect(plan.view.tab).toBe("leads");
    expect(plan.view.layout).toBe("list");
    expect(plan).toMatchObject({ focusSearch: true, composeCall: false, hint: null });
  });

  it("closes an open record, so the list and its box are in view", () => {
    const before = openRecord(parseView("tab=contacts"), "contact", "c-1");
    const plan = findRecordPlan(before);
    expect(plan.view.record).toBeNull();
    expect(viewHref(plan.view)).toBe("/crm?tab=contacts");
  });

  it("opens the Deals list from Reports, from Settings and from nothing", () => {
    for (const before of [parseView("tab=reports"), parseView("tab=settings"), null]) {
      expect(viewHref(findRecordPlan(before).view)).toBe(DEALS_LIST);
    }
  });
});

describe("afterDialogsClose", () => {
  /** A host whose frames run when the test says, with a dialog it opens and closes. */
  function host(open: boolean) {
    const state = { open, queue: [] as Array<() => void>, cancelled: 0 };
    return {
      state,
      host: {
        dialogOpen: () => state.open,
        nextFrame: (run: () => void) => state.queue.push(run),
        cancelFrame: () => void (state.cancelled += 1),
      },
      frame: () => state.queue.shift()?.(),
    };
  }

  it("waits while the command bar is still closing, then lets it settle", () => {
    const h = host(true);
    let ran = 0;
    afterDialogsClose(() => void (ran += 1), h.host);
    for (let i = 0; i < 10; i++) h.frame();
    expect(ran).toBe(0);
    h.state.open = false;
    for (let i = 0; i < SETTLE_FRAMES; i++) h.frame();
    expect(ran).toBe(0);
    h.frame();
    expect(ran).toBe(1);
    h.frame();
    expect(ran).toBe(1);
  });

  it("gives up waiting after MAX_WAIT_FRAMES, so a dialog left open never blocks it", () => {
    const h = host(true);
    let ran = 0;
    afterDialogsClose(() => void (ran += 1), h.host);
    for (let i = 0; i < MAX_WAIT_FRAMES + SETTLE_FRAMES + 1; i++) h.frame();
    expect(ran).toBe(1);
  });

  it("can be cancelled", () => {
    const h = host(false);
    const cancel = afterDialogsClose(() => undefined, h.host);
    cancel();
    expect(h.state.cancelled).toBe(1);
  });
});

// ── The page mounts the jobs (spec: beside the two `new-lead` mounts) ─────

describe("the CRM page", () => {
  const page = read("../page.tsx");
  const gate = page.slice(page.indexOf("function CrmGate"));
  const inner = page.slice(page.indexOf("function CrmPageInner"), page.indexOf("function CrmGate"));

  it("mounts one ShellJob for each CRM job, in the page and in the gate", () => {
    for (const id of CRM_JOB_IDS) {
      expect(inner, `${id} in the page`).toMatch(new RegExp(`id="${id}"|id: "${id}"|"${id}"`));
    }
    // The gate says why nothing opens, for each job, through one list.
    expect(gate).toMatch(/CRM_JOB_IDS\.map\(/);
    expect(gate).toMatch(/<ShellJob[\s\S]{0,80}key=\{id\}[\s\S]{0,40}id=\{id\}/);
  });

  it("changes the view through the browser's history, never the Next router", () => {
    // PR #914 bans `router.push` and `router.replace` under `app/crm/`. The
    // jobs follow `shell/doJob.tsx`: the address changes, and nothing waits on
    // the server.
    const run = inner.slice(inner.indexOf("function runJob"), inner.indexOf("function runJob") + 1400);
    expect(run).toContain("replaceView(viewHref(plan.view))");
    expect(run).not.toMatch(/router\.(push|replace)/);
  });

  it("keeps the view from before the job link, because the link replaces it", () => {
    expect(inner).toMatch(/viewBeforeJob\.current = view/);
    expect(inner).toMatch(/searchParams\.get\("do"\)/);
  });

  it("refuses New deal in Mirror mode with its own line", () => {
    expect(inner).toContain("NEW_DEAL_MIRROR");
  });
});

// ── The Call composer ─────────────────────────────────────────────────────

const timeline = (callRequest?: number) =>
  renderToStaticMarkup(
    createElement(Timeline, {
      entries: [],
      onLog: () => undefined,
      onToggleTask: () => undefined,
      saving: false,
      callRequest,
    }),
  );

/** The label of the composer pill that is on. */
const activeComposer = (html: string) =>
  html.match(/<button[^>]*bg-primary text-primary-foreground[^>]*>[\s\S]*?<\/button>/)?.[0].replace(/<[^>]*>/g, "").trim();

describe("the Timeline composer", () => {
  it("starts on Note", () => {
    expect(activeComposer(timeline())).toBe("Note");
    expect(activeComposer(timeline(0))).toBe("Note");
  });

  it("starts on Call when log-call asks", () => {
    expect(activeComposer(timeline(1))).toBe("Call");
  });

  it("focuses only after the command bar has closed (review P2)", () => {
    // The bar's `Modal` gives focus back when it has closed. A plain effect
    // focused the composer first, and the bar took the focus away again.
    const src = read("./Timeline.tsx");
    const effect = src.slice(src.indexOf("useEffect(() => {"), src.indexOf("}, [callRequest]);"));
    expect(effect).toMatch(/afterDialogsClose\(/);
    // Its own record sheet is a dialog too, so the wait leaves that one out.
    expect(effect).toMatch(/browserHost\(composer\.current\)/);
    expect(effect).not.toMatch(/^\s*if \(callRequest\) composer\.current\?\.focus\(\);/m);
  });
});

describe("otherDialogOpen", () => {
  // The composer sits in the record sheet, which is a dialog too. Only a
  // dialog that does NOT hold the composer is one to wait for.
  const node = { name: "composer" };
  const dialog = (holds: boolean) => ({ contains: (n: unknown) => holds && n === node });

  it("ignores the dialog that holds the element", () => {
    expect(otherDialogOpen([dialog(true)], node)).toBe(false);
  });

  it("waits for any other dialog, such as the closing command bar", () => {
    expect(otherDialogOpen([dialog(true), dialog(false)], node)).toBe(true);
    expect(otherDialogOpen([dialog(false)], null)).toBe(true);
  });

  it("finds nothing when no dialog is open", () => {
    expect(otherDialogOpen([], node)).toBe(false);
  });
});

describe("focusAskFor (review P2: an ask whose list never renders)", () => {
  const ask = { entity: "deals" as const, at: 1_000 };

  it("focuses when its list is on screen in time, and the ask is spent", () => {
    expect(focusAskFor(ask, "deals", 1_500)).toEqual({ focus: true, keep: null });
  });

  it("waits while another view is still on screen", () => {
    expect(focusAskFor(ask, null, 1_500)).toEqual({ focus: false, keep: ask });
  });

  it("drops an ask that is too old, so a later visit to the list keeps its focus", () => {
    expect(focusAskFor(ask, "deals", 1_000 + FOCUS_ASK_MS + 1)).toEqual({ focus: false, keep: null });
    expect(focusAskFor(ask, null, 1_000 + FOCUS_ASK_MS + 1)).toEqual({ focus: false, keep: null });
  });

  it("does nothing with no ask", () => {
    expect(focusAskFor(null, "deals", 1_500)).toEqual({ focus: false, keep: null });
  });
});

describe("the page reads the focus ask through focusAskFor", () => {
  it("never compares the ask by hand", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(/focusAskFor\(focusFor\.current, listEntity, Date\.now\(\)\)/);
  });
});

// ── The Find rows ─────────────────────────────────────────────────────────

describe("the CRM kinds of the bar's Find group (item 4 of the scope)", () => {
  it("each has a known icon, the one its rail row draws", () => {
    expect(FIND_ICONS).toMatchObject({
      lead: "Target",
      deal: "Handshake",
      contact: "Contact",
      company: "Building2",
    });
    for (const name of Object.values(FIND_ICONS)) expect(isKnownIcon(name), name).toBe(true);
  });
});
