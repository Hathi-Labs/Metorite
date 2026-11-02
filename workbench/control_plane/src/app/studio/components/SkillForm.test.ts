// The rules of `SkillForm` (STU-F2b), through `lib/studio/skillForm.ts` and
// the client, because the unit tests run in Node with no DOM.
// Mutations: let the picker keep a `package` row, send a second create on a
// double submit, or map a 422 prefix to the wrong field, and a test fails.
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MAX_ACTIONS,
  createSubmitGuard,
  errorsOf,
  movesOf,
  pickableActions,
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
    expect(out.description).toBe("Use this when triage my inbox");
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
      2: "The catalog serves no action 'x'",
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
    const fetchMock = vi.fn(
      async (..._args: unknown[]) =>
        new Response(JSON.stringify({ skill: { id: "s1" }, version: null }), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const guard = createSubmitGuard();
    const click = () => guard.run(() => createSkill(toCreate(FORM)));
    await Promise.all([click(), click()]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/studio/skills");
  });

  it("lets a later submit through once the first answered", async () => {
    const guard = createSubmitGuard();
    const send = vi.fn(async () => "ok");
    await guard.run(send);
    await guard.run(send);
    expect(send).toHaveBeenCalledTimes(2);
  });
});
