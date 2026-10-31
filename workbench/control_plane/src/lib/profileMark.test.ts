// PZ-20b, personalization.md §13.3 item 1 — the "used your profile" mark.
//
// R7 fence `pz20b-mark`: the mark draws only for the event
// `assistant_profile_used`. The name equals the server's. The panel hides the
// event, MessageBubble draws the mark, and AgentChat does not change.
// R7 fence `pz20b-reader`: the bubble draws the mark only for the member who
// ran the turn, or for a live turn that names no run member.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { PROFILE_USED_EVENT, profileMarkShown, profileUsedIn } from "@/lib/profileMark";

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

describe("profileMarkShown", () => {
  const marked = [{ name: PROFILE_USED_EVENT }];
  const turn = (runMemberEmail?: string) => ({
    role: "assistant", customEvents: marked, runMemberEmail,
  });

  it("draws for the member who ran the turn, in any case", () => {
    expect(profileMarkShown(turn("ana@alpha.example"), "Ana@Alpha.example")).toBe(true);
  });

  it("draws for a live turn of this browser and for no reader", () => {
    expect(profileMarkShown(turn(undefined), "ana@alpha.example")).toBe(true);
    expect(profileMarkShown(turn("ana@alpha.example"), undefined)).toBe(true);
    expect(profileMarkShown(turn("ana@alpha.example"), "")).toBe(true);
  });

  it("draws none for another reader of a shared chat", () => {
    expect(profileMarkShown(turn("ana@alpha.example"), "bo@alpha.example")).toBe(false);
  });

  it("draws none for a turn with no event or for a member's own turn", () => {
    expect(profileMarkShown({ role: "assistant", customEvents: [] }, undefined)).toBe(false);
    expect(profileMarkShown({ role: "user", customEvents: marked }, undefined)).toBe(false);
  });
});

describe("where the mark lives", () => {
  it("the panel hides the event, so it never shows as raw data", () => {
    expect(src("src/components/GenerativeUIPanel.tsx")).toContain(`"${PROFILE_USED_EVENT}"`);
  });

  it("MessageBubble draws it with the reader rule, as a link to Assistant settings", () => {
    const bubble = src("src/components/MessageBubble.tsx");
    expect(bubble).toContain("profileMarkShown(message, viewerEmail)");
    expect(bubble).toContain('href="/settings/assistant"');
  });

  it("the history read keeps the run member of each turn", () => {
    expect(src("src/lib/sessions.ts")).toContain("runMemberEmail: r.runMemberEmail ?? undefined");
  });

  it("AgentChat does not know the mark", () => {
    expect(src("src/components/AgentChat.tsx")).not.toContain("profileUsedIn");
    expect(src("src/components/AgentChat.tsx")).not.toContain("profileMarkShown");
  });
});
