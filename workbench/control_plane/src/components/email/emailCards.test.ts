/**
 * The Email chat cards keep their payload (follow-up of #945, "chat cards:
 * one look").
 *
 * The cards moved onto the shared header, the shared `Button` and the
 * shared `Textarea`. Nothing they send may change. This file holds, by
 * value:
 *
 * - the draft card's two acts: Save sends the text in the mailbox of the
 *   draft, and Send saves it and then sends that draft;
 * - the rule cards' write: the whole rule goes back with only `enabled`
 *   changed;
 * - what the rule and settings cards read from the tool's args (the rule
 *   create payload and the settings write), so the card shows what the
 *   tool wrote;
 * - `clipWords`, the client twin of the server's `clip_words`: a cut ends at
 *   a word, never inside one.
 *
 * The browser twin is `e2e/email-chat-cards.spec.ts`, whose payload half ran
 * green on `main` before the move.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/chat" }));

const api = vi.hoisted(() => ({
  saveDraftText: vi.fn(),
  sendDraft: vi.fn(),
  listRules: vi.fn(),
  updateRule: vi.fn(),
}));
vi.mock("@/app/email/lib/api", async (load) => ({ ...(await load<object>()), ...api }));

import {
  changedSettings,
  fmtVal,
  ruleActions,
  ruleConditions,
  sendDraftReply,
  setRuleEnabled,
} from "@/components/email/EmailToolCards";
import { clipWords } from "@/lib/fencedText";

const RULE = {
  id: "r1",
  accountId: "acct-1",
  name: "Invoices",
  enabled: true,
  automated: false,
  instructions: "Invoices from vendors",
  actions: [{ type: "LABEL", label: "Invoices" }],
};

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
});

describe("the draft card sends what it sent before", () => {
  it("Send saves the text, then sends the draft the save named", async () => {
    api.saveDraftText.mockResolvedValue({ created: true, id: "draft-1" });
    api.sendDraft.mockResolvedValue({ sent: true });
    expect(await sendDraftReply("acct-1", "msg-1", "Hello there")).toBe("sent");
    expect(api.saveDraftText.mock.calls).toEqual([["acct-1", "msg-1", "Hello there"]]);
    expect(api.sendDraft.mock.calls).toEqual([["acct-1", "draft-1"]]);
  });

  it("a save that names no draft sends nothing, and the draft stays in Drafts", async () => {
    api.saveDraftText.mockResolvedValue({ created: false });
    expect(await sendDraftReply("acct-1", "msg-1", "Hello")).toBe("saved");
    expect(api.sendDraft).not.toHaveBeenCalled();
  });

  it("a failed save sends nothing", async () => {
    api.saveDraftText.mockRejectedValue(new Error("502"));
    await expect(sendDraftReply("acct-1", "msg-1", "Hello")).rejects.toThrow("502");
    expect(api.sendDraft).not.toHaveBeenCalled();
  });
});

describe("the rule cards write the whole rule back", () => {
  it("Disable writes the rule with enabled off, and nothing else changed", async () => {
    api.listRules.mockResolvedValue([RULE, { ...RULE, id: "r2", name: "Other" }]);
    api.updateRule.mockResolvedValue(RULE);
    expect(await setRuleEnabled("acct-1", "r1", false)).toBe(true);
    expect(api.listRules.mock.calls).toEqual([["acct-1"]]);
    expect(api.updateRule.mock.calls).toEqual([["r1", { ...RULE, enabled: false }]]);
  });

  it("On writes it with enabled on", async () => {
    api.listRules.mockResolvedValue([{ ...RULE, enabled: false }]);
    expect(await setRuleEnabled("acct-1", "r1", true)).toBe(true);
    expect(api.updateRule.mock.calls).toEqual([["r1", { ...RULE, enabled: true }]]);
  });

  it("a rule that is not there writes nothing", async () => {
    api.listRules.mockResolvedValue([]);
    expect(await setRuleEnabled("acct-1", "r1", false)).toBe(false);
    expect(api.updateRule).not.toHaveBeenCalled();
  });
});

describe("the rule card shows what the tool created", () => {
  const ARGS = {
    name: "Vendor invoices",
    instructions: "Invoices and payment reminders from our vendors, and every reminder after it",
    from_pattern: "@vendor.example.com",
    subject_pattern: " ",
    action_type: "MOVE_FOLDER",
    label: "Finance",
    second_action_type: "MARK_READ",
    forward_to: "ap@example.com",
  };

  it("the When rows, whole: a long condition is not cut inside a word", () => {
    expect(ruleConditions(ARGS)).toEqual([
      { label: "AI", value: "Invoices and payment reminders from our vendors, and every reminder after it" },
      { label: "From", value: "@vendor.example.com" },
    ]);
  });

  it("the Then badges", () => {
    expect(ruleActions(ARGS)).toEqual(["MOVE FOLDER: Finance", "MARK READ", "FORWARD → ap@example.com"]);
    // create_rule labels by default.
    expect(ruleActions({ label: "Finance" })).toEqual(["LABEL: Finance"]);
  });
});

describe("the settings card shows what the tool wrote", () => {
  it("each known setting, in the order of the call, and never the mailbox", () => {
    expect(
      changedSettings({
        account_id: "acct-1",
        auto_run: true,
        digest_categories: [],
        signature: "Best, Ops",
        unknown_key: "x",
        draft_replies: null,
      }),
    ).toEqual([
      ["Auto-run rules", "on"],
      ["Digest categories", "all"],
      ["Signature", "Best, Ops"],
    ]);
  });

  it("a long value is cut at a word", () => {
    const v = fmtVal("Please keep every reply short and friendly, and sign it as Ops");
    expect(v).toBe("Please keep every reply short and…");
    expect(v.length).toBeLessThanOrEqual(40);
  });
});

describe("clipWords, the client twin of clip_words", () => {
  it("a text that fits comes back whole", () => {
    expect(clipWords("  short text  ", 40)).toBe("short text");
  });

  it("a long text ends at a word, with an ellipsis, within the limit", () => {
    const out = clipWords("The quick brown fox jumps over the lazy dog", 20);
    expect(out).toBe("The quick brown fox…");
    expect(out.length).toBeLessThanOrEqual(20);
  });

  it("a cut drops a comma or a dash before the ellipsis", () => {
    expect(clipWords("Alpha, beta, gamma, delta", 13)).toBe("Alpha, beta…");
  });

  it("one long token is cut at the limit", () => {
    expect(clipWords("x".repeat(30), 10)).toBe(`${"x".repeat(9)}…`);
  });
});
