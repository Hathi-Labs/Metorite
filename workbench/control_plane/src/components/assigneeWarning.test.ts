/**
 * The card of a task for a person who is not in the people list (task 3597).
 *
 * The fixture is the card of a real run: three tasks, two of them for an
 * address that `people` does not know. Each test names the mutation that
 * turns it red (R7).
 *
 * 1. The warning draws first, with the warning tone and the words.
 * 2. `gives access to` does not name a stranger.
 * 3. A tick row with a stranger carries the mark, and a member alone does not.
 * 4. A card with no stranger draws as before. The ticks and the answer stay.
 * 5. The real shapes of the server: ONE fence with a comma list.
 *
 * REAL_SHAPES below is the output of `_fields_block` in
 * apps/skills/skill-projects/skill_projects/writes.py, pasted as it came.
 * `tests/unit/test_card_stranger_warning.py` asserts the same strings, so a
 * change in the server breaks that test and not only this one.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ConfirmationCard, { parseCardBody } from "@/components/ConfirmationCard";
import {
  ASSIGNEE_ROW_MARK,
  ASSIGNEE_WARNING,
  MAX_SHOWN,
  assigneeWarningText,
  fieldsWithoutStrangers,
  rowIsStranger,
  strangerAddresses,
} from "@/lib/assigneeWarning";
import { NO_TICKS, approveRows, rowsFromEvent, ticksOf } from "@/lib/confirmationQueue";

const SRC = fileURLToPath(new URL("..", import.meta.url));

const ADDR = "pankajg@shaurryatele.com";
const MEMBER = "asha@shaurryatele.com";
const CONTEXT = [
  "This change is recorded as yours, made through the Projects assistant.",
  "mailbox: This text leaves your mailbox. Everyone who can see «Horizon Industrial Park» will see it.",
  "seen by: «everyone in the organization (8 people)»",
  `gives access to: «${ADDR}»`,
  `not in the directory: «${ADDR}»`,
].join("\n");
const ROWS_EVENT = [
  { id: "t1", label: "«Survey the site»", hint: `${ADDR} · no due date · status Backlog` },
  { id: "t2", label: "«Order the cable»", hint: `${ADDR} · no due date · status Backlog` },
  { id: "t3", label: "«Book the crane»", hint: `${MEMBER} · no due date · status Backlog` },
];

/** The server's text for 1, 2 and 3 strangers. Keep equal to the Python test. */
const REAL_SHAPES = {
  one: "This change is recorded as yours, made through the Projects assistant.\nseen by: «everyone in the organization (8 people)»\ngives access to: «a@x.com»\nnot in the directory: «a@x.com»",
  two: "This change is recorded as yours, made through the Projects assistant.\nseen by: «everyone in the organization (8 people)»\ngives access to: «a@x.com, b@x.com, m@x.com»\nnot in the directory: «a@x.com, b@x.com»",
  three:
    "This change is recorded as yours, made through the Projects assistant.\nseen by: «everyone in the organization (8 people)»\ngives access to: «a@x.com, b@x.com, c@x.com and 4 more»\nnot in the directory: «a@x.com, b@x.com, c@x.com»",
};

const draw = (context: string, rows: unknown[] = ROWS_EVENT) =>
  renderToStaticMarkup(
    createElement(ConfirmationCard, {
      title: "Create 3 tasks in «Horizon Industrial Park»?",
      detail: "one batch ... untick a task to leave it out",
      context,
      fenced: true,
      rows: rowsFromEvent(rows),
      onApprove: () => {},
      onReject: () => {},
    }),
  );

const accessField = (html: string) => {
  const at = html.indexOf('data-card-field="gives access to"');
  return at < 0 ? "" : html.slice(at, html.indexOf("</div>", at));
};
const parse = (context: string) => parseCardBody(context).fields;
const plain = (html: string) => html.replace(/&#x27;/g, "'");

describe("the warning row", () => {
  const html = draw(CONTEXT);

  it("draws first in the body, with the warning tone and the words", () => {
    // Mutation: draw the stranger row as a plain row again → red.
    // Mutation: draw the warning below the tick rows → red.
    const at = html.indexOf("data-assignee-warning");
    expect(at).toBeGreaterThan(0);
    expect(at).toBeLessThan(html.indexOf("data-confirmation-rows"));
    expect(at).toBeLessThan(html.indexOf("data-card-field="));
    const tag = plain(html.slice(html.lastIndexOf("<p", at), html.indexOf("</p>", at)));
    expect(tag).toContain("text-warning");
    expect(tag).toContain('role="status"');
    expect(tag).toContain(ASSIGNEE_WARNING.one(ADDR));
    expect(html).not.toContain('data-card-field="not in the directory"');
  });

  it("explains in plain words and uses no word of the code", () => {
    expect(ASSIGNEE_WARNING.one(ADDR)).toBe(
      `${ADDR} is not in your organization's people list. The task lists this address, but this person cannot see the task or get a notice until they are invited. Invite the person, or pick someone from the list.`,
    );
    expect(ASSIGNEE_WARNING.many(["a@x.com, b@x.com"])).toBe(
      "a@x.com, b@x.com are not in your organization's people list. The task lists these addresses, but these people cannot see the task or get a notice until they are invited. Invite them, or pick people from the list.",
    );
    expect(ASSIGNEE_ROW_MARK).toBe("Assignee not in the list");
    expect(ASSIGNEE_ROW_MARK).not.toContain("member");
    expect(ASSIGNEE_WARNING.one(ADDR).toLowerCase()).not.toContain("directory");
    expect(ASSIGNEE_WARNING.many(["a@x.com, b@x.com"]).toLowerCase()).not.toContain("directory");
  });

  it("keeps the other rows, in their order", () => {
    // Mutation: drop every row but the stranger row → red.
    const order = ['data-card-field="mailbox"', 'data-card-field="seen by"'].map((k) => html.indexOf(k));
    expect(order[0]).toBeGreaterThan(0);
    expect(order[0]).toBeLessThan(order[1]);
  });

  it("no longer says that the stranger gets access", () => {
    // Mutation: keep the stranger in "gives access to" → red.
    expect(html).not.toContain('data-card-field="gives access to"');
  });
});

describe("the mark on a tick row", () => {
  const html = draw(CONTEXT);
  const li = (id: string) => {
    const at = html.indexOf(`data-confirmation-row="${id}"`);
    return html.slice(at, html.indexOf("</li>", at));
  };

  it("marks the rows of the stranger, and no other", () => {
    // Mutation: mark every row, or no row → red.
    for (const id of ["t1", "t2"]) {
      expect(li(id)).toContain("data-assignee-row-mark");
      expect(li(id)).toContain(ASSIGNEE_ROW_MARK);
    }
    expect(li("t3")).not.toContain("data-assignee-row-mark");
    expect(html.match(/data-assignee-row-mark/g)).toHaveLength(2);
  });

  it("changes no tick and no answer", () => {
    // Mutation: add a row id, or drop a tick, in the card → red.
    const rows = rowsFromEvent(ROWS_EVENT)!;
    expect(rows.map((r) => r.id)).toEqual(["t1", "t2", "t3"]);
    expect(rows.every((r) => r.checked)).toBe(true);
    const card = { key: "k", rows };
    expect(approveRows(ticksOf(card, NO_TICKS))).toBe('APPROVE {"rows":["t1","t2","t3"]}');
    const src = readFileSync(`${SRC}/components/ConfirmationCard.tsx`, "utf8");
    expect(src).not.toMatch(/approveRows|ROWS_ANSWER_PREFIX/);
  });

  it("reads the hint in any case, and not a later segment", () => {
    expect(rowIsStranger(`${ADDR.toUpperCase()} · no due date`, [ADDR])).toBe(true);
    expect(rowIsStranger(`${MEMBER} · ${ADDR}`, [ADDR])).toBe(false);
    expect(rowIsStranger(undefined, [ADDR])).toBe(false);
    expect(rowIsStranger(`${ADDR} · x`, [])).toBe(false);
  });
});

describe("strangers in the real shape of the server", () => {
  const rows = [
    { id: "r1", label: "«One»", hint: "a@x.com, Mia (m@x.com) · no due date · status Backlog" },
    { id: "r2", label: "«Two»", hint: "Mia (m@x.com) · no due date · status Backlog" },
    { id: "r3", label: "«Three»", hint: "b@x.com · no due date · status Backlog" },
  ];
  const html = draw(REAL_SHAPES.two, rows);

  it("reads one fence with a comma list as separate addresses", () => {
    // Mutation: read the fence as one item → red.
    expect(strangerAddresses(parse(REAL_SHAPES.one))).toEqual(["a@x.com"]);
    expect(strangerAddresses(parse(REAL_SHAPES.two))).toEqual(["a@x.com", "b@x.com"]);
    expect(strangerAddresses(parse(REAL_SHAPES.three))).toEqual(["a@x.com", "b@x.com", "c@x.com"]);
    // A list of fences gives the same result.
    expect(strangerAddresses([{ key: "not in the directory", value: "«a@x.com», «b@x.com»" }])).toEqual(["a@x.com", "b@x.com"]);
    expect(strangerAddresses([{ key: "not in the directory", value: "«A@x.com, a@x.com»" }])).toEqual(["A@x.com"]);
  });

  it("names both in one warning, and marks each row that holds a stranger", () => {
    // Mutation: mark no row, or one address only → red.
    expect(plain(html)).toContain(ASSIGNEE_WARNING.many(["a@x.com, b@x.com"]));
    expect(html.match(/data-assignee-warning/g)).toHaveLength(1);
    const marked = (id: string) => {
      const at = html.indexOf(`data-confirmation-row="${id}"`);
      return html.slice(at, html.indexOf("</li>", at)).includes("data-assignee-row-mark");
    };
    // r1 holds a stranger and a member. r2 holds a member only.
    expect([marked("r1"), marked("r2"), marked("r3")]).toEqual([true, false, true]);
  });

  it("keeps the member in gives access to and drops both strangers", () => {
    // Mutation: keep a stranger in the row → red.
    const f = accessField(html);
    expect(f).toContain("m@x.com");
    expect(f).not.toContain("a@x.com");
    expect(f).not.toContain("b@x.com");
  });

  it("keeps an 'and N more' tail, and drops a row of strangers alone", () => {
    const f = accessField(draw(REAL_SHAPES.three, rows));
    expect(f).toContain("4 more");
    expect(f).not.toContain("a@x.com");
    expect(draw(REAL_SHAPES.one, rows)).not.toContain('data-card-field="gives access to"');
  });
});

describe("an address inside another address", () => {
  it("marks ann and does not mark joann, whole address only", () => {
    // Mutation: compare by substring → red.
    expect(rowIsStranger("ann@x.com · no due date", ["ann@x.com"])).toBe(true);
    expect(rowIsStranger("joann@x.com · no due date", ["ann@x.com"])).toBe(false);
    expect(rowIsStranger("Joann (joann@x.com) · no due date", ["ann@x.com"])).toBe(false);
    expect(rowIsStranger("ann@x.com.au · no due date", ["ann@x.com"])).toBe(false);
    expect(rowIsStranger("ann@x.co · no due date", ["ann@x.com"])).toBe(false);
    expect(rowIsStranger("Jo (joann@x.com), ann@x.com · due", ["ann@x.com"])).toBe(true);
  });

  it("keeps a member whose address holds the stranger's", () => {
    const out = fieldsWithoutStrangers([{ key: "gives access to", value: "«ann@x.com, joann@x.com»" }], ["ann@x.com"]);
    expect(out).toEqual([{ key: "gives access to", value: "«joann@x.com»" }]);
  });
});

describe("fifty strangers", () => {
  const fifty = Array.from({ length: 50 }, (_, i) => `person${i + 1}@longcompany.example`);
  const ctx = `${REAL_SHAPES.one.split("\n").slice(0, 2).join("\n")}\nnot in the directory: «${fifty.join(", ")}»`;
  const rows = fifty.map((a, i) => ({ id: `r${i}`, label: `«Task ${i}»`, hint: `${a} · no due date` }));
  const html = draw(ctx, rows);

  it("names five and counts the rest, in the warning only", () => {
    // Mutation: print every address → red.
    expect(MAX_SHOWN).toBe(5);
    const text = assigneeWarningText(fifty);
    expect(text).toContain(`${fifty.slice(0, 5).join(", ")} and 45 more are not in your organization's people list.`);
    expect(text).not.toContain(fifty[5]);
    expect(html).toContain("and 45 more are not in");
  });

  it("keeps a mark on every row, and the sentence wraps", () => {
    expect(html.match(/data-assignee-row-mark/g)).toHaveLength(50);
    const at = html.indexOf("data-assignee-warning");
    expect(html.slice(html.lastIndexOf("<p", at), at)).toContain("break-words");
  });
});

describe("no stranger", () => {
  const ctx = CONTEXT.split("\n").filter((l) => !l.startsWith("not in the directory")).join("\n");
  const html = draw(ctx);

  it("draws the card as before", () => {
    // Mutation: draw the warning or a mark with no stranger → red.
    expect(html).not.toContain("data-assignee-warning");
    expect(html).not.toContain("data-assignee-row-mark");
    expect(html).toContain('data-card-field="gives access to"');
    expect(html).toContain(ADDR);
  });

  it("leaves the fields as they are", () => {
    const fields = [{ key: "gives access to", value: `«${ADDR}»` }, { key: "seen by", value: "«x»" }];
    expect(fieldsWithoutStrangers(fields, [])).toEqual(fields);
    expect(strangerAddresses(fields)).toEqual([]);
  });
});
