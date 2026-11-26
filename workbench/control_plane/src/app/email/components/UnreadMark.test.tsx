// EM-RU (task 3688): the unread signal of a mail row. Node environment, so
// the markup comes from react-dom/server and the handler is called directly.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  ReadToggle,
  UnreadBar,
  UnreadLabel,
  readToggleHandler,
  readTone,
  rowButtonClass,
} from "./UnreadMark";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

describe("an unread row", () => {
  it("shows the bar, the weight and Unread in the name", () => {
    expect(html(createElement(UnreadBar, { isRead: false }))).toContain('data-unread-bar="on"');
    expect(html(createElement(UnreadBar, { isRead: false }))).toContain("opacity-100");
    expect(html(createElement(UnreadBar, { isRead: false }))).toContain("bg-primary");
    expect(html(createElement(UnreadLabel, { isRead: false }))).toContain("Unread,");
    const t = readTone(false);
    expect(t.sender).toContain("font-semibold");
    expect(t.subject).toContain("font-semibold");
    expect(t.time).toContain("text-primary");
  });
});

describe("a read row", () => {
  it("has no bar, no weight and no Unread label", () => {
    expect(html(createElement(UnreadBar, { isRead: true }))).toContain('data-unread-bar="off"');
    expect(html(createElement(UnreadBar, { isRead: true }))).toContain("opacity-0");
    expect(html(createElement(UnreadLabel, { isRead: true }))).toBe("");
    const t = readTone(true);
    expect(t.sender).not.toContain("font-semibold");
    expect(t.subject).not.toContain("font-semibold");
    expect(t.time).not.toContain("text-primary");
  });
});

describe("a selected row", () => {
  it("is a fill and has no left border", () => {
    const cls = rowButtonClass(true);
    expect(cls).toContain("bg-primary/10");
    expect(cls).not.toMatch(/border-l/);
    expect(rowButtonClass(false)).not.toMatch(/border-l/);
  });
});

describe("the read toggle", () => {
  it("names the act in the title and the label", () => {
    const unread = html(createElement(ReadToggle, { email: { id: "a", isRead: false }, update: vi.fn() }));
    expect(unread).toContain('aria-label="Mark as read"');
    expect(unread).toContain('title="Mark as read"');
    const read = html(createElement(ReadToggle, { email: { id: "a", isRead: true }, update: vi.fn() }));
    expect(read).toContain('aria-label="Mark as unread"');
  });

  it("calls the update once and does not open the mail", () => {
    const update = vi.fn();
    const stopPropagation = vi.fn();
    const preventDefault = vi.fn();
    readToggleHandler({ id: "m1", isRead: false }, update)({ stopPropagation, preventDefault });
    expect(update).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledWith("m1", { isRead: true });
    expect(stopPropagation).toHaveBeenCalled();
    readToggleHandler({ id: "m1", isRead: true }, update)({ stopPropagation, preventDefault });
    expect(update).toHaveBeenLastCalledWith("m1", { isRead: false });
  });
});
