// WS-17 EM-T8e-3 — the chat scope, the UI half (§11.7.5 of
// `project-docs/specs/email_app_master_plan.md`, D-EM-23 and D-EM-24).
//
// R7 fences named here:
//   * `email-chat-scope-picker`: the picker offers All inboxes only with two or
//     more mailboxes, and names each mailbox as "label · address". It starts on
//     the scope of the page. A pick changes the chat, not the page.
//   * `email-chat-scope-persona`: in All inboxes, the persona holds no "Active
//     account", no default account_id and no settings of a mailbox. It lists
//     each mailbox as "label · address".
//   * `email-chat-scope-fallback`: a scope on a removed mailbox gives All
//     inboxes, or the only mailbox, by the rule of `pickInitialView`.
//   * `email-chat-keeps-all-inboxes`: the move of EM-T8d skips the chat, and
//     the page passes the scope of the chat, never the hidden mailbox.
//   * `email-chat-scope-no-hidden-mailbox`: in All inboxes,
//     `emailContext.accountId` is null and no `getAssistantSettings` call
//     runs. An open mail names its mailbox in the persona.
//
// Vitest here runs in node and cannot render `EmailAssistantChat`. So the
// decisions are pure functions in `chatScope.ts`, tested by behaviour, and a
// source scan proves that the component wires them.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { chatMailboxOptions, chatScope, chatSettingsRead } from "./chatScope";
import { buildEmailAssistantPersona, chatMailboxName } from "./emailAssistantPersona";
import { ALL_INBOXES } from "./emailStore";

const ROOT = join(__dirname, "..");
const read = (rel: string) =>
  readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const work = { id: "acc-work", emailAddress: "vj@fracktal.in", displayLabel: "Fracktal", isDefault: true };
const home = { id: "acc-home", emailAddress: "vj@outlook.com", displayLabel: "Personal", isDefault: false };
const both = [work, home];
const SETTINGS = {
  about: "I am the CTO of Fracktal Works.",
  personal_instructions: "Never promise delivery dates.",
  writing_style: "Short, direct, no filler.",
  learned_writing_style: "Tends to drop pleasantries.",
};
const count = (text: string, part: string) => text.split(part).length - 1;

describe("email-chat-scope-picker", () => {
  it("offers All inboxes first, and only for two or more mailboxes", () => {
    expect(chatMailboxOptions([work])).toEqual([
      { id: "acc-work", label: "Fracktal · vj@fracktal.in" },
    ]);
    expect(chatMailboxOptions(both)).toEqual([
      { id: ALL_INBOXES, label: "All inboxes" },
      { id: "acc-work", label: "Fracktal · vj@fracktal.in" },
      { id: "acc-home", label: "Personal · vj@outlook.com" },
    ]);
  });

  it("names a mailbox by label and address, never by the raw label (MB-15)", () => {
    // No display label: the address shows once.
    expect(chatMailboxName({ id: "x", emailAddress: "sales@x.test" })).toBe("sales@x.test");
    // Two Outlook mailboxes share the raw label "Outlook". It never shows.
    expect(chatMailboxName({ id: "x", label: "Outlook", emailAddress: "a@x.test" })).toBe("a@x.test");
    // The gateway shape, which the chat app passes.
    expect(chatMailboxName({ id: "x", label: "Outlook", email_address: "a@x.test", display_label: "Fracktal" }))
      .toBe("Fracktal · a@x.test");
  });

  it("starts on the scope of the page", () => {
    expect(chatScope(both, ALL_INBOXES, null)).toEqual({ allInboxes: true, accountId: null, pickerId: ALL_INBOXES });
    expect(chatScope(both, "acc-home", null)).toEqual({ allInboxes: false, accountId: "acc-home", pickerId: "acc-home" });
  });

  it("holds a pick while the page scope it was made on stands", () => {
    expect(chatScope(both, ALL_INBOXES, { against: ALL_INBOXES, scope: "acc-home" }).accountId).toBe("acc-home");
    expect(chatScope(both, "acc-work", { against: "acc-work", scope: ALL_INBOXES }).allInboxes).toBe(true);
    // The page moved: the chat follows the page again.
    expect(chatScope(both, "acc-work", { against: ALL_INBOXES, scope: "acc-home" }).accountId).toBe("acc-work");
  });

  it("changes the chat, not the page", () => {
    const chat = codeOnly(read("components/EmailAssistantChat.tsx"));
    expect(chat).not.toMatch(/\bselectAccount\b/);
    expect(chat).not.toMatch(/\bselectAll\b/);
    expect(chat).toContain("(scope: string) => setScopePick({ against: pageScope ?? null, scope })");
    expect(chat).toContain("onMailboxChange={pickChatScope}");
    expect(chat).toContain("const mailboxOptions = useMemo(() => chatMailboxOptions(accounts), [accounts]);");
    expect(chat).toContain("activeMailboxId={pickerId}");
  });
});

describe("email-chat-scope-persona", () => {
  it("names no default mailbox and carries no settings in All inboxes", () => {
    const p = buildEmailAssistantPersona({
      accounts: both,
      allInboxes: true,
      // Both are ignored in All inboxes, so a stale value cannot leak in.
      selectedAccountId: "acc-work",
      settings: SETTINGS,
    });
    expect(p).toContain("• Fracktal · vj@fracktal.in (account_id acc-work)");
    expect(p).toContain("• Personal · vj@outlook.com (account_id acc-home)");
    expect(p).toContain("Scope: All inboxes.");
    expect(p).toMatch(/Leave account_id out of a write act/);
    expect(p).not.toContain("Active account");
    expect(p).not.toMatch(/Use (this )?account_id/i);
    // Each id shows once, in its list line, so no id is a default.
    expect(count(p, "acc-work")).toBe(1);
    expect(count(p, "acc-home")).toBe(1);
    for (const v of Object.values(SETTINGS)) expect(p).not.toContain(v);
    expect(p).not.toMatch(/assistant configuration/i);
  });

  it("gives one mailbox its account_id and its settings", () => {
    const p = buildEmailAssistantPersona({ accounts: both, selectedAccountId: "acc-work", settings: SETTINGS });
    expect(p).toContain('Active account: "Fracktal · vj@fracktal.in" (account_id: acc-work)');
    expect(p).toContain("Never promise delivery dates.");
    expect(p).toContain("Short, direct, no filler.");
    expect(p).not.toContain("Scope: All inboxes");
  });

  it("names two Outlook mailboxes apart in the gateway shape (MB-15)", () => {
    const p = buildEmailAssistantPersona({
      accounts: [
        { id: "a", label: "Outlook", email_address: "a@x.test", display_label: "Fracktal" },
        { id: "b", label: "Outlook", email_address: "b@y.test", display_label: "Personal" },
      ],
      selectedAccountId: "a",
    });
    expect(p).toContain("• Fracktal · a@x.test (account_id a)");
    expect(p).toContain("• Personal · b@y.test (account_id b)");
    expect(p).not.toContain("Outlook");
  });

  it("leaves the chat app on one mailbox, with no change to it", () => {
    const chatPage = codeOnly(read("../chat/page.tsx"));
    expect(chatPage).toContain("buildEmailAssistantPersona({");
    expect(chatPage).not.toContain("allInboxes");
  });
});

describe("email-chat-scope-fallback", () => {
  it("falls back to All inboxes, or to the only mailbox", () => {
    expect(chatScope(both, "acc-gone", null).allInboxes).toBe(true);
    expect(chatScope([work], "acc-gone", null)).toEqual({ allInboxes: false, accountId: "acc-work", pickerId: "acc-work" });
    // A pick on a mailbox that the member then removes.
    expect(chatScope(both, ALL_INBOXES, { against: ALL_INBOXES, scope: "acc-gone" }).allInboxes).toBe(true);
    expect(chatScope([home], "acc-home", { against: "acc-home", scope: "acc-gone" }).accountId).toBe("acc-home");
    // All inboxes with one mailbox left is that mailbox.
    expect(chatScope([home], ALL_INBOXES, null).accountId).toBe("acc-home");
    expect(chatScope([], ALL_INBOXES, null)).toEqual({ allInboxes: false, accountId: null, pickerId: null });
  });

  it("uses the rule of pickInitialView, not a second rule", () => {
    const src = codeOnly(read("lib/chatScope.ts"));
    expect(src).toContain("const view = pickInitialView(accounts, preferred);");
  });
});

describe("email-chat-keeps-all-inboxes", () => {
  const page = codeOnly(read("page.tsx"));

  it("skips the chat in the move of EM-T8d, and automation still moves", () => {
    expect(page).toContain(
      'if (!automationFeature || automationFeature === "chat" || !viewAll) return;');
    expect(page).toContain("st.selectAccount(target);");
  });

  it("passes the scope of the chat, never the hidden mailbox", () => {
    const start = page.indexOf("<EmailAssistantChat");
    const mount = page.slice(start, page.indexOf("/>", start));
    expect(mount).toContain("pageScope={viewAll ? ALL_INBOXES : selectedAccountId}");
    expect(mount).not.toContain("selectedAccountId={");
  });
});

describe("email-chat-scope-no-hidden-mailbox", () => {
  it("reads no settings in All inboxes", () => {
    const spy = vi.fn(async (id: string) => id);
    expect(chatSettingsRead({ allInboxes: true, accountId: null }, spy)).toBeNull();
    expect(chatSettingsRead({ allInboxes: true, accountId: "acc-work" }, spy)).toBeNull();
    expect(spy).not.toHaveBeenCalled();
    expect(chatSettingsRead({ allInboxes: false, accountId: "acc-work" }, spy)).not.toBeNull();
    expect(spy).toHaveBeenCalledWith("acc-work");
  });

  it("gives the tool cards and the settings read no mailbox in All inboxes", () => {
    expect(chatScope(both, ALL_INBOXES, null).accountId).toBeNull();
    const chat = codeOnly(read("components/EmailAssistantChat.tsx"));
    expect(chat).toContain("} = chatScope(accounts, pageScope ?? null, scopePick);");
    expect(chat).toContain("emailContext={{ accountId: chatAccountId, emailId: selectedEmailId }}");
    // The settings read goes through chatSettingsRead, with the chat scope,
    // and nowhere else.
    expect(chat).toContain(
      "chatSettingsRead(\n      { allInboxes: chatAllInboxes, accountId: chatAccountId },\n      getAssistantSettings,");
    expect(chat).not.toMatch(/getAssistantSettings\(/);
    // The page's selectedAccountId never reaches the chat. The one name left is
    // the option of the persona builder, and it takes the chat mailbox.
    expect(chat.match(/\bselectedAccountId\b/g)).toEqual(["selectedAccountId"]);
    expect(chat).toContain("selectedAccountId: chatAccountId,");
    expect(chat).toContain("allInboxes: chatAllInboxes,");
  });

  it("names the mailbox of an open mail in both scopes", () => {
    const openEmail = {
      id: "msg-1",
      accountId: "acc-home",
      subject: "Quote request",
      from: { name: "Ayush", email: "ayush@x.test" },
    };
    const line = "mailbox: Personal · vj@outlook.com (account_id acc-home)";
    const all = buildEmailAssistantPersona({ accounts: both, allInboxes: true, openEmail });
    const one = buildEmailAssistantPersona({ accounts: both, selectedAccountId: "acc-work", openEmail });
    expect(all).toContain(line);
    expect(one).toContain(line);
    expect(all).toContain("msg-1");
  });
});
