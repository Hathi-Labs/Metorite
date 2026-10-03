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
// WS-17 EM-T8f-3 (§11.7.6) adds two fences here:
//   * `email-chat-picker-dot`: with two or more mailboxes, each mailbox option
//     carries the dot of `mailboxAccent()`, and All inboxes none. The picker
//     in `AgentChat.tsx` uses no raw palette class, and `/chat` sends no dot.
//   * `email-chat-removed-note`: when the mailbox of the scope leaves, the
//     chat shows one note that names it and the new scope.
//
// Vitest here runs in node and cannot render `EmailAssistantChat`. So the
// decisions are pure functions in `chatScope.ts`, tested by behaviour, and a
// source scan proves that the component wires them.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { mailboxAccent } from "../components/MailboxChip";
import {
  chatMailboxOptions,
  chatScope,
  chatSettingsRead,
  rememberChatScope,
  type ChatScopeMemory,
  type ChatScopePick,
} from "./chatScope";
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
    // EM-T8f-3 adds a dot to each mailbox option (`email-chat-picker-dot`).
    expect(chatMailboxOptions(both).map(({ id, label }) => ({ id, label }))).toEqual([
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
    expect(chatScope(both, ALL_INBOXES, { against: ALL_INBOXES, scope: "acc-home" }))
      .toEqual({ allInboxes: false, accountId: "acc-home", pickerId: "acc-home" });
    // The picker marks All inboxes, never the mailbox of the page (review F2).
    expect(chatScope(both, "acc-work", { against: "acc-work", scope: ALL_INBOXES }))
      .toEqual({ allInboxes: true, accountId: null, pickerId: ALL_INBOXES });
    // The page moved: the chat follows the page again.
    expect(chatScope(both, "acc-work", { against: ALL_INBOXES, scope: "acc-home" }).accountId).toBe("acc-work");
  });

  it("changes the chat, not the page", () => {
    const chat = codeOnly(read("components/EmailAssistantChat.tsx"));
    expect(chat).not.toMatch(/\bselectAccount\b/);
    expect(chat).not.toMatch(/\bselectAll\b/);
    expect(chat).toContain("(scope: string) => setScopePick({ against: pageScope ?? null, scope })");
    // The pick records the page scope of now, not of the first render (F3).
    expect(chat).toContain(
      "(scope: string) => setScopePick({ against: pageScope ?? null, scope }),\n    [pageScope],");
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
    // A read tool that needs one mailbox runs for each mailbox (review F4).
    expect(p).toMatch(/call it once for each mailbox, and never answer for all from one/);
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
    expect(chatScope(both, "acc-gone", null))
      .toEqual({ allInboxes: true, accountId: null, pickerId: ALL_INBOXES });
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
    // Review F1: the settings clear before the read, and a late read drops.
    const effect = chat.slice(chat.indexOf("const read = chatSettingsRead("));
    // The clear sits just before the read, after the All inboxes return.
    expect(effect).toContain(
      'setChatModel("tier-powerful");\n    setAcctSettings(null);\n    let cancelled = false;\n    read\n      .then(');
    expect(effect).toContain("if (cancelled) return;");
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

/** Tailwind's own palette, named: the shape of rule 5 of `conformance.test.ts`. */
const RAW_PALETTE =
  /\b(?:bg|text|border|ring|fill|stroke|from|via|to|shadow|outline|decoration|accent|caret)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950)\b/;

describe("email-chat-picker-dot", () => {
  const slotted = [{ ...work, colorSlot: 3 }, { ...home, colorSlot: 11 }];

  it("gives each mailbox option the dot of mailboxAccent(), and All inboxes none", () => {
    const options = chatMailboxOptions(slotted);
    expect(options[0]).toEqual({ id: ALL_INBOXES, label: "All inboxes" });
    expect(options[1].accent).toBe(mailboxAccent(slotted[0]).dot);
    expect(options[2].accent).toBe(mailboxAccent(slotted[1]).dot);
    expect(options.slice(1).map((o) => o.accent)).toEqual(["bg-cat-3", "bg-cat-11"]);
  });

  it("takes the hash of the id for a mailbox with no slot, still on the ramp", () => {
    const options = chatMailboxOptions(both);
    expect(options[1].accent).toBe(mailboxAccent({ id: "acc-work" }).dot);
    expect(options[2].accent).toBe(mailboxAccent({ id: "acc-home" }).dot);
    for (const o of options.slice(1)) expect(o.accent).toMatch(/^bg-cat-\d{1,2}$/);
  });

  it("gives one mailbox no dot, because the chips show for two or more (§11.0)", () => {
    expect(chatMailboxOptions([slotted[0]])).toEqual([{ id: "acc-work", label: "Fracktal · vj@fracktal.in" }]);
  });

  it("draws the dot beside the label in AgentChat, with no raw palette class", () => {
    const agent = codeOnly(read("../../components/AgentChat.tsx"));
    const start = agent.indexOf("{(mailboxes?.length ?? 0) > 0 && (");
    const end = agent.indexOf("{!lockModel && (", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    const picker = agent.slice(start, end);
    expect(picker).not.toMatch(RAW_PALETTE);
    expect(picker).not.toMatch(/#[0-9a-fA-F]{3,6}\b|\bstyle=\{/);
    expect(picker).toContain("{mb.accent && (");
    expect(picker).toContain("className={`h-1.5 w-1.5 shrink-0 rounded-full ${mb.accent}`}");
    expect(picker).toContain("{activeMailbox?.accent ? (");
    // The mark of the option in force is the house token for "on".
    expect(picker).toContain('<span className="text-primary text-[10px] shrink-0">✓</span>');
    // The prop stays optional, so another caller draws the picker as before.
    expect(agent).toContain("mailboxes?: { id: string; label: string; accent?: string }[];");
  });

  it("leaves /chat with no dot, so its picker looks the same", () => {
    const chatPage = codeOnly(read("../chat/page.tsx"));
    const start = chatPage.indexOf("const emailMailboxOptions = useMemo(");
    const options = chatPage.slice(start, chatPage.indexOf("[emailAccounts],", start));
    expect(start).toBeGreaterThan(-1);
    expect(options).not.toMatch(/\baccent\b/);
    expect(chatPage).not.toMatch(/\bnotice=/);
  });

  it("passes the options with their dots to the picker", () => {
    const chat = codeOnly(read("components/EmailAssistantChat.tsx"));
    expect(chat).toContain("const mailboxOptions = useMemo(() => chatMailboxOptions(accounts), [accounts]);");
    expect(chat).toContain("mailboxes={mailboxOptions}");
  });
});

describe("email-chat-removed-note", () => {
  const sales = { id: "acc-sales", emailAddress: "sales@fracktal.in", displayLabel: "Sales", isDefault: false };
  const three = [work, home, sales];
  /** One render: the scope from `chatScope`, then the memory step. */
  const step = (
    prev: ChatScopeMemory | null,
    accounts: typeof three,
    page: string | null,
    pick: ChatScopePick | null,
  ) => rememberChatScope(prev, accounts, chatScope(accounts, page, pick).pickerId);
  const onHome = { against: ALL_INBOXES, scope: "acc-home" };

  it("names the removed mailbox and All inboxes, in one note", () => {
    const before = step(null, three, ALL_INBOXES, onHome);
    expect(before).toEqual({ pickerId: "acc-home", name: "Personal · vj@outlook.com", note: null });
    const left = [work, sales];
    const after = step(before, left, ALL_INBOXES, onHome);
    expect(after.pickerId).toBe(ALL_INBOXES);
    expect(after.note).toBe("Personal · vj@outlook.com is no longer connected. The chat now uses All inboxes.");
    // The next render gives back the same memory, so the note shows once and
    // the update in the component stops.
    expect(step(after, left, ALL_INBOXES, onHome)).toBe(after);
  });

  it("names the mailbox that the page moved to", () => {
    const before = step(null, both, "acc-home", null);
    // A disconnect of the mailbox in view moves the page to the default.
    const after = step(before, [work], "acc-work", null);
    expect(after.note).toBe(
      "Personal · vj@outlook.com is no longer connected. The chat now uses Fracktal · vj@fracktal.in.",
    );
  });

  it("says so when no mailbox is left", () => {
    const before = step(null, [home], "acc-home", null);
    expect(step(before, [], null, null).note).toBe(
      "Personal · vj@outlook.com is no longer connected. No mailbox is connected.",
    );
  });

  it("gives no note for a pick, and a pick clears the note", () => {
    const start = step(null, three, ALL_INBOXES, null);
    const picked = step(start, three, ALL_INBOXES, onHome);
    expect(picked.note).toBeNull();
    const noted = step(picked, [work, sales], ALL_INBOXES, onHome);
    expect(noted.note).not.toBeNull();
    const again = step(noted, [work, sales], ALL_INBOXES, { against: ALL_INBOXES, scope: "acc-work" });
    expect(again.note).toBeNull();
    // A move away from a mailbox that is still connected is not a removal:
    // a pick, or the page that moves to another mailbox.
    const onWork = step(start, three, "acc-work", null);
    expect(onWork.pickerId).toBe("acc-work");
    expect(step(onWork, three, "acc-work", { against: "acc-work", scope: "acc-home" }).note).toBeNull();
    expect(step(onWork, three, "acc-sales", null).note).toBeNull();
  });

  it("gives no note when All inboxes ends because one mailbox is left", () => {
    const before = step(null, both, ALL_INBOXES, null);
    const after = step(before, [work], ALL_INBOXES, null);
    expect(after.pickerId).toBe("acc-work");
    expect(after.note).toBeNull();
  });

  it("names the removed mailbox by its last name, and a rename keeps the note", () => {
    const before = step(null, both, "acc-home", null);
    const renamed = step(before, [work, { ...home, displayLabel: "Home" }], "acc-home", null);
    expect(renamed.name).toBe("Home · vj@outlook.com");
    expect(renamed.note).toBeNull();
    const after = step(renamed, [work], "acc-work", null);
    expect(after.note).toBe("Home · vj@outlook.com is no longer connected. The chat now uses Fracktal · vj@fracktal.in.");
    const workRenamed = step(after, [{ ...work, displayLabel: "Works" }], "acc-work", null);
    expect(workRenamed.name).toBe("Works · vj@fracktal.in");
    expect(workRenamed.note).toBe(after.note);
  });

  it("holds the memory in the chat, and draws one note through AgentChat", () => {
    const chat = codeOnly(read("components/EmailAssistantChat.tsx"));
    expect(chat).toContain("const nextScopeMemory = rememberChatScope(scopeMemory, accounts, pickerId);");
    expect(chat).toContain("if (nextScopeMemory !== scopeMemory) setScopeMemory(nextScopeMemory);");
    expect(chat).toContain("onDismiss: () => setScopeMemory((m) => (m ? { ...m, note: null } : m)),");
    expect(chat).toContain("notice={scopeNotice}");
    const agent = codeOnly(read("../../components/AgentChat.tsx"));
    expect(agent).toContain("notice?: { text: string; onDismiss?: () => void } | null;");
    const at = agent.indexOf("{notice && (");
    expect(at).toBeGreaterThan(-1);
    const block = agent.slice(at, agent.indexOf("{!canSend && (", at));
    expect(block).toContain('role="status"');
    expect(block).toContain("{notice.text}");
    expect(block).toContain('type="button"');
    expect(block).not.toMatch(RAW_PALETTE);
    for (const name of [...block.matchAll(/name="([A-Za-z0-9]+)"/g)].map((m) => m[1])) {
      expect(isKnownIcon(name), name).toBe(true);
    }
    expect(isKnownIcon("X")).toBe(true);
  });
});
