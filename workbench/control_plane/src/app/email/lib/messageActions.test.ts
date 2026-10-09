// The action row of one message (owner, 2026-10-10), and "Loading message…".
//
// R7 fences named here:
//   * `email-row-tiers`: a wide card shows labels, a medium one icons, and a
//     narrow one moves Reply all and Forward into the menu.
//   * `email-row-menu`: the menu holds the owner's groups in order, each item
//     names a real icon, and the light toggle draws in one place only.
//   * `email-row-keys`: Up, Down, Home and End move and wrap.
//   * `email-row-per-message`: each card of a thread and the single email get
//     the same row, and each handler gets THAT message.
//   * `email-capture-thread-message`: Add to My Tasks opens for a message of a
//     thread that is not a row of the list, in that message's mailbox.
//   * `email-loading-per-message`: "Loading message…" belongs to one fetch of
//     one mail, and only while that mail has no body.
//   * `email-patch-per-message`: a read, flag, star or label change of a
//     message that is not a row of the list reaches its card, and a thread
//     move does not change the count of the list (review fix round 1).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  updateEmail: vi.fn(),
  updateEmailLabels: vi.fn(),
}));
vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, ...api };
});

import { isKnownIcon } from "@/lib/icons";
import { useEmailStore, withPatch } from "./emailStore";
import type { Email } from "./types";
import {
  ICONS_MIN_PX, LABELS_MIN_PX, actionRowTier, lightToggleInRow, menuStep, messageMenuGroups,
  type MenuState,
} from "./messageActions";

const read = (rel: string) =>
  readFileSync(join(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const STATE: MenuState = {
  tier: "labels", dark: true, lightVersion: false, isRead: true, isFlagged: false, isStarred: false,
};
const ids = (s: MenuState) => messageMenuGroups(s).map((g) => g.map((i) => i.id));

describe("email-row-tiers", () => {
  it("hides the labels first, then moves Reply all and Forward into the menu", () => {
    expect(actionRowTier(0)).toBe("labels");
    expect(actionRowTier(LABELS_MIN_PX)).toBe("labels");
    expect(actionRowTier(LABELS_MIN_PX - 1)).toBe("icons");
    expect(actionRowTier(ICONS_MIN_PX)).toBe("icons");
    expect(actionRowTier(ICONS_MIN_PX - 1)).toBe("collapsed");
    expect(ids({ ...STATE, tier: "icons" })[0]).toEqual(["tasks"]);
    expect(ids({ ...STATE, tier: "collapsed" })[0]).toEqual(["reply-all", "forward"]);
  });
});

describe("email-row-menu", () => {
  it("lists the owner's groups, in order", () => {
    expect(ids(STATE)).toEqual([
      ["tasks"],
      ["archive", "delete", "move", "label"],
      ["read", "flag", "star"],
      ["print", "download"],
      ["activity"],
      ["block", "junk"],
    ]);
  });

  it("names each item by the message's state", () => {
    const labels = (s: MenuState) => messageMenuGroups(s).flat().map((i) => i.label);
    expect(labels(STATE)).toEqual(expect.arrayContaining(["Mark as unread", "Flag", "Star"]));
    expect(labels({ ...STATE, isRead: false, isFlagged: true, isStarred: true }))
      .toEqual(expect.arrayContaining(["Mark as read", "Unflag", "Unstar"]));
  });

  it("draws the light toggle in the row, or in the menu when the row is narrow, and never in light mode", () => {
    expect(lightToggleInRow(STATE)).toBe(true);
    expect(ids(STATE).flat()).not.toContain("light");
    const narrow = { ...STATE, tier: "collapsed" as const };
    expect(lightToggleInRow(narrow)).toBe(false);
    expect(ids(narrow).flat()).toContain("light");
    for (const tier of ["labels", "icons", "collapsed"] as const) {
      expect(lightToggleInRow({ tier, dark: false })).toBe(false);
      expect(ids({ ...STATE, tier, dark: false }).flat()).not.toContain("light");
    }
  });

  it("names a real icon for each item", () => {
    for (const s of [STATE, { ...STATE, tier: "collapsed" as const }, { ...STATE, lightVersion: true, tier: "collapsed" as const }]) {
      for (const item of messageMenuGroups(s).flat()) expect(isKnownIcon(item.icon), item.icon).toBe(true);
    }
  });
});

describe("email-row-keys", () => {
  it("moves and wraps", () => {
    expect(menuStep(5, 0, "ArrowDown")).toBe(1);
    expect(menuStep(5, 4, "ArrowDown")).toBe(0);
    expect(menuStep(5, 0, "ArrowUp")).toBe(4);
    expect(menuStep(5, 3, "Home")).toBe(0);
    expect(menuStep(5, 1, "End")).toBe(4);
    expect(menuStep(5, 2, "a")).toBe(2);
    expect(menuStep(0, 0, "ArrowDown")).toBe(-1);
  });

  it("closes on Escape with focus back on the button, and Tab leaves all but the label view", () => {
    const src = codeOnly(read("components/MessageActions.tsx"));
    expect(src).toMatch(/event\.key === "Escape"\) \{[\s\S]{0,120}close\(true\);/);
    expect(src).toMatch(/event\.key === "Tab"\) \{\s*if \(view !== "label" \|\| from < 0\) \{\s*close\(false\);/);
    // The label view's toggles and its text box are stops of the same keys.
    expect(src).toContain(`const MENU_STOPS = '[role="menuitem"], [role="menuitemcheckbox"], [data-menu-stop]';`);
    const labels = codeOnly(read("components/LabelMenu.tsx"));
    expect(labels).toContain('role={menu ? "menuitemcheckbox" : undefined}');
    expect(labels).toContain("aria-checked={menu ? on : undefined}");
    expect(labels).toContain('data-menu-stop={menu ? "" : undefined}');
    expect(src).toContain('panelProps={{ role: "menu", "aria-label": "More actions", onKeyDown: onMenuKey }}');
    expect(src).toContain('aria-haspopup="menu"');
    expect(src).toContain("aria-expanded={open}");
  });
});

describe("email-row-per-message", () => {
  const detail = codeOnly(read("components/EmailDetail.tsx"));
  const conversation = codeOnly(read("components/ConversationView.tsx"));

  it("draws one row on the single email and on each card of a thread", () => {
    expect(detail.match(/\{messageActions\(email\)\}/g)).toHaveLength(1);
    expect(detail).toContain("renderActions={messageActions}");
    expect(conversation).toContain("{renderActions?.(view)}");
    // The old trio of a card and the big capture button are gone.
    expect(conversation).not.toContain("CardAction");
    expect(detail).not.toContain('<span className="hidden sm:inline">Add to My Tasks</span>');
  });

  it("gives every handler the message of the row", () => {
    const row = detail.slice(detail.indexOf("const messageActions = (m: Email) =>"));
    const block = row.slice(0, row.indexOf("/>") + 2);
    for (const wire of [
      "onReply={(mode) => startReply(mode, m)}",
      "onUpdate={(updates) => actOn(m, updates)}",
      "onDelete={() => deleteOne(m)}",
      "onTasks={() => captureEmailToTasks(m.id, m.accountId)}",
      "onBlock={() => void blockSender(m)}",
      "onDownload={() => downloadEml(m)}",
      "onActivity={() => setTimelineFor(m)}",
      "lightVersion={lightVersions.has(m.id)}",
      "onToggleLight={() => toggleLightVersion(m.id)}",
    ]) {
      expect(block, wire).toContain(wire);
    }
    // The pane keeps one handler of each: the row calls the store's own.
    expect(detail).toContain("void updateEmail(target.id, updates);");
    expect(detail).toContain("void deleteEmail(target.id);");
  });
});

describe("email-capture-thread-message", () => {
  beforeEach(() => {
    useEmailStore.setState({ emails: [], taskCapturePopupEmailId: null, taskCapturePopupAccountId: null, taskCaptureNotice: null });
  });

  it("opens the popup for a message the list does not hold, in its own mailbox", () => {
    useEmailStore.getState().captureEmailToTasks("thread-msg-2", "box-b");
    const s = useEmailStore.getState();
    expect(s.taskCapturePopupEmailId).toBe("thread-msg-2");
    expect(s.taskCapturePopupAccountId).toBe("box-b");
    expect(s.taskCaptureNotice).toBeNull();
    s.closeTaskCapturePopup();
    expect(useEmailStore.getState().taskCapturePopupAccountId).toBeNull();
  });

  it("still asks to save a draft first when nothing names the message", () => {
    useEmailStore.getState().captureEmailToTasks("unsaved");
    const s = useEmailStore.getState();
    expect(s.taskCapturePopupEmailId).toBeNull();
    expect(s.taskCaptureNotice?.title).toMatch(/Save this draft first/);
  });

  it("the popup reads the named mailbox first", () => {
    expect(codeOnly(read("page.tsx"))).toContain(
      "const acctId = taskCapturePopupAccountId ?? popupEmail?.accountId ?? selectedAccountId;",
    );
  });
});

describe("email-loading-per-message", () => {
  const detail = codeOnly(read("components/EmailDetail.tsx"));

  it("keys the loading line to one mail, and clears it in every fetch", () => {
    // The bug: a boolean that the last mail set to true stayed true when a
    // mail with a body opened before that fetch ended.
    expect(detail).not.toMatch(/setLoadingDetail\(/);
    // A number for each fetch: after A, B, A the end of the first fetch of A
    // cannot clear the second one.
    expect(detail).toContain("const loading = { id: email.id, seq: detailSeqRef.current };");
    expect(detail).toContain("setLoadingDetailFor((cur) => (cur?.seq === loading.seq ? null : cur));");
    expect(detail).toContain(
      "const loadingDetail = loadingDetailFor?.id === email.id && !view.bodyHtml && !view.bodyText;",
    );
  });
});

describe("email-patch-per-message", () => {
  const older = {
    id: "older-1", accountId: "box-a", isRead: true, isFlagged: false, isStarred: false,
    folder: "inbox", categories: ["Clients"],
  } as unknown as Email;

  beforeEach(() => {
    api.updateEmail.mockReset().mockImplementation(async (id: string) => ({ id }));
    api.updateEmailLabels.mockReset().mockImplementation(async (id: string) => ({ id }));
    useEmailStore.setState({ emails: [], emailsTotal: 7, messagePatches: {}, selectedFolder: "inbox" });
  });

  it("toggles read both ways on a message that is not a row of the list", async () => {
    const s = () => useEmailStore.getState();
    await s().updateEmail(older.id, { isRead: false });
    expect(withPatch(older, s().messagePatches).isRead).toBe(false);
    await s().updateEmail(older.id, { isRead: true });
    expect(withPatch(older, s().messagePatches).isRead).toBe(true);
    expect(api.updateEmail.mock.calls).toEqual([[older.id, { isRead: false }], [older.id, { isRead: true }]]);
  });

  it("does not change the count of the list for a move of a thread message", async () => {
    await useEmailStore.getState().updateEmail(older.id, { folder: "archive" });
    expect(useEmailStore.getState().emailsTotal).toBe(7);
  });

  it("adds a label to the labels the caller holds, and shows it at once", async () => {
    await useEmailStore.getState().applyLabel(older.id, "Urgent", true, older.categories);
    expect(withPatch(older, useEmailStore.getState().messagePatches).categories).toEqual(["Clients", "Urgent"]);
    await useEmailStore.getState().applyLabel(older.id, "Clients", false, older.categories);
    expect(withPatch(older, useEmailStore.getState().messagePatches).categories).toEqual(["Urgent"]);
  });

  it("puts the patch back when the write fails", async () => {
    api.updateEmail.mockRejectedValueOnce(new Error("Gateway error 502"));
    await useEmailStore.getState().updateEmail(older.id, { isFlagged: true });
    expect(useEmailStore.getState().messagePatches[older.id]).toBeUndefined();
  });

  it("each card and each menu reads the patch", () => {
    expect(codeOnly(read("components/ConversationView.tsx"))).toContain(
      "const view = withPatch(hydrated[m.id] ?? m, messagePatches);",
    );
    expect(codeOnly(read("components/MessageActions.tsx"))).toContain(
      "const live = withPatch(emails.find((e) => e.id === message.id) ?? message, messagePatches);",
    );
  });
});
