/**
 * The rules of a chat upload (the incident of 2026-10-09).
 *
 * A member attached a .docx on My Tasks. The note told the agent to use a
 * `read_file` tool that does not exist. In the main chat the upload answered
 * 404, and the member saw only a red icon. These cases fence the one list of
 * kinds, the size cap, the error sentence and the note.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  CHAT_UPLOAD_ACCEPT,
  CHAT_UPLOAD_ACCEPT_ATTR,
  CHAT_UPLOAD_MAX_BYTES,
  IMAGE_UPLOAD_KINDS,
  postUpload,
  READABLE_UPLOAD_KINDS,
  refuseUpload,
  uploadErrorMessage,
  uploadNote,
} from "./chatUpload";

const REPO = join(process.cwd(), "..", "..");

function pySet(file: string, name: string): Set<string> {
  const src = readFileSync(join(REPO, file), "utf-8");
  const at = src.indexOf(name);
  expect(at, `${name} in ${file}`).toBeGreaterThan(-1);
  const block = src.slice(at, src.indexOf("\n\n", at));
  return new Set(Array.from(block.matchAll(/"(\.[a-z0-9]+)"/g), (m) => m[1]));
}

describe("the one list of kinds", () => {
  it("offers every kind read_attachment reads, and only those as documents", () => {
    // SUPPORTED_SUFFIXES is built from three sets in attachment_text.py.
    const src = readFileSync(
      join(REPO, "packages/acb_skills/acb_skills/attachment_text.py"), "utf-8",
    );
    const start = src.indexOf("_TEXT_SUFFIXES = ");
    const end = src.indexOf("SUPPORTED_SENTENCE = ");
    const py = new Set(Array.from(src.slice(start, end).matchAll(/"(\.[a-z0-9]+)"/g), (m) => m[1]));
    expect(py.size).toBeGreaterThan(5);
    expect(new Set(READABLE_UPLOAD_KINDS)).toEqual(py);
  });

  it("offers only kinds the gateway's upload route takes", () => {
    const allowed = pySet("apps/services/gateway/gateway/routes/workspace.py", "_ALLOWED_EXTENSIONS = {");
    for (const kind of CHAT_UPLOAD_ACCEPT) expect(allowed.has(kind), kind).toBe(true);
  });

  it("is one list, and the input's accept attribute is that list", () => {
    expect(CHAT_UPLOAD_ACCEPT).toEqual([...READABLE_UPLOAD_KINDS, ...IMAGE_UPLOAD_KINDS]);
    expect(CHAT_UPLOAD_ACCEPT_ATTR.split(",")).toEqual([...CHAT_UPLOAD_ACCEPT]);
  });

  it("caps a file at the gateway's 25 MB", () => {
    const src = readFileSync(
      join(REPO, "apps/services/gateway/gateway/routes/workspace.py"), "utf-8",
    );
    expect(src).toContain("_MAX_UPLOAD_BYTES = 25 * 1024 * 1024");
    expect(CHAT_UPLOAD_MAX_BYTES).toBe(25 * 1024 * 1024);
  });
});

describe("refuseUpload", () => {
  it("lets a readable file under the cap go", () => {
    expect(refuseUpload([{ name: "Brief.DOCX", size: 38_000 }])).toBeNull();
    expect(refuseUpload([{ name: "photo.png", size: 1 }])).toBeNull();
  });

  it("refuses a file over 25 MB before it is sent", () => {
    const why = refuseUpload([{ name: "big.pdf", size: CHAT_UPLOAD_MAX_BYTES + 1 }]);
    expect(why).toContain("big.pdf");
    expect(why).toContain("25 MB");
  });

  it("refuses a kind the picker does not offer (a drop skips accept)", () => {
    const why = refuseUpload([{ name: "deck.pptx", size: 10 }]);
    expect(why).toContain("deck.pptx");
    expect(why).toContain(".docx");
  });
});

describe("uploadErrorMessage", () => {
  const NO_WS = "No workspace found for this session. Start a chat with an agent first.";

  it("reads the gateway's detail through the proxy's wrapper", () => {
    const body = { error: JSON.stringify({ detail: NO_WS }) };
    expect(uploadErrorMessage(404, body)).toBe(NO_WS);
  });

  it("reads a bare detail too", () => {
    expect(uploadErrorMessage(400, { detail: "Unsupported file type: .exe." })).toBe(
      "Unsupported file type: .exe.",
    );
  });

  it("names the cap on a 413 with no detail", () => {
    expect(uploadErrorMessage(413, null)).toContain("25 MB");
  });

  it("never shows a server's raw body for a 5xx", () => {
    const msg = uploadErrorMessage(500, { error: "Traceback (most recent call last)…" });
    expect(msg).toBe("The upload failed (HTTP 500). Try again.");
  });
});

describe("uploadNote", () => {
  const note = uploadNote([{ name: "brief.docx", path: "inputs/t-1/brief.docx" }]);

  it("names read_attachment, the one reader, and never read_file", () => {
    expect(note).toContain("read_attachment");
    expect(note).not.toContain("read_file");
  });

  it("keeps the 📎 Uploaded lead and the path that read_attachment takes", () => {
    expect(note.startsWith("📎 Uploaded 1 file(s): brief.docx")).toBe(true);
    expect(note).toContain("`inputs/t-1/brief.docx`");
  });

  it("lists no kinds, so a new kind needs no edit here", () => {
    for (const kind of READABLE_UPLOAD_KINDS.filter((k) => k !== ".docx")) {
      expect(note).not.toContain(kind);
    }
  });
});

describe("postUpload", () => {
  function server(...statuses: number[]) {
    const urls: string[] = [];
    const impl = (async (url: string) => {
      urls.push(url);
      return new Response("{}", { status: statuses.shift() ?? 200 });
    }) as unknown as typeof fetch;
    return { impl, urls };
  }

  it("retries one 404, when the session row may still be on its way", async () => {
    const { impl, urls } = server(404, 200);
    const res = await postUpload("s1", new FormData(), 0, impl);
    expect(res.status).toBe(200);
    expect(urls).toEqual(["/api/agent/workspace/s1/upload", "/api/agent/workspace/s1/upload"]);
  });

  it("retries a 404 only once, and nothing else", async () => {
    const twice = server(404, 404, 200);
    expect((await postUpload("s1", new FormData(), 0, twice.impl)).status).toBe(404);
    expect(twice.urls).toHaveLength(2);
    for (const status of [200, 400, 413, 500]) {
      const once = server(status, 200);
      expect((await postUpload("s1", new FormData(), 0, once.impl)).status).toBe(status);
      expect(once.urls).toHaveLength(1);
    }
  });
});
