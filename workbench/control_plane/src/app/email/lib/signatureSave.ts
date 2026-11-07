// The save of a signature from the settings dialog (WS-17 SG-1, fix round 1).
//
// The server can refuse a signature save: an image it cannot host, no https
// address on this server, or a cap (409, 413, 422, 503). So the signature does
// not save the way a toggle does:
//
// 1. Nothing changes the settings state BEFORE the answer. The card and the
//    state keep the STORED signature until a save succeeds.
// 2. A refused save keeps the dialog open, with the server's sentence inline
//    and the member's edit still in the editor.
// 3. A later save of another setting sends the stored signature, never an
//    edit that did not save, because it builds on the settings state.
//
// Pure functions, so the runner (no DOM) tests each rule. SettingsTab wires them.
import type { AssistantSettings } from "./types";

export type SignatureSave =
  | { ok: true; settings: AssistantSettings }
  | { ok: false; error: string };

/** Save one signature on top of the STORED settings. It never throws. */
export async function saveSignature(
  stored: AssistantSettings,
  signature: string,
  save: (next: AssistantSettings) => Promise<AssistantSettings>,
): Promise<SignatureSave> {
  try {
    return { ok: true, settings: await save({ ...stored, signature }) };
  } catch (e) {
    return {
      ok: false,
      error: (e as Error).message || "The signature was not saved. Try again.",
    };
  }
}

/** The settings that the tab keeps after a signature save: the server's
 *  answer, or the stored settings, unchanged, after a refusal. */
export function settingsAfterSave(
  stored: AssistantSettings,
  result: SignatureSave,
): AssistantSettings {
  return result.ok ? result.settings : stored;
}

/** What the dialog shows after a save. */
export interface SignatureDialogState {
  open: boolean;
  /** The HTML in the editor. */
  draft: string;
  /** The server's sentence of a refused save, shown inside the dialog. */
  error: string | null;
}

/** A refused save keeps the dialog open with the edit and the sentence. A
 *  saved one closes it. */
export function dialogAfterSave(draft: string, result: SignatureSave): SignatureDialogState {
  if (!result.ok) return { open: true, draft, error: result.error };
  return { open: false, draft: result.settings.signature ?? "", error: null };
}

/** The body of a save of OTHER settings (a toggle): it builds on the settings
 *  state, which holds the stored signature, so no unsaved edit goes with it. */
export function patchBody(
  settings: AssistantSettings,
  patch: Partial<AssistantSettings>,
): AssistantSettings {
  return { ...settings, ...patch };
}
