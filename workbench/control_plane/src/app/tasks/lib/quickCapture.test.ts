/**
 * S6g — the mobile capture sheet (`QuickCapture`) parses `#` the way the Inbox
 * capture box does: through the store's ONE capture-line flow
 * (`captureLine`), which reads `quickAdd.parseProjectToken`. No second parser.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CaptureDestination } from "./quickAdd";
import { useTaskStore } from "./taskStore";

const read = (rel: string) =>
  readFileSync(resolve(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");

const TARGETS: CaptureDestination[] = [
  { id: "p1", name: "Printer v3", kind: "project" },
  { id: "a1", name: "Home", kind: "area" },
];

describe("captureLine — the one flow both capture boxes call", () => {
  let captureTo: ReturnType<typeof vi.fn>;
  let capture: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    captureTo = vi.fn().mockResolvedValue({});
    capture = vi.fn();
    useTaskStore.setState({ captureTo, capture, promoteDialog: null } as never);
  });
  afterEach(() => vi.restoreAllMocks());

  it("a #Project line goes to captureTo, with the token out of the title", () => {
    const to = useTaskStore.getState().captureLine("Fix the jam #Printer v3", { targets: TARGETS });
    expect(to?.id).toBe("p1");
    expect(captureTo).toHaveBeenCalledWith("Fix the jam", TARGETS[0], undefined, undefined);
    expect(capture).not.toHaveBeenCalled();
  });

  it("a line with no token is a plain capture, dates included", () => {
    const dates = { deferUntil: "2026-10-01T00:00:00Z" };
    useTaskStore.getState().captureLine("Call the dentist", { targets: TARGETS, dates });
    expect(capture).toHaveBeenCalledWith("Call the dentist", undefined, dates);
    expect(captureTo).not.toHaveBeenCalled();
  });

  it("the chip's destination wins, and the line is the title as typed", () => {
    useTaskStore.getState().captureLine("Water #plants", { targets: TARGETS, dest: TARGETS[1] });
    expect(captureTo).toHaveBeenCalledWith("Water #plants", TARGETS[1], undefined, undefined);
  });

  it("a project that needs fields opens the one promote dialog", async () => {
    captureTo.mockResolvedValue({ needsFields: { taskId: "t9", destinationId: "p1" } });
    useTaskStore.getState().captureLine("Order parts #Printer v3", { targets: TARGETS });
    await Promise.resolve();
    await Promise.resolve();
    expect(useTaskStore.getState().promoteDialog).toEqual({ id: "t9", destination: "p1" });
  });
});

describe("the sources", () => {
  it("QuickCapture and the Inbox both call captureLine, and neither parses by itself", () => {
    const sheet = read("components/QuickCapture.tsx");
    const inbox = read("components/InboxView.tsx");
    expect(sheet).toMatch(/captureLine\(t, \{/);
    // The Inbox box hands `captureLine` to `submitCaptureBox` (quickAdd.ts).
    expect(inbox).toMatch(/submitCaptureBox\([\s\S]*?captureLine,/);
    // The only parser is `quickAdd.parseProjectToken`.
    expect(sheet).not.toMatch(/split\(\/\s\+\/\)[\s\S]*#/);
    expect(inbox).not.toMatch(/parseProjectToken\(/);
    expect(sheet).toMatch(/captureDestinations\(\{ areas, tree: destinations\(roots\), projects \}\)/);
  });

  it("one promote dialog, hosted once on the page", () => {
    const page = read("page.tsx");
    expect(page.match(/<PromoteHost \/>/g)?.length).toBe(2);
    expect(read("components/InboxView.tsx")).not.toMatch(/<PromoteDialog/);
  });
});
