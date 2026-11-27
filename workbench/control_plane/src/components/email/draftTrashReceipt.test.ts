// Task 3977 (email_app_master_plan.md §18.13). A trash that checked Drafts
// answers with the number that went. The member can pick more or fewer drafts
// than the call named, so the receipt never counts the ids of the call.
//
// Mutation: read `ids.length` again → "counts what went" is red.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { parseCardBody } from "@/components/ConfirmationCard";
import type { ToolEvent } from "@/components/MarkdownMessage";

import EmailToolCards from "./EmailToolCards";

function draw(result: string, ids: string[]): string {
  const event: ToolEvent = {
    id: "t-trash", name: "manage_inbox",
    args: { action: "trash", message_ids: ids }, result, status: "done",
  };
  return renderToStaticMarkup(createElement(EmailToolCards, { toolEvents: [event], accountId: "acc-1" }));
}

describe("the receipt of a draft trash", () => {
  it("counts what went, not what the call named", () => {
    expect(draw("Trashed 2 message(s): 2 draft(s).\nNo matching draft is left", ["m1"]))
      .toContain("2 emails");
    expect(draw("Trashed 1 message(s): 1 draft(s).\n1 draft(s) did not move.", ["m1", "m2", "m3"]))
      .toContain("1 email");
  });

  it("keeps the count of the call for an older answer", () => {
    expect(draw("trash: affected 3 message(s).", ["m1", "m2", "m3"])).toContain("3 emails");
  });

  it.each([
    "Trashed 0 message(s): the mailbox did not confirm a trash.\nThe mailbox provider did not confirm",
    "Trashed 0 message(s): the answer of a trash did not come back.\nThey can be in Trash or not.",
  ])("claims neither a trash nor no change when the head counts 0", (result) => {
    // Review round 1 and task 4026. Mutation: drop the `Trashed 0` rule of
    // `noActionOf` -> "Inbox updated" with its check mark is back. Map it to
    // "unchanged" -> the card says that nothing changed, which can be false.
    const html = draw(result, ["m1"]);
    expect(html).toContain('data-email-card="no-action"');
    expect(html).toContain("Not confirmed");
    expect(html).toContain("Check your mailbox.");
    expect(html).not.toContain("Nothing changed");
    expect(html).not.toContain("Inbox updated");
    expect(html).not.toContain("Trashed 1 email");
  });

  it("draws no trashed receipt when nothing went", () => {
    const html = draw("Nothing changed. No message was moved to Trash.", ["m1"]);
    expect(html).toContain('data-email-card="no-action"');
    expect(html).not.toContain("1 email");
  });
});

describe("the plain card of one draft", () => {
  // Task 4026. The context of `_plain_card_context` for one draft, as
  // `test_the_plain_card_of_one_draft_reads_as_a_list` makes it. Its last
  // line holds no ": ", so the card draws a list and never a grid of rows.
  // Mutation: drop the last line → the body reads as one `key: value` row.
  const ONE_DRAFT =
    "Each draft that moves to Trash:\n" +
    "- To kim@client.test: Lunch · saved 2026-11-26 06:05 · starts: Dear Salil,\n" +
    "No other matching draft is in Drafts, as Metorite last synced it.";

  it("reads as a list, not as rows", () => {
    const body = parseCardBody(ONE_DRAFT);
    expect(body.fields).toEqual([]);
    expect(body.text).toBe(ONE_DRAFT);
  });

  it("was a grid with no last line", () => {
    const before = ONE_DRAFT.split("\n").slice(0, 2).join("\n");
    expect(parseCardBody(before).fields.length).toBe(1);
  });
});
