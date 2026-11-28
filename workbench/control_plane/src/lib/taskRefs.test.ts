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
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { MarkdownBody } from "@/components/MarkdownMessage";
import { forView, keepLineBreaks } from "@/app/projects/lib/inlineUploads";
import { projectPillLabel } from "@/components/ProjectLinkPill";
import { projectIdOfHref } from "@/lib/entityIndex";
import {
  TaskPill,
  TaskRefPill,
  TaskRefText,
  createTaskRefResolver,
  findTaskRefs,
  refsUrl,
  splitTaskRefs,
  taskRefResolver,
  taskRefResolverFor,
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

  it("with no option, finds no word form: the card's and the gateway's rules (task 1595)", () => {
    expect(refs("See task 3596, Task #12 and (task 7)")).toEqual(["#12"]);
    expect(findTaskRefs("task 35")).toEqual([]);
    expect(findTaskRefs("task 35", { wordForm: false })).toEqual([]);
    expect(splitTaskRefs("do the task 3 times")).toEqual([{ kind: "text", text: "do the task 3 times" }]);
  });
});

describe("findTaskRefs with { wordForm: true }, the chat reply's finder (task 1595)", () => {
  const words = (text: string) =>
    findTaskRefs(text, { wordForm: true }).map((r) => text.slice(r.start, r.end));

  it("finds the number after the word task, and keeps the word out of it", () => {
    expect(words("See task 3596, Task #12 and (task 7)")).toEqual(["3596", "#12", "7"]);
    expect(findTaskRefs("task 35", { wordForm: true })).toEqual([{ start: 5, end: 7, number: 35 }]);
    expect(splitTaskRefs("(task 7)", { wordForm: true })).toEqual([
      { kind: "text", text: "(task " },
      { kind: "ref", ref: "7", number: 7 },
      { kind: "text", text: ")" },
    ]);
  });

  it("does not link a position, as the Projects tools write one", () => {
    // `task 5 of 7` and the card line `task 1: #7 «…»`. The `#7` still links.
    expect(words("stopped: task 5 of 7, «Step 5», was refused.")).toEqual([]);
    expect(words("task 5 of7")).toEqual([]);
    expect(words("task 1: #7 «Fix the extruder»")).toEqual(["#7"]);
    expect(words("task 5 of the plan")).toEqual(["5"]);
  });

  it("does not link a plural, a longer word, a hyphen before task, a glued word or a time", () => {
    expect(words("tasks 3, multitask 5, subtask 6, my-task 4, task12, task-12, task 10:30")).toEqual([]);
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

/** The task links in one rendered answer, by the number they carry. With
 *  `wordForm`, the answer is the chat reply's (`MessageBubble`, task 1595).
 *  Without it the prop is absent, so the renderer's own default holds. */
function linked(content: string, wordForm = false): string[] {
  const html = renderToStaticMarkup(
    createElement(MarkdownBody, {
      content, entityPills: true, fences: true, sessionId: SCOPE, ...(wordForm ? { wordForm } : {}),
    }),
  );
  return [...html.matchAll(/href="\/projects\?task=0+-0+-4000-8000-0*(\d+)"/g)].map((m) => `#${m[1]}`);
}

describe("bare references in Markdown", () => {
  it("links a bare #171 in a paragraph", () => {
    expect(linked("Both #171 and #87 are open.")).toEqual(["#171", "#87"]);
  });

  it("links the number after the word task in the chat reply, but not in code (task 1595)", () => {
    expect(linked("Both task 171 and (task 87) are open, not `task 12`.", true)).toEqual(["#171", "#87"]);
  });

  it("does not link the word form with no `wordForm`: a generative-UI card's text (task 1595)", () => {
    // `GenUiText` and `GenerativeUINode` pass `entityPills` and `fences`, and
    // no `wordForm`. Only the chat reply links `task 171`.
    expect(linked("Both task 171 and (task 87) are open, and #88.")).toEqual(["#88"]);
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

// ── WS-57 TR-5: one grammar in two languages ────────────────────────────────

type SharedCase = { name: string; text: string; numbers: number[] };

// …/src/lib → …/src → control_plane → workbench → repo
const SHARED: { cases: SharedCase[]; markdown_cases: SharedCase[] } = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../../tests/fixtures/task_ref_cases.json", import.meta.url)),
    "utf-8",
  ),
);

describe("the shared case file (tests/fixtures/task_ref_cases.json)", () => {
  const CASES = SHARED.cases;

  it("is present and not trivial", () => {
    // A file that quietly emptied would leave this block and the pytest half
    // both green while they checked nothing.
    expect(CASES.length).toBeGreaterThanOrEqual(30);
    expect(CASES.some((c) => c.numbers.length > 0)).toBe(true);
    expect(CASES.some((c) => c.numbers.length === 0)).toBe(true);
  });

  for (const c of CASES) {
    it(`agrees with the gateway: ${c.name}`, () => {
      expect(findTaskRefs(c.text).map((r) => r.number)).toEqual(c.numbers);
    });
  }

  it("the chat reply's finder reads each `#N` of these cases as the gateway does (task 1595)", () => {
    for (const c of CASES) {
      const hashes = findTaskRefs(c.text, { wordForm: true }).filter((r) => c.text[r.start] === "#");
      expect(hashes.map((r) => r.number), c.name).toEqual(c.numbers);
    }
  });
});

// ── Task 1595: the word form, the chat reply's own rule ─────────────────────

type WordFormMarkdownCase = SharedCase & { default_numbers: number[] };

// The gateway has no copy of rule 8 (owner answer 7dbf65d9, 2026-11-22), so
// these rows live beside this test and pytest never reads them.
const WORD_FORM: { cases: SharedCase[]; markdown_cases: WordFormMarkdownCase[] } = JSON.parse(
  readFileSync(fileURLToPath(new URL("./taskRefWordForm.cases.json", import.meta.url)), "utf-8"),
);

describe("the word-form case file (src/lib/taskRefWordForm.cases.json)", () => {
  it("is present and not trivial", () => {
    expect(WORD_FORM.cases.length).toBeGreaterThanOrEqual(20);
    expect(WORD_FORM.cases.some((c) => c.numbers.length > 0)).toBe(true);
    expect(WORD_FORM.cases.some((c) => c.numbers.length === 0)).toBe(true);
    expect(WORD_FORM.markdown_cases.length).toBeGreaterThanOrEqual(10);
  });

  for (const c of WORD_FORM.cases) {
    it(`with wordForm: ${c.name}`, () => {
      expect(findTaskRefs(c.text, { wordForm: true }).map((r) => r.number)).toEqual(c.numbers);
    });
    it(`with no option, only the \`#N\`s: ${c.name}`, () => {
      const hashes = findTaskRefs(c.text, { wordForm: true }).filter((r) => c.text[r.start] === "#");
      expect(findTaskRefs(c.text)).toEqual(hashes);
    });
  }

  // The chat reply's own pipeline: `MarkdownBody` with `fences` and
  // `taskRefs`, as `MessageBubble` draws it. Every number from 1 to 100 is
  // one task, so each pill is a number the Markdown layer let through.
  const every = createTaskRefResolver(async (ns) =>
    ns.map((n) => ({ id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`, task_number: n, title: `T${n}` })),
  );
  beforeAll(async () => {
    for (let n = 1; n <= 100; n += 1) every.request("", n);
    await every.flush();
  });
  // With no `wordForm` the prop is absent, so the renderer's default holds.
  const pills = (text: string, wordForm: boolean) => {
    const html = renderToStaticMarkup(
      createElement(MarkdownBody, {
        content: text, fences: true, taskRefs: true, taskRefResolver: every, ...(wordForm ? { wordForm } : {}),
      }),
    );
    return [...html.matchAll(/href="\/projects\?task=0+-0+-4000-8000-0*(\d+)"/g)].map((m) => Number(m[1]));
  };

  for (const c of WORD_FORM.markdown_cases) {
    it(`in the chat reply's Markdown: ${c.name}`, () => {
      expect(pills(c.text, true)).toEqual(c.numbers);
      expect(pills(c.text, false)).toEqual(c.default_numbers);
    });
  }

  it("the task card's Markdown never links the word form", () => {
    // The card's mode is `taskRefs` alone. It reads no `wordForm`.
    for (const c of WORD_FORM.markdown_cases) {
      const html = renderToStaticMarkup(
        createElement(MarkdownBody, {
          content: keepLineBreaks(forView(c.text)), plainEmails: true, taskRefs: true, wordForm: true,
          taskRefResolver: every, projectPills: true,
        }),
      );
      const found = [...html.matchAll(/href="\/projects\?task=0+-0+-4000-8000-0*(\d+)"/g)].map((m) => Number(m[1]));
      expect(found, c.name).toEqual(c.default_numbers);
    }
  });
});

// ── Task 1595: only the chat reply turns the word form on ───────────────────

describe("the source fence: only the chat reply passes `wordForm`", () => {
  const SRC = fileURLToPath(new URL("..", import.meta.url));
  const files = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? files(join(dir, e.name)) : [join(dir, e.name)],
    );
  const sources = files(SRC)
    .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f))
    .map((f) => ({ path: relative(SRC, f).replace(/\\/g, "/"), text: readFileSync(f, "utf-8") }));

  it("names the option in the finder, the plugin, the renderer and the chat reply only", () => {
    const naming = sources.filter((s) => /\bwordForm\b/.test(s.text)).map((s) => s.path).sort();
    expect(naming).toEqual([
      "components/MarkdownMessage.tsx",
      "components/MessageBubble.tsx",
      "lib/remarkEntityPills.ts",
      "lib/taskRefs.ts",
    ]);
  });

  it("sets it as a prop only in MessageBubble, on the reply's two renders", () => {
    // A JSX attribute: `wordForm` alone on its line, or `wordForm={…}`.
    // `MarkdownMessage` only hands its own prop on to `MarkdownBody`.
    const setting = sources.flatMap((s) =>
      [...s.text.matchAll(/^\s*(wordForm(?:=\{[^}]*\})?)\s*$/gm)].map((m) => `${s.path}: ${m[1]}`),
    );
    expect(setting.sort()).toEqual([
      "components/MarkdownMessage.tsx: wordForm={wordForm}",
      "components/MessageBubble.tsx: wordForm",
      "components/MessageBubble.tsx: wordForm",
    ]);
  });

  it("the card's callers pass no option to the finder", () => {
    const header = sources.find((s) => s.path === "components/TaskPanelHeader.tsx")!.text;
    expect(header).toMatch(/findTaskRefs\(value\)/);
    expect(header).not.toMatch(/wordForm/);
    const finder = sources.find((s) => s.path === "lib/taskRefs.ts")!.text;
    // `TaskRefText` splits with the defaults.
    expect(finder).toMatch(/\.\.\.splitTaskRefs\(text\)\.map/);
  });
});

describe("the shared Markdown cases, through the card's own pipeline", () => {
  // Every number from 1 to 100 is one task, so each pill drawn is a number
  // the Markdown layer let through. The card draws a description through
  // `keepLineBreaks(forView(…))` and `MarkdownBody` with `taskRefs`, and so
  // does this. The gateway reads the same cases through `prose_of`.
  const every = createTaskRefResolver(async (ns) =>
    ns.map((n) => ({ id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`, task_number: n, title: `T${n}` })),
  );
  beforeAll(async () => {
    for (let n = 1; n <= 100; n += 1) every.request("", n);
    await every.flush();
  });
  const pills = (text: string) => {
    const html = renderToStaticMarkup(
      createElement(MarkdownBody, {
        content: keepLineBreaks(forView(text)), plainEmails: true, taskRefs: true,
        taskRefResolver: every, projectPills: true,
      }),
    );
    return [...html.matchAll(/href="\/projects\?task=0+-0+-4000-8000-0*(\d+)"/g)].map((m) => Number(m[1]));
  };

  it("is present and not trivial", () => {
    expect(SHARED.markdown_cases.length).toBeGreaterThanOrEqual(30);
  });

  for (const c of SHARED.markdown_cases) {
    it(`agrees with the gateway: ${c.name}`, () => {
      expect(pills(c.text)).toEqual(c.numbers);
    });
  }
});

// ── WS-57 TR-5: the task card's store and its plain-text draw ───────────────

describe("the root store", () => {
  it("asks the refs read with the root, and the app's store asks without", () => {
    expect(refsUrl([12, 13])).toBe("/api/projects/search/refs?numbers=12,13");
    expect(refsUrl([12], "aaaaaaaa-0000-4000-8000-000000000001")).toBe(
      "/api/projects/search/refs?numbers=12&root=aaaaaaaa-0000-4000-8000-000000000001",
    );
  });

  it("is one store per root, and the app's store when there is no root", () => {
    const root = "aaaaaaaa-0000-4000-8000-000000000001";
    expect(taskRefResolverFor(root)).toBe(taskRefResolverFor(root));
    expect(taskRefResolverFor(root)).not.toBe(taskRefResolver);
    expect(taskRefResolverFor(null)).toBe(taskRefResolver);
    expect(taskRefResolverFor("")).toBe(taskRefResolver);
  });

  it("sends the root on the wire", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify({ rows: [] }), { status: 200 });
    });
    try {
      const r = taskRefResolverFor("bbbbbbbb-0000-4000-8000-000000000002");
      r.request("", 7);
      await r.flush();
    } finally {
      vi.unstubAllGlobals();
    }
    expect(urls).toEqual(["/api/projects/search/refs?numbers=7&root=bbbbbbbb-0000-4000-8000-000000000002"]);
  });
});

describe("TaskRefText", () => {
  it("draws each #n of plain text as a pill, and keeps every other word", async () => {
    const r = createTaskRefResolver(async () => [
      { id: "00000000-0000-4000-8000-000000000012", task_number: 12, title: "Twelve" },
    ]);
    r.request("", 12);
    r.request("", 9999);
    await r.flush();
    const out = renderToStaticMarkup(
      createElement(TaskRefText, { text: "Fix #12 «x» and #9999, `#12`", resolver: r }),
    );
    expect(out.match(/href="\/projects\?task=/g)).toHaveLength(2);
    expect(out).toContain("Twelve");
    expect(out).toContain("«x» and #9999, `");
  });

  it("keeps `task 3` as words, even when a task has the number (task 1595)", async () => {
    // The card's title draws with the default rules: the word form is the
    // chat reply's only, because `task 3` in a title is often a count.
    const r = createTaskRefResolver(async () => [
      { id: "00000000-0000-4000-8000-000000000003", task_number: 3, title: "Three" },
      { id: "00000000-0000-4000-8000-000000000012", task_number: 12, title: "Twelve" },
    ]);
    r.request("", 3);
    r.request("", 12);
    await r.flush();
    const out = renderToStaticMarkup(
      createElement(TaskRefText, { text: "Do the task 3 times, then (task 3) and #12", resolver: r }),
    );
    expect(out.match(/href="\/projects\?task=/g)).toHaveLength(1);
    expect(out).toContain('href="/projects?task=00000000-0000-4000-8000-000000000012"');
    expect(out).not.toContain("Three");
    expect(out).toContain("Do the task 3 times, then (task 3) and ");
  });
});

// ── WS-57 TR-5: the task card's Markdown ────────────────────────────────────

describe("MarkdownBody with taskRefs alone (the task card)", () => {
  const card = async (content: string) => {
    const r = createTaskRefResolver(async (numbers) =>
      numbers.filter((n) => n !== 9999).map((n) => ({
        id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
        task_number: n,
        title: `Card task ${n}`,
      })),
    );
    for (const n of [12, 13, 9999]) r.request("", n);
    await r.flush();
    return renderToStaticMarkup(
      createElement(MarkdownBody, { content, plainEmails: true, taskRefs: true, taskRefResolver: r, projectPills: true }),
    );
  };

  it("draws #12 as a pill with the task's title, and #9999 as text", async () => {
    const out = await card("Blocked on #12. Not #9999.");
    expect(out).toContain('href="/projects?task=00000000-0000-4000-8000-000000000012"');
    expect(out).toContain("Card task 12");
    expect(out).toContain("Not #9999.");
  });

  it("changes no other word: marks, an email, a bullet and bold stay as written", async () => {
    const out = await card("«keep» asha@example.com today.**Bold**\n\n• one");
    expect(out).toContain("«keep»");
    expect(out).toContain("asha@example.com");
    expect(out).not.toContain("mailto:");
    expect(out).toContain("today.<strong");
    expect(out).toContain("• one");
    expect(out).not.toContain("data-entity-pill");
  });

  it("draws no pill in code or in a link's words", async () => {
    const out = await card("`#12` and [see #13](https://example.com/x)\n\n```\n#12\n```");
    expect(out).not.toContain("/projects?task=");
  });

  it("draws a link to a project as the project pill, which opens it", async () => {
    const id = "5b0c7a52-3f4e-4d2a-9c1e-0a1b2c3d4e5f";
    const out = await card(`The board: [Ops board](/projects?project=${id}).`);
    expect(out).toContain(`href="/projects?project=${id}"`);
    expect(out).toContain("lucide-folder-kanban");
    expect(out).toContain("Ops board");
  });

  it("leaves a project link alone where projectPills is off", () => {
    const id = "5b0c7a52-3f4e-4d2a-9c1e-0a1b2c3d4e5f";
    const out = renderToStaticMarkup(
      createElement(MarkdownBody, { content: `[Ops](/projects?project=${id})`, plainEmails: true }),
    );
    expect(out).not.toContain("lucide-folder-kanban");
  });
});

describe("projectIdOfHref", () => {
  const id = "5b0c7a52-3f4e-4d2a-9c1e-0a1b2c3d4e5f";
  it("reads the in-app path and this origin's full URL, and nothing else", () => {
    expect(projectIdOfHref(`/projects?project=${id}`, "https://app.metorite.com")).toBe(id);
    expect(projectIdOfHref(`https://app.metorite.com/projects?project=${id}`, "https://app.metorite.com")).toBe(id);
    expect(projectIdOfHref(`https://evil.example/projects?project=${id}`, "https://app.metorite.com")).toBeNull();
    expect(projectIdOfHref(`//evil.example/projects?project=${id}`, "https://app.metorite.com")).toBeNull();
    expect(projectIdOfHref("/projects?project=not-a-uuid", "https://app.metorite.com")).toBeNull();
    expect(projectIdOfHref(`/projects/other?project=${id}`, "https://app.metorite.com")).toBeNull();
    expect(projectIdOfHref(undefined)).toBeNull();
  });

  it("names the pill by the project, else by the link's own words", () => {
    expect(projectPillLabel("Ops", "anything")).toBe("Ops");
    expect(projectPillLabel(null, "Ops board")).toBe("Ops board");
    expect(projectPillLabel(undefined, `https://app.metorite.com/projects?project=${id}`)).toBe("Project");
    expect(projectPillLabel(undefined, "")).toBe("Project");
  });
});
