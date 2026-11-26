// Task 3977 (email_app_master_plan.md §18.13). A trash that checked Drafts
// answers with the number that went. The member can pick more or fewer drafts
// than the call named, so the receipt never counts the ids of the call.
//
// Mutation: read `ids.length` again → "counts what went" is red.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

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

  it("never says Trashed when the mailbox did not confirm a trash", () => {
    // Review round 1: the provider did not confirm, so the head counts 0.
    const html = draw(
      "Trashed 0 message(s): the mailbox did not confirm a trash.\nThe mailbox provider did not confirm",
      ["m1"],
    );
    expect(html).not.toContain("Trashed 1 email");
    expect(html).toContain("the mailbox did not confirm a trash");
  });

  it("draws no trashed receipt when nothing went", () => {
    const html = draw("Nothing changed. No message was moved to Trash.", ["m1"]);
    expect(html).toContain('data-email-card="no-action"');
    expect(html).not.toContain("1 email");
  });
});
