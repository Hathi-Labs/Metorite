// WS-17 EM-T8d — All inboxes, the UI half (§11.4 and §11.7.4 of
// `project-docs/specs/email_app_master_plan.md`, decision D-EM-22).
//
// R7 fences named here:
//   * `email-all-initial-view`: two or more mailboxes and no stored choice open
//     All inboxes. "all" in the URL or storage opens it. A stored mailbox that
//     is gone falls back with no error. One mailbox never opens it.
//   * `email-all-scope`: in All inboxes, the list, search, paging, refresh and
//     facets read with no `account_id`, and `selectedAccountId` stays a real
//     mailbox.
//   * `email-all-rows`: each row names its mailbox with the chip.
//   * `email-all-sidebar`: the switcher draws "All inboxes" with the summed
//     unread count, a mailbox row is never selected with it, and the tree
//     draws only the folders every mailbox has, with no count.
//   * `email-all-limits`: All inboxes drops the label filter, offers no "load
//     older", keeps a mail that "Open in inbox" opens, and new mail goes from
//     the default mailbox.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AccountSidebar } from "../components/AccountSidebar";
import { ALL_INBOXES, listScope, pickInitialView } from "./emailStore";
import type { EmailAccount, EmailFolder } from "./types";

const ROOT = join(__dirname, "..");
const read = (rel: string) =>
  readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const a = { id: "a", isDefault: false };
const b = { id: "b", isDefault: true };

describe("the first view (§11.4)", () => {
  it("opens All inboxes for two or more mailboxes and no stored choice", () => {
    expect(pickInitialView([a, b], null)).toEqual({ accountId: "b", viewAll: true });
  });

  it("opens the stored scope", () => {
    expect(pickInitialView([a, b], ALL_INBOXES)).toEqual({ accountId: "b", viewAll: true });
    expect(pickInitialView([a, b], "a")).toEqual({ accountId: "a", viewAll: false });
  });

  it("falls back with no error when the stored mailbox is gone", () => {
    expect(pickInitialView([a, b], "gone")).toEqual({ accountId: "b", viewAll: true });
    expect(pickInitialView([a], "gone")).toEqual({ accountId: "a", viewAll: false });
  });

  it("never opens All inboxes for one mailbox, and nothing for none", () => {
    expect(pickInitialView([a], ALL_INBOXES)).toEqual({ accountId: "a", viewAll: false });
    expect(pickInitialView([a], null)).toEqual({ accountId: "a", viewAll: false });
    expect(pickInitialView([], null)).toEqual({ accountId: null, viewAll: false });
  });
});

describe("the scope of a read (D-EM-22)", () => {
  it("reads every mailbox in All inboxes, and one mailbox otherwise", () => {
    expect(listScope({ viewAll: true, selectedAccountId: "a" })).toBeUndefined();
    expect(listScope({ viewAll: false, selectedAccountId: "a" })).toBe("a");
    expect(listScope({ viewAll: false, selectedAccountId: null })).toBeUndefined();
  });

  it("is the scope of every list, search, paging and refresh read", () => {
    const store = codeOnly(read("lib/emailStore.ts"));
    expect((store.match(/accountId: listScope\(/g) ?? []).length).toBe(6);
    // The one read of a single mailbox is "load older", which returns early in
    // All inboxes (it pages one mailbox of the provider).
    const single = [...store.matchAll(/accountId: selectedAccountId,/g)].map((m) => m.index ?? 0);
    const backfill = store.indexOf("backfillOlder: async");
    const after = store.indexOf("\n  },\n", backfill);
    expect(single.length).toBe(1);
    expect(single[0]).toBeGreaterThan(backfill);
    expect(single[0]).toBeLessThan(after);
    expect(codeOnly(read("components/QuickFilters.tsx"))).toContain(
      "useEmailStore((s) => (s.viewAll ? null : s.selectedAccountId))");
  });
});

const box = (id: string, label: string, unread: number): EmailAccount => ({
  id,
  provider: "microsoft",
  emailAddress: `${id}@x.test`,
  label: "",
  displayLabel: label,
  colorSlot: 1,
  unreadCount: unread,
  syncEnabled: true,
});
const folders: EmailFolder[] = [
  { key: "inbox", label: "Inbox", count: 7, type: "system" } as EmailFolder,
  { key: "projects", label: "Projects", count: 3, type: "user" } as EmailFolder,
];

describe("the switcher (§11.4)", () => {
  const render = (viewAll: boolean, accounts: EmailAccount[]) =>
    renderToStaticMarkup(createElement(AccountSidebar, {
      accounts,
      selectedAccountId: "a",
      onAccountSelect: () => {},
      folders,
      selectedFolder: "inbox",
      onFolderSelect: () => {},
      viewAll,
      onSelectAll: () => {},
      showAutomation: false,
    }));

  it("draws All inboxes with the summed unread count", () => {
    const html = render(false, [box("a", "Fracktal", 2), box("b", "Personal", 3)]);
    expect(html).toContain("All inboxes");
    expect(html).toContain("2 mailboxes");
    expect(html).toContain(">5<");
  });

  it("draws no All inboxes for one mailbox", () => {
    expect(render(false, [box("a", "Fracktal", 2)])).not.toContain("All inboxes");
  });

  it("draws only the folders of every mailbox, with no count, in All inboxes", () => {
    const html = render(true, [box("a", "Fracktal", 2), box("b", "Personal", 3)]);
    expect(html).toContain(">Inbox<");
    expect(html).not.toContain(">Projects<");
    expect(html).not.toContain(">7<");
    expect(html).toContain('aria-pressed="true"');
  });
});

describe("All inboxes on the page and in the list", () => {
  const page = codeOnly(read("page.tsx"));
  const store = codeOnly(read("lib/emailStore.ts"));

  it("names the mailbox on each row", () => {
    const list = codeOnly(read("components/EmailList.tsx"));
    expect(list).toContain("viewAll && accounts.length > 1 ? accounts.find((a) => a.id === accountId)");
    expect(list).toContain("<MailboxChip account={box} />");
  });

  it("names the scope in the header and the mobile top bar", () => {
    expect(page).toContain("All inboxes · {accounts.length} mailboxes");
    expect(page).toContain("`All inboxes · ${accounts.length} mailboxes`");
  });

  it("drops the label filter, offers no load older, and keeps an opened mail", () => {
    const selectAll = store.slice(store.indexOf("selectAll: () => {"));
    expect(selectAll.slice(0, 900)).toContain("selectedLabel: null,");
    expect(store).toContain("if (backfilling || !selectedAccountId || get().viewAll) return;");
    expect(page).toMatch(/const canBackfillFolder =\s*!viewAll &&/);
    expect(store).toContain("if (!get().viewAll && email.accountId && email.accountId !== get().selectedAccountId");
  });

  it("sends new mail from the default mailbox", () => {
    expect(page).toContain("const defaultAccountId = accounts.find((a) => a.isDefault)?.id ?? null;");
  });

  it("stores the scope as all", () => {
    expect(store).toContain("persistAccountId(ALL_INBOXES);");
  });

  // Two P1s of the review: a row act ran in the selected mailbox, not in the
  // mailbox of the row (D-EM-19).
  it("runs the rule test in the mailbox of the row", () => {
    const list = codeOnly(read("components/EmailList.tsx"));
    expect(list).toContain("const accountId = email.accountId || selectedAccountId;");
    expect(list).toContain("runTestOnMessage(accountId, email.id, false)");
    expect(list).not.toContain("runTestOnMessage(selectedAccountId");
  });

  it("colours a label only in the mailbox whose colours the view shows", () => {
    const list = codeOnly(read("components/EmailList.tsx"));
    // Bulk keeps its swatches for one mailbox, and has none in All inboxes (F2).
    expect(list).toContain("? (viewAll ? null : selectedAccountId)");
    expect(list).toContain("(!viewAll || ctx.email.accountId === selectedAccountId ? ctx.email.accountId : null)");
    expect(list).toContain("setLabelColor(name, c, colorAccountId)");
    expect(list).not.toMatch(/setLabelColor\(name, c\)/);
  });

  // `email-all-automation-names-mailbox` (F4, F5): automation and the chat act
  // on one mailbox, so opening one leaves All inboxes for a mailbox the
  // switcher names, the mailbox of the open mail first.
  it("opens automation and the chat in a named mailbox", () => {
    expect(page).toContain("if (!automationFeature || !viewAll) return;");
    expect(page).toContain("const target = open?.accountId || defaultAccountId || st.selectedAccountId;");
    expect(page).toContain("st.selectAccount(target);");
  });
});
