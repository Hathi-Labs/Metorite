// Multi-inbox UI, item 2: "Copy rules from..." on the Rules tab, with a
// preview and a question first. Fence `email-rules-tab-copy`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { copyRulesPreview, rulesStepCopyChoices, type CopyTarget } from "./mailboxSettings";

const read = (rel: string) =>
  readFileSync(join(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");

describe("email-rules-tab-copy", () => {
  it("names the rules that will copy, and the ones left out as off", () => {
    const p = copyRulesPreview(
      [
        { name: "Needs Reply", enabled: true },
        { name: "Old promo", enabled: false },
        { name: "Receipts", enabled: true },
      ],
      "Work",
    );
    expect(p.willCopy).toEqual(["Needs Reply", "Receipts"]);
    expect(p.leftOut).toEqual([{ name: "Old promo", why: "It is off in Work." }]);
    expect(p.question).toBe("Copy 2 rules from Work into this mailbox?");
  });

  it("says plainly when nothing will copy", () => {
    const p = copyRulesPreview([], "Work");
    expect(p.willCopy).toEqual([]);
    expect(p.question).toBe("Work has no rules to copy.");
    expect(copyRulesPreview([{ name: "A", enabled: true }], "Work").question)
      .toBe("Copy 1 rule from Work into this mailbox?");
  });

  it("offers each other mailbox as a source, never the target", () => {
    const accounts = [
      { id: "a", emailAddress: "a@x.test", displayLabel: "Work" },
      { id: "b", emailAddress: "b@x.test", displayLabel: "Home" },
    ];
    expect(rulesStepCopyChoices(accounts, "a").map((c) => c.id)).toEqual(["b"]);
  });

  it("is wired into the Rules tab, through the shared Modal and the copy guard", () => {
    const tab = read("components/automation/ai-settings/RulesTab.tsx");
    expect(tab).toContain("CopyRulesDialog");
    expect(tab).toContain("Copy rules from");
    const dialog = read("components/automation/ai-settings/CopyRulesDialog.tsx");
    expect(dialog).toContain('from "@/components/ui/Modal"');
    expect(dialog).not.toContain("../ui");
    expect(dialog).toContain("ruleCopier(");
    expect(dialog).toContain("copyReport(");
    expect(dialog).toContain("copyRulesPreview(");
  });
});

// The preview mirrors `_left_out_reason` of `rule_copy.py`, reason by reason.
describe("email-rules-tab-copy preview mirrors the route", () => {
  const target = (over: Partial<CopyTarget> = {}): CopyTarget => ({
    rules: [],
    ownAddresses: ["me@work.test", "me@home.test"],
    provider: "microsoft",
    ...over,
  });
  const rule = (name: string, actions: { type: string; label?: string; to_address?: string }[] = [], extra = {}) => ({
    name,
    enabled: true,
    system_type: null,
    actions,
    ...extra,
  });
  const run = (rules: ReturnType<typeof rule>[], t: CopyTarget) =>
    copyRulesPreview(rules as never, "Work", t);

  it("leaves out a forward to an own address, any case, in Cc too", () => {
    const p = run(
      [
        rule("Fwd", [{ type: "FORWARD", to_address: "Me@Home.test" }]),
        rule("Out", [{ type: "FORWARD", to_address: "x@other.test" }]),
      ],
      target(),
    );
    expect(p.willCopy).toEqual(["Out"]);
    expect(p.leftOut[0].name).toBe("Fwd");
    expect(p.leftOut[0].why).toContain("loop");
  });

  it("leaves out a rule with a folder or label when the target is IMAP", () => {
    const p = run(
      [rule("Move", [{ type: "MOVE_FOLDER", label: "Bills" }]), rule("Tag", [{ type: "LABEL", label: "x" }]), rule("Plain", [{ type: "ARCHIVE" }])],
      target({ provider: "imap" }),
    );
    expect(p.willCopy).toEqual(["Plain"]);
    expect(p.leftOut.map((r) => r.name)).toEqual(["Move", "Tag"]);
  });

  it("leaves out a move to Sent or Drafts for every provider", () => {
    const p = run([rule("ToSent", [{ type: "MOVE_FOLDER", label: "Sent Items" }]), rule("ToDraft", [{ type: "MOVE_FOLDER", label: "draft" }])], target());
    expect(p.willCopy).toEqual([]);
    expect(p.leftOut).toHaveLength(2);
  });

  it("leaves out a Gmail move to a system label, and keeps a user label and Trash", () => {
    const gmail = target({ provider: "gmail" });
    const p = run(
      [
        rule("Star", [{ type: "MOVE_FOLDER", label: "Starred" }]),
        rule("Cat", [{ type: "MOVE_FOLDER", label: "category_promotions" }]),
        rule("User", [{ type: "MOVE_FOLDER", label: "Bills" }]),
        rule("Bin", [{ type: "MOVE_FOLDER", label: "Trash" }]),
        rule("Empty", [{ type: "MOVE_FOLDER", label: "  " }]),
      ],
      gmail,
    );
    expect(p.willCopy).toEqual(["User", "Bin", "Empty"]);
    expect(p.leftOut.map((r) => r.name)).toEqual(["Star", "Cat"]);
    // Outlook keeps "Starred" as a user folder name.
    expect(run([rule("Star", [{ type: "MOVE_FOLDER", label: "Starred" }])], target()).willCopy).toEqual(["Star"]);
  });

  it("leaves out a reply rule when the target has one, and keeps its name", () => {
    const t = target({ rules: [{ name: "Reply", system_type: "REPLY" }] });
    const p = run([rule("Needs Reply"), rule("Other")], t);
    expect(p.willCopy).toEqual(["Other"]);
    expect(p.leftOut[0].name).toBe("Needs Reply");
    expect(p.leftOut[0].why).toContain("reply rule");
  });

  it("copies only the first reply rule of the source", () => {
    const p = run([rule("Needs Reply"), rule("X", [], { system_type: "TO_REPLY" })], target());
    expect(p.willCopy).toEqual(["Needs Reply"]);
    expect(p.leftOut.map((r) => r.name)).toEqual(["X"]);
  });

  it("leaves out a rule the target holds, by name or as a copy, and lists its name to skip", () => {
    const t = target({ rules: [{ name: "receipts", system_type: null }, { name: "Alerts (copy 2)", system_type: null }] });
    const p = run([rule("Receipts"), rule("Alerts"), rule("New")], t);
    expect(p.willCopy).toEqual(["New"]);
    expect(p.leftOut.map((r) => r.name)).toEqual(["Receipts", "Alerts"]);
    expect(p.leftOut[0].why).toContain("already");
    // The dialog sends these names, and the route copies the rest.
    expect(p.alreadyHere).toEqual(["Receipts", "Alerts"]);
    expect(run([rule("New")], t).alreadyHere).toEqual([]);
    expect("blocked" in p).toBe(false);
  });

  it("still offers the rest when every preset name is shared", () => {
    const t = target({ rules: [{ name: "Newsletters", system_type: null }] });
    const p = run([rule("Newsletters"), rule("Vendors")], t);
    expect(p.willCopy).toEqual(["Vendors"]);
    expect(p.question).toBe("Copy 1 rule from Work into this mailbox?");
  });

  it("counts only what the route copies in the question", () => {
    const t = target({ provider: "imap" });
    const p = run([rule("A"), rule("B", [{ type: "LABEL", label: "x" }]), rule("C", [], { enabled: false })], t);
    expect(p.question).toBe("Copy 1 rule from Work into this mailbox?");
  });

  it("works with no target context, as before", () => {
    expect(copyRulesPreview([{ name: "A", enabled: true }], "Work").willCopy).toEqual(["A"]);
  });
});

describe("email-rules-tab-copy sends the held names", () => {
  it("copyRules posts skip_names, capped, only when there are some", async () => {
    const api = readFileSync(join(__dirname, "api.ts"), "utf-8").replace(/\r\n/g, "\n");
    expect(api).toContain("skip_names: skipNames.slice(0, 200)");
    const dialog = read("components/automation/ai-settings/CopyRulesDialog.tsx");
    expect(dialog).toContain("copyRules(from, to, skipRef.current)");
    expect(dialog).toContain("skipRef.current = preview?.alreadyHere");
    expect(dialog).not.toContain("blocked");
  });
});
