// WS-17 EM-T8d review — All inboxes, tested on the REAL store with a mocked
// gateway. The verifier found that text fences let three store rules break
// unseen; these drive the actions and check the state.
//
// R7 fences named here:
//   * `email-all-store-select`: picking one mailbox leaves All inboxes, and
//     All inboxes moves a custom folder to the Inbox and drops the label.
//   * `email-all-store-accounts`: a re-read with one mailbox ends All inboxes,
//     and a selected mailbox that is gone picks the view again.
//   * `email-all-store-disconnect`: a disconnect in All inboxes re-reads the
//     list, and ends the scope when one mailbox is left.
//   * `email-all-store-refresh-race`: a background refresh for another scope
//     never lands in the view.
//   * `email-all-store-sync`: a sync of the scope reaches each mailbox in All
//     inboxes, and only the selected one otherwise.
//   * `email-all-store-label`: no label filter in All inboxes, and the colour
//     of a label of another mailbox goes to that mailbox only.
//
// WS-17 EM-T8g-2 (§11.7.7) adds, at the end of this file:
//   * `email-separate-leaves-at-once`: a toggle sends the `PATCH`. In All
//     inboxes the rows, the checks and the open mail of a mailbox kept
//     separate leave at once, and the list and the sums are read again. A
//     toggle that leaves fewer than two pooled mailboxes ends All inboxes for
//     the default mailbox. A mail of a separate mailbox opens in that mailbox.
//   * `email-all-skips-separate`, the store half: the folder sums and
//     `syncScope` reach the pooled mailboxes only.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listEmails: vi.fn(),
  searchEmails: vi.fn(),
  listEmailAccounts: vi.fn(),
  listEmailFolders: vi.fn(),
  listLabels: vi.fn(),
  deleteEmailAccount: vi.fn(),
  triggerSync: vi.fn(),
  setLabelColor: vi.fn(),
  snoozeEmail: vi.fn(),
  setMailboxPooled: vi.fn(),
  getEmail: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, ...api };
});

import {
  FOLDER_SUM_CONCURRENCY,
  FOLDER_SUM_TIMEOUT_MS,
  FOLDER_SUMS_AFTER_SYNC_MS,
  useEmailStore,
} from "./emailStore";
import type { EmailAccount, EmailFolder } from "./types";

const box = (id: string, isDefault = false): EmailAccount => ({
  id,
  provider: "microsoft",
  emailAddress: `${id}@x.test`,
  label: "",
  unreadCount: 0,
  syncEnabled: true,
  isDefault,
});
const page = (ids: string[]) => ({
  emails: ids.map((id) => ({ id, accountId: id.split("-")[0], threadId: id })),
  total: ids.length,
});
const folders: EmailFolder[] = [
  { key: "inbox", label: "Inbox", count: 0, type: "system" } as EmailFolder,
  { key: "projects", label: "Projects", count: 0, type: "user" } as EmailFolder,
];
const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  for (const f of Object.values(api)) f.mockReset();
  api.listEmails.mockResolvedValue(page([]));
  api.searchEmails.mockResolvedValue({ ...page([]), hybrid: false });
  api.listEmailFolders.mockResolvedValue([]);
  api.listLabels.mockResolvedValue([]);
  api.triggerSync.mockResolvedValue({});
  api.setLabelColor.mockResolvedValue({});
  api.deleteEmailAccount.mockResolvedValue(undefined);
  useEmailStore.setState({
    accounts: [box("a", true), box("b")],
    selectedAccountId: "a",
    viewAll: true,
    folders,
    selectedFolder: "inbox",
    selectedLabel: null,
    emails: [],
    emailsPage: 1,
    emailsLoading: false,
    labelColors: {},
    syncStatus: {},
    authErrors: {},
  });
});

describe("select", () => {
  it("leaves All inboxes when the member picks one mailbox", () => {
    useEmailStore.getState().selectAccount("b");
    expect(useEmailStore.getState().viewAll).toBe(false);
    expect(useEmailStore.getState().selectedAccountId).toBe("b");
  });

  it("moves a custom folder to the Inbox and drops the label", () => {
    useEmailStore.setState({ viewAll: false, selectedFolder: "projects", selectedLabel: "Clients" });
    useEmailStore.getState().selectAll();
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedFolder, s.selectedLabel]).toEqual([true, "inbox", null]);
  });
});

describe("accounts", () => {
  it("ends All inboxes when a re-read finds one mailbox", async () => {
    api.listEmailAccounts.mockResolvedValue([box("a", true)]);
    await useEmailStore.getState().fetchAccounts();
    expect(useEmailStore.getState().viewAll).toBe(false);
  });

  it("picks the view again when the selected mailbox is gone", async () => {
    api.listEmailAccounts.mockResolvedValue([box("b", true), box("c")]);
    await useEmailStore.getState().fetchAccounts();
    const s = useEmailStore.getState();
    expect([s.selectedAccountId, s.viewAll]).toEqual(["b", true]);
  });
});

describe("disconnect", () => {
  it("re-reads All inboxes when another mailbox goes", async () => {
    useEmailStore.setState({ accounts: [box("a", true), box("b"), box("c")] });
    await useEmailStore.getState().deleteAccount("b");
    expect(useEmailStore.getState().viewAll).toBe(true);
    expect(api.listEmails).toHaveBeenCalled();
    expect(api.listEmails.mock.calls.at(-1)?.[0].accountId).toBeUndefined();
  });

  it("ends All inboxes when one mailbox is left", async () => {
    await useEmailStore.getState().deleteAccount("b");
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId]).toEqual([false, "a"]);
  });
});

describe("a background refresh", () => {
  it("never lands in a view of another scope", async () => {
    let finish: (v: unknown) => void = () => {};
    api.listEmails.mockImplementationOnce(() => new Promise((r) => { finish = r; }));
    const refresh = useEmailStore.getState().softRefresh();
    // The member opens mailbox a while the All-inboxes read is out.
    api.listEmails.mockResolvedValue(page(["a-1"]));
    useEmailStore.getState().selectAccount("a");
    await flush();
    finish(page(["a-1", "b-1"]));
    await refresh;
    await flush();
    expect(useEmailStore.getState().emails.map((e) => e.id)).toEqual(["a-1"]);
  });
});

describe("sync", () => {
  // A Refresh in All inboxes sets a timer for the read of the sums
  // (EM-T8f-3 review F2). Fake timers keep it out of the later tests.
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reaches each mailbox in All inboxes", () => {
    useEmailStore.getState().syncScope();
    expect(api.triggerSync.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b"]);
  });

  it("reaches only the selected mailbox otherwise", () => {
    useEmailStore.setState({ viewAll: false });
    useEmailStore.getState().syncScope();
    expect(api.triggerSync.mock.calls.map((c) => c[0])).toEqual(["a"]);
  });
});

describe("labels", () => {
  it("has no label filter in All inboxes", () => {
    useEmailStore.getState().selectLabel("Clients");
    expect(useEmailStore.getState().selectedLabel).toBeNull();
  });

  it("colours a label in the mailbox it names, and only there", async () => {
    await useEmailStore.getState().setLabelColor("Clients", "red", "b");
    expect(api.setLabelColor).toHaveBeenCalledWith("b", "Clients", "red");
    // The chips of the selected mailbox keep their colours.
    expect(useEmailStore.getState().labelColors).toEqual({});
  });
});

// The second review round (F3, F6, F8): three rules the first fences missed,
// and two leaks it found.
//   * `email-all-store-snooze`: a snooze drops the thread of its own mailbox
//     only, and never a row of another mailbox with the same thread id.
//   * `email-all-store-select-clears`: All inboxes opens with no mail open.
//   * `email-all-store-stored-scope`: removing the selected mailbox keeps the
//     stored scope on All inboxes.
//   * `email-all-store-removed-rows`: a removed mailbox leaves the list and the
//     reading pane at once, from this tab or from another.
//   * `email-all-store-label-seed`: rows of All inboxes add no labels.
describe("the second review round", () => {
  const row = (id: string, threadId: string, categories: string[] = []) => ({
    id, accountId: id.split("-")[0], threadId, categories,
  });

  it("snoozes the thread of one mailbox only", () => {
    api.snoozeEmail.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({ emails: [row("a-1", "t"), row("b-1", "t")] as never });
    void useEmailStore.getState().snoozeEmail("a-1", "2026-10-04T09:00:00Z");
    expect(useEmailStore.getState().emails.map((e) => e.id)).toEqual(["b-1"]);
  });

  it("opens All inboxes with no mail open and nothing checked", () => {
    useEmailStore.setState({ viewAll: false, selectedEmailId: "a-1", selectedIds: new Set(["a-1"]) });
    useEmailStore.getState().selectAll();
    const s = useEmailStore.getState();
    expect([s.selectedEmailId, s.selectedIds.size]).toEqual([null, 0]);
  });

  it("keeps All inboxes stored when the selected mailbox goes", async () => {
    const stored = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (k: string) => stored.get(k) ?? null,
        setItem: (k: string, v: string) => void stored.set(k, v),
        removeItem: (k: string) => void stored.delete(k),
      },
      location: { href: "http://x.test/email", search: "" },
      history: { replaceState: () => {} },
    });
    try {
      useEmailStore.setState({ accounts: [box("a", true), box("b"), box("c")] });
      await useEmailStore.getState().deleteAccount("a");
      expect(stored.get("cc.email.selectedAccountId")).toBe("all");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("drops the rows and the open mail of a mailbox removed here", async () => {
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({
      accounts: [box("a", true), box("b"), box("c")],
      emails: [row("a-1", "t1"), row("b-1", "t2")] as never,
      selectedEmailId: "b-1",
    });
    await useEmailStore.getState().deleteAccount("b");
    const s = useEmailStore.getState();
    expect([s.emails.map((e) => e.id), s.selectedEmailId]).toEqual([["a-1"], null]);
  });

  it("drops the rows of a mailbox that another tab removed", async () => {
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({
      accounts: [box("a", true), box("b"), box("c")],
      emails: [row("a-1", "t1"), row("c-1", "t2")] as never,
      selectedEmailId: "c-1",
    });
    api.listEmailAccounts.mockResolvedValue([box("a", true), box("b")]);
    await useEmailStore.getState().fetchAccounts();
    const s = useEmailStore.getState();
    expect([s.emails.map((e) => e.id), s.selectedEmailId, s.viewAll]).toEqual([["a-1"], null, true]);
  });

  it("adds no label from the rows of All inboxes", async () => {
    api.listEmails.mockResolvedValue({ emails: [row("b-1", "t", ["Clients"])], total: 1 });
    useEmailStore.setState({ availableLabels: [] });
    await useEmailStore.getState().fetchEmails();
    expect(useEmailStore.getState().availableLabels).toEqual([]);
  });
});

// WS-17 EM-T8f-3 — the store half of `email-all-folder-sums`
// (`allInboxes.test.ts` holds the pure half). All inboxes reads the folders
// of each mailbox, at most FOLDER_SUM_CONCURRENCY at one time, and sums the
// provider counts of the six well-known folders. A failed read adds 0, and
// the list never waits on the reads.
describe("email-all-folder-sums, the store half", () => {
  /** A read that the test settles by hand. */
  function deferred<T>() {
    let resolve: (v: T) => void = () => {};
    let reject: (e: unknown) => void = () => {};
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }
  const raw = (name: string, count: number, type = "system") => ({
    provider_folder_id: name, name, type, message_count: count, unread_count: 0,
  });
  // A round that an earlier test started lands before this test begins, and
  // its calls do not count here.
  beforeEach(async () => {
    await flush();
    await flush();
    api.listEmailFolders.mockReset();
    api.listEmailFolders.mockResolvedValue([]);
    useEmailStore.setState({ allFolderCounts: null });
  });

  it("reads each mailbox and sums the provider counts, by the canonical name", async () => {
    api.listEmailFolders.mockImplementation(async (id: string) =>
      id === "a"
        ? [raw("Inbox", 7), raw("Sent Items", 2), raw("Deleted Items", 1), raw("Projects", 9, "user")]
        : [raw("Inbox", 5), raw("Junk Email", 3), raw("Archive", 4), raw("Drafts", 1)],
    );
    useEmailStore.setState({ viewAll: false, allFolderCounts: null });
    useEmailStore.getState().selectAll();
    await flush();
    await flush();
    expect(api.listEmailFolders.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b"]);
    expect(useEmailStore.getState().allFolderCounts).toEqual({
      inbox: 12, drafts: 1, sent: 2, archive: 4, junk: 3, trash: 1,
    });
    // The tree of the selected mailbox is not the sum.
    expect(useEmailStore.getState().folders).toBe(folders);
  });

  it("adds 0 for a mailbox whose read fails", async () => {
    api.listEmailFolders.mockImplementation(async (id: string) => {
      if (id === "b") throw Object.assign(new Error("401"), { status: 401 });
      return [raw("Inbox", 7)];
    });
    // The tree on screen holds 50. A failed read must not add it.
    useEmailStore.setState({
      viewAll: false,
      allFolderCounts: null,
      folders: [{ key: "inbox", label: "Inbox", count: 50, type: "system" } as EmailFolder],
    });
    useEmailStore.getState().selectAll();
    await flush();
    await flush();
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(7);
  });

  it("adds 0 for a read that does not merge, and the round still lands", async () => {
    api.listEmailFolders.mockImplementation(async (id: string) => (id === "b" ? null : [raw("Inbox", 7)]));
    useEmailStore.setState({ viewAll: true, allFolderCounts: null });
    await expect(useEmailStore.getState().fetchAllFolderCounts()).resolves.toBeUndefined();
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(7);
  });

  it("never makes the list wait on the reads", async () => {
    const pending = deferred<never[]>();
    api.listEmailFolders.mockReturnValue(pending.promise);
    api.listEmails.mockResolvedValue(page(["a-1", "b-1"]));
    useEmailStore.setState({ viewAll: false, allFolderCounts: null });
    useEmailStore.getState().selectAll();
    await flush();
    expect(useEmailStore.getState().emails.map((e) => e.id)).toEqual(["a-1", "b-1"]);
    expect(useEmailStore.getState().emailsLoading).toBe(false);
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
    pending.resolve([]);
    await flush();
    await flush();
  });

  it("keeps at most FOLDER_SUM_CONCURRENCY reads out at one time", async () => {
    const reads = new Map<string, ReturnType<typeof deferred<ReturnType<typeof raw>[]>>>();
    api.listEmailFolders.mockImplementation((id: string) => {
      const d = deferred<ReturnType<typeof raw>[]>();
      reads.set(id, d);
      return d.promise;
    });
    const ids = ["a", "b", "c", "d", "e", "f"];
    useEmailStore.setState({ accounts: ids.map((id) => box(id, id === "a")), viewAll: false, allFolderCounts: null });
    useEmailStore.getState().selectAll();
    await flush();
    expect(FOLDER_SUM_CONCURRENCY).toBe(4);
    expect(api.listEmailFolders).toHaveBeenCalledTimes(4);
    reads.get("a")!.resolve([raw("Inbox", 1)]);
    await flush();
    expect(api.listEmailFolders).toHaveBeenCalledTimes(5);
    for (const id of ids) reads.get(id)?.resolve([raw("Inbox", 1)]);
    await flush();
    for (const id of ids) reads.get(id)?.resolve([raw("Inbox", 1)]);
    await flush();
    await flush();
    expect(api.listEmailFolders).toHaveBeenCalledTimes(6);
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(6);
  });

  it("runs one round at a time: a request while one is out asks for one more", async () => {
    const first = deferred<ReturnType<typeof raw>[]>();
    const second = deferred<ReturnType<typeof raw>[]>();
    api.listEmailFolders
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce([raw("Inbox", 2)])
      .mockReturnValue(second.promise);
    useEmailStore.setState({ viewAll: true, allFolderCounts: null });
    void useEmailStore.getState().fetchAllFolderCounts();
    void useEmailStore.getState().fetchAllFolderCounts();
    void useEmailStore.getState().fetchAllFolderCounts();
    await flush();
    // One round of two reads is out. The two later requests start nothing.
    expect(api.listEmailFolders).toHaveBeenCalledTimes(2);
    first.resolve([raw("Inbox", 100)]);
    await flush();
    await flush();
    // One more round, not two, and the stale first round never lands.
    expect(api.listEmailFolders).toHaveBeenCalledTimes(4);
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
    second.resolve([raw("Inbox", 3)]);
    await flush();
    await flush();
    expect(api.listEmailFolders).toHaveBeenCalledTimes(4);
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(6);
  });

  it("reads nothing outside All inboxes, and a view that left takes nothing", async () => {
    useEmailStore.setState({ viewAll: false, allFolderCounts: null });
    await useEmailStore.getState().fetchAllFolderCounts();
    expect(api.listEmailFolders).not.toHaveBeenCalled();
    const out = deferred<ReturnType<typeof raw>[]>();
    api.listEmailFolders.mockReturnValue(out.promise);
    useEmailStore.setState({ viewAll: true });
    const run = useEmailStore.getState().fetchAllFolderCounts();
    await flush();
    useEmailStore.getState().selectAccount("b");
    out.resolve([raw("Inbox", 9)]);
    await run;
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
  });

  it("reads the sums again when a mailbox leaves All inboxes", async () => {
    api.listEmailFolders.mockResolvedValue([raw("Inbox", 3)]);
    useEmailStore.setState({ accounts: [box("a", true), box("b"), box("c")], allFolderCounts: null });
    await useEmailStore.getState().deleteAccount("c");
    await flush();
    await flush();
    expect(api.listEmailFolders.mock.calls.map((call) => call[0]).sort()).toEqual(["a", "b"]);
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(6);
  });
});

// WS-17 EM-T8f-3 review round 1. More of the store half of
// `email-all-folder-sums`:
//   * F1: each path into All inboxes reads the sums. They are the first load,
//     a fallback when the selected mailbox is gone, and a removal in another
//     tab.
//   * F2: a Refresh of N mailboxes whose syncs end apart reads at most 2
//     rounds. A second Refresh moves the read, and a sync alone reads none.
//   * F4: the sums go when the member leaves All inboxes, and when a mailbox
//     leaves, so an old sum never shows while a new round is out.
//   * F5: a hung read adds 0 after FOLDER_SUM_TIMEOUT_MS, and the next
//     request runs.
describe("email-all-folder-sums, the review round", () => {
  function deferred<T>() {
    let resolve: (v: T) => void = () => {};
    const promise = new Promise<T>((res) => { resolve = res; });
    return { promise, resolve };
  }
  const raw = (name: string, count: number) => ({
    provider_folder_id: name, name, type: "system", message_count: count, unread_count: 0,
  });
  const SUMS = { inbox: 99, drafts: 0, sent: 0, archive: 0, junk: 0, trash: 0 };
  beforeEach(async () => {
    await flush();
    await flush();
    api.listEmailFolders.mockReset();
    api.listEmailFolders.mockResolvedValue([raw("Inbox", 4)]);
    useEmailStore.setState({ allFolderCounts: null });
  });

  it("F1: reads the sums on the first load into All inboxes", async () => {
    useEmailStore.setState({ accounts: [], selectedAccountId: null, viewAll: false });
    api.listEmailAccounts.mockResolvedValue([box("a", true), box("b")]);
    await useEmailStore.getState().fetchAccounts();
    await flush();
    await flush();
    expect(useEmailStore.getState().viewAll).toBe(true);
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(8);
  });

  it("F1: reads the sums again when the selected mailbox is gone", async () => {
    useEmailStore.setState({ allFolderCounts: SUMS });
    api.listEmailAccounts.mockResolvedValue([box("b", true), box("c")]);
    await useEmailStore.getState().fetchAccounts();
    await flush();
    await flush();
    expect(useEmailStore.getState().viewAll).toBe(true);
    expect(api.listEmailFolders.mock.calls.map((c) => c[0])).toContain("c");
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(8);
  });

  it("F1 and F4: a removal in another tab clears the sums, then reads them again", async () => {
    const out = deferred<ReturnType<typeof raw>[]>();
    api.listEmailFolders.mockReturnValue(out.promise);
    useEmailStore.setState({ accounts: [box("a", true), box("b"), box("c")], allFolderCounts: SUMS });
    api.listEmailAccounts.mockResolvedValue([box("a", true), box("b")]);
    await useEmailStore.getState().fetchAccounts();
    await flush();
    // The old sum, with c in it, never shows while the new round is out.
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
    expect(api.listEmailFolders.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b"]);
    out.resolve([raw("Inbox", 5)]);
    await flush();
    await flush();
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(10);
  });

  it("F4: leaving All inboxes clears the sums", () => {
    useEmailStore.setState({ allFolderCounts: SUMS });
    useEmailStore.getState().selectAccount("a");
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
  });

  it("F4: a disconnect clears the sums before the new round lands", async () => {
    const out = deferred<ReturnType<typeof raw>[]>();
    api.listEmailFolders.mockReturnValue(out.promise);
    useEmailStore.setState({ accounts: [box("a", true), box("b"), box("c")], allFolderCounts: SUMS });
    await useEmailStore.getState().deleteAccount("c");
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
    out.resolve([raw("Inbox", 1)]);
    await flush();
    await flush();
  });

  it("F4: open A, disconnect C, return to All inboxes: no old sum shows", async () => {
    useEmailStore.setState({ accounts: [box("a", true), box("b"), box("c")], allFolderCounts: SUMS });
    useEmailStore.getState().selectAccount("a");
    await flush();
    await useEmailStore.getState().deleteAccount("c");
    const out = deferred<ReturnType<typeof raw>[]>();
    api.listEmailFolders.mockReturnValue(out.promise);
    useEmailStore.getState().selectAll();
    await flush();
    expect(useEmailStore.getState().viewAll).toBe(true);
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
    out.resolve([raw("Inbox", 2)]);
    await flush();
    await flush();
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(4);
  });

  describe("with fake timers", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    const ids = ["a", "b", "c", "d", "e", "f", "g", "h"];
    function eightMailboxes() {
      const accounts = ids.map((id) => box(id, id === "a"));
      useEmailStore.setState({ accounts, selectedAccountId: "a", viewAll: true, allFolderCounts: null });
      api.listEmailAccounts.mockResolvedValue(accounts);
      // Each folder read takes 800 ms, as in the probe of the review.
      api.listEmailFolders.mockImplementation(
        () => new Promise((r) => setTimeout(() => r([raw("Inbox", 1)]), 800)),
      );
    }
    // `catchUp` reads the tree of the selected mailbox "a" only, so the reads
    // of "h" count the rounds of the sums.
    const rounds = () => api.listEmailFolders.mock.calls.filter((c) => c[0] === "h").length;

    it("F2: a Refresh whose N syncs end 3 s apart reads at most 2 rounds", async () => {
      eightMailboxes();
      api.triggerSync.mockImplementation(
        (id: string) => new Promise((r) => setTimeout(() => r({}), 3000 * (ids.indexOf(id) + 1))),
      );
      useEmailStore.getState().syncScope();
      await vi.advanceTimersByTimeAsync(120_000);
      expect(rounds()).toBeGreaterThanOrEqual(1);
      expect(rounds()).toBeLessThanOrEqual(2);
      expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(8);
    });

    it("F2: the read comes after the last sync, and a second Refresh moves it", async () => {
      eightMailboxes();
      api.triggerSync.mockResolvedValue({});
      useEmailStore.getState().syncScope();
      await vi.advanceTimersByTimeAsync(FOLDER_SUMS_AFTER_SYNC_MS - 1000);
      expect(rounds()).toBe(0);
      useEmailStore.getState().syncScope();
      await vi.advanceTimersByTimeAsync(FOLDER_SUMS_AFTER_SYNC_MS - 1000);
      expect(rounds()).toBe(0);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(rounds()).toBe(1);
    });

    it("F2: a sync of one mailbox alone reads no sums", async () => {
      eightMailboxes();
      api.triggerSync.mockResolvedValue({});
      await useEmailStore.getState().triggerSync("h");
      await vi.advanceTimersByTimeAsync(60_000);
      expect(rounds()).toBe(0);
    });

    it("F5: a hung read adds 0 after the timeout, and the next request runs", async () => {
      useEmailStore.setState({ accounts: [box("a", true), box("b")], viewAll: true });
      api.listEmailFolders.mockImplementation((id: string) =>
        id === "b" ? new Promise(() => {}) : Promise.resolve([raw("Inbox", 7)]),
      );
      void useEmailStore.getState().fetchAllFolderCounts();
      await vi.advanceTimersByTimeAsync(FOLDER_SUM_TIMEOUT_MS - 1);
      expect(useEmailStore.getState().allFolderCounts).toBeNull();
      await vi.advanceTimersByTimeAsync(1);
      expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(7);
      api.listEmailFolders.mockImplementation(() => Promise.resolve([raw("Inbox", 2)]));
      const next = useEmailStore.getState().fetchAllFolderCounts();
      await vi.advanceTimersByTimeAsync(0);
      await next;
      expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(4);
      expect(FOLDER_SUM_TIMEOUT_MS).toBe(15_000);
    });
  });
});

// ── WS-17 EM-T8g-2 — "Keep separate", the store half ───────────────────────

describe("email-separate-leaves-at-once", () => {
  const row = (id: string) => ({
    id, accountId: id.split("-")[0], threadId: id, categories: [], folder: "inbox", isRead: true,
  });
  const raw = (name: string, count: number) => ({
    provider_folder_id: name, name, type: "system", message_count: count, unread_count: 0,
  });
  const sep = (id: string): EmailAccount => ({ ...box(id), inAllInboxes: false });
  const SUMS = { inbox: 99, drafts: 0, sent: 0, archive: 0, junk: 0, trash: 0 };
  beforeEach(async () => {
    await flush();
    await flush();
    api.listEmailFolders.mockReset();
    api.listEmailFolders.mockResolvedValue([raw("Inbox", 3)]);
    // The PATCH answer holds no default flag and no unread count.
    api.setMailboxPooled.mockImplementation(async (id: string, pooled: boolean) =>
      ({ ...box(id), inAllInboxes: pooled }));
    useEmailStore.setState({
      accounts: [box("a", true), box("b"), box("c")],
      selectedEmailId: null,
      selectedEmailOverride: null,
      selectedIds: new Set(),
      allFolderCounts: null,
      error: null,
    });
  });

  /** A read that the test settles by hand, so an old sum cannot land first.
   *  `afterEach` settles it too, so a failed case leaves no round out. */
  let pending: (() => Promise<void>) | null = null;
  afterEach(async () => {
    if (pending) await pending();
    pending = null;
  });
  function held() {
    let resolve: (v: ReturnType<typeof raw>[]) => void = () => {};
    const promise = new Promise<ReturnType<typeof raw>[]>((r) => { resolve = r; });
    api.listEmailFolders.mockReturnValue(promise);
    pending = async () => {
      resolve([raw("Inbox", 1)]);
      await flush();
      await flush();
    };
    return pending;
  }

  it("sends the PATCH, and the rows, the checks and the open mail of the mailbox leave at once", async () => {
    const release = held();
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({
      emails: [row("a-1"), row("b-1"), row("c-1"), row("c-2")] as never,
      selectedEmailId: "c-1",
      selectedIds: new Set(["b-1", "c-2"]),
      allFolderCounts: SUMS,
    });
    const ok = await useEmailStore.getState().setInAllInboxes("c", false);
    const s = useEmailStore.getState();
    expect(ok).toBe(true);
    expect(api.setMailboxPooled).toHaveBeenCalledWith("c", false);
    expect(s.emails.map((e) => e.id)).toEqual(["a-1", "b-1"]);
    expect([...s.selectedIds]).toEqual(["b-1"]);
    expect([s.selectedEmailId, s.viewAll]).toEqual([null, true]);
    expect(s.accounts.map((m) => m.inAllInboxes !== false)).toEqual([true, true, false]);
    // The old sum, with c in it, never shows while the new round is out.
    expect(s.allFolderCounts).toBeNull();
    // The list of All inboxes is read again, with no account_id.
    expect(api.listEmails).toHaveBeenCalledTimes(1);
    expect(api.listEmails.mock.calls[0][0].accountId).toBeUndefined();
    await release();
  });

  it("reads the sums again over the pooled mailboxes", async () => {
    await useEmailStore.getState().setInAllInboxes("c", false);
    await flush();
    await flush();
    expect(api.listEmailFolders.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b"]);
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(6);
  });

  it("puts a mailbox back: the list and the sums are read again with it", async () => {
    const release = held();
    useEmailStore.setState({ accounts: [box("a", true), box("b"), sep("c")], allFolderCounts: SUMS });
    await useEmailStore.getState().setInAllInboxes("c", true);
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
    expect(api.listEmails).toHaveBeenCalledTimes(1);
    expect(api.listEmails.mock.calls[0][0].accountId).toBeUndefined();
    await release();
    expect(api.listEmailFolders.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b", "c"]);
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(3);
  });

  it("ends All inboxes for the default mailbox when fewer than two are pooled", async () => {
    useEmailStore.setState({ accounts: [box("a", true), box("b")], selectedAccountId: "b", viewAll: true });
    await useEmailStore.getState().setInAllInboxes("b", false);
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId]).toEqual([false, "a"]);
    expect(api.listEmails.mock.calls.at(-1)?.[0].accountId).toBe("a");
  });

  it("moves only the flag: the default and the unread count stay", async () => {
    useEmailStore.setState({ accounts: [{ ...box("a", true), unreadCount: 4 }, box("b"), box("c")] });
    await useEmailStore.getState().setInAllInboxes("a", false);
    const a = useEmailStore.getState().accounts[0];
    expect([a.isDefault, a.unreadCount, a.inAllInboxes]).toEqual([true, 4, false]);
  });

  it("changes nothing on a refusal, says so, and reads the accounts again", async () => {
    api.setMailboxPooled.mockRejectedValue(Object.assign(new Error("No fields to update"), { status: 400 }));
    api.listEmailAccounts.mockResolvedValue([box("a", true), box("b"), box("c")]);
    useEmailStore.setState({ emails: [row("a-1"), row("c-1")] as never, selectedEmailId: "c-1" });
    const ok = await useEmailStore.getState().setInAllInboxes("c", false);
    const s = useEmailStore.getState();
    expect(ok).toBe(false);
    expect(s.error).toBe("No fields to update");
    expect([s.emails.map((e) => e.id), s.selectedEmailId, s.viewAll]).toEqual([["a-1", "c-1"], "c-1", true]);
    expect(s.accounts.every((m) => m.inAllInboxes !== false)).toBe(true);
    expect(api.listEmails).not.toHaveBeenCalled();
    // The list on screen becomes the server's again (review F6).
    expect(api.listEmailAccounts).toHaveBeenCalledTimes(1);
  });

  it("takes the answer of the server over the request", async () => {
    // The server kept the mailbox in All inboxes (review F6).
    api.setMailboxPooled.mockResolvedValue({ ...box("c"), inAllInboxes: true });
    useEmailStore.setState({ emails: [row("a-1"), row("c-1")] as never });
    expect(await useEmailStore.getState().setInAllInboxes("c", false)).toBe(true);
    const s = useEmailStore.getState();
    expect(s.accounts.every((m) => m.inAllInboxes !== false)).toBe(true);
    expect([s.emails.map((e) => e.id), s.viewAll]).toEqual([["a-1", "c-1"], true]);
    expect(api.listEmails).not.toHaveBeenCalled();
  });

  it("moves only the flag outside All inboxes", async () => {
    useEmailStore.setState({ viewAll: false, selectedAccountId: "a" });
    await useEmailStore.getState().setInAllInboxes("c", false);
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId]).toEqual([false, "a"]);
    expect(api.listEmails).not.toHaveBeenCalled();
  });

  it("opens a mail of a separate mailbox in that mailbox, never in All inboxes", async () => {
    useEmailStore.setState({ accounts: [box("a", true), box("b"), sep("c")], viewAll: true, selectedAccountId: "a" });
    api.getEmail.mockResolvedValue({ id: "c-9", accountId: "c", threadId: "c-9" });
    await useEmailStore.getState().openEmailById("c-9");
    let s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId, s.selectedEmailId, s.selectedEmailOverride?.id])
      .toEqual([false, "c", "c-9", "c-9"]);
    // A mail of a pooled mailbox stays in All inboxes, with its chip.
    useEmailStore.setState({ viewAll: true, selectedAccountId: "a", emails: [] });
    api.getEmail.mockResolvedValue({ id: "b-9", accountId: "b", threadId: "b-9" });
    await useEmailStore.getState().openEmailById("b-9");
    s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId, s.selectedEmailOverride?.id]).toEqual([true, "a", "b-9"]);
  });

  it("drops the rows, the checks and the open mail of a mailbox that another tab kept separate", async () => {
    const release = held();
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({
      emails: [row("a-1"), row("c-1"), row("c-2")] as never,
      selectedEmailId: "c-1",
      selectedIds: new Set(["a-1", "c-2"]),
      allFolderCounts: SUMS,
    });
    api.listEmailAccounts.mockResolvedValue([box("a", true), box("b"), sep("c")]);
    await useEmailStore.getState().fetchAccounts();
    const s = useEmailStore.getState();
    expect([s.emails.map((e) => e.id), s.selectedEmailId, s.viewAll, s.allFolderCounts])
      .toEqual([["a-1"], null, true, null]);
    // A check that stayed would let a bulk act reach a hidden mail (review F3).
    expect([...s.selectedIds]).toEqual(["a-1"]);
    await release();
    expect(api.listEmailFolders.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b"]);
  });

  it("reads the list and the sums again when another tab puts a mailbox back", async () => {
    const release = held();
    useEmailStore.setState({ accounts: [box("a", true), box("b"), sep("c")], allFolderCounts: SUMS });
    api.listEmailAccounts.mockResolvedValue([box("a", true), box("b"), box("c")]);
    await useEmailStore.getState().fetchAccounts();
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
    expect(api.listEmails).toHaveBeenCalledTimes(1);
    await release();
    expect(api.listEmailFolders.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b", "c"]);
  });

  it("reads the list and the sums again when another tab connects a mailbox", async () => {
    const release = held();
    useEmailStore.setState({ accounts: [box("a", true), box("b")], allFolderCounts: SUMS });
    api.listEmailAccounts.mockResolvedValue([box("a", true), box("b"), box("d")]);
    await useEmailStore.getState().fetchAccounts();
    expect(useEmailStore.getState().allFolderCounts).toBeNull();
    expect(api.listEmails).toHaveBeenCalledTimes(1);
    await release();
    expect(api.listEmailFolders.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b", "d"]);
  });
});

// ── WS-17 EM-T8g-2 review round 1 ──────────────────────────────────────────
//   * F1: a disconnect reads the pool, not the count of mailboxes.
//   * F4: only the newest list read lands, and a background read or a page
//     that started under another pool drops its answer.
//   * F5: the hidden selected mailbox of All inboxes stays pooled.
//   * F7: a quiet re-read reconciles the pool, as a full re-read does.
describe("email-separate-leaves-at-once, review round 1", () => {
  const row = (id: string) => ({
    id, accountId: id.split("-")[0], threadId: id, categories: [], folder: "inbox", isRead: true,
  });
  const sep = (id: string, isDefault = false): EmailAccount => ({ ...box(id, isDefault), inAllInboxes: false });
  function deferred<T>() {
    let resolve: (v: T) => void = () => {};
    const promise = new Promise<T>((r) => { resolve = r; });
    return { promise, resolve };
  }
  beforeEach(async () => {
    await flush();
    await flush();
    api.listEmailFolders.mockReset();
    api.listEmailFolders.mockResolvedValue([]);
    api.setMailboxPooled.mockImplementation(async (id: string, pooled: boolean) =>
      ({ ...box(id), inAllInboxes: pooled }));
    useEmailStore.setState({
      accounts: [box("a", true), box("b"), box("c")],
      selectedEmailId: null,
      selectedEmailOverride: null,
      selectedIds: new Set(),
      allFolderCounts: null,
      emailsPage: 1,
      emailsTotal: 0,
      loadingMore: false,
      searchQuery: "",
      searchFilters: [],
      error: null,
    });
  });
  afterEach(async () => {
    await flush();
    await flush();
  });

  it("F1: a disconnect that leaves one pooled mailbox ends All inboxes", async () => {
    useEmailStore.setState({ accounts: [box("a", true), box("b"), sep("c")] });
    await useEmailStore.getState().deleteAccount("b");
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId]).toEqual([false, "a"]);
    expect(api.listEmails.mock.calls.at(-1)?.[0].accountId).toBe("a");
  });

  it("F1: a disconnect of the hidden mailbox moves it to a pooled mailbox", async () => {
    // The default, c, is separate. All inboxes keeps a, and a goes.
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({
      accounts: [sep("c", true), box("a"), box("b"), box("d")],
      selectedAccountId: "a",
      emails: [row("a-1"), row("b-1")] as never,
    });
    await useEmailStore.getState().deleteAccount("a");
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId]).toEqual([true, "b"]);
    expect(s.emails.map((e) => e.id)).toEqual(["b-1"]);
    expect(api.listEmailFolders.mock.calls.map((c) => c[0])).toContain("b");
    expect(api.listLabels.mock.calls.map((c) => c[0])).toEqual(["b"]);
  });

  it("F4: a list read that started before the toggle never lands after it", async () => {
    const old = deferred<ReturnType<typeof page>>();
    api.listEmails.mockReturnValueOnce(old.promise).mockResolvedValue(page(["a-1", "b-1"]));
    const stale = useEmailStore.getState().fetchEmails();
    await useEmailStore.getState().setInAllInboxes("c", false);
    await flush();
    old.resolve(page(["a-1", "b-1", "c-1"]));
    await stale;
    await flush();
    expect(useEmailStore.getState().emails.map((e) => e.id)).toEqual(["a-1", "b-1"]);
  });

  it("F4: a stale read that fails leaves the error and the list to the newer read", async () => {
    let fail: (e: unknown) => void = () => {};
    api.listEmails
      .mockReturnValueOnce(new Promise((_, reject) => { fail = reject; }))
      .mockResolvedValue(page(["a-1"]));
    const stale = useEmailStore.getState().fetchEmails();
    await useEmailStore.getState().fetchEmails();
    fail(new Error("Gateway error 502"));
    await stale;
    const s = useEmailStore.getState();
    expect([s.error, s.emailsLoading, s.emails.map((e) => e.id)]).toEqual([null, false, ["a-1"]]);
  });

  it("F4: a background read that started under another pool drops its answer", async () => {
    useEmailStore.setState({ emails: [row("a-1")] as never, emailsLoading: false });
    const late = deferred<ReturnType<typeof page>>();
    api.listEmails.mockReturnValueOnce(late.promise);
    const refresh = useEmailStore.getState().softRefresh();
    // Another tab keeps c separate. No list read starts here.
    useEmailStore.setState({ accounts: [box("a", true), box("b"), sep("c")] });
    late.resolve(page(["a-1", "c-1"]));
    await refresh;
    expect(useEmailStore.getState().emails.map((e) => e.id)).toEqual(["a-1"]);
  });

  it("F4: a background read drops its answer when a newer read started", async () => {
    useEmailStore.setState({ emails: [row("a-1")] as never, emailsLoading: false });
    const late = deferred<ReturnType<typeof page>>();
    api.listEmails.mockReturnValueOnce(late.promise).mockResolvedValueOnce(page(["a-2"]));
    const refresh = useEmailStore.getState().softRefresh();
    await useEmailStore.getState().fetchEmails();
    late.resolve(page(["a-1"]));
    await refresh;
    expect(useEmailStore.getState().emails.map((e) => e.id)).toEqual(["a-2"]);
  });

  it("F4: a page of older mail drops when the toggle read the list again", async () => {
    useEmailStore.setState({
      emails: [row("a-1"), row("c-1")] as never, emailsTotal: 10, emailsPage: 1,
    });
    const more = deferred<ReturnType<typeof page>>();
    api.listEmails.mockReturnValueOnce(more.promise).mockResolvedValue(page(["a-1", "b-1"]));
    const loading = useEmailStore.getState().loadMoreEmails();
    await useEmailStore.getState().setInAllInboxes("c", false);
    await flush();
    more.resolve(page(["c-2"]));
    await loading;
    const s = useEmailStore.getState();
    expect([s.emails.map((e) => e.id), s.loadingMore]).toEqual([["a-1", "b-1"], false]);
  });

  it("F5: a toggle of the hidden mailbox moves it to the default pooled mailbox", async () => {
    useEmailStore.setState({ selectedAccountId: "a" });
    await useEmailStore.getState().setInAllInboxes("a", false);
    const s = useEmailStore.getState();
    // a was the default. The first pooled mailbox takes its place.
    expect([s.viewAll, s.selectedAccountId]).toEqual([true, "b"]);
    expect(api.listEmailFolders.mock.calls.map((c) => c[0])).toContain("b");
    expect(api.listLabels.mock.calls.map((c) => c[0])).toEqual(["b"]);
  });

  it("F5: All inboxes, opened from a separate mailbox, keeps a pooled one", () => {
    useEmailStore.setState({
      accounts: [box("a", true), box("b"), sep("c")], viewAll: false, selectedAccountId: "c",
    });
    useEmailStore.getState().selectAll();
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId]).toEqual([true, "a"]);
    expect(api.listLabels.mock.calls.map((c) => c[0])).toEqual(["a"]);
    // From a pooled mailbox, nothing moves.
    api.listLabels.mockClear();
    useEmailStore.setState({ viewAll: false, selectedAccountId: "b" });
    useEmailStore.getState().selectAll();
    expect(useEmailStore.getState().selectedAccountId).toBe("b");
    expect(api.listLabels).not.toHaveBeenCalled();
  });

  it("F7: a quiet re-read drops the rows, the checks and the open mail of a mailbox that left", async () => {
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({
      emails: [row("a-1"), row("c-1")] as never,
      selectedEmailId: "c-1",
      selectedIds: new Set(["a-1", "c-1"]),
    });
    api.listEmailAccounts.mockResolvedValue([box("a", true), box("b"), sep("c")]);
    await useEmailStore.getState().refreshAccounts();
    const s = useEmailStore.getState();
    expect([s.emails.map((e) => e.id), [...s.selectedIds], s.selectedEmailId, s.viewAll])
      .toEqual([["a-1"], ["a-1"], null, true]);
    expect(api.listEmails).toHaveBeenCalledTimes(1);
  });

  it("F7: a quiet re-read with one pooled mailbox ends All inboxes", async () => {
    api.listEmailAccounts.mockResolvedValue([box("a", true), sep("b"), sep("c")]);
    await useEmailStore.getState().refreshAccounts();
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId]).toEqual([false, "a"]);
  });
});

// ── WS-17 EM-T8g-2 review round 2 ──────────────────────────────────────────
//   * F3, the class: a check never outlives its row, and a bulk act reaches
//     only the checked rows of the list on screen.
//   * P3: a re-read that removes the hidden mailbox, and `replaceAccount`,
//     reconcile the pool too.
//   * The three mutants that survived round 1: the end of All inboxes goes to
//     the default, an open mail held as an override goes too, and with no
//     mailbox left All inboxes ends.
describe("email-separate-leaves-at-once, review round 2", () => {
  const row = (id: string) => ({
    id, accountId: id.split("-")[0], threadId: id, categories: [], folder: "inbox", isRead: true,
  });
  const sep = (id: string, isDefault = false): EmailAccount => ({ ...box(id, isDefault), inAllInboxes: false });
  const real = { deleteEmail: useEmailStore.getState().deleteEmail, updateEmail: useEmailStore.getState().updateEmail };
  const deleted = vi.fn();
  const updated = vi.fn();
  beforeEach(async () => {
    await flush();
    await flush();
    deleted.mockReset();
    updated.mockReset();
    api.listEmailFolders.mockReset();
    api.listEmailFolders.mockResolvedValue([]);
    api.setMailboxPooled.mockImplementation(async (id: string, pooled: boolean) =>
      ({ ...box(id), inAllInboxes: pooled }));
    // The acts on one mail are spies, so a test sees which ids a bulk act sends.
    useEmailStore.setState({
      accounts: [box("a", true), box("b"), box("c")],
      deleteEmail: deleted as never,
      updateEmail: updated as never,
      selectedEmailId: null,
      selectedEmailOverride: null,
      selectedIds: new Set(),
      allFolderCounts: null,
      emailsPage: 1,
      emailsLoading: false,
      error: null,
    });
  });
  afterEach(async () => {
    await flush();
    await flush();
    useEmailStore.setState(real);
  });

  it("F3 probe: check c-9, a refresh drops it, keep c separate, Delete: only a-1 goes", async () => {
    useEmailStore.setState({ emails: [row("a-1"), row("c-9")] as never, selectedIds: new Set(["a-1", "c-9"]) });
    api.listEmails.mockResolvedValueOnce(page(["a-1"])).mockReturnValue(new Promise(() => {}));
    await useEmailStore.getState().softRefresh();
    await useEmailStore.getState().setInAllInboxes("c", false);
    useEmailStore.getState().bulkDeleteSelected();
    expect(deleted.mock.calls.map((c) => c[0])).toEqual(["a-1"]);
  });

  it("F3 (c): a background refresh that drops a checked row drops its check", async () => {
    useEmailStore.setState({ emails: [row("a-1"), row("c-9")] as never, selectedIds: new Set(["a-1", "c-9"]) });
    api.listEmails.mockResolvedValueOnce(page(["a-1"]));
    await useEmailStore.getState().softRefresh();
    expect([...useEmailStore.getState().selectedIds]).toEqual(["a-1"]);
  });

  it("F3 (c): a list read that drops a checked row drops its check", async () => {
    useEmailStore.setState({ emails: [row("a-1"), row("c-9")] as never, selectedIds: new Set(["a-1", "c-9"]) });
    api.listEmails.mockResolvedValueOnce(page(["a-1"]));
    await useEmailStore.getState().fetchEmails();
    expect([...useEmailStore.getState().selectedIds]).toEqual(["a-1"]);
  });

  it("F3 (b): a bulk act with a stale check reaches only the rows on screen", () => {
    useEmailStore.setState({ emails: [row("a-1")] as never, selectedIds: new Set(["a-1", "c-9"]) });
    useEmailStore.getState().bulkUpdateSelected({ isRead: true });
    expect(updated.mock.calls.map((c) => c[0])).toEqual(["a-1"]);
    useEmailStore.setState({ selectedIds: new Set(["a-1", "c-9"]) });
    useEmailStore.getState().bulkDeleteSelected();
    expect(deleted.mock.calls.map((c) => c[0])).toEqual(["a-1"]);
  });

  it("F3 (a): a toggle keeps only the checks of the rows that stay", async () => {
    api.listEmails.mockReturnValue(new Promise(() => {}));
    // c-9 left the list earlier, and its check stayed.
    useEmailStore.setState({ emails: [row("a-1"), row("b-1")] as never, selectedIds: new Set(["a-1", "c-9"]) });
    await useEmailStore.getState().setInAllInboxes("c", false);
    expect([...useEmailStore.getState().selectedIds]).toEqual(["a-1"]);
  });

  it("P3: a re-read that removes the hidden mailbox and keeps c separate drops the checks", async () => {
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({
      selectedAccountId: "a", viewAll: true,
      emails: [row("a-1"), row("c-1")] as never, selectedIds: new Set(["c-1"]),
    });
    api.listEmailAccounts.mockResolvedValue([box("b", true), sep("c")]);
    await useEmailStore.getState().fetchAccounts();
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId, [...s.selectedIds]]).toEqual([false, "b", []]);
    s.bulkDeleteSelected();
    expect(deleted).not.toHaveBeenCalled();
  });

  it("P3: a re-read that removes the mailbox in view drops its checks and its open mail", async () => {
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({
      accounts: [box("a", true), box("b")], selectedAccountId: "a", viewAll: false,
      emails: [row("a-1")] as never, selectedIds: new Set(["a-1"]), selectedEmailId: "a-1",
    });
    api.listEmailAccounts.mockResolvedValue([box("b", true)]);
    await useEmailStore.getState().fetchAccounts();
    const s = useEmailStore.getState();
    expect([s.selectedAccountId, [...s.selectedIds], s.selectedEmailId]).toEqual(["b", [], null]);
  });

  it("P3: replaceAccount reconciles the pool", async () => {
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({ emails: [row("a-1"), row("c-1")] as never, selectedIds: new Set(["a-1", "c-1"]) });
    useEmailStore.getState().replaceAccount(sep("c"));
    const s = useEmailStore.getState();
    expect([s.emails.map((e) => e.id), [...s.selectedIds], s.viewAll]).toEqual([["a-1"], ["a-1"], true]);
  });

  it("V-F5b: a toggle that ends All inboxes goes to the default, also a separate one", async () => {
    useEmailStore.setState({ accounts: [sep("c", true), box("a"), box("b")], selectedAccountId: "a", viewAll: true });
    await useEmailStore.getState().setInAllInboxes("b", false);
    const s = useEmailStore.getState();
    expect([s.viewAll, s.selectedAccountId]).toEqual([false, "c"]);
  });

  it("V-F3c: the open mail of a mailbox that left goes, also when it is not a row", async () => {
    api.listEmails.mockReturnValue(new Promise(() => {}));
    useEmailStore.setState({
      emails: [row("a-1")] as never,
      selectedEmailId: "c-9",
      selectedEmailOverride: row("c-9") as never,
    });
    await useEmailStore.getState().setInAllInboxes("c", false);
    const s = useEmailStore.getState();
    expect([s.selectedEmailId, s.selectedEmailOverride]).toEqual([null, null]);
  });

  it("V-ALL0: with no mailbox left, All inboxes ends and nothing of the list stays", async () => {
    useEmailStore.setState({ emails: [row("a-1")] as never, selectedIds: new Set(["a-1"]), selectedAccountId: "a" });
    api.listEmailAccounts.mockResolvedValue([]);
    await useEmailStore.getState().fetchAccounts();
    const s = useEmailStore.getState();
    expect([s.viewAll, s.emails.length, s.selectedIds.size]).toEqual([false, 0, 0]);
  });
});

describe("email-all-skips-separate, the store half", () => {
  const raw = (name: string, count: number) => ({
    provider_folder_id: name, name, type: "system", message_count: count, unread_count: 0,
  });
  const sep = (id: string): EmailAccount => ({ ...box(id), inAllInboxes: false });
  beforeEach(async () => {
    await flush();
    await flush();
    api.listEmailFolders.mockReset();
    api.listEmailFolders.mockResolvedValue([raw("Inbox", 2)]);
    useEmailStore.setState({ accounts: [box("a", true), box("b"), sep("c")], allFolderCounts: null });
  });

  it("sums the folders of the pooled mailboxes only", async () => {
    await useEmailStore.getState().fetchAllFolderCounts();
    expect(api.listEmailFolders.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b"]);
    expect(useEmailStore.getState().allFolderCounts?.inbox).toBe(4);
  });

  it("reads no sums when fewer than two are pooled", async () => {
    useEmailStore.setState({ accounts: [box("a", true), sep("c")] });
    await useEmailStore.getState().fetchAllFolderCounts();
    expect(api.listEmailFolders).not.toHaveBeenCalled();
  });

  it("ends All inboxes on a re-read that finds one pooled mailbox", async () => {
    api.listEmailAccounts.mockResolvedValue([box("a", true), sep("b")]);
    await useEmailStore.getState().fetchAccounts();
    expect(useEmailStore.getState().viewAll).toBe(false);
  });

  describe("with fake timers", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it("syncs the pooled mailboxes in All inboxes, and the separate one in its own view", () => {
      useEmailStore.getState().syncScope();
      expect(api.triggerSync.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b"]);
      api.triggerSync.mockClear();
      useEmailStore.setState({ viewAll: false, selectedAccountId: "c" });
      useEmailStore.getState().syncScope();
      expect(api.triggerSync.mock.calls.map((c) => c[0])).toEqual(["c"]);
    });
  });
});
