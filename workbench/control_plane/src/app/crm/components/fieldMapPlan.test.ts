// The plan of the Field map: the screen (WS-53 CRM-Q18-2, spec
// crm_platform.md section 18.9.6, items 1 to 10). The runner has no DOM, so a
// component is rendered to a string or called for its element tree. The API
// is faked: `fetch` for the calls of `api.ts`, and a `PlanCalls` object for
// the flows of `lib/fieldMap.ts` that the card runs.
import { readFileSync } from "node:fs";

import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import Button from "@/components/ui/Button";

import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import {
  APPROVE_DRAFT_LABEL,
  DRAFT_LABEL,
  GO_BACK_LABEL,
  PLAN_BUSY,
  PLAN_MOVED,
  PLAN_TITLE,
  addToDraft,
  approveDraft,
  approvedLine,
  diffLine,
  goBack,
  goBackBody,
  historyLine,
  nextDraftChanges,
  openDraft,
  planDiff,
  previousVersion,
  runPlanWrite,
  stateLabel,
  type MappingPlan,
  type PlanCalls,
  type PlanChange,
  type PlanList,
  type PlanSummary,
} from "../lib/fieldMap";
import { dateTime } from "../lib/format";
import {
  FieldMapCardView,
  PlanPanel,
  type FieldMapHandlers,
  type PlanHandlers,
  type PlanView,
} from "./FieldMapCard";
import PipelineSettings from "./PipelineSettings";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

// ── Fixtures ────────────────────────────────────────────────────────────────

const summary = (over: Partial<PlanSummary>): PlanSummary => ({
  version: 1,
  state: "approved",
  rule_count: 1,
  created_by: "ana@acme.test",
  created_at: "2026-10-14T09:00:00Z",
  approved_by: "ana@acme.test",
  approved_at: "2026-10-14T09:00:00Z",
  based_on: null,
  ...over,
});

const field = (entity: string, name: string, column: string) => ({
  kind: "field",
  entity,
  field_api_name: name,
  field_type: "text",
  target_column: column,
  profile_version: 3,
});

const plan = (s: PlanSummary, rules: MappingPlan["rules"]): MappingPlan => {
  const { rule_count: _count, ...rest } = s;
  return { ...rest, rules };
};

// Version 1 was in use, version 2 is in use now, and draft 3 is built on it.
const V1 = summary({ version: 1, state: "superseded", rule_count: 1 });
const V2 = summary({
  version: 2,
  state: "approved",
  rule_count: 2,
  approved_by: "raj@acme.test",
  created_at: "2026-10-15T11:30:00Z",
  approved_at: "2026-10-15T11:30:00Z",
  based_on: 1,
});
const D3 = summary({
  version: 3,
  state: "draft",
  rule_count: 2,
  created_at: "2026-10-16T08:00:00Z",
  approved_by: null,
  approved_at: null,
  based_on: 2,
});
const LIST: PlanList = { approved: 2, versions: [D3, V2, V1] };

const APPROVED = plan(V2, [field("lead", "Sector", "industry"), field("contact", "Desk_Phone", "phone")]);
const DRAFT = plan(D3, [field("lead", "Sector", "lost_note"), field("deal", "Region", "next_step")]);
const EARLIER = plan(V1, [field("lead", "Sector", "industry")]);

const view = (over: Partial<PlanView> = {}): PlanView => ({
  list: LIST,
  approved: APPROVED,
  draft: DRAFT,
  opened: null,
  openedPlan: null,
  busy: null,
  notice: null,
  ...over,
});

const noHandlers: PlanHandlers = { onApprove: () => undefined, onGoBack: () => undefined, onOpen: () => undefined };

const drawPanel = (v: PlanView, handlers: PlanHandlers = noHandlers) =>
  renderToStaticMarkup(createElement(PlanPanel, { view: v, handlers }));

const fieldHandlers: FieldMapHandlers = { busy: null, onPick: () => undefined, onDecide: () => undefined };

const drawCard = (v: PlanView) =>
  renderToStaticMarkup(
    createElement(FieldMapCardView, {
      maps: { leads: null, contacts: null, organizations: null, deals: null },
      handlers: fieldHandlers,
      plan: v,
      planHandlers: noHandlers,
    }),
  );

/** Every element in a tree that a function component returned. */
function elements(node: ReactNode): ReactElement[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const props = node.props as { children?: ReactNode };
  return [node, ...elements(props.children)];
}

/** The `Button` whose label is *label*, in the tree of the panel. */
function buttonOf(tree: ReactNode, label: string): ReactElement<{ onClick?: () => void }> | undefined {
  return elements(tree).find((el) => {
    if (el.type !== Button) return false;
    const children = (el.props as { children?: ReactNode }).children;
    return children === label;
  }) as ReactElement<{ onClick?: () => void }> | undefined;
}

/** A fake of the plan calls: it records each call and answers from a script. */
function fakeCalls(script: Partial<Record<"draft" | "approve" | "restore", Array<MappingPlan | Error>>> = {}) {
  const log: string[] = [];
  const answer = (kind: "draft" | "approve" | "restore") => {
    const next = script[kind]?.shift();
    if (next instanceof Error) return Promise.reject(next);
    return Promise.resolve(next ?? APPROVED);
  };
  const calls: PlanCalls = {
    draft: (changes) => {
      log.push(`draft ${JSON.stringify(changes)}`);
      return answer("draft");
    },
    approve: (version) => {
      log.push(`approve ${version}`);
      return answer("approve");
    },
    restore: (version) => {
      log.push(`restore ${version}`);
      return answer("restore");
    },
    reread: () => {
      log.push("reread");
    },
  };
  return { calls, log };
}

const conflict = (code: string) => new CrmError("The gateway refused.", 409, code);
const noWait = { wait: async () => undefined };

/** Run *work* with `fetch` faked, and return each request as [url, method, body]. */
async function withFetch(
  body: unknown,
  work: () => Promise<unknown>,
): Promise<Array<[string, string | undefined, unknown]>> {
  const calls: Array<[string, string | undefined, unknown]> = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push([String(input), init?.method, init?.body]);
    const text = JSON.stringify(body);
    return { ok: true, status: 200, json: async () => JSON.parse(text), text: async () => text };
  }) as typeof globalThis.fetch;
  try {
    await work();
  } finally {
    globalThis.fetch = original;
  }
  return calls;
}

// ── Item 1: one call for each route of CRM-Q18-1 ─────────────────────────────

describe("the calls of the plan (item 1)", () => {
  it("reads the list and one version through the proxy", async () => {
    expect(await withFetch(LIST, () => api.listMappingPlans())).toEqual([
      ["/api/crm/mapping/plans", undefined, undefined],
    ]);
    expect(await withFetch(DRAFT, () => api.getMappingPlan(3))).toEqual([
      ["/api/crm/mapping/plans/3", undefined, undefined],
    ]);
  });

  it("posts a draft, an approve and a restore", async () => {
    const changes: PlanChange[] = [
      { op: "put", entity: "lead", field_api_name: "Sector", target_column: "industry" },
    ];
    expect(await withFetch(DRAFT, () => api.draftMappingPlan(changes))).toEqual([
      ["/api/crm/mapping/plans", "POST", JSON.stringify({ changes })],
    ]);
    expect(await withFetch(APPROVED, () => api.approveMappingPlan(3))).toEqual([
      ["/api/crm/mapping/plans/3/approve", "POST", undefined],
    ]);
    expect(await withFetch(APPROVED, () => api.restoreMappingPlan(1))).toEqual([
      ["/api/crm/mapping/plans/1/restore", "POST", undefined],
    ]);
  });

  it("keeps the code of a 409, so the card can act on it", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = (async () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: { code: "plan_moved", message: "The plan changed." } }),
    })) as unknown as typeof globalThis.fetch;
    try {
      await expect(api.approveMappingPlan(3)).rejects.toMatchObject({ status: 409, code: "plan_moved" });
    } finally {
      globalThis.fetch = original;
    }
  });
});

// ── Item 2: the map in use, who approved it, and when ────────────────────────

describe("the map in use (item 2)", () => {
  it("names who approved the map in use and when", () => {
    const html = drawPanel(view());
    expect(html).toContain(PLAN_TITLE);
    expect(html).toContain("raj@acme.test");
    expect(html).toContain(dateTime(V2.approved_at));
    expect(approvedLine(V2)).toBe(`In use since ${dateTime(V2.approved_at)}. Approved by raj@acme.test.`);
  });

  it("names the sync when the sync approved it", () => {
    expect(approvedLine({ ...V2, approved_by: "system" })).toContain("the sync, after a change in Zoho");
  });

  it("draws from the list, which holds no rules", () => {
    // The list type has `rule_count` and no `rules` (CRM-Q18-1 fix round).
    expect(LIST.versions.every((v) => !("rules" in v))).toBe(true);
    expect(drawPanel(view({ approved: null, draft: null }))).toContain("raj@acme.test");
  });
});

// ── Item 3: a change makes a draft, and changes no live rule ─────────────────

describe("a change on the card makes a draft (item 3)", () => {
  it("posts a draft with the changes of the open draft and the new one, and nothing else", async () => {
    const { calls, log } = fakeCalls();
    const change: PlanChange = { op: "put", entity: "contact", field_api_name: "Fax", target_column: "lost_note" };
    expect(await addToDraft(calls, APPROVED, DRAFT, change)).toBe("drafted");
    const posted = JSON.parse(log[0].slice("draft ".length)) as PlanChange[];
    expect(posted).toEqual([
      { op: "put", entity: "lead", field_api_name: "Sector", target_column: "lost_note" },
      { op: "remove", entity: "contact", field_api_name: "Desk_Phone" },
      { op: "put", entity: "deal", field_api_name: "Region", target_column: "next_step" },
      change,
    ]);
    // No approve, no restore: a draft changes no live rule.
    expect(log).toEqual([log[0], "reread"]);
  });

  it("replaces the change of the same field, and sends nothing for the column in use", async () => {
    const back: PlanChange = { op: "put", entity: "lead", field_api_name: "Sector", target_column: "industry" };
    expect(nextDraftChanges(APPROVED, DRAFT, back).map((c) => c.field_api_name)).toEqual([
      "Desk_Phone",
      "Region",
    ]);
    const { calls, log } = fakeCalls();
    expect(await addToDraft(calls, APPROVED, null, back)).toBe("unchanged");
    expect(log).toEqual([]);
  });

  it("does not map by hand through the live rule route any more", () => {
    const card = read("./FieldMapCard.tsx");
    expect(card).not.toContain("createFieldRule");
    expect(card).toMatch(/addToDraft\(PLAN_CALLS/);
  });

  it("marks the draft as not applied, and shows its changes against the map in use", () => {
    const html = drawPanel(view());
    // The visible mark inside the draft, not only the region's name. The
    // history marks the same version too, so read the draft part alone.
    const start = html.indexOf(`aria-label="${DRAFT_LABEL}"`);
    const part = html.slice(start, html.indexOf(">History<"));
    expect(start).toBeGreaterThan(-1);
    expect(part).toContain(`>${DRAFT_LABEL}</span>`);
    expect(html).toContain("Nothing changes until you approve");
    for (const row of planDiff(APPROVED, DRAFT)) expect(html).toContain(diffLine(row));
    expect(diffLine(planDiff(APPROVED, DRAFT)[0])).toBe("Change: Leads · Sector → Lost note, not Industry");
    expect(planDiff(APPROVED, DRAFT).map((r) => r.kind)).toEqual(["change", "remove", "add"]);
  });

  it("shows no draft when no draft is open", () => {
    const html = drawPanel(view({ list: { approved: 2, versions: [V2, V1] }, draft: null }));
    expect(html).not.toContain(DRAFT_LABEL);
    expect(html).not.toContain(APPROVE_DRAFT_LABEL);
  });
});

// ── Item 4: one button approves, and the two 409s ───────────────────────────

describe("approve the draft (item 4)", () => {
  it("approves the open draft with one press", () => {
    const pressed: number[] = [];
    const tree = PlanPanel({ view: view(), handlers: { ...noHandlers, onApprove: (v) => pressed.push(v) } });
    buttonOf(tree, APPROVE_DRAFT_LABEL)?.props.onClick?.();
    expect(pressed).toEqual([3]);
  });

  it("says that the map moved, reads the plan again, and drops the old draft", async () => {
    const { calls, log } = fakeCalls({ approve: [conflict("plan_moved")] });
    const outcome = await approveDraft(calls, 3, noWait);
    expect(outcome).toEqual({ kind: "moved", message: PLAN_MOVED });
    expect(log).toEqual(["approve 3", "reread"]);
    // The new read: the sync approved version 4 on top of version 2.
    const after: PlanList = {
      approved: 4,
      versions: [summary({ version: 4, approved_by: "system", based_on: 2 }), D3, { ...V2, state: "superseded" }, V1],
    };
    expect(openDraft(LIST)?.version).toBe(3);
    expect(openDraft(after)).toBeNull();
    const html = drawPanel(view({ list: after, notice: outcome.kind === "moved" ? outcome.message : null }));
    expect(html).not.toContain(APPROVE_DRAFT_LABEL);
    expect(html).toContain('role="alert"');
    expect(html).toContain("The field map changed after your draft");
  });

  it("tries once more after a plan_busy", async () => {
    const { calls, log } = fakeCalls({ approve: [conflict("plan_busy"), APPROVED] });
    const outcome = await approveDraft(calls, 3, noWait);
    expect(outcome.kind).toBe("done");
    expect(log).toEqual(["approve 3", "approve 3", "reread"]);
  });

  it("asks the admin to try again after a second plan_busy", async () => {
    const { calls, log } = fakeCalls({ approve: [conflict("plan_busy"), conflict("plan_busy")] });
    expect(await approveDraft(calls, 3, noWait)).toEqual({ kind: "busy", message: PLAN_BUSY });
    expect(log).toEqual(["approve 3", "approve 3", "reread"]);
    expect(PLAN_BUSY).toContain("Try again");
  });

  it("waits before the second try", async () => {
    const waited: number[] = [];
    const { calls } = fakeCalls({ approve: [conflict("plan_busy"), APPROVED] });
    await runPlanWrite(() => calls.approve(3), { wait: async (ms) => void waited.push(ms) });
    expect(waited).toEqual([1000]);
  });

  it("keeps the sentence of any other refusal, with no second try", async () => {
    const { calls, log } = fakeCalls({ approve: [new CrmError("Pick another column.", 422, "rule_illegal")] });
    expect(await approveDraft(calls, 3, noWait)).toEqual({ kind: "failed", message: "Pick another column." });
    expect(log).toEqual(["approve 3", "reread"]);
  });
});

// ── Item 5: "Go back" asks first, and names the date ─────────────────────────

describe("go back (item 5)", () => {
  it("picks the map that was in use last", () => {
    expect(previousVersion(LIST)?.version).toBe(1);
    const older = summary({ version: 5, state: "superseded", approved_at: "2026-10-01T00:00:00Z" });
    expect(previousVersion({ ...LIST, versions: [...LIST.versions, older] })?.version).toBe(1);
    expect(previousVersion({ approved: 1, versions: [summary({})] })).toBeNull();
  });

  it("only opens the question when pressed", () => {
    const seen: string[] = [];
    const tree = PlanPanel({
      view: view(),
      handlers: {
        onApprove: () => seen.push("approve"),
        onGoBack: () => seen.push("ask"),
        onOpen: () => seen.push("open"),
      },
    });
    buttonOf(tree, GO_BACK_LABEL)?.props.onClick?.();
    expect(seen).toEqual(["ask"]);
    // The card wires the press to the question, and the question to the restore.
    const card = read("./FieldMapCard.tsx");
    expect(card).toContain("onGoBack: () => setConfirming(true)");
    expect(card).toMatch(/<ConfirmDialog[\s\S]*body=\{goBackBody\(previous\)\}[\s\S]*onConfirm=\{\(\) => void restore\(\)\}/);
  });

  it("names the date of the earlier map in the question", () => {
    expect(goBackBody(V1)).toContain(dateTime(V1.approved_at));
    expect(goBackBody(V1)).toContain("1 rule");
  });

  it("restores the earlier map, then reads the plan again", async () => {
    const { calls, log } = fakeCalls({ restore: [EARLIER] });
    expect((await goBack(calls, V1, noWait)).kind).toBe("done");
    expect(log).toEqual(["restore 1", "reread"]);
  });

  it("draws no Go back with no earlier map", () => {
    const html = drawPanel(view({ list: { approved: 1, versions: [summary({})] }, draft: null }));
    expect(html).not.toContain(GO_BACK_LABEL);
  });
});

// ── Item 6: the history ──────────────────────────────────────────────────────

describe("the history (item 6)", () => {
  it("lists each version with its rule count, its state and its time", () => {
    const html = drawPanel(view());
    expect(html).toContain("History");
    for (const v of LIST.versions) {
      expect(html).toContain(stateLabel(v, D3));
      expect(html).toContain(historyLine(v));
    }
    expect(html).toContain("2 rules");
    expect(html).toContain("1 rule");
    expect(stateLabel(V2, D3)).toBe("In use");
    expect(stateLabel(V1, D3)).toBe("Earlier");
    expect(stateLabel(D3, D3)).toBe(DRAFT_LABEL);
    expect(stateLabel(D3, null)).toBe("Draft, not used");
  });

  it("opens the rules of one version, read only", () => {
    const opened: Array<number | null> = [];
    const tree = PlanPanel({ view: view(), handlers: { ...noHandlers, onOpen: (v) => opened.push(v) } });
    const show = elements(tree).filter(
      (el) => el.type === Button && (el.props as { children?: ReactNode }).children === "Show rules",
    );
    expect(show).toHaveLength(3);
    (show[2].props as { onClick: () => void }).onClick();
    expect(opened).toEqual([1]);

    const html = drawPanel(view({ opened: 1, openedPlan: EARLIER }));
    expect(html).toContain("Hide rules");
    expect(html).toContain("Leads · Sector → Industry");
    // The rules have no control: the list is read only.
    const rules = html.slice(html.indexOf("Leads · Sector → Industry") - 200);
    const list = rules.slice(rules.lastIndexOf("<ul", 200), rules.indexOf("</ul>") + 5);
    expect(list).not.toContain("<button");
  });

  it("reads one version through GET /crm/mapping/plans/{version}", () => {
    expect(read("./FieldMapCard.tsx")).toMatch(/usePlan\(opened\)/);
    expect(read("./FieldMapCard.tsx")).toMatch(/api\.getMappingPlan\(version\)/);
  });
});

// ── Item 7: the approve and the reject of one rule stay ──────────────────────

describe("one rule (item 7)", () => {
  it("keeps the per-rule approve and reject, and reads the history again after each", () => {
    const card = read("./FieldMapCard.tsx");
    expect(card).toContain("api.decideFieldRule(rule.id, status)");
    // `run` drops each key of the card, and the plan keys start with it.
    expect(card).toContain("invalidate(KEY_PREFIX)");
    expect(card).toContain("const PLANS_KEY = `${KEY_PREFIX}plans`");
    expect(card).toContain("`${KEY_PREFIX}plan:${version}`");
  });
});

// ── Item 8: no engine word, and no card for a member or a Native CRM ─────────

const ENGINE_WORDS = /snapshot|superseded|\bv\d+\b|version\s*\d|plan_moved|plan_busy|based_on|rule_count/i;

describe("the words of the card (item 8)", () => {
  const states: Array<[string, PlanView]> = [
    ["a draft and the history", view()],
    ["one version open", view({ opened: 1, openedPlan: EARLIER })],
    ["the draft open", view({ opened: 3, openedPlan: DRAFT })],
    ["after a moved draft", view({ notice: PLAN_MOVED })],
    ["after a busy approve", view({ notice: PLAN_BUSY, busy: null })],
    ["while it approves", view({ busy: "approve" })],
    ["no draft", view({ list: { approved: 2, versions: [V2, V1] }, draft: null })],
    ["loading", view({ list: null })],
    ["failed", view({ list: null, failed: true })],
    ["approved by the sync", view({ list: { approved: 2, versions: [{ ...V2, approved_by: "system" }, V1] } })],
  ];

  it.each(states)("shows no engine word: %s", (_name, v) => {
    const html = drawCard(v);
    // Read the text and the attributes, not the class names.
    const text = html.replace(/class="[^"]*"/g, "");
    expect(text).not.toMatch(ENGINE_WORDS);
  });

  it("asks the Go back question with no engine word", () => {
    expect(goBackBody(V1)).not.toMatch(ENGINE_WORDS);
    expect(goBackBody(summary({ version: 12, state: "superseded", rule_count: 4 }))).not.toMatch(ENGINE_WORDS);
  });

  it("would catch a version number", () => {
    expect("Approved v1 by ana").toMatch(ENGINE_WORDS);
    expect("Version 3").toMatch(ENGINE_WORDS);
    expect("Superseded").toMatch(ENGINE_WORDS);
  });
});

describe("who sees the card (item 8)", () => {
  const page = (over: Record<string, unknown>) =>
    renderToStaticMarkup(
      createElement(PipelineSettings, {
        section: "field-mapping",
        dealStatuses: [],
        leadStatuses: [],
        lostReasons: [],
        saving: false,
        onSaveStatus: async () => true,
        onRemoveStatus: async () => true,
        onReorderStatuses: async () => undefined,
        onSaveLostReason: async () => true,
        onRemoveLostReason: async () => true,
        onReorderLostReasons: async () => undefined,
        ...over,
      }),
    );

  it("draws the plan for an admin in Mirror mode", () => {
    expect(page({ readOnly: true, showAdminCards: true })).toContain(`aria-label="${PLAN_TITLE}"`);
  });

  it("draws no card for a member who is not an admin", () => {
    const html = page({ readOnly: true, showAdminCards: false });
    expect(html).not.toContain(PLAN_TITLE);
    expect(html).not.toContain("Field mapping");
  });

  it("draws no plan in a Native CRM, even for a studio admin", () => {
    for (const studioAdmin of [false, true]) {
      const html = page({ readOnly: false, showAdminCards: true, studioAdmin });
      expect(html).not.toContain(PLAN_TITLE);
      expect(html).not.toContain(DRAFT_LABEL);
      expect(html).not.toContain("History");
    }
  });
});
