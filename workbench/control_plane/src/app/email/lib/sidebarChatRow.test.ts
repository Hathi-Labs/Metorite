/**
 * The Email sidebar draws no "Chat" row while the one assistant is on (D99).
 *
 * The owner reported the row on 2026-10-28: the top bar is the only chat
 * surface, and a click on the row already went there (`page.tsx`
 * `handleOpenAutomation`). With the flag off, the row stays, as
 * `lib/shell/chatFlag.ts` promises.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountSidebar, automationItems } from "../components/AccountSidebar";

const render = () =>
  renderToStaticMarkup(
    createElement(AccountSidebar, {
      accounts: [],
      selectedAccountId: "",
      onAccountSelect: () => {},
      folders: [],
      selectedFolder: "inbox",
      onFolderSelect: () => {},
      onOpenAutomation: () => {},
      showMailbox: false,
    }),
  );

const chatRow = (html: string) => />Chat</.test(html);

describe("the Email sidebar and the one assistant (D99)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("draws no Chat row when the one assistant is on", () => {
    vi.stubEnv("NEXT_PUBLIC_SHELL_CHAT", "1");
    vi.stubEnv("NEXT_PUBLIC_SHELL_BAR", "1");
    vi.stubEnv("NEXT_PUBLIC_SHELL_NAV", "1");
    const html = render();
    expect(chatRow(html)).toBe(false);
    // The other automation rows stay.
    expect(html).toContain("Email Cleaner");
    expect(html).toContain("AI Settings");
  });

  it("keeps the Chat row when the one assistant is off", () => {
    vi.stubEnv("NEXT_PUBLIC_SHELL_CHAT", "");
    expect(chatRow(render())).toBe(true);
  });

  it("filters only the chat row", () => {
    const on = automationItems(true).map((item) => item.key);
    const off = automationItems(false).map((item) => item.key);
    expect(off).toContain("chat");
    expect(on).not.toContain("chat");
    expect(on).toEqual(off.filter((key) => key !== "chat"));
  });
});
