// Multi-inbox UI, item 6: the chat picker marks a mailbox that needs a
// reconnect, as the account switcher does. Fence `email-chat-picker-attention`.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AgentChat from "@/components/AgentChat";

import { chatMailboxOptions } from "./chatScope";

const ok = { id: "a", emailAddress: "a@x.test", displayLabel: "Work", isDefault: true };
const bad = { id: "b", emailAddress: "b@x.test", displayLabel: "Home", isDefault: false, syncStatus: "error" };
const lapsed = { id: "c", emailAddress: "c@x.test", isDefault: false, needsReconnect: true };

describe("email-chat-picker-attention", () => {
  it("flags a mailbox whose sync failed or whose sign-in lapsed", () => {
    const opts = chatMailboxOptions([ok, bad, lapsed]);
    expect(opts.find((o) => o.id === "a")?.attention).toBeUndefined();
    expect(opts.find((o) => o.id === "b")?.attention).toBe(true);
    expect(opts.find((o) => o.id === "c")?.attention).toBe(true);
    // One mailbox gets the flag too, so a failure never hides.
    expect(chatMailboxOptions([bad])[0].attention).toBe(true);
  });

  it("draws the mark in the trigger when the mailbox in force needs a reconnect", () => {
    const draw = (attention: boolean) =>
      renderToStaticMarkup(
        createElement(AgentChat, {
          agentName: "email-assistant",
          sessionId: "s",
          model: "tier-powerful",
          lockModel: true,
          mailboxes: [{ id: "b", label: "Home", attention }],
          activeMailboxId: "b",
        }),
      );
    expect(draw(true)).toContain("Needs reconnecting");
    expect(draw(false)).not.toContain("Needs reconnecting");
  });
});
