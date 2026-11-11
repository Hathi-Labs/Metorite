/**
 * The one bar-tap signal (MOB-9 fix round, task 2002). A hand-rolled overlay
 * above z-50 listens, or a bar tap opens a sheet under its scrim.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p: string) => readFileSync(join(SRC, p), "utf-8");

/**
 * The class fence: a file with a `fixed inset-0` overlay at a layer from z-51
 * to z-99 sits above the phone bar's sheets. It listens to the bar tap, or the
 * EXEMPT map says why it does not.
 */
const EXEMPT: Record<string, string> = {
  "app/agents/page.tsx":
    "Agent Registry is a preview surface with its own wizard windows, and no phone bar tab opens a sheet there.",
  "app/build/apps/[slug]/edit/page.tsx":
    "The workshop edit page is a non-pane page. Its history sheet has its own close and no bar tab acts there.",
  "app/calendar/components/CalendarSettings.tsx":
    "CalendarView draws it inside a relative isolate root, so it never covers the shell's layers.",
  "app/calendar/components/EndOfDayReview.tsx":
    "CalendarView draws it inside a relative isolate root, so it never covers the shell's layers.",
  "app/calendar/components/PlanDayPanel.tsx":
    "CalendarView draws it inside a relative isolate root, so it never covers the shell's layers.",
  "app/calendar/components/ScheduleSheet.tsx":
    "CalendarView draws it inside a relative isolate root, so it never covers the shell's layers.",
  "app/calendar/components/StartupRitual.tsx":
    "CalendarView draws it inside a relative isolate root, so it never covers the shell's layers.",
  "app/chat/page.tsx":
    "The chat page is the assistant itself. Its agent picker is a door and Ask opens no sheet over it.",
  "components/AppShell.tsx":
    "The shell's own Menu drawer. It owns the bar tap and closes itself in the tap handlers.",
  "components/ui/OverflowTip.tsx":
    "A tooltip with no pointer events. It takes no tap and hides itself on pointer leave.",
};

function tsxFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) tsxFiles(full, out);
    else if (name.endsWith(".tsx") && !/\.(test|spec)\.tsx$/.test(name)) out.push(full);
  }
  return out;
}

const LAYER = /z-\[(5[1-9]|[6-9][0-9])\]/;
const overlayFiles = tsxFiles(SRC)
  .map((f) => f.slice(SRC.length + 1).split("\\").join("/"))
  .filter((rel) =>
    readFileSync(join(SRC, rel), "utf-8")
      .split(/\r?\n/)
      .some((l) => l.includes("fixed") && l.includes("inset-0") && LAYER.test(l)),
  );

describe("every overlay that must give way listens to the bar tap", () => {
  it("the scan finds the overlays", () => {
    expect(overlayFiles.length).toBeGreaterThan(20);
  });

  it.each(overlayFiles)("%s listens or says why not", (rel) => {
    const src = read(rel);
    const listens = src.includes("useCloseOnBarTap(") || src.includes("useBarTap(");
    if (listens) return;
    const reason = EXEMPT[rel] ?? "";
    expect(reason.length, `${rel} has a fixed inset-0 overlay above z-50: call useCloseOnBarTap or add a reason to EXEMPT`).toBeGreaterThanOrEqual(20);
  });

  it("an exempt file still has an overlay and still does not listen", () => {
    for (const rel of Object.keys(EXEMPT)) {
      expect(overlayFiles, `${rel} no longer has a z-51..99 overlay: drop it from EXEMPT`).toContain(rel);
    }
  });

  it.each([
    "components/ui/Modal.tsx",
    "app/tasks/components/QuickCapture.tsx",
    "app/tasks/components/ClarifyModal.tsx",
    "app/tasks/components/ReclarifyModal.tsx",
    "app/tasks/components/FocusMode.tsx",
  ])("%s uses useCloseOnBarTap", (file) => {
    expect(read(file)).toContain("useCloseOnBarTap(");
  });

  it("the email composer closes through the path that keeps the draft", () => {
    const src = read("app/email/components/ComposePanel.tsx");
    // The pending save runs first, as the X does, and only while it is open.
    expect(src).toMatch(/useCloseOnBarTap\(\(\) => \{\s*autosave\.flush\(\);\s*onClose\(\);\s*\}, open\);/);
    // ⚠️ The hook sits ABOVE `if (!open) return null`. Below it, opening the
    // window added a hook and React threw (fix round 2 shipped this; caught
    // in supervision, 2026-11-07).
    expect(src.indexOf("useCloseOnBarTap(")).toBeLessThan(src.indexOf("if (!open) return null;"));
  });

  it("the key panel closes only while a key is selected", () => {
    expect(read("app/integrations/components/ApiKeysView.tsx")).toContain(
      "useCloseOnBarTap(() => setSelected(null), selected !== null)",
    );
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
