// WS-17 SG-1, fix round 1 (verifier P1): a signature save that the server
// refuses keeps the dialog open with the edit and the sentence, the settings
// keep the STORED signature, and a later toggle sends no unsaved edit.
import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

import {
  dialogAfterSave, patchBody, removedLine, saveSignature, settingsAfterSave,
} from "./signatureSave";
import type { AssistantSettings } from "./types";

const STORED = {
  account_id: "acc-1",
  signature: "<p>Jane Doe</p>",
  draft_replies: false,
} as unknown as AssistantSettings;
const EDIT = '<p>Jane Doe, CEO</p><img src="data:image/gif;base64,R0lGOD">';
const REFUSAL = "This image is a WebP file. Upload it again as a PNG or a JPEG.";

const refuse = vi.fn(async () => { throw new Error(REFUSAL); });
const accept = vi.fn(async (next: AssistantSettings) => ({ ...next, signature: "<p>Jane Doe, CEO</p>" }));

describe("a refused signature save", () => {
  it("keeps the dialog open, with the server's sentence and the member's edit", async () => {
    const result = await saveSignature(STORED, EDIT, refuse);
    expect(result).toEqual({ ok: false, error: REFUSAL });
    expect(dialogAfterSave(EDIT, result)).toEqual({ open: true, draft: EDIT, error: REFUSAL, notice: null });
  });

  it("keeps the STORED signature in the settings, so the card shows what saved", async () => {
    const result = await saveSignature(STORED, EDIT, refuse);
    const after = settingsAfterSave(STORED, result);
    expect(after).toBe(STORED);
    expect(after.signature).toBe("<p>Jane Doe</p>");
    // The body that went to the server held the edit on top of the stored row.
    expect(refuse).toHaveBeenCalledWith({ ...STORED, signature: EDIT });
  });

  it("does not send the unsaved edit with a later toggle", async () => {
    const result = await saveSignature(STORED, EDIT, refuse);
    const body = patchBody(settingsAfterSave(STORED, result), { draft_replies: true });
    expect(body.signature).toBe("<p>Jane Doe</p>");
    expect(body.draft_replies).toBe(true);
  });
});

describe("a saved signature", () => {
  it("closes the dialog and keeps the server's cleaned signature", async () => {
    const result = await saveSignature(STORED, EDIT, accept);
    expect(dialogAfterSave(EDIT, result)).toEqual({
      open: false, draft: "<p>Jane Doe, CEO</p>", error: null, notice: null,
    });
    expect(settingsAfterSave(STORED, result).signature).toBe("<p>Jane Doe, CEO</p>");
  });
});

describe("the clean removed something (D-EM-70 stays strict)", () => {
  const removed = [
    { kind: "link" as const, value: "http://acme.example/about" },
    { kind: "image" as const, value: "http://cdn.example/logo.png" },
  ];

  it("says what went, in one plain line", () => {
    expect(removedLine(removed)).toBe(
      "We removed 2 items that do not use https: http://acme.example/about, http://cdn.example/logo.png",
    );
    expect(removedLine([removed[0]])).toBe(
      "We removed 1 item that does not use https: http://acme.example/about",
    );
    expect(removedLine([])).toBeNull();
    expect(removedLine(undefined)).toBeNull();
  });

  it("keeps the dialog open on the saved signature, with that line", async () => {
    const save = vi.fn(async (next: AssistantSettings) => ({
      ...next, signature: "<p>Jane</p>", signature_removed: removed,
    }));
    const result = await saveSignature(STORED, EDIT, save);
    expect(dialogAfterSave(EDIT, result)).toEqual({
      open: true, draft: "<p>Jane</p>", error: null, notice: removedLine(removed),
    });
    expect(settingsAfterSave(STORED, result).signature).toBe("<p>Jane</p>");
  });

  it("draws the line in the dialog", () => {
    const tab = readFileSync(
      new URL("../components/automation/ai-settings/SettingsTab.tsx", import.meta.url), "utf8",
    );
    expect(tab).toMatch(/setNotice\(next\.notice\)/);
    expect(tab).toMatch(/\{notice && \(\s*<p role="status" data-signature-removed/);
  });
});

describe("SettingsTab wires the rules", () => {
  const tab = readFileSync(
    new URL("../components/automation/ai-settings/SettingsTab.tsx", import.meta.url), "utf8",
  );

  it("saves the signature with no optimistic state and keeps the dialog on a refusal", () => {
    expect(tab).toMatch(/onSave=\{persistSignature\}/);
    expect(tab).toMatch(/saveSignature\(s, signature, saveAssistantSettings\)/);
    expect(tab).toMatch(/settingsAfterSave\(now, result\)/);
    expect(tab).toMatch(/dialogAfterSave\(draft, await onSave\(draft\)\)/);
    expect(tab).toMatch(/if \(!next\.open\) onClose\(\);/);
    expect(tab).toMatch(/data-signature-error/);
    const block = tab.slice(tab.indexOf('dialog === "signature"'), tab.indexOf('dialog === "knowledge"'));
    expect(block).not.toMatch(/persist\(|setDialog\(null\);\s*\}/);
  });

  it("builds a toggle on the settings state", () => {
    expect(tab).toMatch(/persist\(patchBody\(s, p\)\)/);
  });
});
