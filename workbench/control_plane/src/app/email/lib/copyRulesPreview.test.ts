// Multi-inbox UI, item 2: "Copy rules from..." on the Rules tab, with a
// preview and a question first. Fence `email-rules-tab-copy`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { copyRulesPreview, rulesStepCopyChoices } from "./mailboxSettings";

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
