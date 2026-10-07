/**
 * A confirmation card's fields, as a member reads them (owner reports,
 * 2026-10-07). `cardFields.ts` is the one key → label map; the Python fakes
 * read it too (`tests/unit/_card_words.py`). Each rule names its mutation.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import ConfirmationCard, { parseCardBody, runKey } from "@/components/ConfirmationCard";
import {
  CARD_FIELDS,
  cardKey,
  fieldSpec,
  formatCardDate,
  formatMinutes,
  parseFieldValue,
  repeatsLine,
} from "@/lib/cardFields";
import { categoricalAccent } from "@/lib/categorical";

/** The words a member sees: the markup with every tag (and attribute) removed. */
const visible = (html: string) => html.replace(/<[^>]*>/g, " ");

const card = (props: Record<string, unknown>) =>
  renderToStaticMarkup(
    createElement(ConfirmationCard, { title: "", fenced: true, onApprove: () => {}, onReject: () => {}, ...props } as never),
  );

describe("the map", () => {
  // Mutation caught: a label that is the raw key again.
  it("every label is product words, never a wire key", () => {
    for (const [key, spec] of Object.entries(CARD_FIELDS)) {
      expect(spec.label, key).not.toMatch(/_/);
      expect(spec.label.charAt(0), key).toBe(spec.label.charAt(0).toUpperCase());
    }
  });

  it("reads numbered keys, custom fields, and an unknown key as words", () => {
    expect(cardKey("task 12")).toBe("task N");
    expect(fieldSpec("task 3").kind).toBe("task");
    expect(fieldSpec("field Cost centre").label).toBe("Cost centre");
    expect(fieldSpec("tags_add")).toMatchObject({ label: "Add tags", kind: "tag" });
    expect(fieldSpec("some_new_key").label).toBe("Some new key");
  });
});

describe("a value", () => {
  it("splits a change at the arrow outside the marks", () => {
    const v = parseFieldValue("«a → b» → «c»");
    expect(v.before).toEqual([{ text: "a → b", named: true }]);
    expect(v.after).toEqual([{ text: "c", named: true }]);
  });

  it("reads a fenced list, a Python list and None", () => {
    expect(parseFieldValue("«q4», «Bug»", true).after.map((i) => i.text)).toEqual(["q4", "Bug"]);
    expect(parseFieldValue("['urgent', 'p0']", true).after.map((i) => i.text)).toEqual(["urgent", "p0"]);
    expect(parseFieldValue("None → 30").before).toEqual([{ text: "None", named: false }]);
  });

  it("formats a date by hand and a duration", () => {
    expect(formatCardDate("2026-10-07")).toBe("7 Oct 2026");
    expect(formatCardDate("2026-10-07T14:05:00+00:00")).toBe("7 Oct 2026, 14:05");
    expect(formatCardDate("cleared")).toBe("cleared");
    expect(formatMinutes("90")).toBe("1 h 30 min");
  });

  it("knows when a field only repeats a line", () => {
    expect(repeatsLine("4 tasks changed", [undefined, "4 tasks  changed"])).toBe(true);
    expect(repeatsLine("#7 «x»", ["#7 x"])).toBe(true);
    expect(repeatsLine("other", ["4 tasks"])).toBe(false);
  });
});

describe("screenshot A: the batch card", () => {
  const html = card({
    title: "Create 8 tasks in «Email Application»?",
    detail: "one batch in «Email Application» · untick a task to leave it out",
    rows: [{ id: "r1", label: "Draft the spec", hint: "nobody assigned · due 9 Oct 2026", checked: true }],
  });

  // Mutation caught: `{summary}` drawn as text again.
  it("shows no mark, and the project as a quiet emphasis", () => {
    expect(html).not.toContain("«");
    expect(html).not.toContain("»");
    expect(html).toContain('aria-label="Create 8 tasks in Email Application?"');
    expect(html).toMatch(/data-fenced-name[^>]*>Email Application<\/span>/);
  });
});

describe("the bulk card (owner's second screenshot)", () => {
  const LONG = "Fix the conversation view when the thread is very long and wraps";
  const html = card({
    title: "Change 4 tasks at once?",
    detail: "one change across 4 tasks",
    context: [
      "This change is recorded as yours, made through the Projects assistant.",
      "impact: one change across 4 tasks",
      "tags_add: «Bug»",
      "status: «Done»",
      "due_at: «2026-10-09»",
      "subtasks: «stay as they are, unless you ask for them too»",
      "task 1: #28 «Fix Inbox Cleaner 'Keep Approved' doing nothing»",
      "task 2: #29 «Ship it»",
      `task 3: #30 «${LONG}»`,
    ].join("\n"),
  });

  // Mutation caught: the raw key as the label (`label(key)` humanised).
  it("labels each key in product words, and no raw key shows", () => {
    expect(visible(html)).not.toContain("tags_add");
    expect(visible(html)).not.toContain("due_at");
    expect(html).toContain(">Add tags<");
    expect(html).toContain(">Due<");
  });

  // Mutation caught: dropping the dedupe (`withoutRepeats`).
  it("does not repeat the detail as an Impact row", () => {
    expect(html).not.toContain(">Impact<");
  });

  // Mutation caught: kind ignored, every value drawn as text.
  it("draws a tag as its pill, a status as its chip and a date formatted", () => {
    expect(html).toContain(categoricalAccent("Bug").chip);
    expect(html).toContain("9 Oct 2026");
    expect(visible(html)).not.toContain("2026-10-09");
    expect(html).toMatch(/Done/);
  });

  // Mutation caught: one "Task 1"/"Task 2" row each, or a title cut in the text.
  it("lists the tasks as one Tasks field of task pills, with each title whole", () => {
    expect(html).toContain(">Tasks<");
    expect(html).not.toContain(">Task 1<");
    expect(html).toContain("#28");
    expect(html).toContain(LONG);
    expect(html).not.toContain("«");
  });
});

describe("review round 1", () => {
  // Mutation caught: grouping every key with a digit (the Budget label was lost).
  it("keeps two custom fields apart, and groups only a word then a number", () => {
    const body = parseCardBody(
      "This change is recorded as yours.\nfield Budget: «10000»\nfield Q3 target: «2026-12-31»\nfield Phase 2: «yes»",
    );
    expect(body.fields.map((f) => f.label)).toEqual(["Budget", "Q3 target", "Phase 2"]);
    expect(body.fields.every((f) => f.values === undefined)).toBe(true);
    expect(runKey("task 12")).toBe("task");
    expect(runKey("source 2")).toBe("source");
    expect(runKey("field Q3 target")).toBe("");
  });

  // Mutation caught: drawing an unfenced card (an email) through FencedText.
  it("shows a card the server did not fence exactly as it will be sent", () => {
    const html = card({
      fenced: false,
      title: "Send this email?",
      detail: "To a@b.io · Subject: «Devis» final",
      context: "Il a dit « oui ».\nMerci",
    });
    expect(html).toContain("«Devis»");
    expect(html).toContain("Il a dit « oui ».");
    expect(html).not.toContain("data-fenced-name");
  });

  it("keeps an unfenced date as written", () => {
    const html = card({ fenced: false, title: "Log this?", context: "due: 2026-10-08\nstage: Won" });
    expect(html).toContain("2026-10-08");
  });
});
