/**
 * WS-27bn — the Reports UX pass (2026-09-29), `projects_reports.md` §6.5.
 *
 * Each rule of the pass that a function can hold is pinned here, without a
 * DOM. The subjects answers are shaped as `GET /projects/reports/subjects`
 * answers. They are fixtures, not mirrors.
 */
import { describe, expect, it } from "vitest";

import { filterOptions } from "@/components/ui/SelectButton";

import type { ProjectRow, ReportRow, ReportSubjects, ReportTemplate } from "./api";
import {
  AS_OF_TODAY,
  PERIOD_FREE_SECTIONS,
  REPORT_SECTIONS,
  SCOPE_PROMPT,
  TEAM_HINT,
  builderName,
  builderStateFrom,
  builderStateFromLink,
  builderStateFromTemplate,
  deleteShown,
  editTitle,
  hiddenTeamHint,
  newBuilderState,
  periodFree,
  railGroups,
  saveRefusal,
  reportCardLine,
  reportHeaderLine,
  scopeChoices,
  scopeOptions,
  scopePhrase,
  scopePrompt,
  sectionGroups,
  startingTeam,
  subjectLabel,
  subjectOptions,
  withSubject,
} from "./reportBuilder";

const T1: ReportTemplate = {
  key: "team_pulse",
  name: "Team pulse",
  question: "How is each person on the team today, and who needs help?",
  scope_kinds: ["team", "project", "org"],
  available: true,
  sections: ["pulse", "conflicts", "rebalance"],
  weeks: 1,
  skip_current_week: false,
};

const T2: ReportTemplate = {
  key: "my_day",
  name: "My day",
  question: "What do I work on today?",
  scope_kinds: ["person"],
  available: true,
  sections: ["pulse"],
  weeks: 1,
  skip_current_week: false,
  requires_subject: "self",
};

const T5: ReportTemplate = {
  key: "project_status",
  name: "Project status",
  question: "Will this project finish on time, and what blocks it?",
  scope_kinds: ["project"],
  available: true,
  sections: ["finished", "outlook", "stuck", "conflicts"],
  weeks: 4,
  skip_current_week: true,
};

const T4: ReportTemplate = {
  key: "weekly_delivery",
  name: "Weekly delivery",
  question: "What did we finish last week, and how fast?",
  scope_kinds: ["project", "org"],
  available: true,
  sections: ["finished", "throughput", "load", "stuck"],
  weeks: 1,
  skip_current_week: true,
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

const MEMBER: ReportSubjects = {
  everyone: false,
  me: "mia@example.test",
  people: [{ email: "mia@example.test", name: "Mia Rao" }],
  teams: [],
};

const LEAD: ReportSubjects = {
  everyone: false,
  me: "lee@example.test",
  people: [
    { email: "ana@example.test", name: "Ana Shah" },
    { email: "lee@example.test", name: "Lee Park" },
    { email: "mia@example.test", name: null },
  ],
  teams: [{ slug: "hardware", name: "Hardware" }],
};

const LEAD_OF_TWO: ReportSubjects = {
  ...LEAD,
  teams: [
    { slug: "hardware", name: "Hardware" },
    { slug: "design", name: "Design team" },
  ],
};

const ADMIN: ReportSubjects = {
  everyone: true,
  me: "boss@example.test",
  people: [
    { email: "ana@example.test", name: "Ana Shah" },
    { email: "ana2@example.test", name: "Ana Shah" },
    { email: "boss@example.test", name: "Boss" },
    { email: "meera@example.test", name: "Meera Iyer" },
  ],
  teams: [{ slug: "hardware", name: "Hardware" }],
};

const TREE = [
  {
    id: "p-x2",
    name: "Printer X2",
    children: [{ id: "p-fw", name: "Firmware", children: [] }],
  },
  { id: "p-solo", name: "Solo", children: [] },
] as unknown as ProjectRow[];
const SCOPES = scopeOptions(TREE);

const MEERA = { kind: "person", email: "meera@example.test" } as const;
const HARDWARE = { kind: "team", slug: "hardware" } as const;

function row(over: Partial<ReportRow> = {}): ReportRow {
  return {
    id: "r1",
    project_id: null,
    scope: "portfolio",
    name: "Weekly delivery",
    config: {
      weeks: 1,
      skip_current_week: true,
      include_subtree: true,
      sections: ["finished"],
      template: "weekly_delivery",
    },
    created_by: "ana@example.test",
    created_at: "2026-09-28T00:00:00Z",
    ...over,
  };
}

describe("(2) a project-only template asks for a project", () => {
  it("Project status with no project prompts, and a project clears it", () => {
    const state = builderStateFromTemplate(T5)!;
    expect(scopePrompt(state, T5, false)).toBe(SCOPE_PROMPT);
    expect(SCOPE_PROMPT).toBe("Choose a project in the In chip to see this report.");
    expect(scopePrompt({ ...state, projectId: "p-x2" }, T5, false)).toBeNull();
  });

  it("a template that takes the organization never prompts", () => {
    for (const t of [T1, T4, null]) {
      expect(scopePrompt(newBuilderState(), t, false)).toBeNull();
    }
  });

  it("the scope chip leaves Whole organization out for a project-only template", () => {
    expect(scopeChoices(SCOPES, T5, false).map((o) => o.label)).toEqual([
      "Printer X2",
      "Firmware",
      "Solo",
    ]);
    expect(scopeChoices(SCOPES, T4, false)[0].label).toBe("Whole organization");
  });

  it("an edit of a saved org-wide Project status keeps Save on and its scope", () => {
    // A T5 saved before the UX pass can have no project. PATCH cannot change
    // the scope, so the edit must not ask for one.
    const saved = builderStateFrom(
      row({
        name: "Status",
        config: { ...row().config, template: "project_status", sections: ["outlook"] },
      })
    );
    expect(saved.projectId).toBeNull();
    expect(scopePrompt(saved, T5, true)).toBeNull();
    expect(saveRefusal(saved)).toBeNull();
    expect(scopeChoices(SCOPES, T5, true)[0].label).toBe("Whole organization");
    // A new Project status still asks for a project.
    expect(scopePrompt(builderStateFromTemplate(T5)!, T5, false)).toBe(SCOPE_PROMPT);
    expect(scopeChoices(SCOPES, T5, false)[0].label).toBe("Printer X2");
  });
});

describe("(7) Team pulse starts on the reader's one team", () => {
  it("a lead of exactly one team starts on that team", () => {
    const draft = builderStateFromTemplate(T1)!;
    expect(startingTeam(draft, T1, LEAD, false).subject).toEqual(HARDWARE);
  });

  it("an admin, a lead of two teams and a member start on Everyone", () => {
    const draft = builderStateFromTemplate(T1)!;
    for (const answer of [ADMIN, LEAD_OF_TWO, MEMBER]) {
      expect(startingTeam(draft, T1, answer, false)).toBe(draft);
    }
  });

  it("a choice, an edit, a subject or a template with no team scope stays", () => {
    const draft = builderStateFromTemplate(T1)!;
    expect(startingTeam({ ...draft, subjectTouched: true }, T1, LEAD, false)).toEqual({
      ...draft,
      subjectTouched: true,
    });
    expect(startingTeam(draft, T1, LEAD, true)).toBe(draft);
    const about = withSubject(draft, MEERA);
    expect(startingTeam(about, T1, LEAD, false)).toBe(about);
    const weekly = builderStateFromTemplate(T4)!;
    expect(startingTeam(weekly, T4, LEAD, false)).toBe(weekly);
    expect(startingTeam(newBuilderState(), null, LEAD, false).subject).toBeNull();
    expect(startingTeam(draft, T1, undefined, false)).toBe(draft);
  });

  it("a saved report and a link subject count as chosen", () => {
    expect(builderStateFrom(row()).subjectTouched).toBe(true);
    expect(
      builderStateFromLink({ template: T1, subject: MEERA, node: null }).subjectTouched
    ).toBe(true);
    expect(builderStateFromLink({ template: T1, subject: null, node: null }).subjectTouched).toBe(
      false
    );
  });

  it("(16) the hidden line asks for a team only where the reader leads one", () => {
    expect(hiddenTeamHint(LEAD_OF_TWO, null, true)).toBe(TEAM_HINT);
    expect(hiddenTeamHint(LEAD, null, true)).toBe(TEAM_HINT);
    // A team is chosen already, a member has no team, an admin hides nobody.
    expect(hiddenTeamHint(LEAD, HARDWARE, true)).toBeNull();
    expect(hiddenTeamHint(MEMBER, null, true)).toBeNull();
    expect(hiddenTeamHint(ADMIN, null, true)).toBeNull();
    expect(hiddenTeamHint(undefined, null, true)).toBeNull();
  });

  it("(16) the hint is absent on a template with no About chip", () => {
    // Weekly delivery has no About chip, so "Choose a team in About" would
    // point at a control that is not there.
    expect(hiddenTeamHint(LEAD, null, false)).toBeNull();
    expect(hiddenTeamHint(LEAD, null, true)).toBe(TEAM_HINT);
  });
});

describe("(3) a report with no period section reads as of today", () => {
  it("the period-free set is the sections that ignore the period", () => {
    // The lockstep pytest pins this set to `render_body`.
    expect([...PERIOD_FREE_SECTIONS]).toEqual([
      "outlook",
      "load",
      "capacity",
      "pulse",
      "stuck",
      "hygiene",
      "conflicts",
      "rebalance",
    ]);
  });

  it("Team pulse, My day and Data hygiene are period-free, Weekly delivery is not", () => {
    expect(periodFree(builderStateFromTemplate(T1)!)).toBe(true);
    expect(periodFree(builderStateFromTemplate(T2)!)).toBe(true);
    expect(periodFree({ ...newBuilderState(), sections: ["hygiene"] })).toBe(true);
    expect(periodFree(builderStateFromTemplate(T4)!)).toBe(false);
    expect(periodFree(builderStateFromTemplate(T6)!)).toBe(false);
  });

  it("the header says As of the day, not a window", () => {
    expect(AS_OF_TODAY).toBe("As of today");
    expect(
      reportHeaderLine({
        subject: null,
        scope: "Whole organization",
        periodStart: "2026-09-28",
        periodEnd: "2026-10-04",
        periodFree: true,
        asOf: "2026-09-29",
      })
    ).toBe("Whole organization · As of 29 Sep 2026");
  });
});

describe("(13) the rendered header names the subject and the scope", () => {
  it("reads About, In and the period", () => {
    expect(
      reportHeaderLine({
        subject: "Meera Iyer",
        scope: scopePhrase("p-x2", true, SCOPES),
        periodStart: "2026-08-31",
        periodEnd: "2026-09-27",
        periodFree: false,
        asOf: "2026-09-29",
      })
    ).toBe("About Meera Iyer · In Printer X2 and the projects under it · 31 Aug – 27 Sep 2026");
  });

  it("the scope phrase says Whole organization, and the subtree only when there is one", () => {
    expect(scopePhrase(null, true, SCOPES)).toBe("Whole organization");
    expect(scopePhrase("p-x2", false, SCOPES)).toBe("In Printer X2");
    expect(scopePhrase("p-solo", true, SCOPES)).toBe("In Solo");
    expect(scopePhrase("gone", true, SCOPES)).toBe("In a project");
  });

  it("a subject reads as its name, from the subjects answer", () => {
    expect(subjectLabel(MEERA, ADMIN)).toBe("Meera Iyer");
    expect(subjectLabel(HARDWARE, ADMIN)).toBe("Hardware team");
    expect(subjectLabel({ kind: "person", email: "x@example.test" }, ADMIN)).toBe(
      "x@example.test"
    );
    expect(subjectLabel(null, ADMIN)).toBeNull();
  });
});

describe("(6) the subject menu", () => {
  it("Everyone has no heading, and the teams come before the people", () => {
    const options = subjectOptions(LEAD);
    expect(options.map((o) => o.label)).toEqual([
      "Everyone",
      "Hardware team",
      "Me",
      "Ana Shah",
      "mia@example.test",
    ]);
    expect(options.map((o) => o.group)).toEqual([
      undefined,
      "Teams",
      "People",
      "People",
      "People",
    ]);
  });

  it("the address shows on Me, and on two people with one name, and nowhere else", () => {
    const lead = subjectOptions(LEAD);
    expect(lead.find((o) => o.label === "Me")?.hint).toBe("lee@example.test");
    expect(lead.find((o) => o.label === "Ana Shah")?.hint).toBeUndefined();
    const admin = subjectOptions(ADMIN);
    const anas = admin.filter((o) => o.label === "Ana Shah").map((o) => o.hint);
    expect(anas).toEqual(["ana@example.test", "ana2@example.test"]);
    expect(admin.find((o) => o.label === "Meera Iyer")?.hint).toBeUndefined();
  });

  it("a search by address finds a named person, and the address stays hidden", () => {
    const options = subjectOptions(LEAD);
    const ana = options.find((o) => o.label === "Ana Shah");
    expect(ana?.hint).toBeUndefined();
    expect(filterOptions(options, "ana@example").map((o) => o.label)).toEqual(["Ana Shah"]);
    expect(filterOptions(options, "Ana").map((o) => o.label)).toEqual(["Ana Shah"]);
  });

  it("a saved team the answer omits stays inside the Teams group", () => {
    const gone = { kind: "team", slug: "gone" } as const;
    const options = subjectOptions(LEAD, gone, true);
    const groups = options.map((o) => o.group);
    // One run of Teams, then one run of People: no second Teams heading.
    expect(groups).toEqual([undefined, "Teams", "Teams", "People", "People", "People"]);
    expect(options[2]).toMatchObject({ value: "team:gone", hint: "as saved" });
  });
});

describe("(4) the sections sit under three labels, in SECTIONS order", () => {
  it("each group keeps the server's order, and every section is in one group", () => {
    const groups = sectionGroups();
    expect(groups.map((g) => g.label)).toEqual([
      "What happened",
      "Where things stand",
      "Who needs help",
    ]);
    expect(groups.map((g) => g.sections.map((s) => s.key))).toEqual([
      ["finished", "throughput"],
      ["outlook", "load", "capacity", "stuck", "hygiene"],
      ["pulse", "conflicts", "rebalance"],
    ]);
    const order = REPORT_SECTIONS.map((s) => s.key);
    for (const g of groups) {
      const keys = g.sections.map((s) => s.key);
      expect(keys).toEqual(order.filter((k) => keys.includes(k)));
    }
    expect(groups.flatMap((g) => g.sections.map((s) => s.key)).sort()).toEqual(
      [...order].sort()
    );
  });
});

describe("(5) the name follows the chips until the member types one", () => {
  it("derives the name from the template and the subject or the scope", () => {
    const t6 = withSubject(builderStateFromTemplate(T6)!, MEERA);
    expect(builderName(t6, T6, ADMIN, SCOPES)).toBe("1:1 prep: Meera Iyer");
    const t5 = { ...builderStateFromTemplate(T5)!, projectId: "p-x2" };
    expect(builderName(t5, T5, ADMIN, SCOPES)).toBe("Project status: Printer X2");
    const t1 = withSubject(builderStateFromTemplate(T1)!, HARDWARE);
    expect(builderName(t1, T1, LEAD, SCOPES)).toBe("Team pulse: Hardware team");
  });

  it("a report with nothing to name keeps the template name, or Untitled report", () => {
    expect(builderName(builderStateFromTemplate(T4)!, T4, ADMIN, SCOPES)).toBe(
      "Weekly delivery"
    );
    // My day is always about its author, so the name says nothing more.
    const t2 = withSubject(builderStateFromTemplate(T2)!, MEERA);
    expect(builderName(t2, T2, ADMIN, SCOPES)).toBe("My day");
    expect(builderName(newBuilderState(), null, ADMIN, SCOPES)).toBe("Untitled report");
    const custom = withSubject(newBuilderState(), MEERA);
    expect(builderName(custom, null, ADMIN, SCOPES)).toBe("Report: Meera Iyer");
  });

  it("a typed name wins, and an edit keeps the saved name", () => {
    const typed = {
      ...withSubject(builderStateFromTemplate(T6)!, MEERA),
      name: "Meera, monthly",
      nameTouched: true,
    };
    expect(builderName(typed, T6, ADMIN, SCOPES)).toBe("Meera, monthly");
    const saved = builderStateFrom(row({ name: "Kept" }));
    expect(saved.nameTouched).toBe(true);
    expect(builderName(saved, T4, ADMIN, SCOPES)).toBe("Kept");
    expect(newBuilderState().nameTouched).toBe(false);
    expect(builderStateFromTemplate(T6)!.nameTouched).toBe(false);
  });

  it("a card shows the subject and the scope, and the template only when it adds", () => {
    expect(reportCardLine(row(), "Weekly delivery", null, "Whole organization")).toBe(
      "Whole organization"
    );
    expect(
      reportCardLine(row({ name: "Friday" }), "Weekly delivery", null, "Whole organization")
    ).toBe("Weekly delivery · Whole organization");
    expect(reportCardLine(row(), "Custom", "Meera Iyer", "Printer X2")).toBe(
      "Custom · About Meera Iyer · Printer X2"
    );
    // A derived name says the template and the subject, so the line does not.
    expect(
      reportCardLine(row({ name: "1:1 prep: Meera Iyer" }), "1:1 prep", "Meera Iyer", "Whole organization")
    ).toBe("Whole organization");
  });
});

describe("(12) the rail splits yours from shared", () => {
  it("splits by the server's mine, newest first", () => {
    const rows = [
      row({ id: "a", mine: true, created_at: "2026-09-01T00:00:00Z" }),
      row({ id: "b", mine: false, created_at: "2026-09-02T00:00:00Z" }),
      row({ id: "c", mine: true, created_at: "2026-09-03T00:00:00Z" }),
      row({ id: "d", created_at: "2026-09-04T00:00:00Z" }),
    ];
    const { yours, shared } = railGroups(rows);
    expect(yours.map((r) => r.id)).toEqual(["c", "a"]);
    expect(shared.map((r) => r.id)).toEqual(["d", "b"]);
  });
});

describe("(10) edit mode", () => {
  it("Delete shows only when the server's can_delete is true", () => {
    expect(deleteShown(row({ can_delete: true }))).toBe(true);
    expect(deleteShown(row({ can_delete: false }))).toBe(false);
    expect(deleteShown(row({}))).toBe(false);
    expect(deleteShown(null)).toBe(false);
  });

  it("the title names the report under edit", () => {
    expect(editTitle(row({ name: "Friday" }))).toBe("Edit: Friday");
  });
});
