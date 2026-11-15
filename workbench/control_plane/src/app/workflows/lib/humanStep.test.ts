/**
 * WFC-4a-1 fence: the rules of the human step form.
 *
 * The form writes `assignee`, `title` and `description`, and no other key.
 * A value with `{{` writes nothing, because publish refuses it. The person
 * picker offers active members only, and says what it does in each state.
 * `tests/unit/test_workflows_node_offer.py` holds `HUMAN_STEP_KEYS` here equal
 * to `graph.HUMAN_STEP_KEYS`.
 *
 * Mutation: drop the `{{` check of `humanStepEdit`. "refuses {{" turns red.
 */
import { describe, expect, it } from "vitest";

import type { DirectoryEntry } from "@/lib/rooms";

import {
  DIRECTORY_PEOPLE_CAP,
  HUMAN_STEP_KEYS,
  LITERAL_ONLY_MESSAGE,
  NEW_HUMAN_STEP_CONFIG,
  humanStepEdit,
  humanStepValue,
  personPickerView,
  readOnlyNote,
  type HumanStepKey,
} from "./humanStep";

const person = (subject: string, displayName = subject): DirectoryEntry => ({
  subject,
  displayName,
  kind: "person",
});
const group: DirectoryEntry = {
  subject: "group:sales",
  displayName: "Sales",
  kind: "group",
  memberCount: 4,
};
const read = (data: DirectoryEntry[] | undefined, error: string | null = null) => ({
  data,
  loading: data === undefined && !error,
  error,
});

describe("humanStepEdit", () => {
  it.each(HUMAN_STEP_KEYS)("writes %s alone", (key) => {
    const out = humanStepEdit(key, "Check the invoice");
    expect(out).toEqual({ ok: true, patch: { [key]: "Check the invoice" } });
    if (out.ok) expect(Object.keys(out.patch)).toEqual([key]);
  });

  it.each(HUMAN_STEP_KEYS)("refuses {{ in %s and writes nothing", (key) => {
    const out = humanStepEdit(key, "Check {{trigger.invoice}}");
    expect(out).toEqual({ ok: false, message: LITERAL_ONLY_MESSAGE });
    expect("patch" in out).toBe(false);
  });

  it("refuses a key outside the three", () => {
    const out = humanStepEdit("due" as HumanStepKey, "tomorrow");
    expect(out.ok).toBe(false);
  });

  it("says the refusal in plain words", () => {
    expect(LITERAL_ONLY_MESSAGE).toContain("plain words");
    expect(LITERAL_ONLY_MESSAGE).toContain("earlier steps");
  });

  it("drops a new step with the three keys, empty", () => {
    expect(Object.keys(NEW_HUMAN_STEP_CONFIG).sort()).toEqual([...HUMAN_STEP_KEYS].sort());
    expect(Object.values(NEW_HUMAN_STEP_CONFIG).every((v) => v === "")).toBe(true);
  });

  it("reads a value that is not text as empty", () => {
    expect(humanStepValue({ title: 4 }, "title")).toBe("");
    expect(humanStepValue({ title: "Call" }, "title")).toBe("Call");
  });
});

describe("personPickerView", () => {
  it("says it loads before the first answer", () => {
    expect(personPickerView(read(undefined), "")).toEqual({
      kind: "loading",
      text: "Loading the members of your organization…",
    });
  });

  it("says what failed and what to do", () => {
    const view = personPickerView(read(undefined, "Request failed (503)"), "");
    expect(view.kind).toBe("error");
    if (view.kind === "error") expect(view.text).toContain("try again");
  });

  it("says that no member was found", () => {
    const view = personPickerView(read([group]), "");
    expect(view.kind).toBe("empty");
  });

  it("offers each person and no group", () => {
    const view = personPickerView(read([person("ravi@acme.test", "Ravi"), group]), "");
    expect(view.kind).toBe("ready");
    if (view.kind !== "ready") return;
    expect(view.options.map((o) => o.value)).toEqual(["ravi@acme.test"]);
    expect(view.options[0]).toMatchObject({ label: "Ravi", hint: "ravi@acme.test" });
    expect(view.capped).toBe(false);
  });

  it("keeps the current person when the list does not hold them", () => {
    const view = personPickerView(read([person("ravi@acme.test")]), "old@acme.test");
    expect(view.kind).toBe("ready");
    if (view.kind !== "ready") return;
    expect(view.options[0]).toMatchObject({ value: "old@acme.test" });
    expect(view.options).toHaveLength(2);
  });

  it("says when the list is cut at the cap", () => {
    const many = Array.from({ length: DIRECTORY_PEOPLE_CAP }, (_, i) => person(`p${i}@acme.test`));
    const view = personPickerView(read(many), "");
    expect(view.kind === "ready" && view.capped).toBe(true);
  });
});

describe("readOnlyNote", () => {
  it("says that a task step cannot run", () => {
    expect(readOnlyNote("pm_task")).toBe("This step cannot run in a workflow yet.");
    expect(readOnlyNote("pm_lifecycle")).toBe("This step cannot run in a workflow yet.");
  });

  it("says that the record write has no form yet", () => {
    expect(readOnlyNote("studio_write")).toBe("The form for this step is not built yet.");
  });

  it("gives no note to a step with a form", () => {
    for (const type of ["human_step", "agent", "tool", "approval"]) {
      expect(readOnlyNote(type), type).toBeNull();
    }
  });
});
