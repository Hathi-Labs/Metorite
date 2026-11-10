/**
 * An assignee draws as a person chip, with the name as its label (follow-up
 * of #716 and #735, spec `projects_ai_chat.md` §24.8). `task_detail` showed
 * "Priya (priya@x.io)" as one label with "P(" as its initials, and the board
 * card joined the assignees as plain text.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `personOf` returns the text whole -> "splits a name from its address";
 * - `CardFieldValue` draws the person text as the label again -> "a person
 *   value is a chip with its name";
 * - the board card joins the assignees again -> "the board card draws each
 *   assignee as a person chip";
 * - the priority chip sits beside the title again -> "the board title has
 *   the card's width".
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { ValueElement } from "@/components/CardFieldValue";
import { TEMPLATE_REGISTRY } from "@/components/genUITemplates";
import { projectEvidence } from "@/components/projects/ProjectToolCards";
import type { ToolEvent } from "@/components/MarkdownMessage";
import { personOf } from "@/lib/cardFields";

const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const visible = (markup: string) => markup.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");

describe("personOf", () => {
  it("splits a name from its address", () => {
    expect(personOf("Priya (priya@x.io)")).toEqual({ name: "Priya", email: "priya@x.io" });
    expect(personOf("Priya Rao <priya@x.io>")).toEqual({ name: "Priya Rao", email: "priya@x.io" });
    expect(personOf("priya@x.io")).toEqual({ name: "priya@x.io", email: "priya@x.io" });
    expect(personOf("Priya")).toEqual({ name: "Priya" });
    // Brackets that hold no address stay part of the name.
    expect(personOf("Priya (ops)")).toEqual({ name: "Priya (ops)" });
  });
});

describe("a person value is a chip with its name", () => {
  it("in a card field", () => {
    const out = renderToStaticMarkup(
      createElement(ValueElement, { item: { text: "Priya (priya@x.io)", named: true }, kind: "person" }),
    );
    expect(out).toContain(">Priya<");
    expect(out).toContain('title="Priya · priya@x.io"');
    expect(visible(out)).not.toContain("(priya@x.io)");
  });

  it("in task_detail's assignees", () => {
    const result = [
      "Task #11 «Fix the extruder»",
      `  full_id: ${ID}`,
      "  status: «To do»",
      "  assignees: «Priya (priya@x.io)», «sam@x.io»",
      "  due: 2026-10-09",
    ].join("\n");
    const e: ToolEvent = { id: "t", name: "task_detail", args: {}, result, status: "done", startedAt: 1, endedAt: 2 };
    const out = renderToStaticMarkup(createElement("div", null, projectEvidence(e)));
    expect(out).toContain('title="Priya · priya@x.io"');
    expect(out).toContain(">sam@x.io<");
    expect(visible(out)).not.toContain("(priya@x.io)");
    // The initials come from the name, never from "P(".
    expect(out).not.toContain(">P(<");
    // An unfenced date is a formatted date too.
    expect(visible(out)).toContain("9 Oct 2026");
  });
});

describe("the board card", () => {
  const board = renderToStaticMarkup(
    createElement(() =>
      TEMPLATE_REGISTRY.taskBoard({
        columns: [{
          id: "s1",
          name: "To do",
          tasks: [{
            id: ID,
            number: 7,
            title: "Make the extruder settings page remember the last profile",
            importance: 2,
            assignees: ["Priya (priya@x.io)", "sam@x.io"],
          }],
        }],
      }),
    ),
  );

  it("draws each assignee as a person chip", () => {
    expect(board).toContain('title="Priya · priya@x.io"');
    expect(board).toContain(">sam@x.io<");
    expect(visible(board)).not.toContain("Priya (priya@x.io), sam@x.io");
  });

  it("the board title has the card's width", () => {
    // The first line of the card holds the number and the title only. The
    // priority chip sits in the facts row under it.
    const card = board.split('data-board-card=""')[1];
    const [titleLine, rest] = card.split("</div>");
    expect(titleLine).toContain("Make the extruder settings page");
    expect(titleLine).not.toContain("Priority:");
    expect(rest + card).toContain("Priority:");
  });
});
