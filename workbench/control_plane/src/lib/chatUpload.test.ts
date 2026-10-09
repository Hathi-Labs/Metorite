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
  postUpload,
  refuseUpload,
  uploadErrorMessage,
  uploadNote,
} from "./chatUpload";

const REPO = join(process.cwd(), "..", "..");

function pyKinds(file: string, from: string, to: string): Set<string> {
  const src = readFileSync(join(REPO, file), "utf-8");
  const at = src.indexOf(from);
  expect(at, `${from} in ${file}`).toBeGreaterThan(-1);
  const end = src.indexOf(to, at);
  expect(end, `${to} after ${from}`).toBeGreaterThan(at);
  return new Set(Array.from(src.slice(at, end).matchAll(/"(\.[a-z0-9]+)"/g), (m) => m[1]));
}

const GATEWAY = () => pyKinds(
  "apps/services/gateway/gateway/routes/workspace.py", "_ALLOWED_EXTENSIONS = {", "}",
);

describe("the one list of kinds", () => {
  it("is the gateway's own list: the client refuses only what the gateway refuses", () => {
    const gateway = GATEWAY();
    expect(gateway.size).toBeGreaterThan(30);
    expect(new Set(CHAT_UPLOAD_ACCEPT)).toEqual(gateway);
    expect(CHAT_UPLOAD_ACCEPT).toHaveLength(gateway.size);
  });

  it("lets the gateway take every kind read_attachment reads", () => {
    // SUPPORTED_SUFFIXES is built from three sets in attachment_text.py.
    const readable = pyKinds(
      "packages/acb_skills/acb_skills/attachment_text.py",
      "_TEXT_SUFFIXES = ", "SUPPORTED_SENTENCE = ",
    );
    expect(readable.size).toBeGreaterThan(5);
    const gateway = GATEWAY();
    for (const kind of readable) expect(gateway.has(kind), kind).toBe(true);
  });

  it("is the input's accept attribute", () => {
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

  it("lets a kind go that the gateway takes and read_attachment cannot read", () => {
    for (const name of ["deck.pptx", "data.json", "tool.py", "bundle.zip", "c.yaml"]) {
      expect(refuseUpload([{ name, size: 10 }]), name).toBeNull();
    }
  });

  it("refuses a kind the gateway refuses (a drop skips accept)", () => {
    const why = refuseUpload([{ name: "setup.exe", size: 10 }]);
    expect(why).toContain("setup.exe");
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
    for (const kind of [".pdf", ".xlsx", ".txt", ".csv", ".html"]) {
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

describe("the default agent's aliases", () => {
  it("are the same on the gateway's upload route as in the chat proxy", async () => {
    const { DEFAULT_AGENT_NAMES } = await import("./chatMemorySave");
    const src = readFileSync(
      join(REPO, "apps/services/gateway/gateway/routes/workspace.py"), "utf-8",
    );
    const at = src.indexOf("_DEFAULT_CHAT_AGENT_ALIASES: frozenset[str] = frozenset({");
    expect(at).toBeGreaterThan(-1);
    const block = src.slice(at, src.indexOf("})", at));
    const py = new Set(Array.from(block.matchAll(/"([^"]*)"/g), (m) => m[1]));
    expect(py).toEqual(new Set(DEFAULT_AGENT_NAMES));
  });
});
