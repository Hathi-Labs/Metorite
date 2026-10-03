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

import { useEmailStore } from "./emailStore";
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
