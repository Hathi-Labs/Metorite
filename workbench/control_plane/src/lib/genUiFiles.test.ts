/**
 * Where a card opens a workspace file (the report card, 2026-10-16).
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `reportCardActions` opens another path than the report's -> "the card
 *   opens the report by its own path";
 * - `fileTarget` sends a phone to the side panel -> "a phone opens the
 *   viewer".
 */
import { describe, expect, it, vi } from "vitest";

import { fileTarget, genUiFiles, reportCardActions } from "./genUiFiles";

function deps(over: Partial<Parameters<typeof genUiFiles>[0]> = {}) {
  return { sessionId: "s1", isMobile: false, panelFits: true, openDoc: vi.fn(), onFileOpen: vi.fn(), ...over };
}

describe("fileTarget", () => {
  it("a desktop where the panel fits opens the side panel", () => {
    expect(fileTarget({ isMobile: false, panelFits: true })).toBe("panel");
  });

  it("a phone opens the viewer", () => {
    expect(fileTarget({ isMobile: true, panelFits: true, onFileOpen: () => {} })).toBe("viewer");
    expect(fileTarget({ isMobile: false, panelFits: false, onFileOpen: () => {} })).toBe("viewer");
  });

  it("with no viewer and no panel, nothing opens", () => {
    expect(fileTarget({ isMobile: true, panelFits: true })).toBeNull();
  });
});

describe("genUiFiles", () => {
  it("opens the path in the side panel, named by its file", () => {
    const d = deps();
    genUiFiles(d)!.open!("outputs/q3.report.json");
    expect(d.openDoc).toHaveBeenCalledWith({ path: "outputs/q3.report.json", name: "q3.report.json", sessionId: "s1" });
    expect(d.onFileOpen).not.toHaveBeenCalled();
  });

  it("opens the path in the viewer on a phone", () => {
    const d = deps({ isMobile: true });
    genUiFiles(d)!.open!("outputs/q3.report.json");
    expect(d.openDoc).not.toHaveBeenCalled();
    expect(d.onFileOpen).toHaveBeenCalledWith(expect.objectContaining({ path: "outputs/q3.report.json", name: "q3.report.json" }));
  });

  it("no session, no context. No place to open, no opener", () => {
    expect(genUiFiles(deps({ sessionId: null }))).toBeNull();
    expect(genUiFiles(deps({ isMobile: true, onFileOpen: undefined }))).toEqual({ sessionId: "s1", open: null });
  });
});

describe("reportCardActions", () => {
  it("the card opens the report by its own path, and saves the PDF of this session", () => {
    const open = vi.fn();
    const acts = reportCardActions({ path: "outputs/q3.report.json", pdf: "outputs/q3.pdf" }, { sessionId: "s9", open });
    acts.open!();
    expect(open).toHaveBeenCalledWith("outputs/q3.report.json");
    expect(acts.pdfUrl).toBe("/api/agent/workspace/s9/file?path=outputs%2Fq3.pdf");
  });

  it("offers no act it cannot do", () => {
    expect(reportCardActions({ path: null, pdf: null }, { sessionId: "s", open: vi.fn() })).toEqual({ open: null, pdfUrl: null });
    expect(reportCardActions({ path: "outputs/a.report.json", pdf: "outputs/a.pdf" }, null)).toEqual({ open: null, pdfUrl: null });
    expect(reportCardActions({ path: "outputs/a.report.json", pdf: null }, { sessionId: "s", open: null }).open).toBeNull();
  });
});
