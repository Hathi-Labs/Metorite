// WS-17 EM-G3c-2 — the files and the autosave of the three email composers
// (§12.3.3b of `project-docs/specs/email_app_master_plan.md`, items 10 to 14).
//
// R7 fences named here:
//   * `email-pick-limit` (item 10): a pick that makes the files of one mail
//     pass 7,500,000 bytes is refused whole, with "Files can be 7.5 MB in all,
//     at most.". The base64 of the limit stays under the cut of the proxy.
//     ComposePanel and EmailDetail ask `pickProblem` before they read a file.
//   * `email-autosave-flush` (item 11): the cleanup of each autosave effect
//     holds the save, and a close, a switch to another mail and an unmount
//     run it at once. A discard and a send drop it.
//   * `email-autosave-wait` (item 12): a Gmail draft that holds a file waits
//     10 seconds, and each other draft waits 1.2 seconds. Each composer asks
//     `autosaveWait`.
//   * `email-save-failed-shows` (item 13): a failed save shows "Not saved",
//     and a 413 shows "Too large to save", in `text-destructive`. The rule
//     reads the status code, never the text.
//   * `email-send-failed-shows` (item 14): each composer shows the text of a
//     failed send, and the DraftCard has a slot for it.
//
// Vitest runs with no DOM here (`vitest.config.ts`), so the composer fences
// read the source, as `fromRow.test.ts` does.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { saveDraft } from "./api";
import {
  AUTOSAVE_WAIT_MS,
  FILES_MAX_BYTES,
  FILES_TOO_LARGE,
  GMAIL_FILES_WAIT_MS,
  autosaveWait,
  base64Bytes,
  createAutosave,
  errorStatus,
  failedSaveStatus,
  pickProblem,
  saveFailureText,
  sendFailureText,
} from "./draftAutosave";

const ROOT = join(__dirname, "..");
const read = (rel: string) =>
  readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const compose = codeOnly(read("components/ComposePanel.tsx"));
const detail = codeOnly(read("components/EmailDetail.tsx"));
const conversation = codeOnly(read("components/ConversationView.tsx"));
const COMPOSERS = { compose, detail, conversation };

/** The part of `src` from `start` up to `end`. */
const between = (src: string, start: string, end: string) => {
  const from = src.indexOf(start);
  expect(from, `missing: ${start}`).toBeGreaterThanOrEqual(0);
  const to = src.indexOf(end, from + start.length);
  expect(to, `missing after ${start}: ${end}`).toBeGreaterThan(from);
  return src.slice(from, to);
};

/** `first` and `then` both occur in `src`, and `first` comes before `then`. */
const comesBefore = (src: string, first: string, then: string) => {
  const a = src.indexOf(first);
  const b = src.indexOf(then);
  expect(a, `missing: ${first}`).toBeGreaterThanOrEqual(0);
  expect(b, `missing: ${then}`).toBeGreaterThanOrEqual(0);
  expect(a, `${first} must come before ${then}`).toBeLessThan(b);
};

/** An error in the shape that `gatewayFetch` throws. */
const gatewayError = (status: number, detail: string) =>
  Object.assign(new Error(detail), { status });

// ── email-pick-limit ─────────────────────────────────────────────────────

describe("email-pick-limit: the files of one mail stay under the cut of the proxy", () => {
  /** The cut of the Next proxy (`experimental.proxyClientMaxBodySize`). */
  const PROXY_CUT = 10_485_760;

  it("is 7,500,000 bytes, with the reason of the spec", () => {
    expect(FILES_MAX_BYTES).toBe(7_500_000);
    expect(FILES_TOO_LARGE).toBe("Files can be 7.5 MB in all, at most.");
  });

  it("keeps the base64 of the limit under the cut, with room for the rest", () => {
    const base64 = Math.ceil(FILES_MAX_BYTES / 3) * 4;
    expect(base64).toBe(10_000_000);
    expect(PROXY_CUT - base64).toBeGreaterThanOrEqual(400_000);
  });

  it("counts the bytes of a base64 text as Buffer decodes them", () => {
    for (const n of [0, 1, 2, 3, 4, 5, 6, 7, 1000, 4097]) {
      const b64 = Buffer.alloc(n, 7).toString("base64");
      expect(base64Bytes(b64), `${n} bytes`).toBe(n);
    }
  });

  it("takes a pick up to the limit, and refuses one byte more", () => {
    expect(pickProblem([], [{ size: 7_500_000 }])).toBeNull();
    expect(pickProblem([], [{ size: 7_500_001 }])).toBe(FILES_TOO_LARGE);
  });

  it("adds the files that the mail holds already", () => {
    const held = [{ contentB64: Buffer.alloc(1000).toString("base64") }];
    expect(pickProblem(held, [{ size: 7_499_000 }])).toBeNull();
    expect(pickProblem(held, [{ size: 7_499_001 }])).toBe(FILES_TOO_LARGE);
  });

  it("adds each file of one pick, and refuses the pick whole", () => {
    expect(pickProblem([], [{ size: 3_000_000 }, { size: 4_500_000 }])).toBeNull();
    expect(pickProblem([], [{ size: 3_000_000 }, { size: 4_500_001 }])).toBe(FILES_TOO_LARGE);
  });

  it("is asked by both composers that pick a file, before they read it", () => {
    const pick = between(compose, "const addFiles = async", "useEffect(");
    comesBefore(pick, "const problem = pickProblem(attachments, picked);", "fileToSendAttachment");
    const reply = between(detail, "const addReplyFiles = async", "const changeFrom");
    comesBefore(reply, "const problem = pickProblem(replyAttachments, picked);", "fileToSendAttachment");
  });
});

// ── email-autosave-wait ──────────────────────────────────────────────────

describe("email-autosave-wait: a Gmail draft with a file waits 10 seconds", () => {
  it("waits 10 seconds for a Gmail draft that holds a file", () => {
    expect(GMAIL_FILES_WAIT_MS).toBe(10_000);
    expect(autosaveWait("gmail", true)).toBe(10_000);
  });

  it("keeps 1.2 seconds for each other draft", () => {
    expect(AUTOSAVE_WAIT_MS).toBe(1_200);
    expect(autosaveWait("gmail", false)).toBe(1_200);
    expect(autosaveWait("microsoft", true)).toBe(1_200);
    expect(autosaveWait("microsoft", false)).toBe(1_200);
    expect(autosaveWait("imap", true)).toBe(1_200);
    expect(autosaveWait(undefined, true)).toBe(1_200);
  });

  it("is asked by each composer, with the flag that the spec names", () => {
    expect(compose).toMatch(/autosaveWait\(\s*accounts\.find\(\(a\) => a\.id === savingFrom\)\?\.provider,\s*draftHasFileRef\.current,/);
    expect(detail).toMatch(/autosaveWait\(\s*accounts\.find\(\(a\) => a\.id === savingFrom\)\?\.provider,\s*replyHasFileRef\.current,/);
    expect(conversation).toMatch(/autosaveWait\(\s*accounts\.find\(\(a\) => a\.id === accountId\)\?\.provider,\s*draft\.hasAttachments,/);
  });

  it("reads hasAttachments of the row that the last autosave returned", () => {
    expect(compose).toMatch(/draftIdRef\.current = saved\.id;\s*draftHasFileRef\.current = saved\.hasAttachments;/);
    expect(detail).toMatch(/draftIdRef\.current = saved\.id;\s*replyHasFileRef\.current = saved\.hasAttachments;/);
  });

  it("reads it from the save before a send too, when the composer keeps that draft", () => {
    expect(compose).toContain(
      "if (saved.id === draftIdRef.current) draftHasFileRef.current = saved.hasAttachments;",
    );
    expect(detail).toContain(
      "if (saved.id === draftIdRef.current) replyHasFileRef.current = saved.hasAttachments;",
    );
  });

  it("keeps no wait of its own in a composer", () => {
    for (const [name, src] of Object.entries(COMPOSERS)) {
      expect(src, name).not.toMatch(/\}, 1200\)/);
      expect(src, name).not.toContain("setTimeout(async");
    }
  });
});

// ── email-autosave-flush ─────────────────────────────────────────────────

describe("email-autosave-flush: the controller", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("runs the save once, after the wait", () => {
    const run = vi.fn();
    const a = createAutosave();
    a.schedule(run, 1200);
    vi.advanceTimersByTime(1199);
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(run).toHaveBeenCalledTimes(1);
    expect(a.pending).toBe(false);
  });

  it("starts the wait again on each edit, and runs only the last save", () => {
    const first = vi.fn();
    const last = vi.fn();
    const a = createAutosave();
    a.schedule(first, 1200);
    vi.advanceTimersByTime(1000);
    a.schedule(last, 1200);
    vi.advanceTimersByTime(1000);
    expect(last).not.toHaveBeenCalled();
    vi.advanceTimersByTime(200);
    expect(last).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });

  it("keeps a held save for a flush, which runs it at once and once only", () => {
    // The cleanup of the effect holds. Then the close flushes.
    const run = vi.fn();
    const a = createAutosave();
    a.schedule(run, 1200);
    a.hold();
    vi.advanceTimersByTime(60_000);
    expect(run).not.toHaveBeenCalled();
    expect(a.pending).toBe(true);
    a.flush();
    expect(run).toHaveBeenCalledTimes(1);
    a.flush();
    vi.advanceTimersByTime(60_000);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("runs a save that still waits for its timer at once on a flush", () => {
    const run = vi.fn();
    const a = createAutosave();
    a.schedule(run, 10_000);
    a.flush();
    expect(run).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(10_000);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("drops the save on a cancel", () => {
    const run = vi.fn();
    const a = createAutosave();
    a.schedule(run, 1200);
    a.cancel();
    a.flush();
    vi.advanceTimersByTime(60_000);
    expect(run).not.toHaveBeenCalled();
    expect(a.pending).toBe(false);
  });

  it("does nothing on a flush with no pending save", () => {
    const a = createAutosave();
    expect(() => a.flush()).not.toThrow();
    expect(a.pending).toBe(false);
  });
});

describe("email-autosave-flush: each composer flushes on a close, a switch and an unmount", () => {
  it("holds the save in the cleanup of each autosave effect", () => {
    for (const [name, src] of Object.entries(COMPOSERS)) {
      expect(src, name).toContain("const [autosave] = useState(() => createAutosave());");
      expect(src, name).toContain("autosave.schedule(async () => {");
      expect(src, name).toContain("return () => autosave.hold();");
      expect(src, name).not.toMatch(/return \(\) => clearTimeout\(handle\);\s*\/\/ eslint-disable-next-line react-hooks\/exhaustive-deps\s*\}, \[(to|replyBody|body)/);
    }
  });

  it("flushes on an unmount in each composer", () => {
    expect(compose).toContain("useEffect(() => () => autosave.flush(), [autosave]);");
    expect(detail).toContain("useEffect(() => () => autosave.flush(), [autosave]);");
    // The card flushes when it shows another draft too.
    expect(conversation).toContain("useEffect(() => () => autosave.flush(), [autosave, draft.id]);");
  });

  it("flushes on the close of ComposePanel, by the X and by the backdrop", () => {
    expect(compose).toMatch(/const closeComposer = \(\) => \{\s*autosave\.flush\(\);\s*onClose\(\);\s*\};/);
    expect(compose.match(/onClick=\{closeComposer\}/g)?.length).toBe(2);
    expect(compose).not.toContain("onClick={onClose}");
  });

  it("flushes on the close of the inline reply, and on a new reply", () => {
    expect(detail).toMatch(/onClick=\{\(\) => \{\s*autosave\.flush\(\);\s*setReplyMode\(null\);\s*\}\}/);
    const start = between(detail, "const startReply = (", "const switchReplyMode");
    comesBefore(start, "autosave.flush();", "draftIdRef.current = null;");
  });

  it("flushes on a switch to another mail, while the draft id still names its draft", () => {
    expect(detail).toMatch(
      /useEffect\(\(\) => \{\s*autosave\.flush\(\);\s*replySessionRef\.current \+= 1;\s*replyHasFileRef\.current = false;\s*if \(!email\) \{\s*setDetail\(null\);/,
    );
    const effect = between(detail, "autosave.flush();\n    replySessionRef.current += 1;", "}, [email?.id]);");
    comesBefore(effect, "autosave.flush();", "draftIdRef.current = null;");
  });

  it("drops the pending save on a discard and on a send", () => {
    expect(compose).toMatch(/autosave\.cancel\(\);\s*if \(draftIdRef\.current\) void deleteEmail\(draftIdRef\.current\);\s*onClose\(\);/);
    expect(between(compose, "const handleSend = async", "const saveFailure")).toContain("autosave.cancel();");
    expect(between(detail, "const resetReplySession = () => {", "const composedReply")).toContain("autosave.cancel();");
    expect(between(detail, "const handleInlineSend = async", "const addReplyFiles")).toContain("autosave.cancel();");
    expect(between(conversation, "const send = async", "const discard")).toContain("autosave.cancel();");
    const discard = between(conversation, "const discard = async", "const runAi");
    comesBefore(discard, "autosave.cancel();", "onDismiss?.()");
  });

  it("guards a save that ends after its session ended", () => {
    expect(compose).toContain("if (sessionRef.current !== session) return;");
    expect(detail).toContain("if (replySessionRef.current !== session) return;");
  });
});

// ── email-save-failed-shows ──────────────────────────────────────────────

describe("email-save-failed-shows: a failed save shows", () => {
  it("is too-large on a 413, by the status code only", () => {
    expect(failedSaveStatus(gatewayError(413, "This mail is too large to send."))).toBe("too-large");
    expect(failedSaveStatus(gatewayError(413, "some other words"))).toBe("too-large");
    expect(failedSaveStatus(gatewayError(500, "This mail is too large to send."))).toBe("not-saved");
  });

  it("is not-saved on each other failure", () => {
    for (const status of [400, 404, 409, 422, 500, 502, 504]) {
      expect(failedSaveStatus(gatewayError(status, "x")), String(status)).toBe("not-saved");
    }
    expect(failedSaveStatus(new TypeError("Failed to fetch"))).toBe("not-saved");
    expect(failedSaveStatus(null)).toBe("not-saved");
    expect(failedSaveStatus("boom")).toBe("not-saved");
    expect(errorStatus({ status: "413" })).toBeUndefined();
  });

  it("draws the words of the spec, and nothing while the save did not fail", () => {
    expect(saveFailureText("not-saved")).toBe("Not saved");
    expect(saveFailureText("too-large")).toBe("Too large to save");
    for (const s of ["idle", "saving", "saved"] as const) expect(saveFailureText(s)).toBeNull();
  });

  it("maps each failed autosave in each composer, never back to idle", () => {
    expect(compose).toContain("if (sessionRef.current === session) setDraftStatus(failedSaveStatus(err));");
    expect(detail).toContain("if (replySessionRef.current === session) setDraftStatus(failedSaveStatus(err));");
    expect(conversation).toContain("setDraftStatus(failedSaveStatus(err));");
    for (const [name, src] of Object.entries(COMPOSERS)) {
      expect(src, name).not.toMatch(/catch \{\s*setDraftStatus\("idle"\);/);
      expect(src, name).not.toContain('useState<"idle" | "saving" | "saved">');
      expect(src, name).toContain("const saveFailure = saveFailureText(draftStatus);");
    }
  });

  it("draws the failure in text-destructive", () => {
    expect(compose).toContain('<span className="text-[10px] text-destructive">{saveFailure}</span>');
    expect(conversation).toContain('<span className="text-[10px] text-destructive ml-auto">{saveFailure}</span>');
    // The footer of the inline reply truncates, so its failure takes a line.
    expect(detail).toMatch(
      /\{\(sendErr \|\| saveFailure\) && \(\s*<p role="alert" className="px-4 py-1\.5 text-\[10px\] text-destructive">\s*\{sendErr \?\? saveFailure\}/,
    );
  });
});

// ── email-send-failed-shows ──────────────────────────────────────────────

describe("email-send-failed-shows: a failed send shows its text", () => {
  it("shows the text of the gateway for a 413 and for the 502 of EM-T9", () => {
    expect(sendFailureText(gatewayError(413, "This mail is too large to send.")))
      .toBe("This mail is too large to send.");
    const file = "The file plan.pdf could not be attached. The mail was not sent.";
    expect(sendFailureText(gatewayError(502, file))).toBe(file);
  });

  it("falls back to one sentence when the error has no text", () => {
    expect(sendFailureText(new Error(""))).toBe("Failed to send");
    expect(sendFailureText(undefined)).toBe("Failed to send");
    expect(sendFailureText({ message: 7 })).toBe("Failed to send");
  });

  it("gives each composer the text of the send", () => {
    expect(compose).toContain("setSendError(sendFailureText(err));");
    expect(detail).toContain("setSendErr(sendFailureText(e));");
    expect(conversation).toContain("setSendError(sendFailureText(err));");
  });

  it("gives the DraftCard a slot for the error of a send", () => {
    expect(conversation).toContain("const [sendError, setSendError] = useState<string | null>(null);");
    expect(conversation).toMatch(/\{sendError && \(\s*<p role="alert" className="mt-1\.5 text-\[10px\] text-destructive">/);
    expect(conversation).not.toContain("/* send failure");
  });

  it("draws each send error in text-destructive, never text-red-500", () => {
    expect(compose).toContain('<span className="text-[10px] text-destructive">{sendError}</span>');
    expect(detail).toContain("{sendErr ?? saveFailure}");
    for (const [name, src] of Object.entries(COMPOSERS)) {
      expect(src, name).not.toMatch(/text-red-500">\s*\{send/);
    }
  });

  it("keeps the error of the inline reply out of the footer that truncates", () => {
    const footer = between(detail, '<span className="text-[10px] truncate min-w-0">', "</span>\n              <div");
    expect(footer).not.toContain("sendErr");
    expect(footer).not.toContain("saveFailure");
  });
});

// ── The contract with api.ts ─────────────────────────────────────────────

describe("the error of saveDraft carries the status and the text of the gateway", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const answer = (status: number, body: unknown) =>
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));

  it("reads a 413 of PUT /email/drafts as too large", async () => {
    answer(413, { detail: "This mail is too large to send." });
    const err = await saveDraft({ accountId: "a" }).catch((e: unknown) => e);
    expect(failedSaveStatus(err)).toBe("too-large");
    expect(sendFailureText(err)).toBe("This mail is too large to send.");
  });

  it("reads the 502 of EM-T9 as not saved, with the name of the file", async () => {
    const file = "The file plan.pdf could not be attached. The mail was not sent.";
    answer(502, { detail: file });
    const err = await saveDraft({ accountId: "a" }).catch((e: unknown) => e);
    expect(failedSaveStatus(err)).toBe("not-saved");
    expect(sendFailureText(err)).toBe(file);
  });
});
