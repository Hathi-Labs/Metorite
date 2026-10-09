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
//   * `email-loading-per-message`: "Loading message…" belongs to one mail,
//     and only while that mail has no body.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";
import { useEmailStore } from "./emailStore";
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

  it("closes on Escape with focus back on the button, and closes on Tab", () => {
    const src = codeOnly(read("components/MessageActions.tsx"));
    expect(src).toMatch(/event\.key === "Escape"\) \{[\s\S]{0,120}close\(true\);/);
    expect(src).toMatch(/event\.key === "Tab"\) \{\s*close\(false\);/);
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
    expect(detail).toContain("setLoadingDetailId(loadingId);");
    expect(detail).toContain("setLoadingDetailId((cur) => (cur === loadingId ? null : cur));");
    expect(detail).toContain(
      "const loadingDetail = loadingDetailId === email.id && !view.bodyHtml && !view.bodyText;",
    );
  });
});
