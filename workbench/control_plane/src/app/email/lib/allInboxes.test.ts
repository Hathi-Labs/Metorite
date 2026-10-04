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
//
// WS-17 EM-T8g-2 (§11.7.7) adds two fences here:
//   * `email-all-skips-separate`: with one separate mailbox among three, the
//     All inboxes row, its unread sum, the header count, `scopeBusy` and
//     `pickInitialView` read only the pooled mailboxes. All inboxes shows for
//     two or more of them. `pooledMailboxes` is the one rule, and no other
//     file reads the flag. `allInboxesStore.test.ts` holds the store half:
//     the folder sums and `syncScope`.
//   * `email-separate-menu`: the menu offers "Keep separate", or "Show in All
//     inboxes" for a separate mailbox. A pick sends the `PATCH`, and the
//     switcher row of a separate mailbox shows the word "Separate".
//
// WS-17 EM-T6e (§10.4.7, D3) adds two fences here:
//   * `email-storage-mailbox` (A5, A6): the storage notice names the mailbox
//     in view, or in All inboxes the first POOLED mailbox at the limit. It
//     never names a separate mailbox there. The reconnect banner and the
//     storage step win over it, and the dialog takes the id of the mailbox
//     that the notice names, never `poolHome`.
//   * `email-storage-switcher` (UC-12): the switcher marks each mailbox at
//     the limit, a separate one too.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { AccountSidebar, accountMenuItems } from "../components/AccountSidebar";
import { FirstSyncBanner } from "../components/FirstSyncBanner";
import { OnboardingPanel } from "../components/OnboardingPanel";
import { RemoveOlderMailDialog } from "../components/RemoveOlderMailDialog";
import {
  ALL_INBOXES,
  SUMMED_FOLDERS,
  allInboxesFolders,
  checkedRows,
  listScope,
  pickInitialView,
  scopeBusy,
  sumFolderCounts,
} from "./emailStore";
import {
  attentionMailbox,
  hasAllInboxes,
  isSeparate,
  mailboxToOpen,
  poolHome,
  pooledMailboxes,
  removalMailbox,
  separateMark,
  separateToggle,
  storageMailbox,
} from "./mailbox";
import { firstSyncPanels, importProgress } from "./onboarding";
import type { EmailAccount, EmailFolder } from "./types";

const ROOT = join(__dirname, "..");
const read = (rel: string) =>
  readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

/**
 * The source with its comments removed and each string kept (EM-T8g-2 review
 * round 2). `codeOnly` cuts a line at a `//` inside a string, so it can hide a
 * read after it. This walks the text, skips each string literal whole, and
 * drops only a real comment. A regex literal is not parsed. A misread one
 * keeps more text, so the scan fails closed.
 */
function stripComments(src: string): string {
  let out = "";
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    const n = src[i + 1];
    if (c === "/" && n === "/") {
      while (i < src.length && src[i] !== "\n") i++;
      continue;
    }
    if (c === "/" && n === "*") {
      const end = src.indexOf("*/", i + 2);
      i = end < 0 ? src.length : end + 2;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      let j = i + 1;
      while (j < src.length && src[j] !== c) {
        if (src[j] === "\\") j++;
        else if (c !== "`" && src[j] === "\n") break;
        j++;
      }
      out += src.slice(i, j + 1);
      i = j + 1;
      continue;
    }
    out += c;
    i++;
  }
  return out;
}

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
    // EM-T8g-2: the count is the pooled mailboxes (`email-all-skips-separate`).
    expect(page).toContain("All inboxes · {pooledCount} mailboxes");
    expect(page).toContain("`All inboxes · ${pooledCount} mailboxes`");
  });

  it("drops the label filter, offers no load older, and keeps an opened mail", () => {
    const selectAll = store.slice(store.indexOf("selectAll: () => {"));
    expect(selectAll.slice(0, 900)).toContain("selectedLabel: null,");
    expect(store).toContain("if (backfilling || !selectedAccountId || get().viewAll) return;");
    expect(page).toMatch(/const canBackfillFolder =\s*!viewAll &&/);
    // A mail of a pooled mailbox stays in All inboxes (`mailboxToOpen`).
    const all = { viewAll: true, selectedAccountId: "a", accounts: [{ id: "a" }, { id: "b" }] };
    expect(mailboxToOpen(all, "b")).toBeNull();
    expect(store).toContain("const target = mailboxToOpen(get(), email.accountId);");
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

// ── WS-17 EM-T8g-2 — "Keep separate" ───────────────────────────────────────

/** Three mailboxes: two in All inboxes, and c kept separate. */
const pa = { ...box("a", "Fracktal", 2), isDefault: true };
const pb = box("b", "Personal", 3);
const sc: EmailAccount = { ...box("c", "Client", 7), inAllInboxes: false };
const three = [pa, pb, sc];

/** The root of `src/`, and one file under it. */
const SRC = join(__dirname, "..", "..", "..");
const readSrc = (rel: string) => readFileSync(join(SRC, rel), "utf-8").replace(/\r\n/g, "\n");

/** Every source file of `src/`, as a path under it. Tests are left out. */
function srcFiles(dir = SRC, rel = ""): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const path = rel ? `${rel}/${name}` : name;
    if (statSync(full).isDirectory()) out.push(...srcFiles(full, path));
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

describe("email-all-skips-separate", () => {
  it("pools each mailbox that is not separate, and a missing flag pools", () => {
    expect(pooledMailboxes(three).map((m) => m.id)).toEqual(["a", "b"]);
    expect(isSeparate(sc)).toBe(true);
    // A gateway before EM-T8g-1 sends no flag: the mailbox stays in.
    expect(isSeparate(box("x", "X", 0))).toBe(false);
    expect(isSeparate({ inAllInboxes: true })).toBe(false);
  });

  it("shows All inboxes for two or more pooled mailboxes only", () => {
    expect(hasAllInboxes(three)).toBe(true);
    expect(hasAllInboxes([pa, sc])).toBe(false);
    expect(hasAllInboxes([sc, { ...pb, inAllInboxes: false }])).toBe(false);
    expect(hasAllInboxes([pa, pb])).toBe(true);
  });

  it("opens the first view on the pooled mailboxes", () => {
    expect(pickInitialView(three, null)).toEqual({ accountId: "a", viewAll: true });
    expect(pickInitialView(three, ALL_INBOXES)).toEqual({ accountId: "a", viewAll: true });
    // Fewer than two pooled: no All inboxes, and the default mailbox opens.
    expect(pickInitialView([pa, sc], null)).toEqual({ accountId: "a", viewAll: false });
    expect(pickInitialView([pa, sc], ALL_INBOXES)).toEqual({ accountId: "a", viewAll: false });
    expect(pickInitialView([pa, sc], "gone")).toEqual({ accountId: "a", viewAll: false });
    // A separate mailbox still opens in its own view.
    expect(pickInitialView(three, "c")).toEqual({ accountId: "c", viewAll: false });
  });

  it("keeps a pooled mailbox selected out of view in All inboxes (review F5)", () => {
    // The default is separate: All inboxes keeps the first pooled mailbox.
    const sepDefault = [{ ...sc, isDefault: true }, { ...pa, isDefault: false }, pb];
    expect(poolHome(sepDefault)?.id).toBe("a");
    expect(pickInitialView(sepDefault, ALL_INBOXES)).toEqual({ accountId: "a", viewAll: true });
    expect(pickInitialView(sepDefault, "gone")).toEqual({ accountId: "a", viewAll: true });
    // Outside All inboxes the default mailbox opens, separate or not.
    expect(pickInitialView([{ ...sc, isDefault: true }, pa], ALL_INBOXES)).toEqual({ accountId: "c", viewAll: false });
    expect(poolHome(three)?.id).toBe("a");
    expect(poolHome([sc])).toBeNull();
  });

  it("names a separate mailbox in the reconnect banner (item 6)", () => {
    const failing: EmailAccount = { ...sc, syncStatus: "error" };
    const state = { viewAll: true, selectedAccountId: "a", accounts: [pa, pb, failing], authErrors: {} };
    expect(attentionMailbox(state)?.id).toBe("c");
    expect(attentionMailbox({ ...state, accounts: [pa, pb, sc], authErrors: { c: "401" } })?.id).toBe("c");
    // One mailbox in view names only that mailbox.
    expect(attentionMailbox({ ...state, viewAll: false })).toBeNull();
    expect(attentionMailbox({ ...state, viewAll: false, selectedAccountId: "c" })?.id).toBe("c");
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain(
      "const attentionAccount = attentionMailbox({ viewAll, selectedAccountId, accounts, authErrors });");
  });

  it("is busy for a pooled mailbox only in All inboxes", () => {
    const state = (viewAll: boolean, busy: string) => ({
      viewAll,
      selectedAccountId: viewAll ? "a" : busy,
      accounts: three,
      syncStatus: { [busy]: "syncing" },
    });
    expect(scopeBusy(state(true, "b"))).toBe(true);
    expect(scopeBusy(state(true, "c"))).toBe(false);
    // In its own view, the separate mailbox is the scope.
    expect(scopeBusy(state(false, "c"))).toBe(true);
  });

  it("draws the All inboxes row with the pooled count and the pooled unread sum", () => {
    const html = renderToStaticMarkup(createElement(AccountSidebar, {
      accounts: three,
      selectedAccountId: "a",
      onAccountSelect: () => {},
      folders,
      selectedFolder: "inbox",
      onFolderSelect: () => {},
      viewAll: false,
      onSelectAll: () => {},
      showAutomation: false,
    }));
    expect(html).toContain(">All inboxes<");
    expect(html).toContain("2 mailboxes");
    expect(html).not.toContain("3 mailboxes");
    // 2 + 3. The 7 of the separate mailbox shows on its own row only.
    expect(html).toContain(">5<");
    expect(html).not.toContain(">12<");
    expect(html).toContain(">7<");
  });

  it("draws no All inboxes row when one mailbox is pooled", () => {
    const html = renderToStaticMarkup(createElement(AccountSidebar, {
      accounts: [pa, sc],
      selectedAccountId: "a",
      onAccountSelect: () => {},
      folders,
      selectedFolder: "inbox",
      onFolderSelect: () => {},
      onSelectAll: () => {},
      showAutomation: false,
    }));
    // The row is gone. The title of the word "Separate" still names it.
    expect(html).not.toContain(">All inboxes<");
    expect(html).not.toContain("mailboxes<");
  });

  it("counts the pooled mailboxes in the header of the page", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain("const pooledCount = pooledMailboxes(accounts).length;");
    expect(page).not.toMatch(/All inboxes · \{accounts\.length\}|All inboxes · \$\{accounts\.length\}/);
  });

  it("is the one rule: no other file names the flag, in any app", () => {
    // Review F2: the bare token, so a destructure `({ inAllInboxes })` and a
    // bracket read `a["inAllInboxes"]` count too. Every source file of `src/`.
    // `stripComments` keeps each string, so a `//` in a string hides nothing
    // (review round 2).
    const files = srcFiles();
    expect(files.length).toBeGreaterThan(400);
    expect(files).toContain("components/email/EmailToolCards.tsx");
    const named = files.filter((f) => /\binAllInboxes\b/.test(stripComments(readSrc(f))));
    // `api.ts` maps and sends it, `mailbox.ts` decides, `types.ts` declares it.
    expect(named.sort()).toEqual(["app/email/lib/api.ts", "app/email/lib/mailbox.ts", "app/email/lib/types.ts"]);
    const wire = files.filter((f) => /\bin_all_inboxes\b/.test(stripComments(readSrc(f))));
    expect(wire).toEqual(["app/email/lib/api.ts"]);
  });

  it("finds a read after a // in a string, and skips a real comment (review round 2)", () => {
    const hidden = 'const s = "a//b"; const x = a.inAllInboxes;';
    // The old filter cut the line at the `//` in the string.
    expect(codeOnly(hidden)).not.toMatch(/\binAllInboxes\b/);
    expect(stripComments(hidden)).toMatch(/\binAllInboxes\b/);
    expect(stripComments("const t = `x//${a}`; a['inAllInboxes'];")).toMatch(/\binAllInboxes\b/);
    expect(stripComments("const u = '/*'; a.inAllInboxes; // */")).toMatch(/\binAllInboxes\b/);
    // A comment still drops out, so a note may name the flag.
    expect(stripComments("x(); // inAllInboxes\n/* inAllInboxes */ y();")).not.toMatch(/\binAllInboxes\b/);
  });
});

describe("email-separate-menu", () => {
  const labels = (items: ReturnType<typeof accountMenuItems>) =>
    items.flatMap((i) => (i.kind === "item" ? [i.label] : []));

  it("offers Keep separate, or Show in All inboxes for a separate mailbox", () => {
    expect(separateToggle(pb, three)).toEqual({ label: "Keep separate", icon: "EyeOff", nextPooled: false });
    expect(separateToggle(sc, three)).toEqual({ label: "Show in All inboxes", icon: "Inbox", nextPooled: true });
    // One mailbox has no All inboxes, so the menu offers neither (§11.0).
    expect(separateToggle(sc, [sc])).toBeNull();
    expect(separateToggle(pa, [pa])).toBeNull();
    for (const name of ["EyeOff", "Inbox"]) expect(isKnownIcon(name), name).toBe(true);
  });

  it("puts the item in the mailbox menu, and a pick sends the choice", () => {
    const picks: Array<[string, boolean]> = [];
    const on = { onToggleSeparate: (id: string, pooled: boolean) => void picks.push([id, pooled]) };
    const keep = accountMenuItems(pb, three, on);
    const show = accountMenuItems(sc, three, on);
    expect(labels(keep)).toContain("Keep separate");
    expect(labels(keep)).not.toContain("Show in All inboxes");
    expect(labels(show)).toContain("Show in All inboxes");
    for (const items of [keep, show]) {
      for (const i of items) {
        if (i.kind === "item" && /separate|All inboxes/.test(i.label)) i.onSelect();
      }
    }
    expect(picks).toEqual([["b", false], ["c", true]]);
    // No handler, or one mailbox: neither label.
    expect(labels(accountMenuItems(pb, three, {}))).not.toContain("Keep separate");
    expect(labels(accountMenuItems(pa, [pa], on))).not.toContain("Keep separate");
  });

  it("offers the way back with one pooled and one separate mailbox (review F6)", () => {
    // No All inboxes shows here, and this menu is the only way back into it.
    const pair = [pa, sc];
    expect(separateToggle(sc, pair)).toEqual({ label: "Show in All inboxes", icon: "Inbox", nextPooled: true });
    expect(separateToggle(pa, pair)?.label).toBe("Keep separate");
    const picks: Array<[string, boolean]> = [];
    const items = accountMenuItems(sc, pair, { onToggleSeparate: (id, pooled) => void picks.push([id, pooled]) });
    for (const i of items) if (i.kind === "item" && i.label === "Show in All inboxes") i.onSelect();
    expect(picks).toEqual([["c", true]]);
    expect(separateMark(sc, pair)).toBe("Separate");
    const html = renderToStaticMarkup(createElement(AccountSidebar, {
      accounts: pair,
      selectedAccountId: "a",
      onAccountSelect: () => {},
      folders,
      selectedFolder: "inbox",
      onFolderSelect: () => {},
      onSelectAll: () => {},
      showAutomation: false,
    }));
    expect(html.match(/>Separate</g)).toHaveLength(1);
  });

  it("shows the word Separate beside the chip of a separate mailbox only", () => {
    expect(separateMark(sc, three)).toBe("Separate");
    expect(separateMark(pb, three)).toBeNull();
    expect(separateMark(sc, [sc])).toBeNull();
    const html = renderToStaticMarkup(createElement(AccountSidebar, {
      accounts: three,
      selectedAccountId: "a",
      onAccountSelect: () => {},
      folders,
      selectedFolder: "inbox",
      onFolderSelect: () => {},
      onSelectAll: () => {},
      showAutomation: false,
    }));
    expect(html.match(/>Separate</g)).toHaveLength(1);
    // The word sits in the row of c, after its label.
    expect(html.indexOf(">Separate<")).toBeGreaterThan(html.indexOf(">Client<"));
    expect(html.indexOf(">Separate<")).toBeLessThan(html.indexOf("c@x.test<"));
    const one = renderToStaticMarkup(createElement(AccountSidebar, {
      accounts: [sc],
      selectedAccountId: "c",
      onAccountSelect: () => {},
      folders,
      selectedFolder: "inbox",
      onFolderSelect: () => {},
      showAutomation: false,
    }));
    expect(one).not.toContain("Separate");
  });

  it("draws the word with the house Badge, and wires the menu on the page", () => {
    const side = codeOnly(read("components/AccountSidebar.tsx"));
    expect(side).toContain('<Badge size="xs" title="Kept out of All inboxes" className="flex-shrink-0">');
    expect(side).toContain("{(onDisconnect || onEditMailbox || onToggleSeparate) && (");
    const page = codeOnly(read("page.tsx"));
    expect(page.match(/onToggleSeparate=\{toggleSeparate\}/g)).toHaveLength(2);
    expect(page).toContain("void setInAllInboxes(id, pooled);");
  });

  describe("the PATCH", () => {
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("sends in_all_inboxes to PATCH /email/accounts/{id}", async () => {
      const calls: Array<{ url: string; method?: string; body?: unknown }> = [];
      vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), method: init?.method, body: JSON.parse(String(init?.body)) });
        return new Response(JSON.stringify({ id: "c", in_all_inboxes: false }), { status: 200 });
      }));
      const api = await import("./api");
      const saved = await api.setMailboxPooled("c", false);
      expect(calls).toEqual([{ url: "/api/email/accounts/c", method: "PATCH", body: { in_all_inboxes: false } }]);
      expect(isSeparate(saved)).toBe(true);
    });

    it("reads a missing field as in All inboxes, and only false as separate", async () => {
      vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
        { id: "old" },
        { id: "in", in_all_inboxes: true },
        { id: "out", in_all_inboxes: false },
        { id: "odd", in_all_inboxes: "false" },
      ]), { status: 200 })));
      const api = await import("./api");
      const accounts = await api.listEmailAccounts();
      expect(accounts.map((m) => [m.id, isSeparate(m)])).toEqual([
        ["old", false], ["in", false], ["out", true], ["odd", false],
      ]);
    });
  });
});

describe("email-separate-leaves-at-once, review round 2: checks", () => {
  it("acts only on the checked rows of the list on screen", () => {
    const emails = [{ id: "a-1" }, { id: "b-1" }];
    expect(checkedRows({ emails, selectedIds: new Set(["c-9", "b-1", "a-1"]) })).toEqual(["a-1", "b-1"]);
    expect(checkedRows({ emails, selectedIds: new Set(["c-9"]) })).toEqual([]);
  });

  it("reads checkedRows for each bulk act and each count, in the store and in both bars", () => {
    const store = codeOnly(read("lib/emailStore.ts"));
    const update = store.slice(store.indexOf("bulkUpdateSelected: (updates) => {"));
    expect(update.slice(0, 300)).toContain("const ids = checkedRows(get());");
    const remove = store.slice(store.indexOf("bulkDeleteSelected: () => {"));
    expect(remove.slice(0, 300)).toContain("const ids = checkedRows(get());");
    expect(store).not.toMatch(/\[\.\.\.get\(\)\.selectedIds\]/);
    const bar = codeOnly(read("components/EmailToolbar.tsx"));
    expect(bar).toContain("const checkedCount = checkedRows({ emails, selectedIds }).length;");
    expect(bar).not.toMatch(/selectedIds\.size/);
    const list = codeOnly(read("components/EmailList.tsx"));
    expect(list).toContain("const checked = checkedRows({ emails, selectedIds });");
    expect(list).not.toMatch(/\[\.\.\.selected\]|selected\.size|\[\.\.\.selectedIds\]|selectedIds\.size/);
  });
});

describe("email-separate-leaves-at-once, the pure half", () => {
  it("opens a mail of a separate mailbox in that mailbox, never in All inboxes", () => {
    const all = { viewAll: true, selectedAccountId: "a", accounts: three };
    expect(mailboxToOpen(all, "c")).toBe("c");
    expect(mailboxToOpen(all, "b")).toBeNull();
    // One mailbox in view keeps the rule of MB-3.
    expect(mailboxToOpen({ ...all, viewAll: false }, "c")).toBe("c");
    expect(mailboxToOpen({ ...all, viewAll: false, selectedAccountId: "c" }, "c")).toBeNull();
  });
});

// ── WS-17 EM-T6e: the storage notice and the switcher mark (D3) ────────────

const STORAGE_MB = 1_048_576;
const atLimit = (acct: EmailAccount): EmailAccount => ({
  ...acct,
  storedBytes: 600 * STORAGE_MB,
  storageLimitBytes: 500 * STORAGE_MB,
  importPhase: "limit",
});
const underLimit = (acct: EmailAccount): EmailAccount => ({
  ...acct,
  storedBytes: 10 * STORAGE_MB,
  storageLimitBytes: 500 * STORAGE_MB,
});

describe("email-storage-mailbox (A5, A6, D3)", () => {
  it("in All inboxes names the first pooled mailbox at the limit, in the order of the list (A5)", () => {
    const d = atLimit(box("d", "Desk", 0));
    const accounts = [underLimit(pa), atLimit(pb), d];
    expect(storageMailbox({ viewAll: true, selectedAccountId: "a", accounts })?.id).toBe("b");
    expect(storageMailbox({ viewAll: true, selectedAccountId: "a", accounts: [underLimit(pa), d, atLimit(pb)] })?.id).toBe("d");
  });

  it("names the mailbox at the limit, never poolHome (A5)", () => {
    const accounts = [underLimit(pa), atLimit(pb), sc];
    expect(poolHome(accounts)?.id).toBe("a");
    expect(storageMailbox({ viewAll: true, selectedAccountId: "a", accounts })?.id).toBe("b");
  });

  it("never names a separate mailbox in All inboxes, and names it in its own view (A5)", () => {
    const accounts = [underLimit(pa), underLimit(pb), atLimit(sc)];
    expect(storageMailbox({ viewAll: true, selectedAccountId: "a", accounts })).toBeNull();
    expect(storageMailbox({ viewAll: false, selectedAccountId: "c", accounts })?.id).toBe("c");
  });

  it("one mailbox in view shows its own notice only (D3)", () => {
    const accounts = [underLimit(pa), atLimit(pb), sc];
    expect(storageMailbox({ viewAll: false, selectedAccountId: "a", accounts })).toBeNull();
    expect(storageMailbox({ viewAll: false, selectedAccountId: "b", accounts })?.id).toBe("b");
  });

  it("shows the gap line in the own view of its mailbox, and not in All inboxes (D2)", () => {
    const gap: EmailAccount = { ...atLimit(pb), storedBytes: 300 * STORAGE_MB };
    const accounts = [underLimit(pa), gap, sc];
    expect(storageMailbox({ viewAll: false, selectedAccountId: "b", accounts })?.id).toBe("b");
    expect(storageMailbox({ viewAll: true, selectedAccountId: "a", accounts })).toBeNull();
  });

  it("the reconnect banner wins: no notice for the mailbox that attentionMailbox names (A6)", () => {
    const failing: EmailAccount = { ...atLimit(pa), syncStatus: "error" };
    const accounts = [failing, atLimit(pb), sc];
    const attention = attentionMailbox({ viewAll: true, selectedAccountId: "a", accounts, authErrors: {} });
    expect(attention?.id).toBe("a");
    expect(storageMailbox({ viewAll: true, selectedAccountId: "a", accounts, attentionId: attention?.id })?.id).toBe("b");
    expect(storageMailbox({ viewAll: false, selectedAccountId: "a", accounts, attentionId: "a" })).toBeNull();
  });

  it("no notice for a mailbox in the storage stage, because the step names it (A6)", () => {
    const accounts = [atLimit(pa), atLimit(pb), sc];
    expect(storageMailbox({ viewAll: false, selectedAccountId: "a", accounts, storageStepId: "a" })).toBeNull();
    expect(storageMailbox({ viewAll: true, selectedAccountId: "a", accounts, storageStepId: "a" })?.id).toBe("b");
  });

  it("the page wires the rule, and the dialog takes the id that the notice or the step names", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toMatch(
      /const storageAccount = storageMailbox\(\{\s*viewAll,\s*selectedAccountId,\s*accounts,\s*attentionId: attentionAccount\?\.id \?\? null,\s*storageStepId: setupStage === "storage" \? selectedAccountId : null,\s*\}\);/,
    );
    expect(page).toContain("onRemove={() => setRemovingId(storageAccount.id)}");
    expect(page).toContain("onRemove={() => setRemovingId(selectedAccount.id)}");
    // The notice, the step, the close, and the clear of review round 1.
    expect(page.match(/setRemovingId\(/g)).toHaveLength(4);
    expect(page).toContain("const removingAccount = removalMailbox(accounts, removingId);");
    expect(page).toMatch(/<RemoveOlderMailDialog\s+account=\{removingAccount\}/);
    // The notice sits below the reconnect banner, and above the import panels.
    const notice = page.indexOf("<StorageNotice");
    expect(notice).toBeGreaterThan(page.indexOf("Reconnect Outlook"));
    expect(notice).toBeLessThan(page.indexOf("{importPanels.map("));
    // An open dialog stops the page shortcuts, so "#" cannot delete the mail behind it.
    expect(page).toContain("editingMailbox || removingAccount || paletteOpen) return;");
  });
});

describe("email-storage-dialog-leaves (review round 1)", () => {
  const dialog = (account: EmailAccount | null, onClose: () => void) =>
    renderToStaticMarkup(createElement(RemoveOlderMailDialog, {
      account,
      named: true,
      onClose,
      onRemoved: () => {},
      onRefresh: async () => null,
    }));

  it("the named mailbox leaves the list while the dialog is open: the dialog closes, and the shortcuts work again", () => {
    // Open: the dialog names b, and the shortcut guard reads b.
    expect(removalMailbox([pa, pb], "b")).toBe(pb);
    // Another tab disconnects b, or a re-read drops it.
    const left = removalMailbox([pa], "b");
    expect(left).toBeNull();
    // The dialog draws nothing, so its onClose never runs. The page must clear the id itself.
    const onClose = vi.fn();
    expect(dialog(left, onClose)).toBe("");
    expect(onClose).not.toHaveBeenCalled();
    // After the clear, a re-read that brings b back opens nothing.
    expect(removalMailbox([pa, pb], null)).toBeNull();
  });

  it("the page guards the shortcuts on the looked-up mailbox, and clears an id that names none", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain("if (removingId !== null && removingAccount === null) setRemovingId(null);");
    // The keydown effect reads the mailbox, never the raw id, in its guard and in its deps.
    const keys = page.slice(page.indexOf("const onKey = (e: KeyboardEvent)"), page.indexOf("const commands = useMemo<Command[]>("));
    expect(keys.length).toBeGreaterThan(500);
    expect(keys).toMatch(/\|\| removingAccount \|\|/);
    expect(keys).toMatch(/disconnecting, editingMailbox, removingAccount, paletteOpen/);
    expect(keys).not.toMatch(/\bremovingId\b/);
  });
});

describe("email-storage-switcher (UC-12, D3)", () => {
  const render = (accounts: EmailAccount[]) =>
    renderToStaticMarkup(createElement(AccountSidebar, {
      accounts,
      selectedAccountId: "a",
      onAccountSelect: () => {},
      folders,
      selectedFolder: "inbox",
      onFolderSelect: () => {},
      viewAll: true,
      onSelectAll: () => {},
      showAutomation: false,
    }));

  it("marks each mailbox at the limit, a separate one too", () => {
    const html = render([atLimit(pa), underLimit(pb), atLimit(sc)]);
    expect(html.match(/aria-label="At the storage limit"/g)).toHaveLength(2);
  });

  it("marks one mailbox at the limit, and none under it", () => {
    expect(render([atLimit(pa)]).match(/aria-label="At the storage limit"/g)).toHaveLength(1);
    expect(render([underLimit(pa), underLimit(pb)])).not.toContain("At the storage limit");
  });

  it("draws the mark in the warning tone, with a known icon", () => {
    const src = codeOnly(read("components/AccountSidebar.tsx"));
    expect(src).toMatch(/<AppIcon name="HardDrive"\s+size=\{10\}\s+className="text-warning"\s+role="img"/);
    expect(isKnownIcon("HardDrive")).toBe(true);
  });
});
