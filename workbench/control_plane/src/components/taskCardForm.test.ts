// The edit form on the card of a new task (`projects_ai_chat.md` §32, EAC-2, task 3469).
//
// The rules of the form are pure and live in `lib/taskEditCard.ts` (`taskEditCard.test.ts`). This file holds
// what the card draws and how the answer is wired. The repo's pattern: server-render the card, and read the
// source for a wire that a render cannot show. `e2e/task-edit-card.spec.ts` runs the whole path in a browser.
//
// R7 fences named here. Each test names the mutation that turns it red.
//
//   * `flag-off-card-unchanged`: a card with no `edit` (the flag is off, or the shape is unknown) draws byte for
//     byte as a card of before, and an `edit` with no answer path draws no form. Mutation: draw the form
//     whenever `edit` is set → red.
//   * `form-draws-the-two-fields`: the form shows the due date as a date field and each person as a chip with
//     a way to take it off, and the card hides the two rows that the form replaces. Mutation: keep the
//     `due_at` row → red.
//   * `approve-is-plain-or-edit`: the Approve of a task card sends `taskApproval(original, edits)`, and Reject and
//     the steer path are those of before. Mutation: send the edit answer when nothing changed → red.
//   * `kept-edits-by-kind`: the queue keeps the edits of a mail card and of a task card in one map, and each
//     reader takes its own kind through a guard. Mutation: cast instead of a guard → red.
//   * `shared-parts-only`: the form builds no control and no palette. Mutation: add a raw `<input` → red.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ConfirmationCard, { CONFIRM_HINT } from "@/components/ConfirmationCard";
import {
  NO_EDITS,
  asNewMailEdits,
  asTaskEdits,
  cardFromEvent,
  closedEdits,
  formEditsReducer,
} from "@/lib/confirmationQueue";
import { isNewMailEdits } from "@/lib/replyCard";
import { TASK_EDITED_HINT, taskEditFromEvent, type TaskCardEdits } from "@/lib/taskEditCard";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(`../${rel}`, import.meta.url)), "utf8").replace(/\r\n/g, "\n");

const VALUES: TaskCardEdits = {
  due: "2026-12-01", start: "", status: "Doing", important: null, leveraged: false,
  description: "Ship the pack.", assignees: ["ann@acme.test", "bob@acme.test"],
};
const PID = "11111111-2222-4333-8444-555555555555";
const EDIT = { kind: "create_task", project_id: PID, values: VALUES };
const CONTEXT = [
  "Check each field, then approve.",
  "project: «Launch»",
  "name: «Ship the pack»",
  "due_at: 2026-12-01",
  "status: Doing",
  "assignees: ann@acme.test, bob@acme.test",
  "not in the directory: bob@acme.test",
].join("\n");
const noop = () => {};
const card = (extra: Record<string, unknown> = {}) =>
  renderToStaticMarkup(createElement(ConfirmationCard, {
    title: "Create this task?", detail: "«Ship the pack» · in Launch", context: CONTEXT, fenced: true,
    onApprove: noop, onReject: noop, ...extra,
  }));
const form = taskEditFromEvent(EDIT)!;

describe("the card with a form (EAC-2)", () => {
  it("flag-off-card-unchanged: no `edit` is the card of before, and an `edit` with no answer path draws no form", () => {
    const before = card();
    expect(before).not.toContain("data-task-edit");
    expect(before).toContain("due_at");
    expect(before).toContain(CONFIRM_HINT);
    expect(card({ edit: form })).toBe(before);
    expect(card({ edit: undefined, onEditAnswer: noop })).toBe(before);
  });

  it("flag-off-card-unchanged: an event with no readable `edit` gives a card with no `edit` key", () => {
    const base = { request_id: "r".repeat(32), title: "Create this task?", context: CONTEXT };
    const plain = cardFromEvent(base)!;
    expect("edit" in plain).toBe(false);
    for (const bad of [null, "x", { kind: "create_task" }, { ...EDIT, kind: "update_task" }, { ...EDIT, values: { ...VALUES, due: "soon" } }]) {
      expect("edit" in cardFromEvent({ ...base, edit: bad })!, JSON.stringify(bad)).toBe(false);
    }
    const withForm = cardFromEvent({ ...base, edit: EDIT })!;
    expect(withForm.edit?.values).toEqual(VALUES);
    expect(withForm.edit?.projectId).toBe(PID);
  });

  it("form-draws-the-two-fields: a date field, a chip for each person, and no stale row", () => {
    const html = card({ edit: form, onEditAnswer: noop });
    expect(html).toContain("data-task-edit-card");
    expect(html).toContain('data-task-edit-state="original"');
    expect(html).toMatch(/<input[^>]*type="date"[^>]*value="2026-12-01"/);
    for (const who of VALUES.assignees) expect(html).toContain(`aria-label="Unassign ${who}"`);
    // The two rows that the form replaces are gone, and the others stay as the card showed them.
    expect(html).not.toContain('data-card-field="due_at"');
    expect(html).not.toContain('data-card-field="assignees"');
    expect(html).not.toContain('data-card-field="not in the directory"');
    expect(html).toContain('data-card-field="status"');
    expect(html).toContain('data-card-field="project"');
    // Nothing changed: the hint of before, and an Approve that is on.
    expect(html).toContain(CONFIRM_HINT);
    expect(html).not.toContain("data-task-edit-edited");
    const approve = html.split("<").find((t) => t.includes("data-confirmation-approve")) ?? "";
    expect(approve).not.toContain(' disabled=""');
  });

  it("form-draws-the-two-fields: kept edits start the form, with the edited state, the summary and the new hint", () => {
    const kept = { ...VALUES, due: "2027-02-03", assignees: ["bob@acme.test"] };
    const html = card({ edit: form, onEditAnswer: noop, savedTaskEdits: kept });
    expect(html).toContain('data-task-edit-state="edited"');
    expect(html).toMatch(/<input[^>]*type="date"[^>]*value="2027-02-03"/);
    expect(html).not.toContain('aria-label="Unassign ann@acme.test"');
    expect(html).toContain("Edited: due 2027-02-03 · removed ann@acme.test.");
    expect(html).toContain("data-task-edit-reset");
    expect(html).toContain(TASK_EDITED_HINT);
  });

  it("a due date that the server would refuse turns Approve off", () => {
    const html = card({ edit: form, onEditAnswer: noop, savedTaskEdits: { ...VALUES, due: "12345-01-01" } });
    const approve = html.split("<").find((t) => t.includes("data-confirmation-approve")) ?? "";
    expect(approve).toContain(' disabled=""');
    expect(html).toContain("Pick a due date");
  });
});

describe("the wiring (EAC-2)", () => {
  const cardSrc = read("components/ConfirmationCard.tsx");
  const queueSrc = read("components/ConfirmationQueue.tsx");

  it("approve-is-plain-or-edit: Approve sends taskApproval(original, edits), and only a card with a form does", () => {
    expect(cardSrc).toContain("const taskForm = edit && onEditAnswer ? edit : undefined;");
    expect(cardSrc).toMatch(/if \(taskForm && task\.edits && onEditAnswer\) onEditAnswer\(taskApproval\(taskForm\.values, task\.edits\)\);\n\s+else onApprove\(\);/);
    // Reject and the steer path are those of before.
    expect(cardSrc).toContain("onClick={() => armed && onReject()}");
    expect(queueSrc).toContain('onReject={() => onAnswer(card, "REJECT")}');
    expect(queueSrc).toContain("onEditAnswer: (answer: \"APPROVE\" | TaskEditApproval) => onAnswer(card, answer),");
  });

  it("approve-is-plain-or-edit: the form is drawn for a live card only, not for the slot of a redraft", () => {
    expect(cardSrc).toContain("{!slot && taskForm && task.edits && (");
  });

  it("kept-edits-by-kind: each reader takes its own kind through a guard, never a cast", () => {
    expect(queueSrc).toContain("savedEdits: asNewMailEdits(savedEdits?.(card.key))");
    expect(queueSrc).toContain("savedTaskEdits: asTaskEdits(savedEdits?.(card.key))");
    for (const src of [cardSrc, queueSrc, read("lib/confirmationQueue.ts"), read("components/TaskCardForm.tsx")]) {
      expect(src).not.toMatch(/\bas (NewMailEdits|TaskCardEdits|FormEdit)\b/);
    }
    // The redraft code reads the mail kind only.
    expect(read("lib/redraftCard.ts")).not.toMatch(/taskEditCard|TaskCardEdits|FormEdits?\b/);
  });

  it("redraftCard-reads-no-form-edits: redraftCard.ts has no FormEdits reader and no task-card import", () => {
    // Mutation: import FormEdits or taskEditCard into redraftCard.ts, and this turns red.
    const src = read("lib/redraftCard.ts");
    expect(src).not.toMatch(/FormEdits?|asTaskEdits|asNewMailEdits|taskEditCard|TaskCardEdits|confirmationQueue/);
  });

  it("kept-edits-by-kind: one map holds both kinds, and the same values change nothing", () => {
    const mail = { subject: "s", body: "b", ticks: { reply: [], reply_all: [] } as never, fileTicks: [] };
    const one = formEditsReducer(NO_EDITS, { key: "task", value: VALUES });
    const two = formEditsReducer(one, { key: "mail", value: mail });
    expect(asTaskEdits(two.get("task"))).toBe(VALUES);
    expect(asNewMailEdits(two.get("task"))).toBeUndefined();
    expect(asNewMailEdits(two.get("mail"))).toBe(mail);
    expect(asTaskEdits(two.get("mail"))).toBeUndefined();
    expect(isNewMailEdits(VALUES)).toBe(false);
    // The same values (a new object) change nothing. A new person does.
    expect(formEditsReducer(one, { key: "task", value: { ...VALUES, assignees: [...VALUES.assignees] } })).toBe(one);
    expect(formEditsReducer(one, { key: "task", value: { ...VALUES, assignees: ["bob@acme.test"] } })).not.toBe(one);
    // A card that left the queue drops its edits, as a mail card does.
    expect(closedEdits(two, new Set(["task", "mail"]), new Set(["mail"])).has("task")).toBe(false);
    expect(closedEdits(two, new Set(["task", "mail"]), new Set(["mail"])).has("mail")).toBe(true);
  });

  it("the chat keeps the edits of both kinds in its thread store", () => {
    const chat = read("components/AgentChat.tsx");
    expect(chat).toContain("const onFormEdits = useCallback((key: string, value: FormEdit) => {");
    expect(chat).not.toContain('import type { NewMailEdits } from "@/lib/replyCard";');
  });

  it("shared-parts-only: the form builds no control, no colour and no icon import of its own", () => {
    const src = read("components/TaskCardForm.tsx");
    expect(src).not.toMatch(/<input\b|<textarea\b|<select\b|<form\b|onSubmit/);
    expect(src).not.toContain("lucide-react");
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\b(?:bg|text)-(?:red|amber|sky|green|blue|violet)-/);
    expect(src).toContain('import { Input } from "@/components/ui/Input";');
    expect(src).toContain('import AssigneePicker from "@/app/projects/components/AssigneePicker";');
    expect(src).toContain('import AssigneeChips from "@/app/projects/components/AssigneeChips";');
    expect(src).toContain('type="date"');
    // A person is added on Enter or a pick, never on a blur, so a click on Approve cannot race the add.
    expect(src).toContain("commitOnBlur={false}");
    // Escape clears the text that no Enter added.
    expect(src).toContain('e.key === "Escape" && typed');
    // The project id of the card never leaves the form: it is an id for a later slice, not an answer value.
    expect(read("lib/taskEditCard.ts")).not.toMatch(/edit = \{[^}]*projectId/);
  });
});
