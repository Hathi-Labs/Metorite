/**
 * The one bar-tap signal (MOB-9 fix round, task 2002). A hand-rolled overlay
 * above z-50 listens, or a bar tap opens a sheet under its scrim.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p: string) => readFileSync(join(SRC, p), "utf-8");

describe("every overlay that must give way listens to the bar tap", () => {
  it.each([
    "components/ui/Modal.tsx",
    "app/tasks/components/QuickCapture.tsx",
    "app/tasks/components/ClarifyModal.tsx",
    "app/tasks/components/ReclarifyModal.tsx",
    "app/tasks/components/FocusMode.tsx",
  ])("%s uses useCloseOnBarTap", (file) => {
    expect(read(file)).toContain("useCloseOnBarTap(");
  });

  it("the Focus room minimizes and never ends the session", () => {
    expect(read("app/tasks/components/FocusMode.tsx")).toContain("useCloseOnBarTap(minimize, !minimized)");
  });

  it("the Modal no longer reads the bar's click itself", () => {
    expect(read("components/ui/Modal.tsx")).not.toContain("data-phone-nav-bar");
  });

  it("the shell sends the signal in the tap of Menu, Ask, a link tab and an event tab", () => {
    const shell = read("components/AppShell.tsx");
    expect(shell).toContain("sendBarTap(detail)");
    expect(shell).toContain("sendBarTap(id)");
    expect(shell).toContain('sendBarTap("menu")');
    expect(shell).toContain('sendBarTap("ask")');
  });

  it("a second tap on Search closes it", () => {
    const page = read("app/projects/page.tsx");
    expect(page).toContain("searchTapClosed.current");
    expect(page).toContain('tab === "projects-search" && searchingRef.current');
  });
});
