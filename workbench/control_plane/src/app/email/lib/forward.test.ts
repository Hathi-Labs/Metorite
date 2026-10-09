/**
 * The Forward of the reading pane (follow-up 4 of #766).
 *
 * Owner report, 2026-10-09: the pane's Forward cleared every file of the
 * email and sent a new mail. The pane now sends `POST /email/forward`. This
 * file fences the request it builds, the words of each refusal, and the
 * markers it reads out of the route's own details.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  FORWARD_DETAIL_MARKERS,
  FORWARD_UNSURE,
  OUTLOOK_ALL_OR_NONE,
  fileSizeText,
  forwardFailure,
  forwardFilesOf,
  forwardRequest,
  outlookSubset,
  visibleFileName,
  type ForwardFile,
} from "./forward";

const PDF = "0f8fad5b-d9cb-469f-a165-70867728950e";
const SHEET = "1b4e28ba-2fa1-41d2-883f-0016d3cca427";

const files = (pdf: boolean, sheet: boolean): ForwardFile[] => [
  { id: PDF, filename: "quote.pdf", sizeBytes: 2 * 1024 * 1024, checked: pdf },
  { id: SHEET, filename: "rates.xlsx", sizeBytes: 30_000, checked: sheet },
];

const base = {
  messageId: "mail-1",
  accountId: "box-a",
  to: ["geo@fracktal.test"],
  cc: [] as string[],
  bcc: [] as string[],
  note: "See the quote.",
};

function refusal(status: number, detail?: string): Error & { status: number } {
  const err = new Error(detail ?? `Gateway error ${status}`) as Error & { status: number };
  err.status = status;
  return err;
}

describe("the request the pane builds", () => {
  // Mutation caught: the old pane path, which sent no file of the email.
  it("every file kept asks for every file, with no id list", () => {
    expect(forwardRequest({ ...base, files: files(true, true) })).toEqual({
      message_id: "mail-1",
      account_id: "box-a",
      to: ["geo@fracktal.test"],
      note: "See the quote.",
      include_attachments: true,
    });
  });

  it("some files kept name each kept file", () => {
    const body = forwardRequest({ ...base, cc: ["cc@x.test"], bcc: ["b@x.test"], files: files(true, false) });
    expect(body.include_attachments).toBe(true);
    expect(body.attachment_ids).toEqual([PDF]);
    expect(body.cc).toEqual(["cc@x.test"]);
    expect(body.bcc).toEqual(["b@x.test"]);
  });

  it("no file kept forwards the text only", () => {
    const body = forwardRequest({ ...base, files: files(false, false) });
    expect(body.include_attachments).toBe(false);
    expect(body.attachment_ids).toBeUndefined();
  });

  // Mutation caught: a pane whose detail did not load forwarded no file.
  it("a pane with no file list asks for every file", () => {
    const body = forwardRequest({ ...base, files: [] });
    expect(body.include_attachments).toBe(true);
    expect(body.attachment_ids).toBeUndefined();
  });

  it("an empty note and empty lists stay off the wire", () => {
    const body = forwardRequest({ ...base, note: "  \n", files: files(true, true) });
    expect(body).not.toHaveProperty("note");
    expect(body).not.toHaveProperty("cc");
    expect(body).not.toHaveProperty("bcc");
  });

  // Review round 1: a right-to-left override cannot turn a name around.
  it("a chip name holds no bidi control", () => {
    expect(visibleFileName("invoice\u202Efdp.exe")).toBe("invoicefdp.exe");
    expect(visibleFileName("a\u2066b\u2069\u200Ec\u061C")).toBe("abc");
    expect(visibleFileName("quote.pdf")).toBe("quote.pdf");
    // The chip draws the cleaned name, in the text and in the title, in <bdi>.
    const chip = readFileSync(join(__dirname, "../components/ForwardFileChips.tsx"), { encoding: "utf-8" });
    expect(chip).toContain("const name = visibleFileName(f.filename);");
    expect(chip).toMatch(/<bdi [^>]*>\{name\}<\/bdi>/);
    expect(chip).not.toMatch(/\$\{f\.filename\}|\{f\.filename\}/);
  });

  it("the chips start with every file kept, and show a size", () => {
    const chips = forwardFilesOf([
      { id: PDF, filename: "quote.pdf", mimeType: "application/pdf", sizeBytes: 2 * 1024 * 1024 },
      { id: "", filename: "no-id.txt", mimeType: "text/plain", sizeBytes: 1 },
    ]);
    expect(chips).toEqual([{ id: PDF, filename: "quote.pdf", sizeBytes: 2 * 1024 * 1024, checked: true }]);
    expect(fileSizeText(2 * 1024 * 1024)).toBe("2.0 MB");
    expect(fileSizeText(30_000)).toBe("29 KB");
    expect(fileSizeText(0)).toBe("");
  });
});

describe("Outlook forwards every file or none", () => {
  it("a subset on Outlook is caught before the send", () => {
    expect(outlookSubset("microsoft", files(true, false))).toBe(true);
    expect(outlookSubset("microsoft", files(true, true))).toBe(false);
    expect(outlookSubset("microsoft", files(false, false))).toBe(false);
    expect(outlookSubset("gmail", files(true, false))).toBe(false);
    expect(outlookSubset(undefined, files(true, false))).toBe(false);
  });
});

describe("the words of each refusal", () => {
  it("413 keeps the route's words and offers a forward without files", () => {
    const f = forwardFailure(refusal(413, "The files of this mail come to 30.0 MB, and a forward carries 25.0 MB at most, so nothing was sent."));
    expect(f.text).toContain("30.0 MB");
    expect(f.offerNoFiles).toBe(true);
    expect(forwardFailure(refusal(413)).text).toBe("This email and its files are too large to forward. Nothing was sent.");
  });

  it("422 of an Outlook subset says Outlook forwards all or none", () => {
    const f = forwardFailure(refusal(422, "On an Outlook mailbox, a forward carries every file of the email or none of them, so nothing was sent."));
    expect(f.text).toBe(`${OUTLOOK_ALL_OR_NONE} Nothing was sent.`);
    expect(f.offerNoFiles).toBe(true);
  });

  it("422 of an IMAP mailbox or a file with no bytes offers a forward without files", () => {
    expect(forwardFailure(refusal(422, "I cannot forward the files of an IMAP mailbox yet, so nothing was sent.")).offerNoFiles).toBe(true);
    expect(forwardFailure(refusal(422, "The mail provider gave no bytes for the file 'a.eml'.")).offerNoFiles).toBe(true);
  });

  it("422 of a provider refusal keeps its words, with no offer", () => {
    const f = forwardFailure(refusal(422, "The mail provider refused the forward: HTTP 400 (ErrorInvalidRecipients). Nothing was sent."));
    expect(f.text).toContain("ErrorInvalidRecipients");
    expect(f.offerNoFiles).toBe(false);
  });

  it("the gateway's own 502 says nothing was sent, and to try again", () => {
    const f = forwardFailure(refusal(502, "The mail provider did not answer, so nothing was sent."));
    expect(f.text).toBe("The mail provider did not answer, so nothing was sent. Try again in a moment.");
    expect(f.unsure).toBe(false);
  });

  // Review round 1, P2. Mutation caught: a 502 with no detail read "nothing
  // was sent". Only the Next proxy answers it (its wait ended), and the
  // gateway can still send the mail, so a member who tried again sent twice.
  it("a 502 with no detail, a 5xx with no detail and a lost request are unsure", () => {
    for (const err of [refusal(502), refusal(504), refusal(500), new Error("socket hang up"), null]) {
      const f = forwardFailure(err);
      expect(f.unsure).toBe(true);
      expect(f.text).toBe(FORWARD_UNSURE);
      expect(f.text).not.toMatch(/nothing was sent/i);
      expect(f.offerNoFiles).toBe(false);
    }
    // The BFF's own body: `{ error }`, so the message is "Gateway error 502".
    expect(forwardFailure(refusal(502, "Gateway error 502")).unsure).toBe(true);
  });

  it("401, 404 and 429 each say what to do", () => {
    expect(forwardFailure(refusal(401, "Email account authentication failed")).text).toBe(
      "Reconnect this mailbox to forward from it. Nothing was sent.");
    expect(forwardFailure(refusal(404, "Email not found")).text).toBe(
      "This email is no longer in the mailbox, so nothing was sent.");
    expect(forwardFailure(refusal(429, "The mail provider asked for a pause. Nothing was sent. Try again later.")).text)
      .toContain("Try again later");
  });

  it("a validation list never shows as an object", () => {
    const err = refusal(422, "[object Object]");
    expect(forwardFailure(err).text).toBe("The mail provider refused the forward. Nothing was sent.");
    expect(forwardFailure(err).unsure).toBe(false);
  });
});

describe("the markers are the route's own words", () => {
  // R7 fence: a reworded detail in forward.py breaks the mapping above.
  const route = readFileSync(
    join(__dirname, "../../../../../../apps/services/gateway/gateway/routes/email/transport/forward.py"),
    { encoding: "utf-8" },
  ).replace(/"\s*\n\s*"/g, "");

  it.each(Object.entries(FORWARD_DETAIL_MARKERS))("%s is in a detail of forward.py", (_name, marker) => {
    expect(route).toContain(marker);
  });
});

describe("the pane sends the forward through the route", () => {
  const pane = readFileSync(join(__dirname, "../components/EmailDetail.tsx"), { encoding: "utf-8" });

  it("a forward goes to POST /email/forward, with the kept files", () => {
    // The forward is the first branch of the one send, after its one drain.
    expect(pane).toMatch(/if \(isForward\) \{\s*await forwardEmail\(forwardRequest\(\{/);
    expect(pane).toContain("if (isForward && forwardBlocked(files)) return;");
    // A sent forward never reaches the optimistic reply of the thread.
    expect(pane).toMatch(/if \(isForward\) \{\s*await finishForward\(session, stale\);\s*return;\s*\}/);
  });

  // Review round 1, P3-a. Mutation caught: a finish with no drain left a
  // late debounced save as a stray "Fwd:" draft.
  it("a sent forward drains its saves, then deletes each draft", () => {
    const finish = pane.slice(pane.indexOf("const finishForward = async"), pane.indexOf("const handleInlineSend"));
    expect(finish).toMatch(
      /const drained = autosave\.drain\(session\);\s*resetReplySession\(\);\s*await drained;\s*const ids = draftsToDiscard\(/,
    );
    // While it is in flight, its fields are read-only and no save starts.
    expect(pane).toContain('const forwarding = sending && replyMode === "forward";');
    expect(pane.match(/readOnly=\{forwarding\}/g)?.length).toBe(4);
    expect(pane).toMatch(/if \(replyMode === "forward" && sendingRef\.current\) \{\s*autosave\.cancel\(\);\s*return;/);
  });

  // Review round 1, P2: an unsure answer offers Sent, never a send.
  it("an unsure forward offers Open Sent", () => {
    expect(pane).toContain("setForwardUnsure(failure.unsure);");
    expect(pane).toMatch(/forwardUnsure && sendErr && \([\s\S]{0,200}selectFolder\("sent"\)/);
  });

  // Review round 1: the full composer drops the files of the email.
  it("Pop out is off while the forward keeps a file", () => {
    expect(pane).toContain('const popOutLosesFiles = replyMode === "forward" && forwardFiles.some((f) => f.checked);');
    expect(pane).toContain("disabled={popOutLosesFiles || forwarding}");
    expect(pane).toContain("setForwardFiles(mode === \"forward\" ? forwardFilesOf(src.attachments) : []);");
  });
});
