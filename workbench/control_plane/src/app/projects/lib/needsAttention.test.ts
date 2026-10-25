// WS-55 SW-11a — Needs attention (`external_agent_access.md` §12.7).
//
// The runner has no DOM. The rules are called directly, and the list is
// rendered to a string. The server decides every row, every count and
// `can_act`, so these tests hold the words, the order, the acts each row
// offers and the wiring of the page. The R8 half is
// `tests/unit/test_needs_attention_r8.py`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { NeedsAttentionList } from "../components/NeedsAttentionList";
import {
  ACTION_LABEL,
  type AttentionPage,
  type AttentionRow,
  CANNOT_ACT,
  EMPTY_COPY,
  attentionGroups,
  attentionPage,
  badgeText,
  cappedLine,
  closeRunCopy,
  ownerText,
  resumeBody,
  rowActions,
  rowDetail,
  rowKey,
  runActBlocked,
} from "./needsAttention";

const TASK = "7f1d0b8a-1c2e-4a4b-9d3e-2f6a1b0c9d8e";
const RUN = "0a3c5e7f-9b1d-4f2a-8c6e-4d2b0a8f6c4e";
const BLOCKER = "5e4d3c2b-1a09-4f8e-b7d6-c5b4a3928170";

function row(over: Partial<AttentionRow>): AttentionRow {
  return {
    kind: "stalled",
    task_id: TASK,
    task_number: 12,
    title: "Fix the extruder",
    project_id: "p1",
    project_name: "Ops",
    parent_task_id: null,
    category: "todo",
    archived: false,
    owner: "alice@x.io",
    owner_name: "Alice Archer",
    since: "2026-10-25T08:00:00+00:00",
    age_hours: 3.4,
    run_id: RUN,
    run_status: "in_progress",
    client_via: "mcp:claude.ai:claude-code",
    client_name: "Claude Code",
    reason: "agent_quiet",
    blocker_id: null,
    blocker_number: null,
    blocker_title: null,
    action: "resume",
    actions: ["resume", "close_run"],
    can_act: true,
    yours: false,
    ...over,
  };
}

const stalled = row({});
const dangling = row({
  kind: "dangling",
  task_number: 13,
  reason: "archived",
  run_status: "awaiting",
  action: "close_run",
  actions: ["close_run"],
  age_hours: 50,
});
const blocked = row({
  kind: "blocked",
  task_number: 14,
  title: "Ship the widget",
  run_id: null,
  run_status: null,
  client_via: null,
  client_name: null,
  reason: null,
  blocker_id: BLOCKER,
  blocker_number: 9,
  blocker_title: "Design the bracket",
  action: "open_blocker",
  actions: ["open_blocker"],
  owner: null,
  owner_name: null,
  age_hours: 0.5,
});

function page(rows: AttentionRow[], over: Partial<AttentionPage> = {}): AttentionPage {
  const counts = { stalled: 0, dangling: 0, blocked: 0 };
  for (const r of rows) counts[r.kind] += 1;
  return {
    rows,
    counts,
    total: rows.length,
    listed: rows.length,
    offset: 0,
    limit: 200,
    capped: false,
    ...over,
  };
}

describe("the header badge", () => {
  it("shows the count only while something waits", () => {
    expect(badgeText(0)).toBeNull();
    expect(badgeText(null)).toBeNull();
    expect(badgeText(undefined)).toBeNull();
    expect(badgeText(Number.NaN)).toBeNull();
    expect(badgeText(1)).toBe("1 needs attention");
    expect(badgeText(3)).toBe("3 need attention");
  });
});

describe("the page shape", () => {
  it("reads the route's answer, and drops a row it cannot read", () => {
    const got = attentionPage({
      rows: [stalled, { kind: "other", task_id: "x" }, { kind: "blocked" }],
      counts: { stalled: 1 },
      total: 1,
    });
    expect(got.rows).toEqual([stalled]);
    expect(got.counts).toEqual({ stalled: 1, dangling: 0, blocked: 0 });
    expect(got.total).toBe(1);
    expect(got.capped).toBe(false);
    expect(attentionPage(null).rows).toEqual([]);
  });

  it("groups the rows by kind, in the server's order, and skips an empty kind", () => {
    const groups = attentionGroups(page([blocked, stalled]));
    expect(groups.map((g) => g.kind)).toEqual(["stalled", "blocked"]);
    expect(groups[0].heading).toBe("Stalled agent runs");
    expect(groups[1].rows).toEqual([blocked]);
  });

  it("says when a kind holds more than the list shows", () => {
    expect(cappedLine(page([stalled]))).toBeNull();
    expect(cappedLine(page([stalled], { total: 250, listed: 200, capped: true }))).toBe(
      "Showing 1 of 250. The oldest come first.",
    );
  });

  it("keys a row by its run or its link, so two rows of one task differ", () => {
    expect(rowKey(stalled)).not.toBe(rowKey(blocked));
    expect(rowKey(blocked)).toContain(BLOCKER);
  });
});

describe("the words of a row", () => {
  it("says why each kind is here, and for how long", () => {
    expect(rowDetail(stalled)).toBe("Claude Code sent nothing for 3 hours.");
    expect(rowDetail(row({ reason: "answer_unread" }))).toBe(
      "Claude Code did not read the answer for 3 hours.",
    );
    expect(rowDetail(dangling)).toBe(
      "The task is archived. The run of Claude Code is still open, quiet for 2 days.",
    );
    expect(rowDetail(row({ kind: "dangling", reason: "cancelled" }))).toContain(
      "The task is cancelled.",
    );
    expect(rowDetail(blocked)).toBe(
      "Blocked by #9 Design the bracket for under an hour. It is in the backlog, and nobody holds it.",
    );
  });

  it("names the owner, the caller as You, and no owner", () => {
    expect(ownerText(stalled)).toBe("Alice Archer");
    expect(ownerText(row({ yours: true }))).toBe("You");
    expect(ownerText(blocked)).toBe("No owner");
    const labels = new Map([["erin@x.io", "Erin Evans"]]);
    expect(ownerText(row({ owner: "erin@x.io", owner_name: "erin@x.io" }), labels)).toBe(
      "Erin Evans",
    );
  });
});

describe("the acts of a row", () => {
  it("offers Resume first and Close the run second on a stalled row", () => {
    expect(rowActions(stalled)).toEqual(["resume", "close_run"]);
    expect(rowActions(dangling)).toEqual(["close_run"]);
    expect(rowActions(blocked)).toEqual(["open_blocker"]);
  });

  it("offers no run act that the server refuses, and says who can act", () => {
    const refused = row({ can_act: false });
    expect(rowActions(refused)).toEqual([]);
    expect(runActBlocked(refused)).toBe(true);
    // Opening a blocker is a read, so it never needs the right.
    expect(rowActions(row({ ...blocked, can_act: false }))).toEqual(["open_blocker"]);
    expect(runActBlocked(blocked)).toBe(false);
  });

  it("asks before it closes a run, and says that the task stays", () => {
    const copy = closeRunCopy(dangling);
    expect(copy.title).toBe("Close this agent run?");
    expect(copy.body).toContain("Claude Code");
    expect(copy.body).toContain("The task keeps its status and its assignees.");
    expect(copy.confirm).toBe(ACTION_LABEL.close_run);
  });

  it("sends the key it is given, and a fresh one when it is given none", () => {
    expect(resumeBody("k1")).toEqual({ idempotency_key: "k1" });
    const a = resumeBody().idempotency_key;
    const b = resumeBody().idempotency_key;
    expect(a).not.toBe(b);
  });
});

describe("the list", () => {
  const noop = () => {};
  const html = (p: AttentionPage | null, error: string | null = null) =>
    renderToStaticMarkup(
      createElement(NeedsAttentionList, {
        page: p,
        error,
        onOpenTask: noop,
        onBack: noop,
        onChanged: noop,
      }),
    );

  it("draws each kind as a section, each row as a task link with its acts", () => {
    const out = html(page([stalled, dangling, blocked]));
    expect(out).toContain("Stalled agent runs");
    expect(out).toContain("Agent runs on closed tasks");
    expect(out).toContain("Blocked by parked work");
    expect(out.indexOf("Stalled agent runs")).toBeLessThan(out.indexOf("Blocked by parked work"));
    expect(out).toContain(`href="/projects?task=${TASK}"`);
    expect(out).toContain(`href="/projects?task=${BLOCKER}"`);
    expect(out).toContain(">Resume<");
    expect(out).toContain(">Close the run<");
    expect(out).toContain("Open the blocker");
    expect(out).toContain("Back to the board");
    expect(out).toContain("Owner: Alice Archer · Ops");
  });

  it("draws no button for a run act the caller may not take", () => {
    const out = html(page([row({ can_act: false })]));
    expect(out).not.toContain(">Resume<");
    expect(out).not.toContain(">Close the run<");
    // The markup escapes the apostrophe, so the check reads the words after it.
    expect(out).toContain(CANNOT_ACT.split("member, ")[1]);
  });

  it("says what is empty, and loads with a skeleton", () => {
    expect(html(page([]))).toContain(EMPTY_COPY.message);
    expect(html(null)).toContain("Loading the list");
    expect(html(null, "Could not read the list.")).toContain("Could not read the list.");
  });
});

describe("the page wires the lens", () => {
  const src = readFileSync(join(__dirname, "../page.tsx"), "utf8");

  it("reads the list for the project on screen, and again after an act", () => {
    expect(src).toContain("attentionApi");
    expect(src).toMatch(/\.list\(attentionProject\)/);
    expect(src).toMatch(/onChanged=\{\(\) => setAttentionVersion\(/);
  });

  it("opens the lens from the badge and the toggle, and a view pick closes it", () => {
    expect(src).toContain("data-attention-badge");
    expect(src).toContain("data-attention-toggle");
    expect(src).toMatch(/onPick=\{\(next\) => \{\s*setAttentionFor\(null\);/);
  });

  it("keeps the lens off the view modes", () => {
    const commands = readFileSync(join(__dirname, "commands.ts"), "utf8");
    expect(commands).not.toContain("attention");
  });
});
