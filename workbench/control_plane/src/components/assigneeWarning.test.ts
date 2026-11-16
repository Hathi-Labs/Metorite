/**
 * The card of a task for a person who is not a member (task 3597).
 *
 * The fixture is the card of a real run: three tasks, two of them for an
 * address that `people` does not know. Each test names the mutation that
 * turns it red (R7).
 *
 * 1. The warning draws first, with the warning tone and the words.
 * 2. `gives access to` does not name a stranger.
 * 3. A tick row whose first address is a stranger carries the mark.
 * 4. A card with no stranger draws as before. The ticks and the answer stay.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ConfirmationCard from "@/components/ConfirmationCard";
import {
  ASSIGNEE_ROW_MARK,
  ASSIGNEE_WARNING,
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

describe("the warning row", () => {
  const html = draw(CONTEXT);

  it("draws first in the body, with the warning tone and the words", () => {
    // Mutation: draw the stranger row as a plain row again → red.
    const at = html.indexOf("data-assignee-warning");
    expect(at).toBeGreaterThan(0);
    expect(at).toBeLessThan(html.indexOf("data-confirmation-rows"));
    expect(at).toBeLessThan(html.indexOf("data-card-field="));
    const tag = html.slice(html.lastIndexOf("<p", at), html.indexOf("</p>", at));
    expect(tag).toContain("text-warning");
    expect(tag).toContain('role="status"');
    expect(tag).toContain(ASSIGNEE_WARNING.one(ADDR));
    expect(html).not.toContain('data-card-field="not in the directory"');
  });

  it("explains in plain words and uses no word of the code", () => {
    expect(ASSIGNEE_WARNING.one(ADDR)).toBe(
      `${ADDR} is not a member of your organization. The task lists this address, but this person will not see the task or get a notice. Invite the person, or pick a member.`,
    );
    expect(ASSIGNEE_WARNING.one(ADDR).toLowerCase()).not.toContain("directory");
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
    const found = strangerAddresses([{ key: "not in the directory", value: `«${ADDR}»` }]);
    const out = fieldsWithoutStrangers([{ key: "gives access to", value: `«${ADDR}», «${MEMBER}»` }], found);
    expect(out).toEqual([{ key: "gives access to", value: `«${MEMBER}»` }]);
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

  it("reads the first address of a hint, in any case", () => {
    expect(rowIsStranger(`${ADDR.toUpperCase()} · no due date`, [ADDR])).toBe(true);
    expect(rowIsStranger(`${MEMBER} · ${ADDR}`, [ADDR])).toBe(false);
    expect(rowIsStranger(undefined, [ADDR])).toBe(false);
    expect(rowIsStranger(`${ADDR} · x`, [])).toBe(false);
  });
});

describe("two strangers", () => {
  const two = CONTEXT.replace(`gives access to: «${ADDR}»`, `gives access to: «${ADDR}», «${MEMBER}»`).replace(
    `not in the directory: «${ADDR}»`,
    `not in the directory: «${ADDR}», «${MEMBER}»`,
  );

  it("names each address in one warning, and marks each row", () => {
    // Mutation: name only the first address → red.
    const html = draw(two);
    expect(html).toContain(ASSIGNEE_WARNING.many([ADDR, MEMBER]));
    expect(html.match(/data-assignee-warning/g)).toHaveLength(1);
    expect(html.match(/data-assignee-row-mark/g)).toHaveLength(3);
    expect(html).not.toContain('data-card-field="gives access to"');
  });

  it("keeps a member who shares the row", () => {
    const ctx = CONTEXT.replace(`gives access to: «${ADDR}»`, `gives access to: «${ADDR}», «kim@shaurryatele.com»`);
    const html = draw(ctx);
    const at = html.indexOf('data-card-field="gives access to"');
    expect(at).toBeGreaterThan(0);
    const field = html.slice(at, html.indexOf("</div>", at));
    expect(field).toContain("kim@shaurryatele.com");
    expect(field).not.toContain(ADDR);
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
