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
import type { AssistantSettings, SignatureRemoved } from "./types";

export type SignatureSave =
  | { ok: true; settings: AssistantSettings }
  | { ok: false; error: string };

/** The sentence of a server with no https address to host an image
 *  (§23.3.9). The server's 503 says the same words (`NOT_SET_UP`). */
export const UPLOAD_OFF = "Image upload is not set up on this server yet. Ask your admin.";

/** The save error that the dialog shows. When the editor already shows the
 *  upload-off line, the error drops that sentence, so the member does not
 *  read it twice (fix round 2, P3). */
export function shownError(error: string | null, uploadOff: boolean): string | null {
  if (!error || !uploadOff) return error;
  const rest = error.replace(UPLOAD_OFF, "").replace(/\s+/g, " ").trim();
  return rest || null;
}

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
  /** The one line that names what the server's clean removed. */
  notice: string | null;
}

/** The one line for the links and images that the clean removed (D-EM-70:
 *  https, mailto and tel only), or null when it removed nothing. */
export function removedLine(removed: SignatureRemoved[] | undefined): string | null {
  const items = removed ?? [];
  if (!items.length) return null;
  const n = items.length;
  const what = n === 1 ? "1 item that does" : `${n} items that do`;
  return `We removed ${what} not use https: ${items.map((r) => r.value).join(", ")}`;
}

/** A refused save keeps the dialog open with the edit and the sentence. A
 *  saved one closes it, unless the clean removed something: then it stays
 *  open on the saved signature and says what went, once. */
export function dialogAfterSave(draft: string, result: SignatureSave): SignatureDialogState {
  if (!result.ok) return { open: true, draft, error: result.error, notice: null };
  const saved = result.settings.signature ?? "";
  const notice = removedLine(result.settings.signature_removed);
  return { open: notice !== null, draft: saved, error: null, notice };
}

/** The body of a save of OTHER settings (a toggle): it builds on the settings
 *  state, which holds the stored signature, so no unsaved edit goes with it. */
export function patchBody(
  settings: AssistantSettings,
  patch: Partial<AssistantSettings>,
): AssistantSettings {
  return { ...settings, ...patch };
}
