/**
 * `lib/taskRefs.ts` parts (a) and (c): the parser and the pill (owner report,
 * 2026-10-13). Part (b), the lookup, is `taskRefResolver.test.ts`.
 *
 * The parser in two halves. `findTaskRefs` holds the rules on a plain string. The second
 * half renders real Markdown through `MarkdownBody`, with a lookup that says
 * EVERY number is a task, so each chip in the output is a reference the parser
 * let through. That is how code, a heading and a URL are proven: those never
 * reach the parser as text, and only the real Markdown tree shows it.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { MarkdownBody } from "@/components/MarkdownMessage";
import {
  TaskPill,
  TaskRefPill,
  createTaskRefResolver,
  findTaskRefs,
  splitTaskRefs,
  taskRefResolver,
} from "@/lib/taskRefs";

const refs = (text: string) => findTaskRefs(text).map((r) => text.slice(r.start, r.end));

describe("findTaskRefs", () => {
  it("finds a bare #171", () => {
    expect(refs("See #171 for the rest")).toEqual(["#171"]);
    expect(findTaskRefs("See #171")).toEqual([{ start: 4, end: 8, number: 171 }]);
  });

  it("finds several references in one line", () => {
    expect(refs("#171 and #87, then #5 (and #6)")).toEqual(["#171", "#87", "#5", "#6"]);
  });

  it("finds a reference at the start and at the end of a line", () => {
    expect(refs("#12 is first")).toEqual(["#12"]);
    expect(refs("the last is #34")).toEqual(["#34"]);
    expect(refs("#7")).toEqual(["#7"]);
  });

  it("keeps the punctuation after a reference out of it", () => {
    expect(refs("#87, then #88. Then #89; #90: #91! #92? (#93)")).toEqual([
      "#87", "#88", "#89", "#90", "#91", "#92", "#93",
    ]);
    const parts = splitTaskRefs("see #87, then");
    expect(parts).toEqual([
      { kind: "text", text: "see " },
      { kind: "ref", ref: "#87", number: 87 },
      { kind: "text", text: ", then" },
    ]);
  });

  it("does not link a colour", () => {
    expect(refs("color: #fff; and #a1b2c3 and #1a2b3c")).toEqual([]);
    // Digits only, but a colour's length: 6 or 8.
    expect(refs("background #112233 and #11223344")).toEqual([]);
  });

  it("does not link a hashtag word or a channel", () => {
    expect(refs("#launch in #general and #dev-team")).toEqual([]);
  });

  it("does not link a number glued to a letter or a digit", () => {
    expect(refs("abc#12 and 9#12 and x_#12")).toEqual([]);
    expect(refs("#12a and #12_b")).toEqual([]);
  });

  it("does not link a URL fragment or an HTML entity", () => {
    expect(refs("https://example.com/page#12")).toEqual([]);
    expect(refs("https://example.com/#12")).toEqual([]);
    expect(refs("go to example.com/#12 now")).toEqual([]);
    expect(refs("www.example.com?x=#12")).toEqual([]);
    expect(refs("&#12; and ##12")).toEqual([]);
  });

  it("does not link an HTML entity", () => {
    expect(refs("a &#123; b &#8212; c")).toEqual([]);
    expect(refs("a &#123; and #124")).toEqual(["#124"]);
  });

  it("does not link another kind of number, or a rank (review round 1)", () => {
    expect(refs("PR #120, Order #45, Sprint #3, invoice #4521, v#2")).toEqual([]);
    expect(refs("Your #1 priority is the launch, and #2 spot goes to QA")).toEqual([]);
    // A task named by a word that can mean a task still reaches the lookup.
    expect(refs("Task #12, issue #13 and bug #14 are open")).toEqual(["#12", "#13", "#14"]);
  });

  it("does not link a number that cannot be a task number", () => {
    expect(refs("#0 and #012 and #1.5")).toEqual([]);
  });
});

// ── In real Markdown ─────────────────────────────────────────────────────────

const SCOPE = "taskRefs.test";

/** Every number from 1 to 200 is a task, titled by its number. */
beforeAll(async () => {
  vi.stubGlobal("fetch", async (url: string) => {
    const numbers = new URL(url, "http://x").searchParams.get("numbers")!.split(",").map(Number);
    return new Response(
      JSON.stringify({
        rows: numbers.map((n) => ({
          id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
          task_number: n,
          title: `Task ${n}`,
          status_name: "To do",
          category: "todo",
          archived: false,
        })),
      }),
      { status: 200 },
    );
  });
  for (let n = 1; n <= 200; n += 1) taskRefResolver.request(SCOPE, n);
  await taskRefResolver.flush();
  vi.unstubAllGlobals();
});

/** The task links in one rendered answer, by the number they carry. */
function linked(content: string): string[] {
  const html = renderToStaticMarkup(
    createElement(MarkdownBody, { content, entityPills: true, fences: true, sessionId: SCOPE }),
  );
  return [...html.matchAll(/href="\/projects\?task=0+-0+-4000-8000-0*(\d+)"/g)].map((m) => `#${m[1]}`);
}

describe("bare references in Markdown", () => {
  it("links a bare #171 in a paragraph", () => {
    expect(linked("Both #171 and #87 are open.")).toEqual(["#171", "#87"]);
  });

  it("does not link inline code", () => {
    expect(linked("Run `#12` then #13")).toEqual(["#13"]);
  });

  it("does not link a fenced code block", () => {
    expect(linked("```\nfix #12 here\n```\n\nand #14")).toEqual(["#14"]);
  });

  it("does not link a heading marker", () => {
    expect(linked("# Heading\n\n## 12 steps\n\nsee #15")).toEqual(["#15"]);
  });

  it("does not link a URL with #12", () => {
    expect(linked("Read https://example.com/page#12 and <https://example.com/#13>, then #16")).toEqual([
      "#16",
    ]);
  });

  it("does not link a colour, a hashtag or a glued number", () => {
    expect(linked("Use #fff, #a1b2c3, #112233, #launch, abc#12 and #12a, not #17")).toEqual(["#17"]);
  });

  it("links a reference at the start and at the end, and keeps its punctuation outside", () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownBody, { content: "#18 first, last #19.", entityPills: true, fences: true, sessionId: SCOPE }),
    );
    expect(linked("#18 first, last #19.")).toEqual(["#18", "#19"]);
    // The full stop sits right after the pill, in its no-wrap group.
    expect(html).toMatch(/<\/a>\.<\/span><\/p>$/);
  });
});

// ── (c) The pill ─────────────────────────────────────────────────────────────

describe("TaskPill and TaskRefPill", () => {
  const task = { id: "00000000-0000-4000-8000-000000000171", number: 171, title: "Composer always steers", status: "To do", category: "todo" };
  const html = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(el);

  it("draws a known task through EntityPill, with the ?task= link", () => {
    const out = html(createElement(TaskPill, { task }));
    expect(out).toContain(`href="/projects?task=${task.id}"`);
    expect(out).toContain("lucide-list-checks");
    expect(out).toMatch(/#171<\/span><span[^>]*>Composer always steers<\/span>/);
  });

  it("draws the plain #n, or the fallback, until a single task is known", async () => {
    const none = createTaskRefResolver(async () => []);
    expect(html(createElement(TaskRefPill, { number: 9, resolver: none }))).toBe("#9");
    none.request("", 9);
    await none.flush();
    expect(html(createElement(TaskRefPill, { number: 9, resolver: none, fallback: "nine" }))).toBe("nine");
  });

  it("draws the pill once the lookup knows the task, and matches a written title", async () => {
    const r = createTaskRefResolver(async () => [{ id: task.id, task_number: 171, title: task.title }]);
    r.request("", 171);
    await r.flush();
    expect(html(createElement(TaskRefPill, { number: 171, resolver: r }))).toContain(`/projects?task=${task.id}`);
    expect(html(createElement(TaskRefPill, { number: 171, title: "Other", resolver: r }))).toBe("#171");
    // Not live (a streaming answer): text, even with the answer cached.
    expect(html(createElement(TaskRefPill, { number: 171, live: false, resolver: r }))).toBe("#171");
  });
});
