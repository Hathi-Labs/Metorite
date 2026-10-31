// PZ-20b, personalization.md §13.3 item 1 — the "used your profile" mark.
//
// R7 fence `pz20b-mark`: the mark draws only for the event
// `assistant_profile_used`. The name equals the server's. The panel hides the
// event, MessageBubble draws the mark, and AgentChat does not change.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { PROFILE_USED_EVENT, profileUsedIn } from "@/lib/profileMark";

const src = (p: string) => readFileSync(join(__dirname, "..", "..", p), "utf-8");

describe("profileUsedIn", () => {
  it("is true only for the profile event", () => {
    expect(profileUsedIn([{ name: PROFILE_USED_EVENT }])).toBe(true);
    expect(profileUsedIn([{ name: "email_mailbox_mark" }, { name: "ai.route" }])).toBe(false);
    expect(profileUsedIn([])).toBe(false);
    expect(profileUsedIn(undefined)).toBe(false);
  });

  it("keeps the event name equal to the server's", () => {
    const py = src("../../packages/acb_common/acb_common/assistant_profile.py");
    expect(py).toContain(`PROFILE_USED_EVENT = "${PROFILE_USED_EVENT}"`);
  });
});

describe("where the mark lives", () => {
  it("the panel hides the event, so it never shows as raw data", () => {
    expect(src("src/components/GenerativeUIPanel.tsx")).toContain(`"${PROFILE_USED_EVENT}"`);
  });

  it("MessageBubble draws it for an assistant answer, as a link to Assistant settings", () => {
    const bubble = src("src/components/MessageBubble.tsx");
    expect(bubble).toContain("profileUsedIn(message.customEvents)");
    expect(bubble).toContain('message.role === "assistant"');
    expect(bubble).toContain('href="/settings/assistant"');
  });

  it("AgentChat does not know the mark", () => {
    expect(src("src/components/AgentChat.tsx")).not.toContain("profileUsedIn");
  });
});
