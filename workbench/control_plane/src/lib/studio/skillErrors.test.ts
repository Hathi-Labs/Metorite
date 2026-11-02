// Where each error of the skill routes shows (STU-F2b). One case per spelling
// that F2a has. Mutation: map `$.actions[2]` to row 0, and the row test fails.
import { describe, expect, it } from "vitest";

import { NAME_TAKEN, placeOfDetail, placeOfFailure } from "./skillErrors";

describe("placeOfDetail", () => {
  it("maps $.actions[i] to row i, with the text after the colon", () => {
    expect(placeOfDetail("$.actions[2]: the catalog serves no action 'x'")).toEqual({
      place: "action", index: 2, text: "The catalog serves no action 'x'",
    });
    expect(placeOfDetail("$.actions[11]: the action 'a' is in the list twice")).toMatchObject({
      place: "action", index: 11,
    });
  });

  it("maps $.actions to the picker", () => {
    expect(placeOfDetail("$.actions: a skill holds at most 35 actions")).toEqual({
      place: "picker", text: "A skill holds at most 35 actions",
    });
  });

  it("maps body.description, body.instructions and body.actions", () => {
    expect(placeOfDetail("body.description must be a non-empty string").place).toBe("description");
    expect(placeOfDetail("body.instructions must be a non-empty string").place).toBe("instructions");
    expect(placeOfDetail("body.actions must be a list of non-empty strings").place).toBe("picker");
  });

  it("maps the store texts of name, slug and description to their fields", () => {
    expect(placeOfDetail("name must not be empty").place).toBe("name");
    expect(placeOfDetail("name holds at most 120 characters").place).toBe("name");
    expect(placeOfDetail("slug holds at most 80 characters").place).toBe("name");
    expect(placeOfDetail("description holds at most 1024 characters").place).toBe("description");
    expect(placeOfDetail("The name gives no slug. Send a slug of letters and digits.").place).toBe("name");
  });

  it("maps a pydantic miss by its first word", () => {
    expect(placeOfDetail("name: Field required").place).toBe("name");
    expect(placeOfDetail("description: Input should be a valid string").place).toBe("description");
  });

  it("shows a text with no known prefix whole, under the form", () => {
    const text = "The body nests deeper than 20 levels. Flatten it.";
    expect(placeOfDetail(text)).toEqual({ place: "form", text });
    expect(placeOfDetail("something odd").place).toBe("form");
    expect(placeOfDetail("body: Field required").place).toBe("form");
  });
});

describe("placeOfFailure", () => {
  it("puts a 409 of create at the name field, with the text of the store", () => {
    expect(placeOfFailure("create", 409, NAME_TAKEN)).toEqual({ place: "name", text: NAME_TAKEN });
  });

  it("puts a 409 of a move under the buttons", () => {
    expect(placeOfFailure("move", 409, "Add a version first")).toEqual({ place: "move", text: "Add a version first" });
  });

  it("maps a 422 of a publish to its action row", () => {
    expect(placeOfFailure("move", 422, "$.actions[1]: the catalog serves no action 'w'")).toMatchObject({
      place: "action", index: 1,
    });
  });

  it("shows any other failure under the form or the buttons", () => {
    expect(placeOfFailure("version", 502, "boom")).toEqual({ place: "form", text: "boom" });
    expect(placeOfFailure("move", 502, "boom")).toEqual({ place: "move", text: "boom" });
  });
});
