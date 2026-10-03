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
import { beforeEach, describe, expect, it, vi } from "vitest";

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
}));

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, ...api };
});

import { FOLDER_SUM_CONCURRENCY, useEmailStore } from "./emailStore";
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
