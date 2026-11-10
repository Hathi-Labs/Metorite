// A refused call never draws as a done act (review of PR #1126, P1).
//
// R7 fence `email-chat-refused-card`. The CRM and email tools now RETURN a
// gateway refusal as text ("Refused: ..."), so the model can read it. The
// stream marks it `success: false` (`acb_common.tool_refusals.is_refusal`),
// and the card keeps its own guard in case a result arrives as "done":
// no "Email sent" over a refused send, no "Trashed 3 emails" over a refused
// trash, and no draft card with a Send button whose body is the refusal.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/chat" }));

import type { ToolEvent } from "@/components/MarkdownMessage";

import EmailToolCards, { noActionOf } from "./EmailToolCards";

const REFUSAL = [
  "Refused: The email app is not available right now (503).",
  "Gateway said: «The mail provider did not answer.»",
  "Next: This is a fault on our side. Try again in a moment.",
].join("\n");

function draw(name: string, args: Record<string, unknown> = {}): string {
  const event: ToolEvent = { id: `t-${name}`, name, args, result: REFUSAL, status: "done" };
  return renderToStaticMarkup(createElement(EmailToolCards, { toolEvents: [event], accountId: "acc-1" }));
}

describe("a refused email tool", () => {
  it("is the no-action kind 'refused'", () => {
    expect(noActionOf(REFUSAL)).toBe("refused");
    expect(noActionOf("  Refused: The CRM refused an argument (422).")).toBe("refused");
    expect(noActionOf("Email sent from Fracktal to ravi@x.in")).toBeNull();
  });

  it("never draws 'Email sent' on the action card", () => {
    const html = draw("send_email", { to: "ravi@x.in", subject: "Hi" });
    expect(html).not.toContain("Email sent");
    expect(html).toContain("Not done");
    expect(html).toContain('data-email-card="no-action"');
  });

  it("never draws 'Trashed' on the inbox card", () => {
    const html = draw("manage_inbox", { action: "trash", message_ids: ["m1", "m2", "m3"] });
    expect(html).not.toContain("Trashed");
    expect(html).toContain("Not done");
  });

  it("never offers Send on a draft whose body is the refusal", () => {
    const html = draw("draft_reply", { email_id: "m1", account_id: "acc-1" });
    expect(html).not.toMatch(/>\s*Send\s*</);
    expect(html).not.toContain("<textarea");
    expect(html).toContain("Not done");
  });
});
