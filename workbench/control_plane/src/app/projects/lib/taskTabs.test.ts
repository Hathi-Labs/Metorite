/**
 * ORC-3 (D181, `specs/one_record_card.md` §5 and §6) — the task card's tabs.
 *
 * Mutations caught, by test:
 * - a section moves to the wrong tab, or drops out of every tab: "each
 *   section of today is on one tab";
 * - a tab other than Overview opens first, or two tabs show at once: "the
 *   card opens on Overview";
 * - the agent run, an open decision or the Add row moves under the bar:
 *   "nothing that waits on the member hides behind a tab";
 * - a card draws an underline `Tabs` of its own, or the CRM shell keeps its
 *   own bar: "one tab bar";
 * - Overview writes `?ttab=overview`, or a tab change drops the other keys
 *   of the address: the URL rules.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/projects",
  useSearchParams: () => new URLSearchParams(),
}));

import { TaskBody } from "../components/TaskBody";
import type { TaskRow } from "./api";
import {
  TASK_TABS,
  TASK_TAB_LABEL,
  readTaskTab,
  searchWithTab,
  tabCount,
} from "./taskTabs";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "..");
const read = (rel: string) => readFileSync(join(src, rel), "utf-8");
/** The source without comments, so a note that names a tag is not a tag. */
const code = (rel: string) =>
  read(rel)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");

const TASK = {
  id: "t1",
  title: "Ship the tabs",
  description: "",
  status_id: "s1",
  project_id: "p1",
  root_project_id: "p1",
  assignees: [],
  tags: [],
} as unknown as TaskRow;

/** The drawn card, cut into its four tab panels by their names. */
function panels(): Record<string, { html: string; hidden: boolean }> {
  const html = renderToStaticMarkup(
    createElement(TaskBody, {
      task: TASK,
      statuses: [{ id: "s1", name: "To do", category: "todo" }] as never,
      onChanged: () => {},
      onOpenTask: () => {},
    }),
  );
  const starts = TASK_TABS.map((t) => {
    const at = html.indexOf(`role="tabpanel" aria-label="${TASK_TAB_LABEL[t]}"`);
    expect(at, `the ${t} panel`).toBeGreaterThan(0);
    return at;
  });
  const out: Record<string, { html: string; hidden: boolean }> = {};
  TASK_TABS.forEach((t, i) => {
    const part = html.slice(starts[i], starts[i + 1] ?? html.length);
    const open = part.slice(0, part.indexOf(">"));
    out[t] = { html: part, hidden: /\bhidden=""/.test(open) };
  });
  return out;
}

describe("the task card's tabs (ORC-3)", () => {
  it("each section of today is on one tab", () => {
    const p = panels();
    const home: Record<string, string> = {
      // A section's heading draws its label in a span.
      "<span>Details</span>": "overview",
      "<span>Description</span>": "overview",
      "<span>Subtasks &amp; related</span>": "related",
      'aria-label="Add a subtask"': "related",
      "<span>Links</span>": "related",
      "Attach files": "files",
      "Nothing attached.": "files",
      "No comments yet.": "discussion",
      ">Activity": "discussion",
    };
    for (const [needle, tab] of Object.entries(home)) {
      for (const t of TASK_TABS) {
        if (t === tab) expect(p[t].html, `${needle} on ${t}`).toContain(needle);
        else expect(p[t].html, `${needle} not on ${t}`).not.toContain(needle);
      }
    }
  });

  it("the card opens on Overview, and one tab shows at a time", () => {
    const p = panels();
    expect(TASK_TABS.filter((t) => !p[t].hidden)).toEqual(["overview"]);
  });

  it("the bar holds the four words, in order", () => {
    expect(TASK_TABS.map((t) => TASK_TAB_LABEL[t])).toEqual([
      "Overview",
      "Related",
      "Files",
      "Discussion",
    ]);
  });

  it("nothing that waits on the member hides behind a tab", () => {
    const body = code("app/projects/components/TaskBody.tsx");
    const bar = body.indexOf("<RecordTabBar");
    const firstPanel = body.indexOf('role="tabpanel"');
    expect(bar).toBeGreaterThan(0);
    expect(firstPanel).toBeGreaterThan(bar);
    for (const tag of ["<AgentRunPanel", "<DecisionPanel", 'aria-label="Add to this task"']) {
      const at = body.indexOf(tag);
      expect(at, tag).toBeGreaterThan(0);
      expect(at, `${tag} before the bar`).toBeLessThan(bar);
      expect(body.lastIndexOf(tag), `${tag} once`).toBe(at);
    }
  });
});

describe("one tab bar (ORC-3)", () => {
  it("both cards draw the shared bar", () => {
    expect(code("app/projects/components/TaskBody.tsx")).toContain(
      'import RecordTabBar from "@/components/RecordTabBar"',
    );
    const shell = code("app/crm/components/RecordShell.tsx");
    expect(shell).toContain('from "@/components/RecordTabBar"');
    expect(shell).toContain("<SharedRecordTabBar");
  });

  it("neither card draws an underline bar of its own", () => {
    for (const rel of ["app/projects/components/TaskBody.tsx", "app/crm/components/RecordShell.tsx"]) {
      expect(code(rel), rel).not.toMatch(/variant="underline"/);
    }
    expect(code("app/crm/components/RecordShell.tsx")).not.toContain('from "@/components/Tabs"');
  });
});

describe("the tab in the address (?ttab=)", () => {
  it("reads a tab, and opens Overview for anything else", () => {
    expect(readTaskTab("?task=1&ttab=files")).toBe("files");
    expect(readTaskTab("?ttab=discussion")).toBe("discussion");
    expect(readTaskTab("?ttab=timeline")).toBe("overview");
    expect(readTaskTab("")).toBe("overview");
  });

  it("writes a tab and keeps the other keys", () => {
    expect(searchWithTab("?app=my-tasks&chip=next", "related")).toBe(
      "?app=my-tasks&chip=next&ttab=related",
    );
    expect(searchWithTab("?ttab=files", "discussion")).toBe("?ttab=discussion");
  });

  it("Overview takes the key out", () => {
    expect(searchWithTab("?app=my-tasks&ttab=files", "overview")).toBe("?app=my-tasks");
    expect(searchWithTab("?ttab=files", "overview")).toBe("");
  });

  it("a tab with content shows its count, and an empty tab shows none", () => {
    expect(tabCount(3)).toBe(3);
    expect(tabCount(0)).toBeUndefined();
  });
});
