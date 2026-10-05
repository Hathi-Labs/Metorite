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
//     run it at once. Review round 1: one save runs at a time, and a save
//     that waits reads the draft id when it runs. A save of a session that
//     ended writes nothing of the new session, and it still deletes the stale
//     drafts of its own session. The standalone DraftCard has a key.
//     Review round 2: a send, a discard and a pop-out drain the chain. They
//     drop each save that did not start, and they await the save that runs
//     before they read the draft id. The inline autosave carries the Cc and
//     the Bcc. A pop-out hands its draft and its Cc to the full composer.
//   * `email-autosave-wait` (item 12): a Gmail draft that holds a file waits
//     10 seconds, and each other draft waits 1.2 seconds. Each composer asks
//     `autosaveWait`.
//   * `email-save-failed-shows` (item 13): a failed save shows "Not saved",
//     and a 413 shows "Too large to save", in `text-destructive`. The rule
//     reads the status code, never the text.
//   * `email-send-failed-shows` (item 14): each composer shows the text of a
//     failed send, and the DraftCard has a slot for it.
//   * `email-draftcard-recipients` (WS-17 EM-T10, §10.4.11): the DraftCard
//     starts from `draftRecipients`, and a Cc or Bcc edit saves. The toggle
//     marks a button only for `true` or `false`. The standalone card gets
//     `view`, and `view` is never the last mail. One guard at the top of
//     `handleInlineSend` stops a second send, and the Send button loads.
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
  draftToUpdate,
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

/** A promise that the test settles by hand, as a slow gateway answers. */
const deferred = <T = void>() => {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

/** Let each promise that can settle now settle. */
const settle = async () => {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
};

/** The scheduled save of a composer: from `autosave.schedule` to its wait. */
const scheduledRun = (src: string) => between(src, "autosave.schedule(async () => {", "}, autosaveWait(");

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

describe("email-autosave-flush: one save runs at a time (review round 1)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts a flushed save only after the save that runs settles", async () => {
    const first = deferred();
    const run1 = vi.fn(() => first.promise);
    const run2 = vi.fn();
    const a = createAutosave();
    a.schedule(run1, 1200);
    a.flush();
    expect(run1).toHaveBeenCalledTimes(1);
    // A close flushes the next edit while the first save still runs.
    a.schedule(run2, 1200);
    a.flush();
    await settle();
    expect(run2).not.toHaveBeenCalled();
    first.resolve();
    await settle();
    expect(run2).toHaveBeenCalledTimes(1);
  });

  it("starts a save that its timer fired only after the save that runs settles", async () => {
    const first = deferred();
    const run2 = vi.fn();
    const a = createAutosave();
    a.schedule(() => first.promise, 1200);
    vi.advanceTimersByTime(1200);
    a.schedule(run2, 1200);
    vi.advanceTimersByTime(1200);
    await settle();
    expect(run2).not.toHaveBeenCalled();
    first.resolve();
    await settle();
    expect(run2).toHaveBeenCalledTimes(1);
  });

  it("gives the waiting save the draft id that the first save stored", async () => {
    // The composer reads its draft id when the save runs, and stores the id
    // of the row that the gateway gives back.
    let draftId: string | null = null;
    const sentIds: (string | null)[] = [];
    const answers = [deferred<string>(), deferred<string>()];
    const save = (n: number) => async () => {
      sentIds.push(draftId);
      draftId = await answers[n].promise;
    };
    const a = createAutosave();
    a.schedule(save(0), 1200);
    a.flush(); // the first save creates the draft
    a.schedule(save(1), 1200);
    a.flush(); // a close flushes the next edit
    answers[0].resolve("d1");
    await settle();
    answers[1].resolve("d1");
    await settle();
    expect(sentIds).toEqual([null, "d1"]);
  });

  it("runs two waiting saves in order, so the newer text lands last", async () => {
    const started: string[] = [];
    const landed: string[] = [];
    const answers = { A: deferred(), B: deferred(), C: deferred() };
    const save = (body: "A" | "B" | "C") => async () => {
      started.push(body);
      await answers[body].promise;
      landed.push(body);
    };
    const a = createAutosave();
    a.schedule(save("A"), 1200);
    a.flush();
    a.schedule(save("B"), 1200);
    a.flush();
    a.schedule(save("C"), 1200);
    a.flush();
    // The gateway would answer the newer saves first.
    answers.C.resolve();
    answers.B.resolve();
    await settle();
    expect(started).toEqual(["A"]);
    expect(landed).toEqual([]);
    answers.A.resolve();
    await settle();
    expect(started).toEqual(["A", "B", "C"]);
    expect(landed).toEqual(["A", "B", "C"]);
  });

  it("starts a save at once again when no save runs", async () => {
    // A switch flushes before it clears the draft id, so the save must read
    // the id at once when nothing waits before it.
    const a = createAutosave();
    const first = deferred();
    a.schedule(() => first.promise, 1200);
    a.flush();
    first.resolve();
    await settle();
    const run = vi.fn();
    a.schedule(run, 1200);
    a.flush();
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("keeps the order after a save that fails", async () => {
    const first = deferred();
    const run2 = vi.fn();
    const a = createAutosave();
    a.schedule(() => first.promise, 1200);
    a.flush();
    a.schedule(run2, 1200);
    a.flush();
    first.reject(new Error("502"));
    await settle();
    expect(run2).toHaveBeenCalledTimes(1);
  });

  it("drops only the pending save on a cancel, and keeps a save that waits", async () => {
    const first = deferred();
    const waiting = vi.fn();
    const pending = vi.fn();
    const a = createAutosave();
    a.schedule(() => first.promise, 1200);
    a.flush();
    a.schedule(waiting, 1200);
    a.flush();
    a.schedule(pending, 1200);
    a.cancel();
    first.resolve();
    await settle();
    vi.advanceTimersByTime(60_000);
    expect(waiting).toHaveBeenCalledTimes(1);
    expect(pending).not.toHaveBeenCalled();
  });
});

describe("email-autosave-flush: a send, a discard and a pop-out drain the chain (review round 2)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("drops a save that waits, settles with the save that runs, and starts no save after", async () => {
    const first = deferred();
    const waiting = vi.fn();
    const pending = vi.fn();
    const a = createAutosave();
    a.schedule(() => first.promise, 1200);
    a.flush();
    a.schedule(waiting, 1200);
    a.flush();
    a.schedule(pending, 1200);
    let drained: boolean | null = null;
    void a.drain().then((dropped) => {
      drained = dropped;
    });
    await settle();
    // The drain waits for the save that runs.
    expect(drained).toBeNull();
    first.resolve();
    await settle();
    expect(drained).toBe(true);
    vi.advanceTimersByTime(60_000);
    a.flush();
    await settle();
    expect(waiting).not.toHaveBeenCalled();
    expect(pending).not.toHaveBeenCalled();
    expect(a.pending).toBe(false);
  });

  it("settles after a save that runs and fails", async () => {
    const first = deferred();
    const a = createAutosave();
    a.schedule(() => first.promise, 1200);
    a.flush();
    let done = false;
    void a.drain().then(() => {
      done = true;
    });
    first.reject(new Error("502"));
    await settle();
    expect(done).toBe(true);
  });

  it("drops the pending save, and gives true", async () => {
    const run = vi.fn();
    const a = createAutosave();
    a.schedule(run, 1200);
    await expect(a.drain()).resolves.toBe(true);
    vi.advanceTimersByTime(60_000);
    expect(run).not.toHaveBeenCalled();
  });

  it("gives false at once when no save runs or waits", async () => {
    const a = createAutosave();
    await expect(a.drain()).resolves.toBe(false);
    // A save that ran and settled before the drain is not a dropped save.
    const first = deferred();
    a.schedule(() => first.promise, 1200);
    a.flush();
    first.resolve();
    await settle();
    await expect(a.drain()).resolves.toBe(false);
  });

  it("runs a save that an edit schedules after the drain", async () => {
    // A send that fails keeps the composer open, and the next edit saves.
    const a = createAutosave();
    await a.drain();
    const run = vi.fn();
    a.schedule(run, 1200);
    vi.advanceTimersByTime(1200);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("gives a send the draft id of a first save that ran (no orphan draft)", async () => {
    let draftId: string | null = null;
    const created = deferred<string>();
    const a = createAutosave();
    a.schedule(async () => {
      draftId = await created.promise;
    }, 1200);
    vi.advanceTimersByTime(1200);
    const sendReads: (string | null)[] = [];
    const send = (async () => {
      await a.drain();
      sendReads.push(draftId);
    })();
    created.resolve("d1");
    await send;
    expect(sendReads).toEqual(["d1"]);
  });

  it("runs the probe of the re-verify: the order is S1 then SEND, and S2 never starts", async () => {
    // The inline reply on Outlook. S1 updates the draft with T1 and is slow.
    // The member types T2 and pauses, so S2 fires and waits. The member types
    // T3 and clicks Send while S3 waits for its timer. The issue order at
    // 519489183 was [S1, S2, SEND], and round 1 gave [S1, SEND, S2].
    const issued: string[] = [];
    const s1 = deferred();
    const a = createAutosave();
    a.schedule(() => {
      issued.push("S1");
      return s1.promise;
    }, 1200);
    vi.advanceTimersByTime(1200);
    a.schedule(() => {
      issued.push("S2");
    }, 1200);
    vi.advanceTimersByTime(1200);
    a.schedule(() => {
      issued.push("S3");
    }, 1200);
    // Send, as each composer does it: drain, then the save before the send.
    const send = (async () => {
      await a.drain();
      issued.push("SEND");
    })();
    await settle();
    expect(issued).toEqual(["S1"]);
    s1.resolve();
    await send;
    await settle();
    vi.advanceTimersByTime(60_000);
    await settle();
    expect(issued).toEqual(["S1", "SEND"]);
  });
});

describe("email-autosave-flush: the draft that a save updates (review round 1)", () => {
  const last = { session: 3, from: "acct-a", id: "d1" };

  it("is the draft of the composer while the session lives", () => {
    expect(draftToUpdate({ session: 3, from: "acct-a" }, { session: 3, draftId: "d2" }, last)).toBe("d2");
    // A change of From cleared the draft id, so the save creates a new draft.
    expect(draftToUpdate({ session: 3, from: "acct-b" }, { session: 3, draftId: null }, last)).toBeNull();
  });

  it("is the draft that its own session saved last, once the session ended", () => {
    expect(draftToUpdate({ session: 3, from: "acct-a" }, { session: 4, draftId: null }, last)).toBe("d1");
  });

  it("is never the draft of the new session", () => {
    expect(draftToUpdate({ session: 3, from: "acct-a" }, { session: 4, draftId: "d9" }, last)).toBe("d1");
    expect(draftToUpdate({ session: 2, from: "acct-a" }, { session: 4, draftId: "d9" }, last)).toBeNull();
    expect(draftToUpdate({ session: 3, from: "acct-b" }, { session: 4, draftId: "d9" }, last)).toBeNull();
    expect(draftToUpdate({ session: 3, from: "acct-a" }, { session: 4, draftId: "d9" }, null)).toBeNull();
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

  /** The send of each composer, up to the code after it. */
  const SENDS = [
    ["compose", between(compose, "const handleSend = async", "const saveFailure")],
    ["detail", between(detail, "const handleInlineSend = async", "const addReplyFiles")],
    ["conversation", between(conversation, "const send = async", "const discard")],
  ] as const;

  it("awaits the drain in each send, before the save before the send (review round 2)", () => {
    for (const [name, send] of SENDS) {
      expect(send, name).toContain("await autosave.drain();");
      comesBefore(send, "await autosave.drain();", "await saveDraft(");
      // A cancel keeps a save that waits, and that save lands after the send.
      expect(send, name).not.toContain("autosave.cancel()");
    }
  });

  it("reads the draft id of a send only after the drain (review round 2)", () => {
    for (const [name, send] of SENDS.slice(0, 2)) {
      const before = between(send, "async", "await autosave.drain();");
      expect(before, name).not.toContain("draftIdRef.current");
      comesBefore(send, "await autosave.drain();", "if (draftIdRef.current ||");
    }
  });

  it("awaits the drain in each discard, before the delete (review round 2)", () => {
    const composeDiscard = between(compose, "const discardDraft = async", "const handleSend");
    const detailDiscard = between(detail, "const discardReply = async", "const composedReply");
    const cardDiscard = between(conversation, "const discard = async", "const runAi");
    for (const [name, discard] of [
      ["compose", composeDiscard],
      ["detail", detailDiscard],
      ["conversation", cardDiscard],
    ] as const) {
      comesBefore(discard, "const drained = autosave.drain();", "await drained;");
      comesBefore(discard, "await drained;", "deleteEmail(");
      expect(discard, name).not.toContain("autosave.cancel()");
    }
    // The composer and the reply ask draftToUpdate after the drain, so a
    // first save that ran, or a session that the reset ended, names its draft.
    for (const [name, discard] of [["compose", composeDiscard], ["detail", detailDiscard]] as const) {
      comesBefore(discard, "await drained;", "const id = draftToUpdate(");
      expect(discard, name).toContain("if (id) void deleteEmail(id);");
      expect(between(discard, "async", "await drained;"), name).not.toContain("deleteEmail(");
    }
    comesBefore(cardDiscard, "const drained = autosave.drain();", "onDismiss?.()");
    // Each Discard button calls the discard that drains.
    expect(compose).toContain("onClick={() => void discardDraft()}");
    expect(detail).toContain("onClick={() => void discardReply()}");
  });

  it("keeps the cancel of the reset of the reply, after the drain of a send or a discard", () => {
    expect(between(detail, "const resetReplySession = () => {", "const discardReply")).toContain("autosave.cancel();");
  });

  /** The guard of each composer, on a save that ends after its session ended. */
  const GUARDS = [
    ["compose", compose, "if (sessionRef.current !== session) {"],
    ["detail", detail, "if (replySessionRef.current !== session) {"],
  ] as const;

  it("guards a save that ends after its session ended, before it writes the draft id", () => {
    for (const [name, src, guard] of GUARDS) {
      const run = scheduledRun(src);
      expect(run, name).toContain(guard);
      comesBefore(run, guard, "draftIdRef.current = saved.id;");
      const ended = between(run, guard, "return;");
      expect(ended, name).not.toContain("draftIdRef.current =");
      expect(ended, name).not.toContain("HasFileRef.current =");
      expect(ended, name).not.toContain("setDraftStatus");
    }
  });

  it("deletes the stale drafts of an ended session, from the list of its schedule", () => {
    for (const [name, src, guard] of GUARDS) {
      const effect = between(src, "const savingFrom = fromId;", "autosave.schedule(async () => {");
      expect(effect, name).toContain("const stale = staleDraftsRef.current;");
      // The cleanup runs inside the guard, before its return.
      const ended = between(scheduledRun(src), guard, "return;");
      expect(ended, name).toContain("if (!stale.includes(saved.id)) dropDrafts(stale);");
      expect(ended, name).toContain("lastSaveRef.current = { session, from: savingFrom, id: saved.id };");
      // The list empties in place, so no draft is deleted twice.
      expect(src, name).toContain("for (const id of list.splice(0)) void deleteEmail(id);");
    }
  });

  it("reads the draft id when the save runs, through draftToUpdate", () => {
    for (const [name, src] of GUARDS) {
      const run = scheduledRun(src);
      comesBefore(run, "const draftId = draftToUpdate(", "await saveDraft(");
      expect(run, name).toMatch(
        /\{ session: (replySessionRef|sessionRef)\.current, draftId: draftIdRef\.current \},\s*lastSaveRef\.current,/,
      );
      expect(run, name).toContain("draftId: draftId ?? undefined,");
      expect(run, name).not.toContain("draftId: draftIdRef.current ?? undefined");
      // A live save keeps the record of the draft for a later save.
      expect(run, name).toMatch(
        /draftIdRef\.current = saved\.id;\s*\w+HasFileRef\.current = saved\.hasAttachments;\s*lastSaveRef\.current = \{ session, from: savingFrom, id: saved\.id \};/,
      );
    }
  });

  it("shows Saving only for the live session", () => {
    expect(scheduledRun(compose)).toContain('if (sessionRef.current === session) setDraftStatus("saving");');
    expect(scheduledRun(detail)).toContain('if (replySessionRef.current === session) setDraftStatus("saving");');
    for (const [name, src] of GUARDS) {
      expect(scheduledRun(src), name).not.toMatch(/^\s*setDraftStatus\("saving"\);/m);
    }
  });

  /** The pop-out of the inline reply, from its start to the command bridge. */
  const popOut = () => between(detail, "const popOutToComposer = async () => {", "cmdRef.current =");
  /** The defaults that the pop-out hands to the full composer. */
  const popOutDefaults = () => between(popOut(), "openCompose({", "});");

  it("opens the full composer dirty when the drain of a pop-out dropped an edit", () => {
    const pop = popOut();
    // The drain gives true when it dropped an edit (review round 2).
    comesBefore(pop, "const drained = autosave.drain();", "const unsavedEdit = await drained;");
    expect(popOutDefaults()).toMatch(/^\s*unsavedEdit,$/m);
    expect(pop).not.toContain("autosave.pending");
    // A flush here as well would make two drafts.
    expect(pop).not.toContain("autosave.flush()");
    expect(codeOnly(read("page.tsx"))).toContain("unsavedEdit={composeDefaults?.unsavedEdit}");
    const open = between(compose, "if (!open) return;", "}, [open]);");
    expect(open).toContain("dirty.current = Boolean(unsavedEdit);");
    expect(open).not.toContain("dirty.current = false;");
  });

  it("hands the draft of the reply to the full composer, after the drain (F2, review round 2)", () => {
    const pop = popOut();
    // The save that runs settles first, so a first save gives its draft id.
    comesBefore(pop, "const unsavedEdit = await drained;", "const draftId = draftToUpdate(");
    comesBefore(pop, "const draftId = draftToUpdate(", "openCompose({");
    expect(pop).toMatch(
      /\{ session: replySessionRef\.current, draftId: draftIdRef\.current \},\s*lastSaveRef\.current,/,
    );
    const defaults = popOutDefaults();
    expect(defaults).toContain("draftId: handOver,");
    expect(defaults).toContain("draftHasFile: handOver ? replyHasFileRef.current : undefined,");
    // The reply closes at once, before the wait.
    comesBefore(pop, "setReplyMode(null);", "await drained;");
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain("draftId={composeDefaults?.draftId}");
    expect(page).toContain("draftHasFile={composeDefaults?.draftHasFile}");
  });

  it("hands over no draft of a reply with a Bcc, because the full composer has no Bcc row", () => {
    expect(popOut()).toContain("const handOver = draftId && !replyBcc.trim() ? draftId : undefined;");
    // The day the composer gets a Bcc row, this rule can go.
    expect(compose).not.toMatch(/\bsetBcc\b|\bbcc:/);
  });

  it("carries the Cc of the reply to the full composer, which saves the draft with it", () => {
    expect(popOutDefaults()).toContain("cc: replyCc,");
    expect(codeOnly(read("page.tsx"))).toContain("defaultCc={composeDefaults?.cc}");
    const open = between(compose, "if (!open) return;", "}, [open]);");
    expect(open).toContain("setCc(defaultCc);");
    expect(scheduledRun(compose)).toContain('cc: cc ? cc.split(",").map((s) => s.trim()).filter(Boolean) : [],');
  });

  it("updates the draft that a pop-out hands over, in the full composer", () => {
    const open = between(compose, "if (!open) return;", "}, [open]);");
    expect(open).toContain("draftIdRef.current = draftId ?? null;");
    expect(open).toContain("draftHasFileRef.current = Boolean(draftId && draftHasFile);");
    expect(open).not.toMatch(/draftIdRef\.current = null;/);
    // The seeded draft counts as the last save of the new session.
    comesBefore(open, "sessionRef.current += 1;", "draftIdRef.current = draftId ?? null;");
    expect(open).toMatch(
      /if \(draftId\) \{\s*lastSaveRef\.current = \{\s*session: sessionRef\.current, from: defaultFromId \|\| accountId, id: draftId,\s*\};/,
    );
  });

  it("carries the Cc and the Bcc in each autosave of the inline reply (review round 2)", () => {
    // The gateway writes the lists that it gets, and `api.ts` sends [] for a
    // missing list. So a save without them cleared the Cc and the Bcc.
    const effect = between(detail, "const savingFrom = fromId;", "}, autosaveWait(");
    const run = scheduledRun(detail);
    expect(run).toContain("cc: ccArr,");
    expect(run).toContain("bcc: bccArr,");
    const prep = between(detail, "if (!replyBody.trim() && toArr.length === 0) {", "const savingFrom = fromId;");
    expect(prep).toContain('const ccArr = replyCc.split(",").map((s) => s.trim()).filter(Boolean);');
    expect(prep).toContain('const bccArr = replyBcc.split(",").map((s) => s.trim()).filter(Boolean);');
    expect(effect).toContain("autosave.schedule(async () => {");
    // An edit of the Cc or of the Bcc starts a save too.
    expect(detail).toContain("}, [replyBody, replyQuote, replyTo, replyCc, replyBcc, replyMode, fromId, email?.id]);");
  });

  it("carries each recipient that its composer holds, in each autosave", () => {
    expect(scheduledRun(compose)).toMatch(/\bcc: cc \?/);
    expect(scheduledRun(conversation)).toContain("cc: ccList(),");
    expect(scheduledRun(conversation)).toContain("bcc: bccList(),");
  });

  it("gives the standalone DraftCard the key of its draft (EM-G3c-2-f1)", () => {
    expect(detail).toMatch(/<DraftCard key=\{email\.id\} draft=\{email\}/);
    expect(conversation).toMatch(/<DraftCard\s+key=\{m\.id\}\s+draft=\{m\}/);
    // Each DraftCard that a composer draws carries a key.
    for (const [name, src] of Object.entries(COMPOSERS)) {
      const all = src.match(/<DraftCard\b/g)?.length ?? 0;
      const keyed = src.match(/<DraftCard\s+key=\{/g)?.length ?? 0;
      expect(keyed, name).toBe(all);
    }
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

  it("clears an old send error of the inline reply when a send starts", () => {
    // `sendErr ?? saveFailure` draws one line, so an old send error would hide
    // a later "Not saved" (review round 1). ComposePanel does the same.
    const send = between(detail, "const handleInlineSend = async", "const fromAccount = ");
    comesBefore(send, "if (!email) return;", "setSendErr(null);");
    expect(between(compose, "const handleSend = async", "await autosave.drain();")).toContain("setSendError(null);");
  });

  it("keeps the error of the inline reply out of the footer that truncates", () => {
    const footer = between(detail, '<span className="text-[10px] truncate min-w-0">', "</span>\n              <div");
    expect(footer).not.toContain("sendErr");
    expect(footer).not.toContain("saveFailure");
  });
});

// ── email-draftcard-recipients (EM-T10) ─────────────────────────────────

describe("email-draftcard-recipients: a draft card keeps the recipients of its draft", () => {
  const card = between(conversation, "export function DraftCard(", "const applyReplyAll = ");

  it("starts the To, the Cc, the Bcc, the toggle and the Cc row from draftRecipients", () => {
    expect(card).toMatch(/const \[start\] = useState\(\(\) =>\s*draftRecipients\(\s*draft,/);
    expect(card).toContain('const [to, setTo] = useState(start.to.join(", "));');
    expect(card).toContain('const [cc, setCc] = useState(start.cc.join(", "));');
    expect(card).toContain('const [bcc, setBcc] = useState(start.bcc.join(", "));');
    expect(card).toContain("const [replyAll, setReplyAll] = useState<boolean | null>(start.replyAll);");
    expect(card).toContain("const [showCc, setShowCc] = useState(start.showCc);");
    // The lists of the reply target no longer seed the card.
    expect(card).not.toContain("useState(replyAllTo.join(");
    expect(card).not.toContain("useState(hasReplyTarget)");
  });

  it("computes the lists of a reply target only when the card has one", () => {
    expect(card).toMatch(/const all = hasReplyTarget && replyTo\s*\?\s*replyRecipients\(replyTo, "reply-all"/);
    expect(card).toMatch(/const only = hasReplyTarget && replyTo\s*\?\s*replyRecipients\(replyTo, "reply"/);
  });

  it("saves after an edit of the Cc or the Bcc (item 4, EM-G3c-2-f2)", () => {
    expect(conversation).toContain("}, [body, quote, to, cc, bcc]);");
    expect(conversation).toContain("onChange={(v) => { dirty.current = true; setCc(v); }}");
    expect(conversation).toContain("onChange={(v) => { dirty.current = true; setBcc(v); }}");
    expect(conversation).not.toContain("onChange={setCc}");
    expect(conversation).not.toContain("onChange={setBcc}");
  });

  it("marks a button only for true or false, so null marks neither", () => {
    expect(conversation).toContain('replyAll === false ? "bg-primary text-primary-foreground"');
    expect(conversation).toContain('replyAll === true ? "bg-primary text-primary-foreground"');
    expect(conversation).not.toMatch(/[!(\s]replyAll \?/);
  });

  it("gives the standalone card view, and view is never the last mail (item 5, C2)", () => {
    expect(detail).toContain("const view: Email = detail?.id === email.id ? detail : email;");
    expect(detail).not.toContain("detail ?? email");
    expect(detail).toContain("<DraftCard key={email.id} draft={email} replyTo={view} />");
  });

  it("runs one inline send at a time, with one guard at the top (item 7, f10)", () => {
    const send = between(detail, "const handleInlineSend = async () => {", "const addReplyFiles");
    // The guard is the first statement, so a click and Ctrl+Enter both meet it.
    expect(between(send, "async () => {", "if (!email) return;").replace(/\s+/g, " ").trim())
      .toBe("async () => { if (sendingRef.current) return;");
    expect(detail.match(/if \(sendingRef\.current\) return;/g)?.length).toBe(1);
    // The state starts after the last early return and before the drain.
    comesBefore(send, 'setSendErr("Add at least one recipient");', "sendingRef.current = true;");
    comesBefore(send, "sendingRef.current = true;", "setSending(true);");
    comesBefore(send, "setSending(true);", "await autosave.drain();");
    expect(between(send, "setSending(true);", "await autosave.drain();")).toContain("try {");
    expect(send).toMatch(/\} finally \{\s*sendingRef\.current = false;\s*setSending\(false\);\s*\}/);
    expect(detail.match(/sendingRef\.current = true;/g)?.length).toBe(1);
  });

  it("draws the inline Send as a Button that loads, with the label Send", () => {
    expect(detail).toMatch(
      /<Button[^>]*\bicon="Send"[^>]*\bloading=\{sending\}[^>]*onClick=\{\(\) => void handleInlineSend\(\)\}[^>]*>\s*Send\s*<\/Button>/,
    );
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
