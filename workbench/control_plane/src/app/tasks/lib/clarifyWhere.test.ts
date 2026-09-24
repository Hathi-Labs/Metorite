/**
 * Clarify — two production defects found by the audit of 2026-09-24.
 *
 * 1. "Clarify →" on a "From Projects" row did nothing. The modal opened only
 *    for INBOX, and a board row carries a derived NEXT, SOMEDAY or WAITING, so
 *    the modal closed itself on its first render.
 * 2. Silent publish. The form pre-selected a company project the assistant
 *    inferred, so one "Organize it" click filed a PRIVATE capture onto a team
 *    board the member never chose.
 *
 * The rules are pure (`lib/clarify.ts`). The wiring is rendered through
 * `react-dom/server`, which needs no DOM, like `TimelineView.test.ts`. That
 * shows what the first render draws. It cannot run an effect, so the modal's
 * close-on-mount is fenced through `isClarifiable`, which that effect reads.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

// A server render reads zustand's INITIAL state (its server snapshot), never
// the state a test set. This store's snapshot is its CURRENT state, so the
// render shows what the test seeded. The store under test is otherwise real.
vi.mock("zustand", async (importOriginal) => {
  const actual = await importOriginal<typeof import("zustand")>();
  return {
    ...actual,
    create: (init: import("zustand").StateCreator<object>) => {
      const api = actual.createStore(init);
      api.getInitialState = api.getState;
      const hook = (selector: (s: unknown) => unknown) => actual.useStore(api, selector as never);
      return Object.assign(hook, api);
    },
  };
});

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    apiCapture: vi.fn(),
    apiOrganize: vi.fn(),
    apiPatchItem: vi.fn(),
    fetchMyRoot: vi.fn(),
    fetchUntriaged: vi.fn(),
    fetchAreas: vi.fn(),
  };
});

import { apiCapture, fetchMyRoot } from "./api";
import { ClarifyModal } from "../components/ClarifyModal";
import { ClarifyPanel } from "../components/ClarifyPanel";
import { WherePicker } from "../components/WherePicker";
import {
  clarifyQueue,
  delegateAllowed,
  initialOwner,
  initialWhere,
  isClarifiable,
  isPersonalTask,
  proposeClarification,
  whereOffersNoProject,
  whereVisibilityHint,
} from "./clarify";
import { useTaskStore } from "./taskStore";
import type { GtdItem, GtdProject } from "./types";

const ROOT = "root-me";
const AREA = { id: "area-home", name: "Home", openCount: 0 } as never;

const BOARD: GtdProject = {
  id: "p-launch",
  source: "LOCAL",
  provider: "local",
  outcome: "Pricing page copy refresh",
  status: "ACTIVE",
  hasNextAction: true,
};

/** An untriaged board row: a colleague assigned it, its disposition is derived. */
const FROM_BOARD: GtdItem = {
  id: "t-board",
  source: "LOCAL",
  title: "Review the pricing page copy",
  disposition: "NEXT",
  isMine: true,
  isTriaged: false,
  projectId: BOARD.id,
  projectName: BOARD.outcome,
  assignedBy: "pm@example.com",
  createdAt: "2026-09-20T08:00:00+00:00",
  updatedAt: "2026-09-20T08:00:00+00:00",
};

/** A private capture whose words match the company board. */
const CAPTURE: GtdItem = {
  id: "c-1",
  source: "LOCAL",
  title: "Check pricing page copy wording",
  disposition: "INBOX",
  isMine: true,
  projectId: ROOT,
  createdAt: "2026-09-21T08:00:00+00:00",
  updatedAt: "2026-09-21T08:00:00+00:00",
};

function seed(over: Record<string, unknown> = {}) {
  useTaskStore.setState({
    backend: "demo",
    items: [FROM_BOARD, CAPTURE],
    projects: [BOARD],
    areas: [AREA],
    personalRootId: ROOT,
    people: [],
    fromProjectIds: new Set([FROM_BOARD.id]),
    selectedItemId: null,
    clarifyModalOpen: false,
    processedThisSession: 0,
    undoSnapshot: null,
    ...over,
  });
}

// ── Defect 1: Clarify on a "From Projects" row ──────────────────────────────

describe("Clarify opens on an untriaged From Projects row", () => {
  beforeEach(() => seed());

  it("a NEXT row in fromProjectIds is clarifiable, and a NEXT row outside it is not", () => {
    const ids = new Set([FROM_BOARD.id]);
    expect(isClarifiable(FROM_BOARD, ids)).toBe(true);
    expect(isClarifiable({ ...FROM_BOARD, id: "other" }, ids)).toBe(false);
    expect(isClarifiable(CAPTURE, ids)).toBe(true);
  });

  it("the modal renders the panel for a NEXT + untriaged row", () => {
    useTaskStore.getState().openClarify(FROM_BOARD.id);
    const html = renderToStaticMarkup(createElement(ClarifyModal));
    expect(html).toContain("Processing inbox");
    expect(html).toContain(FROM_BOARD.title);
    // Both halves of the walk are counted.
    expect(html).toContain("2 left");
  });

  it("a decision drops the row from From Projects and opens the next item", () => {
    useTaskStore.getState().openClarify(FROM_BOARD.id);
    useTaskStore.getState().clarify(FROM_BOARD.id, { kind: "someday" } as never);
    const s = useTaskStore.getState();
    expect(s.fromProjectIds.has(FROM_BOARD.id)).toBe(false);
    expect(s.selectedItemId).toBe(CAPTURE.id);
    expect(s.processedThisSession).toBe(1);
    const html = renderToStaticMarkup(createElement(ClarifyModal));
    expect(html).toContain(CAPTURE.title);
  });

  it("clarifying a capture walks on to an untriaged board row, oldest first", () => {
    const newer = { ...FROM_BOARD, createdAt: "2026-09-22T08:00:00+00:00" };
    seed({ items: [newer, CAPTURE] });
    useTaskStore.getState().openClarify(CAPTURE.id);
    useTaskStore.getState().clarify(CAPTURE.id, { kind: "reference" } as never);
    expect(useTaskStore.getState().selectedItemId).toBe(FROM_BOARD.id);
    expect(useTaskStore.getState().processedThisSession).toBe(1);
  });

  it("skip walks the union too", () => {
    useTaskStore.getState().openClarify(FROM_BOARD.id);
    useTaskStore.getState().skipToNextInbox();
    expect(useTaskStore.getState().selectedItemId).toBe(CAPTURE.id);
  });

  it("the queue is FIFO over both sets and leaves out an archived board row", () => {
    const archived = { ...FROM_BOARD, id: "t-gone", archivedAt: "2026-09-23T00:00:00Z" };
    const q = clarifyQueue([CAPTURE, FROM_BOARD, archived], new Set([FROM_BOARD.id, "t-gone"]));
    expect(q.map((i) => i.id)).toEqual([FROM_BOARD.id, CAPTURE.id]);
  });
});

// ── Defect 2: a company project is never pre-selected on a personal task ────

describe("initialWhere — no silent publish", () => {
  it("a personal task with an inferred company project starts with NO project selected", () => {
    expect(
      initialWhere({
        proposalProjectId: BOARD.id,
        itemProjectId: ROOT,
        areaIds: ["area-home"],
      }),
    ).toEqual({ suggested: BOARD.id });
  });

  it("a personal task with an inferred Area starts with that Area selected", () => {
    expect(
      initialWhere({
        proposalProjectId: "area-home",
        itemProjectId: ROOT,
        areaIds: ["area-home"],
      }),
    ).toEqual({ selected: "area-home" });
  });

  it("an unknown id is a suggestion, never a pre-selection", () => {
    expect(
      initialWhere({ proposalProjectId: "x", itemProjectId: ROOT, areaIds: [] }),
    ).toEqual({ suggested: "x" });
  });

  it("a board task starts on its own board", () => {
    expect(
      initialWhere({
        proposalProjectId: BOARD.id,
        itemProjectId: BOARD.id,
        areaIds: [],
      }),
    ).toEqual({ selected: BOARD.id });
  });

  it("fails closed: a board task never takes an inferred OTHER company project", () => {
    expect(
      initialWhere({ proposalProjectId: "p-other", itemProjectId: BOARD.id, areaIds: [] }),
    ).toEqual({ suggested: "p-other" });
  });

  it("fails closed with personalRootId = null: the panel pre-selects nothing", () => {
    // A first-time member, or a failed fetchMyRoot. isPersonalTask reads the
    // capture as a board task here, and the rule must not care.
    seed({ personalRootId: null });
    expect(isPersonalTask(CAPTURE, null, ["area-home"])).toBe(false);
    const html = renderToStaticMarkup(createElement(ClarifyPanel, { item: CAPTURE }));
    // The heuristic did infer the board: the banner offers it by name.
    expect(html).toContain("Company board · visible to the team");
    // The destination chip names what Accept files into. It names no project.
    expect(html).toMatch(/>Local<\/span>/);
    expect(html).not.toMatch(/Local · Pricing page/);
  });

  it("no proposal project starts on No project", () => {
    expect(initialWhere({ itemProjectId: ROOT, areaIds: [] })).toEqual({});
  });

  it("the local heuristic does infer the board for the capture — so the rule is load-bearing", () => {
    const p = proposeClarification(CAPTURE, [], [BOARD]);
    expect(p.projectId).toBe(BOARD.id);
    expect(p.projectInferred).toBe(true);
  });
});

describe("whereVisibilityHint", () => {
  it("says private when nothing or an Area is picked", () => {
    const ids = [BOARD.id];
    expect(whereVisibilityHint({ companyProjectIds: ids })).toContain("Private to you");
    expect(whereVisibilityHint({ selected: "area-home", companyProjectIds: ids })).toContain(
      "Private to you",
    );
  });

  it("switches to the team once a company project is picked", () => {
    const hint = whereVisibilityHint({ selected: BOARD.id, companyProjectIds: [BOARD.id] });
    expect(hint).toContain("Visible to the team");
    expect(hint).not.toContain("Private to you");
  });
});

describe("the Where list", () => {
  const draw = (includeNoProject: boolean, value?: string, suggestedId?: string) =>
    renderToStaticMarkup(
      createElement(WherePicker, {
        areas: [AREA],
        includeAreas: includeNoProject,
        includeNoProject,
        projects: [BOARD],
        value,
        suggestedId,
        onChange: () => {},
        onCreateArea: async () => undefined,
      }),
    );

  it("a board task's Where list has no 'No project'", () => {
    const personal = isPersonalTask(FROM_BOARD, ROOT, ["area-home"]);
    expect(personal).toBe(false);
    expect(whereOffersNoProject(personal)).toBe(false);
    expect(draw(false, BOARD.id)).not.toContain("No project");
  });

  it("a personal task keeps 'No project', and the company project is only marked suggested", () => {
    expect(whereOffersNoProject(true)).toBe(true);
    const html = draw(true, undefined, BOARD.id);
    expect(html).toContain("No project");
    expect(html).toMatch(/Pricing page copy refresh<\/span><span[^>]*>suggested/);
    // The board row is not pressed: only No project is.
    expect(html).toMatch(/aria-pressed="true"[^>]*>.*No project/);
    expect(html).not.toMatch(/aria-pressed="true"[^>]*>(?:(?!<\/button>).)*Pricing page/);
  });
});

describe("ClarifyPanel — a personal capture with an inferred company project", () => {
  beforeEach(() => seed());

  it("offers the board with a banner that names the team, and does not pre-select it", () => {
    const html = renderToStaticMarkup(createElement(ClarifyPanel, { item: CAPTURE }));
    expect(html).toContain("Company board · visible to the team");
    expect(html).not.toContain("Local project");
    // The recommendation chip names what Accept files into — nothing yet.
    expect(html).not.toMatch(/· Pricing page/);
  });
});

describe("personalRootId is learned once a capture creates the root", () => {
  it("a live capture with no root yet reads GET /my/project and stores the id", async () => {
    seed({ backend: "live", personalRootId: null, settings: { captureDedup: false } as never });
    vi.mocked(apiCapture).mockResolvedValue({ ...CAPTURE, id: "srv-1" });
    vi.mocked(fetchMyRoot).mockResolvedValue({ id: "root-new", name: "Me" });
    useTaskStore.getState().capture("First capture ever");
    for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0));
    expect(fetchMyRoot).toHaveBeenCalledTimes(1);
    expect(useTaskStore.getState().personalRootId).toBe("root-new");
  });
});

describe("owner on a board task — no one-key reassign", () => {
  beforeEach(() => seed({ people: [{ name: "Dana Rao", email: "dana@example.com" }] }));

  it("a board row whose title reads as a delegate opens with owner = Me", () => {
    // "Ask Dana to …" is the heuristic's high-confidence WAITING proposal.
    const row = { ...FROM_BOARD, title: "Ask Dana to review the pricing page copy" };
    const p = proposeClarification(row, [{ name: "Dana Rao", email: "dana@example.com" }], [BOARD]);
    expect(p.suggestedAssignee?.name).toBe("Dana Rao");
    expect(p.confidence).toBe("high");
    expect(initialOwner({ personal: false, hasSuggestedAssignee: true })).toBe("me");
    const html = renderToStaticMarkup(
      createElement(ClarifyPanel, { item: row, reclarify: true }),
    );
    // Re-clarify opens the form: Me is the pressed owner, Delegate is not.
    expect(html).toMatch(/border-primary bg-primary\/10 text-primary"><svg[^]*?<\/svg> Me<\/button>/);
    expect(html).not.toContain("reassigns the task on its board");
    // The recommendation does not claim Accept hands it to Dana.
    expect(html).not.toContain("→ Dana Rao");
  });

  it("a personal capture keeps the proposed delegate", () => {
    expect(initialOwner({ personal: true, hasSuggestedAssignee: true })).toBe("delegate");
    expect(initialOwner({ personal: true, hasSuggestedAssignee: false })).toBe("me");
  });

  it("a board-task delegate applies only when the member picked it this session", () => {
    expect(delegateAllowed({ personal: false, delegating: true, pickedThisSession: false })).toBe(false);
    expect(delegateAllowed({ personal: false, delegating: true, pickedThisSession: true })).toBe(true);
    expect(delegateAllowed({ personal: true, delegating: true, pickedThisSession: false })).toBe(true);
    expect(delegateAllowed({ personal: false, delegating: false, pickedThisSession: false })).toBe(true);
  });
});
