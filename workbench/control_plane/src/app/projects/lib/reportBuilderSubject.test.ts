/**
 * WS-27bn R5b-1 — the subject chip and the link contract
 * (`projects_reports.md` §8 R5b, done-when (a) to (d), and (o) to (q)).
 *
 * The builder's claims are pure, so they are pinned here without a DOM. The
 * subjects answer below is shaped as `GET /projects/reports/subjects`
 * answers. It is a fixture, not a mirror.
 */
import { describe, expect, it } from "vitest";

import type { ReportRow, ReportSubjects, ReportTemplate } from "./api";
import {
  EVERYONE,
  NO_SUBJECT_NOTE,
  builderStateFrom,
  builderStateFromLink,
  builderStateFromTemplate,
  configFor,
  createPayload,
  errorChip,
  newBuilderState,
  parseReportLink,
  parseSubjectValue,
  patchPayload,
  reportLink,
  sectionBlockedBySubject,
  sectionsFor,
  selfSubject,
  subjectChipShown,
  subjectOptions,
  subjectPrompt,
  subjectSectionNote,
  subjectValue,
  withSubject,
} from "./reportBuilder";

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

const T7: ReportTemplate = {
  key: "exceptions",
  name: "Exceptions",
  question: "What is wrong right now, and nothing else?",
  scope_kinds: ["person", "team", "project", "org"],
  available: false,
  waits_for: "A today period",
};

const CATALOGUE = [T1, T2, T4, T6, T7];

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

const ADMIN: ReportSubjects = {
  everyone: true,
  me: "boss@example.test",
  people: [
    { email: "ana@example.test", name: "Ana Shah" },
    { email: "boss@example.test", name: "Boss" },
    { email: "lee@example.test", name: "Lee Park" },
    { email: "mia@example.test", name: "Mia Rao" },
    { email: "noa@example.test", name: "Noa Lim" },
  ],
  teams: [
    { slug: "design", name: "Design team" },
    { slug: "hardware", name: "Hardware" },
  ],
};

const PERSON = { kind: "person", email: "mia@example.test" } as const;
const TEAM = { kind: "team", slug: "hardware" } as const;

describe("(a) an edit keeps the subject", () => {
  const row: ReportRow = {
    id: "r-1",
    project_id: "proj-1",
    scope: "node",
    name: "Mia, weekly",
    config: {
      weeks: 1,
      skip_current_week: true,
      include_subtree: true,
      sections: ["finished", "load"],
      subject: PERSON,
    },
    created_by: "lee@example.test",
    created_at: "2026-09-29T00:00:00Z",
  };

  it("builderStateFrom then patchPayload sends the same subject", () => {
    const state = { ...builderStateFrom(row), name: "Mia, renamed" };
    expect(patchPayload(state).config.subject).toEqual(PERSON);
    expect(patchPayload(state)).toEqual({
      name: "Mia, renamed",
      config: { ...row.config },
    });
  });

  it("a team subject survives too, and so does the create payload", () => {
    const teamRow = { ...row, config: { ...row.config, subject: TEAM } };
    expect(patchPayload(builderStateFrom(teamRow)).config.subject).toEqual(TEAM);
    expect(createPayload(builderStateFrom(teamRow)).config.subject).toEqual(TEAM);
  });

  it("no subject sends no key, as before R5b", () => {
    expect("subject" in configFor(newBuilderState())).toBe(false);
    const plain = { ...row, config: { ...row.config, subject: undefined } };
    expect("subject" in patchPayload(builderStateFrom(plain)).config).toBe(false);
  });
});

describe("(b) the subject chip lists what the server answered", () => {
  const values = (a: ReportSubjects) => subjectOptions(a).map((o) => o.value);

  it("a member sees Everyone and Me only", () => {
    const options = subjectOptions(MEMBER);
    expect(options.map((o) => o.label)).toEqual(["Everyone", "Me"]);
    expect(options[1]).toMatchObject({
      value: "person:mia@example.test",
      hint: "mia@example.test",
      group: "People",
    });
  });

  it("a lead sees Me first, the team members, then the teams they lead", () => {
    const options = subjectOptions(LEAD);
    expect(options.map((o) => o.label)).toEqual([
      "Everyone",
      "Me",
      "Ana Shah",
      "mia@example.test",
      "Hardware team",
    ]);
    expect(options.map((o) => o.group)).toEqual([
      "Everyone",
      "People",
      "People",
      "People",
      "Teams",
    ]);
    // The name first, the address muted beside it. No name: the address.
    expect(options[2].hint).toBe("ana@example.test");
    expect(options[3].hint).toBeUndefined();
  });

  it("an admin sees every person and every team in the answer", () => {
    expect(values(ADMIN)).toEqual([
      EVERYONE,
      "person:boss@example.test",
      "person:ana@example.test",
      "person:lee@example.test",
      "person:mia@example.test",
      "person:noa@example.test",
      "team:design",
      "team:hardware",
    ]);
    // A team named "... team" is not "Design team team".
    expect(subjectOptions(ADMIN).at(-2)?.label).toBe("Design team");
  });

  it("a saved subject the answer omits stays as an option", () => {
    const gone = { kind: "person", email: "gone@example.test" } as const;
    const last = subjectOptions(MEMBER, gone).at(-1);
    expect(last).toMatchObject({
      value: "person:gone@example.test",
      hint: "as saved",
    });
  });

  it("the chip value and its parser agree, and a bad value is refused", () => {
    for (const s of [PERSON, TEAM]) {
      expect(parseSubjectValue(subjectValue(s))).toEqual(s);
    }
    expect(parseSubjectValue(EVERYONE)).toBeNull();
    for (const bad of ["person:", "person:no-at", "team:a b", "group:x", "x"]) {
      expect(parseSubjectValue(bad)).toBeUndefined();
    }
  });

  it("selfSubject is the reader, from the answer", () => {
    expect(selfSubject(MEMBER)).toEqual(PERSON);
    expect(selfSubject(undefined)).toBeNull();
  });
});

describe("(c) a subject turns off outlook and hygiene", () => {
  const state = {
    ...newBuilderState(),
    sections: ["finished", "outlook", "load", "hygiene"],
  };

  it("configFor of a state with a subject holds neither", () => {
    const config = configFor(withSubject(state, PERSON));
    expect(config.sections).toEqual(["finished", "load"]);
    expect(config.sections).not.toContain("outlook");
    expect(config.sections).not.toContain("hygiene");
  });

  it("Everyone gives them back, because the choice was kept", () => {
    const back = withSubject(withSubject(state, TEAM), null);
    expect(configFor(back).sections).toEqual([
      "finished",
      "outlook",
      "load",
      "hygiene",
    ]);
  });

  it("a report of only hygiene falls back to the default sections", () => {
    expect(sectionsFor({ sections: ["hygiene"], subject: PERSON })).toEqual([
      "finished",
      "throughput",
      "load",
      "stuck",
    ]);
  });
});

describe("(d) the link contract", () => {
  it("reportLink builds the one address, with report_node and never project", () => {
    const link = reportLink({ template: "one_on_one", subject: PERSON, node: "n-1" });
    expect(link).toBe(
      "/projects?app=reports&template=one_on_one&subject=person%3Amia%40example.test&report_node=n-1"
    );
    expect(link).not.toMatch(/[?&]project=/);
    expect(reportLink()).toBe("/projects?app=reports");
    expect(reportLink({ node: "n-1" }, "https://x.test")).toBe(
      "https://x.test/projects?app=reports&report_node=n-1"
    );
  });

  it("the parser keeps the template, the subject and the node", () => {
    for (const parts of [
      { template: "one_on_one", subject: PERSON, node: "n-1" },
      { template: "team_pulse", subject: TEAM, node: null },
      { template: null, subject: null, node: "n-2" },
    ]) {
      const params = new URL(reportLink(parts), "https://x.test").searchParams;
      const intent = parseReportLink(params, CATALOGUE);
      expect(intent).not.toBeNull();
      expect(intent!.template?.key ?? null).toBe(parts.template);
      expect(intent!.subject).toEqual(parts.subject);
      expect(intent!.node).toBe(parts.node);
    }
  });

  it("an unknown template, a coming-soon one, or a bad subject opens the home", () => {
    const home = (qs: string) =>
      parseReportLink(new URLSearchParams(qs), CATALOGUE);
    expect(home("template=no_such")).toBeNull();
    expect(home("template=exceptions")).toBeNull();
    expect(home("template=one_on_one&subject=person%3Ano-at")).toBeNull();
    expect(home("subject=group%3Ax")).toBeNull();
    expect(home("")).toBeNull();
  });

  it("a link fills the builder, and a team never fills a person template", () => {
    const state = builderStateFromLink({ template: T6, subject: PERSON, node: "n-1" });
    expect(state).toMatchObject({ template: "one_on_one", subject: PERSON, projectId: "n-1" });
    expect(builderStateFromLink({ template: T6, subject: TEAM, node: null }).subject).toBeNull();
    // A template with no person or team scope takes no subject.
    expect(builderStateFromLink({ template: T4, subject: PERSON, node: null }).subject).toBeNull();
    // No template: a custom report on the node.
    expect(builderStateFromLink({ template: null, subject: null, node: "n-2" })).toMatchObject({
      template: null,
      projectId: "n-2",
    });
  });
});

describe("(o) a template that needs a subject says what to do", () => {
  it("T6 with no person asks for one, and a person clears the line", () => {
    const state = builderStateFromTemplate(T6)!;
    expect(subjectPrompt(state, T6)).toMatch(/Choose a person/);
    expect(subjectPrompt(withSubject(state, TEAM), T6)).toMatch(/Choose a person/);
    expect(subjectPrompt(withSubject(state, PERSON), T6)).toBeNull();
  });

  it("T2 waits for the reader, and a template with no rule never asks", () => {
    expect(subjectPrompt(builderStateFromTemplate(T2)!, T2)).toMatch(/your own day/i);
    expect(subjectPrompt(withSubject(newBuilderState(), PERSON), T2)).toBeNull();
    expect(subjectPrompt(newBuilderState(), T4)).toBeNull();
    expect(subjectPrompt(newBuilderState(), null)).toBeNull();
  });

  it("the chip hides for a template with no person or team scope", () => {
    expect(subjectChipShown(T4)).toBe(false);
    for (const t of [T1, T2, T6, null]) expect(subjectChipShown(t)).toBe(true);
  });
});

describe("(p) the sections line says why two options are off", () => {
  it("shows the line and blocks the two boxes only with a subject", () => {
    const state = { ...newBuilderState(), sections: ["finished"] };
    expect(subjectSectionNote(state)).toBeNull();
    expect(sectionBlockedBySubject(state, "outlook")).toBe(false);
    const about = withSubject(state, PERSON);
    expect(subjectSectionNote(about)).toBe(NO_SUBJECT_NOTE);
    expect(sectionBlockedBySubject(about, "outlook")).toBe(true);
    expect(sectionBlockedBySubject(about, "hygiene")).toBe(true);
    expect(sectionBlockedBySubject(about, "load")).toBe(false);
  });
});

describe("(q) an error shows next to the chip that caused it", () => {
  it.each([
    // The server's own words, from report_scope.py and reports.py.
    ["You may report on yourself only. A report on n@x needs the admin grant (admin:members:read), or the lead role in a team of that person.", "subject"],
    ["A report on the team b needs the admin grant (admin:members:read), or the lead role in that team.", "subject"],
    ["n@x is not an active member of this organization.", "subject"],
    ["other is not a team of this organization.", "subject"],
    ["The template 'my_day' is your own day. Its subject must be you.", "subject"],
    ["The template 'one_on_one' is about one person. Choose a person as its subject.", "subject"],
    ["Project not found", "scope"],
    ["The preview could not be rendered.", "other"],
  ] as const)("%s", (text, chip) => {
    expect(errorChip(text)).toBe(chip);
  });
});
