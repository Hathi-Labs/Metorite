/**
 * The composer's pending files (IMG-1, projects_ai_chat §30.4). The vitest
 * rig runs in node with no DOM, so these cases drive the reducer that the
 * composer's hook runs. The browser half is `e2e/chat-attach-chip.spec.ts`.
 */
import { describe, expect, it } from "vitest";

import {
  attachReducer,
  canSend,
  composeMessage,
  hasFiles,
  pastedImageName,
  readyFiles,
  type PendingFile,
} from "./chatAttachments";
import { uploadNote } from "./chatUpload";

const chip = (id: string, name: string, state: PendingFile["state"] = "uploading"): PendingFile => ({
  id, name, size: 100, state,
});
const ready = (s: PendingFile[], id: string, name: string) =>
  attachReducer(s, { type: "ready", id, name, path: `inputs/t/${name}` });

describe("composer attachments", () => {
  it("a paste or a drop adds a chip that uploads, with no path in the box", () => {
    const s = attachReducer([], { type: "add", items: [chip("1", "Screenshot 2026-11-07 10.00.00.png")] });
    expect(s).toHaveLength(1);
    expect(s[0].state).toBe("uploading");
    expect(s[0].path).toBeUndefined();
  });

  it("names a pasted image Screenshot <date> <time>.png", () => {
    expect(pastedImageName(new Date(2026, 10, 7, 9, 5, 3), "image/png")).toBe(
      "Screenshot 2026-11-07 09.05.03.png",
    );
    expect(pastedImageName(new Date(2026, 10, 7, 9, 5, 3), "image/jpeg")).toMatch(/\.jpg$/);
  });

  it("send is off while an upload runs, and on when it is ready", () => {
    let s = attachReducer([], { type: "add", items: [chip("1", "a.png")] });
    expect(canSend("words", s)).toBe(false);
    s = ready(s, "1", "a.png");
    expect(canSend("words", s)).toBe(true);
  });

  it("a message with files and no words is valid; no words and no files is not", () => {
    const s = ready(attachReducer([], { type: "add", items: [chip("1", "a.png")] }), "1", "a.png");
    expect(canSend("   ", s)).toBe(true);
    expect(canSend("   ", [])).toBe(false);
  });

  it("a failed chip blocks send until a retry or a remove", () => {
    let s = attachReducer([], { type: "add", items: [chip("1", "a.png")] });
    s = attachReducer(s, { type: "fail", id: "1", error: "Too big." });
    expect(canSend("hi", s)).toBe(false);
    expect(s[0].error).toBe("Too big.");
    s = attachReducer(s, { type: "retry", id: "1" });
    expect(s[0]).toMatchObject({ state: "uploading", error: undefined });
    s = attachReducer(s, { type: "fail", id: "1", error: "x" });
    expect(canSend("hi", attachReducer(s, { type: "remove", id: "1" }))).toBe(true);
  });

  it("remove drops the file from the message", () => {
    let s = attachReducer([], { type: "add", items: [chip("1", "a.png"), chip("2", "b.pdf")] });
    s = ready(ready(s, "1", "a.png"), "2", "b.pdf");
    s = attachReducer(s, { type: "remove", id: "1" });
    expect(readyFiles(s).map((f) => f.name)).toEqual(["b.pdf"]);
    expect(composeMessage("hi", s)).not.toContain("a.png");
  });

  it("the sent text is the words, a blank line and the note", () => {
    let s = attachReducer([], { type: "add", items: [chip("1", "a.png"), chip("2", "b.pdf")] });
    s = ready(ready(s, "1", "a.png"), "2", "b.pdf");
    const files = readyFiles(s);
    expect(composeMessage("  Make a task.  ", s)).toBe(`Make a task.\n\n${uploadNote(files)}`);
    expect(composeMessage("", s)).toBe(uploadNote(files));
    expect(composeMessage("Only words", [])).toBe("Only words");
  });

  it("sees a payload with files", () => {
    expect(hasFiles(["text/plain", "Files"])).toBe(true);
    expect(hasFiles(["text/plain"])).toBe(false);
    expect(hasFiles(undefined)).toBe(false);
  });
});
