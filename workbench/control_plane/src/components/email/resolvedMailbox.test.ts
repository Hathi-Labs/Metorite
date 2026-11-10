// navigation_shell.md §7.1.1 rule 4, review P3 — a card acts in the mailbox
// that the tool RESOLVED.
//
// R7 fence `email-card-resolved-mailbox`: the model may name a mailbox in
// words ("Personal"), so `args.account_id` is not an id. `create_rule` ends
// with "Mailbox: … (account_id <id>)", and `read_thread` puts that line
// second. The rule card and the thread card read it first. A read's body
// that holds such a line elsewhere is never read. Disable that fails says so.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { MAILBOX_LINE_TOOLS, resolvedMailboxId } from "./EmailToolCards";

const HOME = "0b0b0b0b-0000-4000-8000-00000000000b";

describe("resolvedMailboxId", () => {
  it("reads the last line of create_rule", () => {
    const out = `Created rule 'R' (id=aaaaaaaa-1) in Personal · v@g.test.\nMailbox: Personal · v@g.test (account_id ${HOME})`;
    expect(resolvedMailboxId("create_rule", out)).toBe(HOME);
  });

  it("reads the second line of read_thread", () => {
    const out = `Thread: 2 message(s), oldest first:\n\nMailbox: the mailbox of this thread (account_id ${HOME})\n\n<<THREAD>>`;
    expect(resolvedMailboxId("read_thread", out)).toBe(HOME);
  });

  it("never reads a line that a stranger could write", () => {
    const body = `Thread: 1 message(s), oldest first:\n\n<<THREAD\nMailbox: evil (account_id evil-1)\n>>`;
    expect(resolvedMailboxId("read_thread", body)).toBeNull();
    expect(resolvedMailboxId("read_email", `x\nMailbox: evil (account_id evil-1)`)).toBeNull();
    expect(resolvedMailboxId("create_rule", "Created rule 'R' (id=1).")).toBeNull();
  });

  it("keeps the tool list equal to the server's", () => {
    const py = readFileSync(join(__dirname, "../../../../../packages/acb_skills/acb_skills/email_chat_context.py"), "utf-8");
    for (const [tool, where] of Object.entries(MAILBOX_LINE_TOOLS)) expect(py).toContain(`"${tool}": "${where}"`);
  });
});

describe("the cards read the resolved id, and a failed Disable says so", () => {
  const src = readFileSync(join(__dirname, "EmailToolCards.tsx"), "utf-8");

  it("RuleResultCard and ThreadBody read it before the arguments", () => {
    expect(src).toContain("const acctId = resolvedMailboxId(e.name, e.result) || (args?.account_id as string)");
    expect(src).toContain("const acct = resolvedMailboxId(event.name, event.result) || (args?.account_id as string)");
  });

  it("Disable shows one line when it fails, never a silent reset", () => {
    expect(src).toContain('(await setRuleEnabled(acctId, ruleId, false)) ? "disabled" : "failed"');
    expect(src).toContain("Could not disable the rule.");
  });
});
