/**
 * The composer's pending files (IMG-1, projects_ai_chat §30.4). The vitest
 * rig runs in node with no DOM, so these cases drive the reducer that the
 * composer's hook runs. The browser half is `e2e/chat-attach-chip.spec.ts`.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  attachReducer,
  canSend,
  composeMessage,
  editStart,
  restoreItems,
  safeFileName,
  hasFiles,
  pastedImageName,
  readyFiles,
  type PendingFile,
} from "./chatAttachments";
import { parseUploadNote, uploadNote } from "./chatUpload";

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

describe("edit of a sent message (IMG-1 fix round)", () => {
  const files = [
    { name: "a.png", path: "inputs/t/a.png" },
    { name: "b.pdf", path: "inputs/t/b.pdf" },
  ];
  const sent = `Make a task.\n\n${uploadNote(files)}`;

  it("opens the words and the files as ready chips", () => {
    const e = editStart(sent, (p) => `/f?path=${p}`);
    expect(e.words).toBe("Make a task.");
    expect(e.items.map((i) => [i.name, i.state, i.path])).toEqual([
      ["a.png", "ready", "inputs/t/a.png"],
      ["b.pdf", "ready", "inputs/t/b.pdf"],
    ]);
    expect(e.items[0].previewUrl).toBe("/f?path=inputs/t/a.png");
    expect(e.items[1].previewUrl).toBeUndefined();
  });

  it("saves the words and the note of the kept files", () => {
    const e = editStart(sent);
    const kept = attachReducer(e.items, { type: "remove", id: e.items[0].id });
    expect(composeMessage("Edited.", kept)).toBe(`Edited.\n\n${uploadNote([files[1]])}`);
    const none = attachReducer(kept, { type: "remove", id: kept[0].id });
    expect(composeMessage("Edited.", none)).toBe("Edited.");
    expect(canSend("", none)).toBe(false);
    expect(canSend("", kept)).toBe(true);
  });

  it("keeps the raw text of a message whose note does not parse", () => {
    const raw = "x\n\n📎 Uploaded 2 file(s): a.png";
    expect(editStart(raw)).toEqual({ words: raw, items: [] });
  });
});

describe("file names", () => {
  it("swaps a backtick for an apostrophe, so the note still parses", () => {
    expect(safeFileName("my`file.png")).toBe("my'file.png");
    const name = safeFileName("my`file.png");
    expect(parseUploadNote(uploadNote([{ name, path: `inputs/t/${name}` }]))?.files[0].name).toBe("my'file.png");
  });
});

describe("a refused send gives its message back (IMG-1 fix round 2)", () => {
  const files = [{ name: "a.png", path: "inputs/t/a.png" }];
  const text = `Words.\n\n${uploadNote(files)}`;

  it("puts the words in the box and the files back as ready chips", () => {
    const back = editStart(text);
    expect(back.words).toBe("Words.");
    const s = attachReducer([], { type: "restore", items: back.items });
    expect(s.map((p) => [p.name, p.state])).toEqual([["a.png", "ready"]]);
    expect(canSend(back.words, s)).toBe(true);
  });

  it("does not add a file twice", () => {
    const back = editStart(text);
    const once = restoreItems([], back.items);
    expect(restoreItems(once, back.items)).toHaveLength(1);
  });
});

describe("source fences", () => {
  const read = (f: string) => readFileSync(join(process.cwd(), "src", f), "utf-8");

  it("the Attach button never submits the composer form", () => {
    const src = read("components/FileUploadButton.tsx");
    expect(src).toMatch(/<button\s+type="button"\s+onClick=\{handleClick\}/);
  });

  it("every button of the chips is type=button", () => {
    const src = read("components/ChatAttachmentChips.tsx");
    for (const m of src.matchAll(/<(button|Button)(?=[\s>])[^>]*>/g)) expect(m[0], m[0]).toContain('type="button"');
  });

  it("the mic beside Attach never submits the composer form (VOX-1)", () => {
    const src = read("components/VoiceInputButton.tsx");
    const tags = [...src.matchAll(/<(button|Button)(?=[\s>])[^>]*>/g)];
    expect(tags.length).toBeGreaterThan(0);
    for (const m of tags) expect(m[0], m[0]).toContain('type="button"');
  });
});
