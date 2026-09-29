/**
 * WS-27bn R5b-2 — the entry points (`projects_reports.md` §8 R5b,
 * done-when (k) and (l)).
 *
 * (k) Each control's link equals `reportLink` with the expected template,
 *     subject and node, and the builder's own parser opens it filled in.
 * (l) "1:1 prep" is absent when the subjects answer omits the person, or
 *     when the read fails.
 *
 * The vitest environment has no DOM. So the decisions are pure functions in
 * `reportEntry.ts`, and the last block reads each host's source to prove it
 * renders the control through them, and never as a disabled button.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { NO_ACCESS } from "@/lib/access";

import type { ProjectRow, ReportSubjects, ReportTemplate } from "./api";
import {
  builderStateFromLink,
  builderSubject,
  parseReportLink,
  reportLink,
} from "./reportBuilder";
import {
  MY_DAY,
  MY_DAY_TEMPLATE,
  ONE_ON_ONE_PREP,
  ONE_ON_ONE_TEMPLATE,
  REPORT_ON_THIS,
  myDayLink,
  myDayShown,
  nodeReportLink,
  oneOnOneLink,
} from "./reportEntry";

const T2: ReportTemplate = {
  key: "my_day",
  name: "My day",
  question: "What do I work on today, and what waits on me?",
  scope_kinds: ["person"],
  available: true,
  sections: ["pulse"],
  weeks: 1,
  skip_current_week: false,
  requires_subject: "self",
};

const T6: ReportTemplate = {
  key: "one_on_one",
  name: "1:1 prep",
  question: "How is this person doing over a month?",
  scope_kinds: ["person"],
  available: true,
  sections: ["finished", "throughput", "pulse"],
  weeks: 4,
  skip_current_week: true,
  requires_subject: "person",
};

const CATALOGUE = [T2, T6];

const NODE = "0b6f3c1e-5d2a-4b7c-9e8f-1a2b3c4d5e6f";
const ROOTS = [
  { id: NODE, name: "Hardware", children: [] } as unknown as ProjectRow,
];

const LEAD: ReportSubjects = {
  everyone: false,
  me: "lee@example.test",
  people: [
    { email: "ana@example.test", name: "Ana Shah" },
    { email: "lee@example.test", name: "Lee Park" },
  ],
  teams: [{ slug: "hardware", name: "Hardware" }],
};

const parse = (href: string) =>
  parseReportLink(new URL(href, "https://x.test").searchParams, CATALOGUE, ROOTS);

describe("(k) each control's link is reportLink with the expected parts", () => {
  it("'Report on this' names the node as the scope, and nothing else", () => {
    const href = nodeReportLink(NODE);
    expect(href).toBe(reportLink({ node: NODE }));
    expect(href).not.toMatch(/[?&]project=/);
    const intent = parse(href);
    expect(intent).toEqual({ template: null, subject: null, node: NODE });
    expect(builderStateFromLink(intent!).projectId).toBe(NODE);
  });

  it("'1:1 prep' names T6 and the person as the subject", () => {
    const href = oneOnOneLink(LEAD, false, "ana@example.test");
    expect(href).toBe(
      reportLink({
        template: "one_on_one",
        subject: { kind: "person", email: "ana@example.test" },
      })
    );
    const intent = parse(href!);
    expect(intent?.template).toBe(T6);
    expect(intent?.subject).toEqual({ kind: "person", email: "ana@example.test" });
    const state = builderStateFromLink(intent!);
    expect(state.template).toBe("one_on_one");
    expect(state.subject).toEqual({ kind: "person", email: "ana@example.test" });
  });

  it("'1:1 prep' matches the address without regard to case", () => {
    expect(oneOnOneLink(LEAD, false, "  Ana@Example.test ")).toBe(
      oneOnOneLink(LEAD, false, "ana@example.test")
    );
  });

  it("'My day' names T2, and the builder makes the reader its subject", () => {
    const href = myDayLink();
    expect(href).toBe(reportLink({ template: "my_day" }));
    const intent = parse(href);
    expect(intent?.template).toBe(T2);
    // The link names no subject. The builder derives the reader for a new
    // report, as it does for T2 from the gallery.
    const draft = builderStateFromLink(intent!);
    const state = builderSubject(
      draft,
      "self",
      { kind: "person", email: LEAD.me },
      false
    );
    expect(state.subject).toEqual({ kind: "person", email: "lee@example.test" });
  });

  it("the template keys are the server's", () => {
    const reports = readFileSync(
      fileURLToPath(
        new URL(
          "../../../../../../apps/services/gateway/gateway/routes/projects/reports.py",
          import.meta.url
        )
      ),
      "utf8"
    );
    expect(MY_DAY_TEMPLATE).toBe("my_day");
    expect(ONE_ON_ONE_TEMPLATE).toBe("one_on_one");
    expect(reports).toContain(`"key": "${MY_DAY_TEMPLATE}"`);
    expect(reports).toContain(`"key": "${ONE_ON_ONE_TEMPLATE}"`);
  });
});

describe("(l) '1:1 prep' is absent unless the subjects answer lists the person", () => {
  it("is absent when the answer omits the person", () => {
    expect(oneOnOneLink(LEAD, false, "zoe@example.test")).toBeNull();
    expect(oneOnOneLink({ ...LEAD, people: [] }, false, "ana@example.test")).toBeNull();
  });

  it("is absent when the read fails, even over an earlier answer", () => {
    expect(oneOnOneLink(LEAD, true, "ana@example.test")).toBeNull();
    expect(oneOnOneLink(undefined, true, "ana@example.test")).toBeNull();
  });

  it("is absent before the answer arrives, and for a person with no address", () => {
    expect(oneOnOneLink(undefined, false, "ana@example.test")).toBeNull();
    expect(oneOnOneLink(null, false, "ana@example.test")).toBeNull();
    expect(oneOnOneLink(LEAD, false, null)).toBeNull();
    expect(oneOnOneLink(LEAD, false, "  ")).toBeNull();
  });

  it("(17) is absent on the reader's own page, because My day covers it", () => {
    expect(oneOnOneLink(LEAD, false, "lee@example.test")).toBeNull();
    expect(oneOnOneLink(LEAD, false, " Lee@Example.test ")).toBeNull();
    expect(oneOnOneLink(LEAD, false, "ana@example.test")).not.toBeNull();
  });
});

describe("the hosts render each control through reportEntry", () => {
  const source = (rel: string) =>
    readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");
  const panel = source("../../people/components/PersonPanel.tsx");
  const tasks = source("../../tasks/page.tsx");
  const dashboard = source("../components/NodeDashboard.tsx");
  const page = source("../page.tsx");

  it("uses the labels of record", () => {
    expect(REPORT_ON_THIS).toBe("Report on this");
    expect(ONE_ON_ONE_PREP).toBe("1:1 prep");
    expect(MY_DAY).toBe("My day");
  });

  it("PersonPanel shows '1:1 prep' only through oneOnOneLink, never disabled", () => {
    expect(panel).toContain('projectsKey("reports/subjects")');
    expect(panel).toMatch(/oneOnOneLink\(\s*subjects\.data,\s*subjects\.error !== null,/);
    const control = panel.slice(
      panel.indexOf("{prepLink ? ("),
      panel.indexOf("{ONE_ON_ONE_PREP}")
    );
    expect(control.length).toBeGreaterThan(0);
    expect(control).not.toMatch(/disabled/);
  });

  it("My Tasks draws 'My day' in the desktop bar and the phone bar", () => {
    expect(tasks).toContain("router.push(myDayLink())");
    expect(tasks.match(/\{myDay\}/g)?.length).toBe(2);
  });

  it("'My day' shows only to a member who can open Projects, and is absent otherwise", () => {
    const member = { ...NO_ACCESS, authenticated: true, is_active: true };
    expect(myDayShown({ ...member, features: ["tasks", "projects"] }, false)).toBe(true);
    expect(myDayShown({ ...member, features: ["tasks"] }, false)).toBe(false);
    // While access resolves, the control waits, as the nav does.
    expect(myDayShown({ ...member, features: ["tasks", "projects"] }, true)).toBe(false);
    // The page reads the one access check, and never draws a dead control.
    expect(tasks).toMatch(/myDayShown\(access, accessLoading\) \?/);
    const control = tasks.slice(tasks.indexOf("const myDay ="), tasks.indexOf("{MY_DAY}"));
    expect(control).not.toMatch(/disabled/);
  });

  it("NodeDashboard draws 'Report on this', and both page renders pass onReport", () => {
    expect(dashboard).toContain("onReport(nodeReportLink(summary.id))");
    expect(page.match(/onReport=\{\(href\) => router\.push\(href\)\}/g)?.length).toBe(2);
  });

  it("(18) the chat's report card links through reportLink", () => {
    const genui = source("../../../components/genUITemplates.tsx");
    expect(genui).toMatch(/reportLink\(\)/);
    expect(genui).toMatch(/import \{[^}]*\breportLink\b[^}]*\} from "@\/app\/projects\/lib\/reportBuilder"/);
    expect(genui).not.toMatch(/app=reports|report_node=/);
  });

  it("(16) the person header lets its actions wrap under the name", () => {
    const header = panel.slice(panel.indexOf("<header"), panel.indexOf("</header>"));
    expect(header).toMatch(/<header className="[^"]*flex-wrap/);
  });

  it("no host writes the address by hand", () => {
    for (const text of [panel, tasks, dashboard]) {
      expect(text).not.toMatch(/app=reports|report_node=/);
    }
  });
});
