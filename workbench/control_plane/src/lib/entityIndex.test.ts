/**
 * The chat's entity index (WS-27bm S9, spec §15): what a «name» in an answer
 * resolves to, from the same message's Projects tool results.
 *
 * The fixture is real skill output. A pytest holds it equal to the Python
 * formatters (`test_the_pill_fixture_is_the_skill_output`).
 */
import { describe, expect, it } from "vitest";

import { buildEntityIndex, resolveEntity } from "./entityIndex";
import {
  OWNER_TOOL_EVENTS,
  PROJECT_ID,
  SPACE_ID,
  TASK3_ID,
  TASK5_ID,
  TASKS_RESULT,
} from "./entityPills.fixture";
import { parseProjectRows, parseTaskRows } from "./projectToolRows";

const index = buildEntityIndex(OWNER_TOOL_EVENTS);

describe("the row parsers", () => {
  it("read the tree's project rows, with their level", () => {
    expect(parseProjectRows(OWNER_TOOL_EVENTS[0].result)).toEqual([
      { id: SPACE_ID, name: "Hathi Labs", level: "space" },
      { id: PROJECT_ID, name: "Projects/Tasks App", level: "project" },
    ]);
  });

  it("never read a vocabulary status as a project", () => {
    const vocab = ["Statuses (1):", `- «Done» [done] · id ${TASK5_ID}`].join("\n");
    expect(parseProjectRows(vocab)).toEqual([]);
    // And a status with a full_id line after it is still not one of the four levels.
    expect(parseProjectRows(["- «Done» [done]", `  full_id: ${TASK5_ID}`].join("\n"))).toEqual([]);
  });

  it("still read the task rows the cards read", () => {
    expect(parseTaskRows(TASKS_RESULT).map((r) => r.number)).toEqual(["#5", "#3"]);
  });
});

describe("resolveEntity", () => {
  it("resolves a task by number and title, with its status", () => {
    const hit = resolveEntity(index, "Notification engine for projects", "#5");
    expect(hit).toMatchObject({
      kind: "task",
      number: "#5",
      href: `/projects?task=${TASK5_ID}`,
      status: "To do",
      category: "todo",
    });
  });

  it("resolves a task by its title alone", () => {
    expect(resolveEntity(index, "Board drag and drop")).toMatchObject({
      kind: "task",
      number: "#3",
      href: `/projects?task=${TASK3_ID}`,
    });
  });

  it("resolves a project, and the level picks the kind", () => {
    expect(resolveEntity(index, "Projects/Tasks App")).toEqual({
      kind: "project",
      name: "Projects/Tasks App",
      level: "project",
      href: `/projects?project=${PROJECT_ID}`,
    });
    expect(resolveEntity(index, "Hathi Labs")).toMatchObject({ kind: "project", level: "space" });
  });

  it("resolves a person, with the local part when no name was printed", () => {
    expect(resolveEntity(index, "vjvarada@hathilabs.com")).toEqual({
      kind: "person",
      label: "vjvarada",
      email: "vjvarada@hathilabs.com",
      agent: false,
    });
  });

  it("uses the name a people read printed", () => {
    const people = buildEntityIndex([
      {
        status: "done",
        result: "People (1):\n- «Vijay Varada» · assignee «vjvarada@hathilabs.com» · load open 3",
      },
    ]);
    expect(resolveEntity(people, "vjvarada@hathilabs.com")).toMatchObject({
      kind: "person",
      label: "Vijay Varada",
    });
    expect(resolveEntity(people, "Vijay Varada")).toMatchObject({
      kind: "person",
      email: "vjvarada@hathilabs.com",
    });
  });

  it("finds a name for an address anywhere in the message's tools (S9 round 2)", () => {
    const tools = buildEntityIndex([
      // A task row names the address only.
      { status: "done", result: TASKS_RESULT },
      {
        status: "done",
        result: [
          "Capacity (2 people):",
          "- «Vijay Varada» · assignee «vjvarada@hathilabs.com» · in this scope: open 3",
          "- «sam@x.io» · assignee «sam@x.io» · not in the directory · in this scope: open 1",
          "At risk (1 of 1):",
          "- «Ship it» · due 2026-10-01 · short 4h · held by «Hal Jordan» («hal@x.io»)",
        ].join("\n"),
      },
    ]);
    expect(resolveEntity(tools, "vjvarada@hathilabs.com")).toMatchObject({ label: "Vijay Varada" });
    expect(resolveEntity(tools, "hal@x.io")).toMatchObject({ label: "Hal Jordan" });
    // An address printed as its own "name" is no name: the local part shows.
    expect(resolveEntity(tools, "sam@x.io")).toMatchObject({ label: "sam" });
  });

  it("resolves a status and a tag from the vocabulary read", () => {
    const vocab = buildEntityIndex([
      {
        status: "done",
        result: [
          "Statuses (1):",
          "- «Blocked» [in_progress] · id 1",
          "Tags (1):",
          "- «urgent» · 4 tasks · id 2",
        ].join("\n"),
      },
    ]);
    expect(resolveEntity(vocab, "Blocked")).toEqual({
      kind: "status",
      name: "Blocked",
      category: "in_progress",
    });
    expect(resolveEntity(vocab, "urgent")).toEqual({ kind: "tag", name: "urgent" });
  });

  it("does not link an ambiguous title", () => {
    const two = buildEntityIndex([
      {
        status: "done",
        result: [
          "- #1 «Launch» · status «To do»",
          `  full_id: ${TASK5_ID}`,
          "- #1 «Launch» · status «To do»",
          `  full_id: ${TASK3_ID}`,
        ].join("\n"),
      },
    ]);
    expect(resolveEntity(two, "Launch")).toEqual({ kind: "unknown", text: "Launch", ambiguous: true });
    // `#n` is unique per root only, so the number does not settle it either.
    const numbered = resolveEntity(two, "Launch", "#1");
    expect(numbered.kind).toBe("task");
    expect("href" in numbered && numbered.href).toBeFalsy();
  });

  it("does not link a name no tool printed", () => {
    expect(resolveEntity(index, "Something else")).toEqual({
      kind: "unknown",
      text: "Something else",
      ambiguous: false,
    });
    const guessed = resolveEntity(index, "Notification engine for projects", "#9");
    expect(guessed.kind).toBe("task");
    expect("href" in guessed && guessed.href).toBeFalsy();
  });

  it("reads a receipt: one task reference, then its id", () => {
    const receipt = buildEntityIndex([
      { status: "done", result: `Commented on #7 «Call the vendor» (comment id a1).\n  full_id: ${TASK5_ID}` },
    ]);
    expect(resolveEntity(receipt, "Call the vendor", "#7")).toMatchObject({
      href: `/projects?task=${TASK5_ID}`,
    });
  });

  it("ignores a tool that failed or is still running", () => {
    const none = buildEntityIndex([
      { status: "error", result: TASKS_RESULT },
      { status: "running", result: TASKS_RESULT },
    ]);
    expect(resolveEntity(none, "Board drag and drop").kind).toBe("unknown");
  });
});
