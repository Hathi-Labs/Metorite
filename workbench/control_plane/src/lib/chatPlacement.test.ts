/**
 * The placement rule of a turn (spec `projects_ai_chat.md` §24, owner
 * 2026-10-08): where a read, a write, an ask and an answer card draw.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - a tool a card file draws is dropped from `PLACEMENT` -> "every tool a
 *   card file draws is classified";
 * - a `skill-projects` export is dropped from `PLACEMENT` -> "every
 *   skill-projects tool is classified" (the Python twin also checks classes);
 * - `ProjectToolCards` draws a read in the flow again -> "a read draws in
 *   its step, and never as a card after the answer";
 * - the trail drops `evidenceFor` -> "the owner's turn: the four reads sit in
 *   the steps";
 * - a write moves into the trail -> "a write draws in the flow";
 * - `genUiPlacement` reads a picker as an answer -> "a picker, a form and a
 *   button are asks".
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { CRM_CARD_TOOLS } from "@/components/crm/CrmEvidence";
import EmailToolCards, { EMAIL_CARD_TOOLS, emailEvidence } from "@/components/email/EmailToolCards";
import MessageBubble from "@/components/MessageBubble";
import type { ToolEvent } from "@/components/MarkdownMessage";
import ProjectToolCards, { PROJECT_CARD_TOOLS, projectEvidence } from "@/components/projects/ProjectToolCards";
import TaskToolCards, { TASK_CARD_TOOLS, taskEvidence } from "@/components/tasks/TaskToolCards";
import { ToolStepRow } from "@/components/ThinkingContainer";
import {
  PLACEMENT,
  genUiPlacement,
  isEvidenceTool,
  placementOf,
} from "@/lib/chatPlacement";
import { OWNER_PICKER, OWNER_READS, ownerTurn } from "@/lib/ownerTurn.fixture";

const ROOT = fileURLToPath(new URL("../../../..", import.meta.url));

const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

function ev(name: string, result: string, status: ToolEvent["status"] = "done"): ToolEvent {
  return { id: `e-${name}`, name, args: {}, result, status, startedAt: 1, endedAt: 2 };
}

describe("one map classifies every tool a card file draws", () => {
  it("every tool a card file draws is classified", () => {
    const unnamed = [...PROJECT_CARD_TOOLS, ...TASK_CARD_TOOLS, ...EMAIL_CARD_TOOLS, ...CRM_CARD_TOOLS]
      .filter((n) => placementOf(n) === undefined);
    expect(unnamed).toEqual([]);
  });

  it("every skill-projects tool is classified", () => {
    // `__all__` is the tool surface (`skill_projects/__init__.py`). The
    // Python twin, `test_chat_placement_classes.py`, also checks each class.
    const init = readFileSync(join(ROOT, "apps/skills/skill-projects/skill_projects/__init__.py"), "utf8");
    const names = new Set<string>();
    for (const block of init.matchAll(/from skill_projects\.\w+ import \(([^)]*)\)/g)) {
      for (const n of block[1].split(",")) if (n.trim()) names.add(n.trim());
    }
    for (const one of init.matchAll(/from skill_projects\.\w+ import (\w+(?:, \w+)*)\n/g)) {
      for (const n of one[1].split(",")) if (n.trim()) names.add(n.trim());
    }
    expect(names.size).toBeGreaterThan(80);
    expect([...names].filter((n) => placementOf(n) === undefined)).toEqual([]);
  });

  it("names each tool once, in one of four kinds", () => {
    expect(new Set(Object.values(PLACEMENT))).toEqual(new Set(["ask", "evidence", "write", "answer"]));
    expect(placementOf("skill_projects.list_tasks")).toBe("evidence");
    expect(placementOf("projects__create_task")).toBe("write");
    expect(placementOf("no_such_tool")).toBeUndefined();
  });

  it("no card file guesses a placement from a name", () => {
    // The map is the one place. A regex on a tool name in a card file is the
    // defect this module replaced.
    const files = [
      "components/projects/ProjectToolCards.tsx",
      "components/tasks/TaskToolCards.tsx",
      "components/email/EmailToolCards.tsx",
      "components/crm/CrmEvidence.tsx",
      "components/ThinkingContainer.tsx",
    ];
    for (const f of files) {
      const src = readFileSync(join(ROOT, "workbench/control_plane/src", f), "utf8");
      expect(src, f).not.toMatch(/\/\^?\(?(?:list|find|get|read)[_|]/);
    }
  });
});

describe("a read draws in its step, and never as a card after the answer", () => {
  const list = ev("list_tasks", `Tasks (1 total):\n- #7 «Fix the extruder» · unassigned\n  full_id: ${ID}`);
  const created = ev("create_task", `Created #9 «Call» in «Ops».\n  full_id: ${ID}`);

  it("a Projects read has a step receipt and no flow card", () => {
    expect(renderToStaticMarkup(createElement(ProjectToolCards, { toolEvents: [list] }))).toBe("");
    const html = renderToStaticMarkup(createElement("div", null, projectEvidence(list)));
    expect(html).toContain("Fix the extruder");
  });

  it("a write draws in the flow, and has no step receipt", () => {
    const html = renderToStaticMarkup(createElement(ProjectToolCards, { toolEvents: [created] }));
    expect(html).toContain("Task created");
    expect(projectEvidence(created)).toBeNull();
  });

  it("a Tasks read and an email read draw in the step too", () => {
    const mine = ev("my_tasks_list", `[NEXT·me] "Ship it" · due today\n  full_id: ${ID}`);
    expect(renderToStaticMarkup(createElement(TaskToolCards, { toolEvents: [mine] }))).toBe("");
    expect(taskEvidence(mine)).not.toBeNull();
    const labels = ev("list_labels", "Labels (1):\n- Finance");
    expect(renderToStaticMarkup(createElement(EmailToolCards, { toolEvents: [labels] }))).toBe("");
    expect(emailEvidence(labels)).not.toBeNull();
  });

  it("a step with a receipt opens to it, in place of the raw output", () => {
    const html = renderToStaticMarkup(
      createElement(ToolStepRow, { event: list, open: true, evidence: projectEvidence(list) }),
    );
    expect(html).toContain("data-step-evidence");
    expect(html).toContain("Fix the extruder");
    expect(html).not.toContain("full_id");
    expect(html).not.toContain("data-step-output");
  });

  it("a closed step with a receipt shows that it opens", () => {
    const html = renderToStaticMarkup(
      createElement(ToolStepRow, { event: list, open: false, evidence: projectEvidence(list) }),
    );
    expect(html).toContain("data-step-has-evidence");
    expect(html).not.toContain("Fix the extruder");
  });
});

describe("the owner's turn (2026-10-08)", () => {
  const [, turn] = ownerTurn({ live: true });
  const html = renderToStaticMarkup(createElement(MessageBubble, { message: turn, sessionId: "s1" }));

  it("the four reads sit in the steps, as four steps that open", () => {
    expect(html.match(/data-step-has-evidence/g)?.length).toBe(OWNER_READS.length);
    for (const e of OWNER_READS) expect(isEvidenceTool(e.name)).toBe(true);
  });

  it("no read draws as a card after the answer", () => {
    // The receipts the owner saw: "Projects", "Vocabulary", "Tasks (10)" and
    // "Task dataset". Each was a card with a title in the flow.
    for (const title of ["Tasks (10)", "Task dataset", ">Vocabulary<", ">Projects<"]) {
      expect(html).not.toContain(title);
    }
    expect(html).not.toContain(" | status_category | ");
  });

  it("the picker is the last element of the turn", () => {
    const pick = html.indexOf('data-chat-ask="genui:req-tags-1"');
    expect(pick).toBeGreaterThan(html.indexOf('data-trail=""'));
    expect(html.slice(pick)).not.toContain("data-step-");
  });
});

describe("a picker, a form and a button are asks", () => {
  it("reads the spec, never the tool name", () => {
    expect(genUiPlacement(OWNER_PICKER)).toBe("ask");
    expect(genUiPlacement({ type: "template", props: { name: "formCard", data: {} } })).toBe("ask");
    expect(genUiPlacement({ type: "card", children: [{ type: "button", props: { label: "Go", action: "go" } }] })).toBe("ask");
    expect(genUiPlacement({ hitl: true, request_id: "r", type: "text", props: { text: "x" } })).toBe("ask");
    expect(genUiPlacement({ type: "template", props: { name: "statDashboard", data: {} } })).toBe("answer");
    expect(genUiPlacement({ type: "template", props: { name: "dataGrid", data: {} } })).toBe("answer");
    expect(genUiPlacement(null)).toBe("answer");
  });
});
