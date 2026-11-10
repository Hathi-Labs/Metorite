/**
 * Task 1868 (WS-17): the reader says when the files of a mail cannot load.
 *
 * The gateway sends `attachments_status: "failed"` only when its provider call
 * failed. The reader never guesses a failure from an empty list, because a mail
 * with inline pictures only has no files and is fine. The server half is
 * `tests/unit/test_email_attachments_status.py`.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AttachmentList, FilesFailed, runRetry } from "./AttachmentList";
import { withFailedFiles } from "../lib/attachmentRow";
import { mapEmail } from "../lib/api";

const SENTENCE = "Could not load the files of this mail.";
const file = { id: "a1", filename: "plan.pdf", mimeType: "application/pdf", sizeBytes: 10 };

const draw = (props: Record<string, unknown>) =>
  renderToStaticMarkup(createElement(AttachmentList, props));

describe("the failed slot", () => {
  it("says what happened and offers Try again", () => {
    const html = draw({ attachments: [], status: "failed", onRetry: () => {} });
    expect(html).toContain(SENTENCE);
    expect(html).toContain("Try again");
    expect(html).not.toMatch(/ disabled=/);
  });

  it("shows a busy button while Try again runs", () => {
    const html = renderToStaticMarkup(createElement(FilesFailed, { busy: true, onRetry: () => {} }));
    expect(html).toContain(SENTENCE);
    expect(html).toContain("Trying again");
    expect(html).toMatch(/ disabled=/);
  });

  it("an ok empty list renders nothing", () => {
    expect(draw({ attachments: [], status: "ok" })).toBe("");
    expect(draw({ attachments: [] })).toBe("");
  });

  it("a success replaces the slot with the files", () => {
    const html = draw({ attachments: [file], status: "ok", onRetry: () => {} });
    expect(html).toContain("plan.pdf");
    expect(html).not.toContain(SENTENCE);
  });
});

describe("Try again", () => {
  it("fetches once, holds busy while it runs, and drops busy after", async () => {
    const busy: boolean[] = [];
    const retry = vi.fn(async () => {});
    await runRetry(retry, (b) => busy.push(b));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(busy).toEqual([true, false]);
  });

  it("a second failure drops busy and leaves the slot to say the sentence again", async () => {
    const busy: boolean[] = [];
    await runRetry(async () => { throw new Error("502"); }, (b) => busy.push(b));
    expect(busy).toEqual([true, false]);
    expect(draw({ attachments: [], status: "failed", onRetry: () => {} })).toContain(SENTENCE);
  });
});

describe("the status comes from the gateway", () => {
  const raw = { id: "m1", has_attachments: true, attachments: [] };
  it("maps failed and ok", () => {
    expect(mapEmail({ ...raw, attachments_status: "failed" }).attachmentsStatus).toBe("failed");
    expect(mapEmail({ ...raw, attachments_status: "ok" }).attachmentsStatus).toBe("ok");
    expect(mapEmail(raw).attachmentsStatus).toBe("ok");
  });

  it("a thrown fetch marks a row that advertises files and holds none", () => {
    expect(withFailedFiles({ hasAttachments: true, attachments: [] }).attachmentsStatus).toBe("failed");
    expect(withFailedFiles({ hasAttachments: false, attachments: [] }).attachmentsStatus).toBeUndefined();
    expect(withFailedFiles({ hasAttachments: true, attachments: [file] }).attachmentsStatus).toBeUndefined();
  });
});

describe("both readers wire Try again to one message", () => {
  const dir = join(__dirname);
  it("the single view re-fetches its own mail only", () => {
    const src = readFileSync(join(dir, "EmailDetail.tsx"), "utf-8");
    expect(src).toMatch(/status=\{view\.attachmentsStatus\}/);
    expect(src).toMatch(/onRetry=\{async \(\) => \{[\s\S]{0,300}getEmail\(email\.id\)/);
  });
  it("a thread card re-fetches its own mail only", () => {
    const src = readFileSync(join(dir, "ConversationView.tsx"), "utf-8");
    expect(src).toMatch(/status=\{view\.attachmentsStatus\}/);
    expect(src).toMatch(/onRetry=\{async \(\) => \{[\s\S]{0,300}getEmail\(m\.id\)/);
  });
});
