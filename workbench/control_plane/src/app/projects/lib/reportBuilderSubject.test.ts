/**
 * WS-27bn R5b-1 — the subject chip and the link contract
 * (`projects_reports.md` §8 R5b, done-when (a) to (d), (o), (p) and (r)).
 *
 * The builder's claims are pure, so they are pinned here without a DOM. The
 * subjects answer below is shaped as `GET /projects/reports/subjects`
 * answers. It is a fixture, not a mirror.
 */
import { describe, expect, it } from "vitest";

import type { ProjectRow, ReportRow, ReportSubjects, ReportTemplate } from "./api";
import {
  AUTHOR_SUBJECT_NOTE,
  DEFAULT_REPORT_SECTIONS,
  EVERYONE,
  NEW_REPORT_NAME,
  NOBODY_TO_CHOOSE,
  NO_SUBJECT_NOTE,
  SELF_SUBJECT_NOTE,
  builderStateFrom,
  builderStateFromLink,
  builderStateFromTemplate,
  builderSubject,
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
  subjectChipNote,
  subjectChipShown,
  subjectChipStatus,
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

  // §6.5 item 6 moved the teams before the people and took the
  // heading off "Everyone". The members listed stay exactly the same.
  it("a lead sees the teams they lead, then Me first, then the team members", () => {
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
    // The address shows on Me only. No name: the address is the label.
    expect(options[2].hint).toBe("lee@example.test");
    expect(options[3].hint).toBeUndefined();
    expect(options[4].hint).toBeUndefined();
  });

  it("an admin sees every person and every team in the answer", () => {
    expect(values(ADMIN)).toEqual([
      EVERYONE,
      "team:design",
      "team:hardware",
      "person:boss@example.test",
      "person:ana@example.test",
      "person:lee@example.test",
      "person:mia@example.test",
      "person:noa@example.test",
    ]);
    // A team named "... team" is not "Design team team".
    expect(subjectOptions(ADMIN)[1]?.label).toBe("Design team");
  });

  it("a saved subject the answer omits stays as an option", () => {
    const gone = { kind: "person", email: "gone@example.test" } as const;
    const last = subjectOptions(MEMBER, gone, true).at(-1);
    expect(last).toMatchObject({
      value: "person:gone@example.test",
      hint: "as saved",
    });
  });

  it("(7) a subject from a link that the answer omits is not in your list", () => {
    // It was never saved, so "as saved" would be false. The server refuses
    // it on the preview.
    const other = { kind: "person", email: "noa@example.test" } as const;
    expect(subjectOptions(MEMBER, other).at(-1)).toMatchObject({
      value: "person:noa@example.test",
      hint: "not in your list",
    });
    expect(subjectOptions(MEMBER, other, false).at(-1)?.hint).toBe("not in your list");
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

const NODE_1 = "0b6f3c1e-5d2a-4b7c-9e8f-1a2b3c4d5e6f";
const NODE_2 = "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f";
const CHILD = { id: NODE_2, name: "Child", children: [] } as unknown as ProjectRow;
const ROOTS = [
  { id: NODE_1, name: "Root", children: [CHILD] } as unknown as ProjectRow,
];

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
      { template: "one_on_one", subject: PERSON, node: NODE_1 },
      { template: "team_pulse", subject: TEAM, node: null },
      // A node under a root is in the tree too.
      { template: null, subject: null, node: NODE_2 },
    ]) {
      const params = new URL(reportLink(parts), "https://x.test").searchParams;
      const intent = parseReportLink(params, CATALOGUE, ROOTS);
      expect(intent).not.toBeNull();
      expect(intent!.template?.key ?? null).toBe(parts.template);
      expect(intent!.subject).toEqual(parts.subject);
      expect(intent!.node).toBe(parts.node);
    }
  });

  it("(3) a node that is not a UUID in the reader's tree is dropped", () => {
    const parse = (qs: string) =>
      parseReportLink(new URLSearchParams(qs), CATALOGUE, ROOTS);
    const unknown = "11111111-2222-4333-8444-555555555555";
    // The server casts the node to a uuid: a bad one made the preview 500.
    for (const bad of ["n-1", "x';--", unknown, `${NODE_1}x`, " "]) {
      const q = `template=one_on_one&report_node=${encodeURIComponent(bad)}`;
      expect(parse(q)).toMatchObject({ template: T6, node: null });
      // A link with only a bad node has nothing left, so it opens the home.
      expect(parse(`report_node=${encodeURIComponent(bad)}`)).toBeNull();
    }
    // With no tree, no node is kept.
    expect(
      parseReportLink(new URLSearchParams(`report_node=${NODE_1}`), CATALOGUE)
    ).toBeNull();
    expect(parse(`report_node=${NODE_1.toUpperCase()}`)).toBeNull();
    expect(parse(`report_node=${NODE_1}`)?.node).toBe(NODE_1);
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

  it("(8) T2 says the list did not load when the read fails", () => {
    const state = builderStateFromTemplate(T2)!;
    expect(subjectPrompt(state, T2, true)).toMatch(/did not load/);
    expect(subjectPrompt(state, T2, true)).not.toMatch(/shows when/);
    expect(subjectPrompt(state, T2, false)).toMatch(/shows when the list/);
    // T6 still asks for a person. The chip carries the Retry.
    expect(subjectPrompt(builderStateFromTemplate(T6)!, T6, true)).toMatch(/Choose a person/);
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

describe("(r) an error shows next to the chip that caused it", () => {
  it.each([
    // The server's own words, from report_scope.py and reports.py.
    ["You may report on yourself only. A report on n@x needs the admin grant (admin:members:read), or the lead role in a team of that person.", "subject"],
    ["A report on the team b needs the admin grant (admin:members:read), or the lead role in that team.", "subject"],
    ["n@x is not an active member of this organization.", "subject"],
    ["other is not a team of this organization.", "subject"],
    ["The template 'my_day' is your own day. Its subject must be you.", "subject"],
    ["The template 'one_on_one' is about one person. Choose a person as its subject.", "subject"],
    ["My day is always about its author. The subject of this 'my_day' report must stay m@x.", "subject"],
    ["Project not found", "scope"],
    ["The preview could not be rendered.", "other"],
  ] as const)("%s", (text, chip) => {
    expect(errorChip(text)).toBe(chip);
  });
});

describe("(1) a T2 is always about its author", () => {
  const memberT2: ReportRow = {
    id: "r-t2",
    project_id: null,
    scope: "portfolio",
    name: "Mia day",
    config: {
      weeks: 1,
      skip_current_week: false,
      include_subtree: true,
      sections: ["pulse"],
      template: "my_day",
      subject: PERSON,
    },
    created_by: "mia@example.test",
    created_at: "2026-09-29T00:00:00Z",
  };
  const admin = selfSubject(ADMIN);

  it("an admin who edits a member's T2 keeps the member as its subject", () => {
    const draft = { ...builderStateFrom(memberT2), name: "Renamed" };
    const state = builderSubject(draft, "self", admin, true);
    expect(state.subject).toEqual(PERSON);
    expect(patchPayload(state).config.subject).toEqual(PERSON);
  });

  it("a new T2 takes the reader, and a link subject never wins", () => {
    const fresh = builderStateFromTemplate(T2, PERSON)!;
    expect(builderSubject(fresh, "self", admin, false).subject).toEqual(admin);
    // No answer yet: the draft stays, and the preview line waits.
    expect(builderSubject(fresh, "self", null, false)).toBe(fresh);
  });

  it("a template with no self rule is never changed", () => {
    const draft = withSubject(newBuilderState(), TEAM);
    expect(builderSubject(draft, "person", admin, false)).toBe(draft);
    expect(builderSubject(draft, null, admin, false)).toBe(draft);
  });

  it("the locked chip names its author for an admin, and says why", () => {
    expect(subjectChipNote("self", ADMIN, PERSON, true)).toBe(AUTHOR_SUBJECT_NOTE);
    expect(subjectChipNote("self", MEMBER, PERSON, true)).toBe(SELF_SUBJECT_NOTE);
    expect(subjectChipNote("self", ADMIN, admin, false)).toBe(SELF_SUBJECT_NOTE);
    // The chip label is the member's name, from the admin's answer.
    const label = subjectOptions(ADMIN, PERSON, true).find(
      (o) => o.value === subjectValue(PERSON)
    )?.label;
    expect(label).toBe("Mia Rao");
    expect(subjectOptions(MEMBER, PERSON, true)[1].label).toBe("Me");
  });
});

describe("(6) a subject that turns off every section fills the checkboxes", () => {
  it("writes the default sections into state.sections", () => {
    const only = { ...newBuilderState(), sections: ["hygiene"] };
    const about = withSubject(only, PERSON);
    // The checkboxes read state.sections. They now show what the preview shows.
    const shown = about.sections.filter((k) => !sectionBlockedBySubject(about, k));
    expect(shown).toEqual([...DEFAULT_REPORT_SECTIONS]);
    expect(configFor(about).sections).toEqual(shown);
    // The hygiene choice is kept, so Everyone gives it back.
    expect(withSubject(about, null).sections).toContain("hygiene");
  });

  it("a link to a custom report with a subject starts with sections on", () => {
    const state = builderStateFromLink({ template: null, subject: PERSON, node: null });
    expect(state.sections.filter((k) => !sectionBlockedBySubject(state, k)).length).toBeGreaterThan(0);
  });

  it("leaves the sections alone when one survives", () => {
    const state = { ...newBuilderState(), sections: ["outlook", "load"] };
    expect(withSubject(state, PERSON).sections).toEqual(["outlook", "load"]);
  });
});

describe("(8) a custom report starts as Untitled report", () => {
  it("names a new custom report Untitled report", () => {
    expect(NEW_REPORT_NAME).toBe("Untitled report");
    expect(newBuilderState().name).toBe("Untitled report");
    expect(builderStateFromTemplate(T4)!.name).toBe("Weekly delivery");
  });
});

describe("(9) the subject chip's loading, empty and failed states", () => {
  it("loading draws the skeleton until the answer arrives", () => {
    expect(subjectChipStatus(undefined, false)).toBe("loading");
    expect(subjectChipStatus(null, false)).toBe("loading");
  });

  it("a failed read draws the Retry line, and an answer wins over an old error", () => {
    expect(subjectChipStatus(undefined, true)).toBe("failed");
    expect(subjectChipStatus(MEMBER, true)).toBe("ready");
    expect(subjectChipStatus(MEMBER, false)).toBe("ready");
  });

  it("an answer with nobody in it says so", () => {
    const empty: ReportSubjects = { everyone: false, me: "x@y.test", people: [], teams: [] };
    expect(subjectChipNote(null, empty, null, false)).toBe(NOBODY_TO_CHOOSE);
    expect(subjectChipNote("person", empty, null, false)).toBe(NOBODY_TO_CHOOSE);
    expect(subjectChipNote(null, MEMBER, null, false)).toBeNull();
  });
});
