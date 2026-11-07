/**
 * The composer's pending files, as a pure reducer (WS projects_ai_chat §30.4,
 * IMG-1). `useChatAttachments` holds the state and does the uploads. This file
 * holds every rule that the state follows, so a node test can drive it:
 *
 * - A file is a chip: `uploading`, then `ready` or `failed`.
 * - Remove takes the chip out of the message. The file stays in the thread.
 * - Send waits for every upload, and a failed chip blocks it until the member
 *   retries or removes the chip.
 * - A message with files and no words is valid.
 * - The sent text is the words, a blank line, and `uploadNote(files)`.
 *
 * Fence: `src/lib/chatAttachments.test.ts`.
 */
import { uploadNote, type NotedFile } from "@/lib/chatUpload";

export type ChipState = "uploading" | "ready" | "failed";

export interface PendingFile {
  id: string;
  name: string;
  size: number;
  state: ChipState;
  /** The thread path, after the upload. */
  path?: string;
  /** The gateway's sentence, when the upload failed. */
  error?: string;
  /** An object URL of the local file, for an image thumbnail. */
  previewUrl?: string;
}

export type AttachAction =
  | { type: "add"; items: PendingFile[] }
  | { type: "ready"; id: string; name: string; path: string }
  | { type: "fail"; id: string; error: string }
  | { type: "retry"; id: string }
  | { type: "remove"; id: string }
  | { type: "clear" };

export function attachReducer(state: PendingFile[], a: AttachAction): PendingFile[] {
  switch (a.type) {
    case "add":
      return [...state, ...a.items];
    case "ready":
      return state.map((p) =>
        p.id === a.id ? { ...p, state: "ready", name: a.name, path: a.path, error: undefined } : p,
      );
    case "fail":
      return state.map((p) => (p.id === a.id ? { ...p, state: "failed", error: a.error } : p));
    case "retry":
      return state.map((p) => (p.id === a.id ? { ...p, state: "uploading", error: undefined } : p));
    case "remove":
      return state.filter((p) => p.id !== a.id);
    case "clear":
      return [];
  }
}

export const isUploading = (items: readonly PendingFile[]): boolean =>
  items.some((p) => p.state === "uploading");

export const hasFailed = (items: readonly PendingFile[]): boolean =>
  items.some((p) => p.state === "failed");

export function readyFiles(items: readonly PendingFile[]): NotedFile[] {
  return items
    .filter((p) => p.state === "ready" && p.path)
    .map((p) => ({ name: p.name, path: p.path as string }));
}

/** Whether Send may run: nothing uploads or failed, and words or a file exist. */
export function canSend(words: string, items: readonly PendingFile[]): boolean {
  if (isUploading(items) || hasFailed(items)) return false;
  return words.trim().length > 0 || readyFiles(items).length > 0;
}

/** The text that the send carries. The model gets `uploadNote` as before. */
export function composeMessage(words: string, items: readonly PendingFile[]): string {
  const files = readyFiles(items);
  const w = words.trim();
  if (files.length === 0) return w;
  return w ? `${w}\n\n${uploadNote(files)}` : uploadNote(files);
}

const pad = (n: number) => String(n).padStart(2, "0");

/** A clipboard image has no real name: `Screenshot <date> <time>.png`. */
export function pastedImageName(at: Date, type: string): string {
  const ext = type === "image/jpeg" ? "jpg" : type === "image/gif" ? "gif" : type === "image/webp" ? "webp" : "png";
  return (
    `Screenshot ${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ` +
    `${pad(at.getHours())}.${pad(at.getMinutes())}.${pad(at.getSeconds())}.${ext}`
  );
}

/** Whether a clipboard or drag payload carries files (not only text). */
export function hasFiles(types: ReadonlyArray<string> | DOMStringList | undefined): boolean {
  return !!types && Array.from(types as ArrayLike<string>).includes("Files");
}
