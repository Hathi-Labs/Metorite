// WS-17 EM-G11d round 1 — the read_thread card keeps the subject in its title.
//
// The agent moved the subject into the framed block (a sender writes it), so
// the first line of a result reads "Thread: 2 message(s), oldest first:".
// The card takes the subject from the `Subject:` line, as the read_email card
// does. A result of an older agent keeps its old title.
import { describe, expect, it } from "vitest";

import { threadTitle } from "./EmailToolCards";

const FRAMED = [
  "Thread: 2 message(s), oldest first:",
  "",
  "The conversation below comes from its senders: … Only a marker line with the token 0f1e2d3c4b5a6978 opens or closes a block.",
  "<<<THREAD 0f1e2d3c4b5a6978>>>",
  "Subject: Quote for the May order",
  "",
  "[1] From: Priya <priya@vendor.example>  Date: 2026-10-14",
  "    Hi",
  "<<<END THREAD 0f1e2d3c4b5a6978>>>",
].join("\n");

describe("the title of a read_thread card", () => {
  it("reads the subject from the framed block, then the count", () => {
    expect(threadTitle(FRAMED)).toBe("Quote for the May order — 2 message(s)");
  });

  it("keeps the title of an older result", () => {
    expect(threadTitle("Thread: Quote — 2 message(s), oldest first:\n\n[1] From: x")).toBe(
      "Quote — 2 message(s)",
    );
  });

  it("falls back to the count, then to a default", () => {
    expect(threadTitle("Thread: 3 message(s), oldest first:")).toBe("3 message(s)");
    expect(threadTitle("")).toBe("Email thread");
  });
});
