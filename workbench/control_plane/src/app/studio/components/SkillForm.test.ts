// The rules of `SkillForm` (STU-F2b), through `lib/studio/skillForm.ts` and
// the client, because the unit tests run in Node with no DOM.
// Mutations: let the picker keep a `package` row, send a second create on a
// double submit, or map a 422 prefix to the wrong field, and a test fails.
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ERROR_IDS,
  MAX_ACTIONS,
  checkValues,
  createSubmitGuard,
  errorsOf,
  firstErrorId,
  loadFailure,
  movesOf,
  pickableActions,
  publishBlock,
  withoutRowErrors,
  toBody,
  toCreate,
  toggleAction,
  type FormValues,
} from "@/lib/studio/skillForm";
import { applyStarter } from "@/lib/studio/skillStarter";
import { createSkill } from "@/lib/studio/skillsApi";
import { NAME_TAKEN } from "@/lib/studio/skillErrors";

afterEach(() => vi.unstubAllGlobals());

const FORM: FormValues = {
  name: " Triage ",
  description: " When mail piles up ",
  instructions: " Do it ",
  actions: ["a1", "a2"],
};

describe("the starter inside the form", () => {
  it("fills empty fields only", () => {
    const out = applyStarter({ name: "Mine", description: "", instructions: "" }, "Triage my inbox");
    expect(out.name).toBe("Mine");
    expect(out.description).toBe("Use this to triage my inbox");
  });
});

describe("the picker", () => {
  const rows = [
    { id: "m1", title: "Read", source: "manifest", class: "A" },
    { id: "w1", title: "Flow", source: "workflow", class: "B" },
    { id: "i1", title: "Send", source: "integration", class: "C" },
    { id: "p1", title: "Pack", source: "package", class: "A" },
    { id: "x1", title: "Bad", source: "manifest", class: "X" },
  ];

  it("drops each package row, and keeps class A, B and C", () => {
    expect(pickableActions(rows).map((r) => r.id)).toEqual(["m1", "w1", "i1"]);
  });

  it("holds at most 35 and no repeat, and keeps the order", () => {
    let list: string[] = [];
    for (let i = 0; i < 40; i++) list = toggleAction(list, `a${i}`);
    expect(list).toHaveLength(MAX_ACTIONS);
    expect(toggleAction(["a", "b"], "a")).toEqual(["b"]);
    expect(toggleAction(["a", "b"], "c")).toEqual(["a", "b", "c"]);
  });
});

describe("what the form sends", () => {
  it("sends one description to the row and to the body", () => {
    const create = toCreate(FORM);
    expect(create.description).toBe("When mail piles up");
    expect(create.body).toEqual({
      description: "When mail piles up",
      instructions: "Do it",
      actions: ["a1", "a2"],
    });
    expect(toBody(FORM)).toEqual(create.body);
  });

  it("shows the moves of each state", () => {
    expect(movesOf("draft")).toEqual(["publish"]);
    expect(movesOf("published")).toEqual(["pause"]);
    expect(movesOf("paused")).toEqual(["resume"]);
    expect(movesOf("archived")).toEqual([]);
  });
});

describe("the errors", () => {
  it("shows the 409 text at the name field", () => {
    expect(errorsOf("create", 409, NAME_TAKEN).name).toBe(NAME_TAKEN);
  });

  it("maps each 422 prefix to its field", () => {
    expect(errorsOf("version", 422, "$.actions[2]: the catalog serves no action 'x'").actions).toEqual({
      2: "This action is not in the catalog now. Remove it.",
    });
    expect(errorsOf("version", 422, "$.actions: a skill holds at most 35 actions").picker).toBeTruthy();
    expect(errorsOf("version", 422, "body.description must be a non-empty string").description).toBeTruthy();
    expect(errorsOf("version", 422, "body.instructions must be a non-empty string").instructions).toBeTruthy();
    expect(errorsOf("create", 422, "name must not be empty").name).toBeTruthy();
    expect(errorsOf("create", 422, "description holds at most 1024 characters").description).toBeTruthy();
  });

  it("shows an unknown prefix whole, under the form", () => {
    const text = "The body nests deeper than 20 levels. Flatten it.";
    expect(errorsOf("create", 422, text)).toEqual({ actions: {}, form: text });
  });
});

describe("a failed load", () => {
  it("is missing for a 404 and a 422 only", () => {
    expect(loadFailure(404)).toBe("missing");
    expect(loadFailure(422)).toBe("missing");
  });

  it("can be tried again for a 5xx, a 502 and a miss of the network", () => {
    expect(loadFailure(500)).toBe("failed");
    expect(loadFailure(502)).toBe("failed");
    expect(loadFailure(0)).toBe("failed");
  });
});

describe("the browser check", () => {
  it("stops a blank form with the plain sentences", () => {
    expect(checkValues({ name: " ", description: "", instructions: "  ", actions: [] }, true)).toEqual({
      actions: {},
      name: "Add a name.",
      description: "Write when to use this skill.",
      instructions: "Write the instructions.",
    });
  });

  it("does not ask an existing skill for a name", () => {
    expect(checkValues({ name: "", description: "d", instructions: "i", actions: [] }, false)).toBeNull();
  });
});

describe("the focus after a failed call", () => {
  it("goes to the first error in the order of the form", () => {
    const e = { actions: { 3: "x", 1: "y" }, picker: "p", instructions: "i", form: "f", move: "m" };
    expect(firstErrorId(e)).toBe(ERROR_IDS.instructions);
    expect(firstErrorId({ ...e, instructions: undefined })).toBe(ERROR_IDS.picker);
    expect(firstErrorId({ ...e, instructions: undefined, picker: undefined })).toBe(ERROR_IDS.action(1));
    expect(firstErrorId({ actions: {}, move: "m", form: "f" })).toBe(ERROR_IDS.move);
    expect(firstErrorId({ actions: {}, form: "f" })).toBe(ERROR_IDS.form);
    expect(firstErrorId({ actions: {}, name: "n", description: "d" })).toBe("skill-name");
    expect(firstErrorId({ actions: {} })).toBeNull();
  });
});

describe("Publish", () => {
  it("is blocked with no version, and with unsaved edits", () => {
    expect(publishBlock({ current_version: null }, false)).toBe("Save a version first.");
    expect(publishBlock({ current_version: 2 }, true)).toContain("Save a version first.");
    expect(publishBlock({ current_version: 2 }, false)).toBeNull();
  });
});

describe("a changed list of actions", () => {
  it("drops the picker error and every row error", () => {
    const out = withoutRowErrors({ actions: { 0: "a" }, picker: "p", name: "n" });
    expect(out).toEqual({ actions: {}, picker: undefined, name: "n" });
  });
});

describe("a double submit", () => {
  it("runs one send, and the second run sends nothing", async () => {
    const guard = createSubmitGuard();
    let release: () => void = () => {};
    const send = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          release = () => resolve("sent");
        }),
    );
    const first = guard.run(send);
    const second = guard.run(send);
    expect(await second).toBeUndefined();
    release();
    expect(await first).toBe("sent");
    expect(send).toHaveBeenCalledTimes(1);
    expect(guard.busy).toBe(false);
  });

  it("sends one POST to create on a double click", async () => {
    const urls: string[] = [];
    const fetchMock = vi.fn(async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify({ skill: { id: "s1" }, version: null }), { status: 201 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const guard = createSubmitGuard();
    const click = () => guard.run(() => createSkill(toCreate(FORM)));
    await Promise.all([click(), click()]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(urls).toEqual(["/api/studio/skills"]);
  });

  it("lets a later submit through once the first answered", async () => {
    const guard = createSubmitGuard();
    const send = vi.fn(async () => "ok");
    await guard.run(send);
    await guard.run(send);
    expect(send).toHaveBeenCalledTimes(2);
  });
});
