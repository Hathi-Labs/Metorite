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
//
// WS-17 EM-T8f-3 (§11.7.6) adds two fences here:
//   * `email-all-folder-sums`: in All inboxes, each well-known folder shows
//     its sum over each mailbox, a failed read adds 0, and a custom folder
//     shows no count. `allInboxesStore.test.ts` holds the store half: the
//     reads of each mailbox, their bound, and the list that never waits.
//   * `email-import-panel-each`: one panel for each importing mailbox, the
//     mailbox in view first. With two or more mailboxes each panel names its
//     mailbox with the chip.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AccountSidebar } from "../components/AccountSidebar";
import { FirstSyncBanner } from "../components/FirstSyncBanner";
import { OnboardingPanel } from "../components/OnboardingPanel";
import {
  ALL_INBOXES,
  SUMMED_FOLDERS,
  allInboxesFolders,
  listScope,
  pickInitialView,
  sumFolderCounts,
} from "./emailStore";
import { firstSyncPanels, importProgress } from "./onboarding";
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

  it("draws only the folders of every mailbox, with no count before the sums land", () => {
    const html = render(true, [box("a", "Fracktal", 2), box("b", "Personal", 3)]);
    expect(html).toContain(">Inbox<");
    expect(html).not.toContain(">Projects<");
    // The 7 is the count of ONE mailbox. It never reads as the sum.
    expect(html).not.toContain(">7<");
    expect(html).toContain('aria-pressed="true"');
  });
});

describe("email-all-folder-sums", () => {
  const tree = (counts: Record<string, number>, extra: EmailFolder[] = []): EmailFolder[] => [
    ...SUMMED_FOLDERS.map((key) => ({ icon: "Inbox", key, label: key, count: counts[key] ?? 0, type: "system" as const })),
    ...extra,
  ];
  const custom = { icon: "Folder", key: "projects", label: "Projects", count: 9, type: "user" } as EmailFolder;

  it("names the six well-known folders of §11.4", () => {
    expect([...SUMMED_FOLDERS]).toEqual(["inbox", "drafts", "sent", "archive", "junk", "trash"]);
  });

  it("sums each well-known folder over each mailbox", () => {
    const sums = sumFolderCounts([
      tree({ inbox: 7, sent: 2, trash: 1 }, [custom]),
      tree({ inbox: 5, drafts: 1, archive: 4, junk: 3 }),
    ]);
    expect(sums).toEqual({ inbox: 12, drafts: 1, sent: 2, archive: 4, junk: 3, trash: 1 });
  });

  it("adds 0 for a mailbox whose read failed", () => {
    expect(sumFolderCounts([tree({ inbox: 7 }), null]).inbox).toBe(7);
    expect(sumFolderCounts([null, null])).toEqual({ inbox: 0, drafts: 0, sent: 0, archive: 0, junk: 0, trash: 0 });
  });

  it("sums no other folder, not even a key that an object already has", () => {
    const sums = sumFolderCounts([[
      { key: "projects", count: 9 },
      { key: "constructor", count: 3 },
      { key: "all", count: 40 },
      { key: "starred", count: 2 },
    ]]);
    expect(Object.keys(sums).sort()).toEqual([...SUMMED_FOLDERS].sort());
    expect(Object.values(sums).every((n) => n === 0)).toBe(true);
  });

  it("gives each well-known folder its sum, and each other folder no count", () => {
    const shown = allInboxesFolders(
      [{ icon: "Mails", key: "all", label: "All", count: 40, type: "system" } as EmailFolder, ...tree({ inbox: 7 }), custom],
      { inbox: 12, drafts: 1, sent: 2, archive: 4, junk: 3, trash: 1 },
    );
    expect(shown.map((f) => [f.key, f.count])).toEqual([
      ["all", 0], ["inbox", 12], ["drafts", 1], ["sent", 2], ["archive", 4], ["junk", 3], ["trash", 1],
    ]);
  });

  it("gives no count before the sums land", () => {
    expect(allInboxesFolders(tree({ inbox: 7, sent: 2 }), null).every((f) => f.count === 0)).toBe(true);
  });

  it("draws the sums in the switcher, and the page passes them to both", () => {
    const html = renderToStaticMarkup(createElement(AccountSidebar, {
      accounts: [box("a", "Fracktal", 2), box("b", "Personal", 3)],
      selectedAccountId: "a",
      onAccountSelect: () => {},
      folders: [...tree({ inbox: 7 }), custom],
      folderSums: { inbox: 12, drafts: 0, sent: 5, archive: 0, junk: 0, trash: 0 },
      selectedFolder: "drafts",
      onFolderSelect: () => {},
      viewAll: true,
      onSelectAll: () => {},
      showAutomation: false,
    }));
    expect(html).toContain(">12<");
    expect(html).toContain(">5<");
    expect(html).not.toContain(">7<");
    expect(html).not.toContain(">Projects<");
    // One mailbox in view draws its own tree, with its own counts.
    const one = renderToStaticMarkup(createElement(AccountSidebar, {
      accounts: [box("a", "Fracktal", 2), box("b", "Personal", 3)],
      selectedAccountId: "a",
      onAccountSelect: () => {},
      folders: [...tree({ inbox: 7 }), custom],
      folderSums: { inbox: 12, drafts: 0, sent: 5, archive: 0, junk: 0, trash: 0 },
      selectedFolder: "drafts",
      onFolderSelect: () => {},
      viewAll: false,
      onSelectAll: () => {},
      showAutomation: false,
    }));
    expect(one).toContain(">7<");
    expect(one).toContain(">Projects<");
    expect(one).not.toContain(">12<");
    const page = codeOnly(read("page.tsx"));
    expect(page.match(/folderSums=\{allFolderCounts\}/g)).toHaveLength(2);
  });
});

describe("email-import-panel-each", () => {
  const importing = (id: string, extra: Partial<EmailAccount> = {}): EmailAccount => ({
    ...box(id, id.toUpperCase(), 0),
    importSince: "2026-07-01T00:00:00Z",
    onboardingDone: false,
    syncStatus: "syncing",
    initialSyncDone: false,
    importPhase: "importing",
    ...extra,
  });
  const done = (id: string): EmailAccount => ({ ...box(id, id.toUpperCase(), 0), initialSyncDone: true });

  it("gives one panel for each importing mailbox, the mailbox in view first", () => {
    const accounts = [importing("a"), done("b"), importing("c"), importing("d", { importPhase: null })];
    const panels = firstSyncPanels(accounts, "c");
    expect(panels.map((p) => [p.account.id, p.surface])).toEqual([
      ["c", "progress"], ["a", "progress"], ["d", "banner"],
    ]);
    // All inboxes has no mailbox in view: the order of the list.
    expect(firstSyncPanels(accounts, null).map((p) => p.account.id)).toEqual(["a", "c", "d"]);
  });

  it("gives no panel to an errored or paused mailbox", () => {
    const accounts = [importing("a", { syncStatus: "error" }), importing("b", { syncEnabled: false }), importing("c")];
    expect(firstSyncPanels(accounts, "a").map((p) => p.account.id)).toEqual(["c"]);
  });

  it("names each panel with two or more mailboxes, and changes nothing for one", () => {
    expect(firstSyncPanels([importing("a"), importing("b")], null).map((p) => p.named)).toEqual([true, true]);
    expect(firstSyncPanels([importing("a"), done("b")], null).map((p) => p.named)).toEqual([true]);
    expect(firstSyncPanels([importing("a")], null).map((p) => p.named)).toEqual([false]);
  });

  it("draws the chip and the address of its mailbox on each surface", () => {
    const a = importing("a", { displayLabel: "Fracktal", emailAddress: "vj@fracktal.in" });
    const progress = importProgress(a, { now: new Date(2026, 9, 3) });
    const panel = renderToStaticMarkup(createElement(OnboardingPanel, { address: a.emailAddress, progress, mailbox: a }));
    const banner = renderToStaticMarkup(createElement(FirstSyncBanner, { address: a.emailAddress, mailbox: a }));
    for (const html of [panel, banner]) {
      expect(html).toContain('aria-label="Mailbox Fracktal, vj@fracktal.in"');
      expect(html).toContain("Connected as vj@fracktal.in");
    }
    expect(panel).toContain('aria-label="Mailbox import, vj@fracktal.in"');
    // One mailbox: no chip, the same panel as before.
    const plain = renderToStaticMarkup(createElement(OnboardingPanel, { address: a.emailAddress, progress }));
    expect(plain).not.toContain("Mailbox Fracktal");
    expect(plain).toContain('aria-label="Mailbox import"');
    expect(renderToStaticMarkup(createElement(FirstSyncBanner, { address: a.emailAddress }))).not.toContain("Mailbox Fracktal");
  });

  it("draws each panel on the page, keyed and named by its mailbox", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain("const importPanels = firstSyncPanels(accounts, viewAll ? null : selectedAccountId);");
    expect(page).toContain("{importPanels.map(({ account, surface, named }) =>");
    expect(page.match(/key=\{account\.id\}/g)).toHaveLength(2);
    expect(page.match(/mailbox=\{named \? account : undefined\}/g)).toHaveLength(2);
    // No single pending mailbox is left on the page.
    expect(page).not.toMatch(/\bpendingAccount\b/);
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

  // `email-all-automation-names-mailbox` (F4, F5): automation acts on one
  // mailbox, so opening it leaves All inboxes for a mailbox the switcher
  // names, the mailbox of the open mail first. EM-T8e-3 rewrote the chat half:
  // the chat has its own All inboxes scope, so the move skips it
  // (`email-chat-keeps-all-inboxes` in `chatScope.test.ts`).
  it("opens automation in a named mailbox", () => {
    expect(page).toContain(
      'if (!automationFeature || automationFeature === "chat" || !viewAll) return;');
    expect(page).toContain("const target = open?.accountId || defaultAccountId || st.selectedAccountId;");
    expect(page).toContain("st.selectAccount(target);");
  });
});
