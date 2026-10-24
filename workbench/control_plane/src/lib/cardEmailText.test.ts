import { describe, expect, it } from "vitest";

import { splitCardEmailText } from "./cardEmailText";

describe("splitCardEmailText (owner report, 2026-10-24)", () => {
  it("splits the held draft card into the subject row and the mail", () => {
    const text = "Subject: RE: Request for Meeting\n\nThe text of the draft:\nDear Dr Sagar,\n\nThank you.\n\nBest regards,\nVijay";
    expect(splitCardEmailText(text)).toEqual({
      head: [{ key: "Subject", value: "RE: Request for Meeting" }],
      body: "Dear Dr Sagar,\n\nThank you.\n\nBest regards,\nVijay",
    });
  });

  it("splits the new-mail card, whose marker is 'The text:'", () => {
    expect(splitCardEmailText("Subject: Brochures\n\nThe text:\nHi Kiran,")?.body).toBe("Hi Kiran,");
  });

  it("keeps every character of the mail, a colon line too", () => {
    const body = "Note: the price is final.\n  Indented line\n\n";
    expect(splitCardEmailText(`Subject: X\n\nThe text:\n${body}`)?.body).toBe(body);
  });

  it("gives null for a text that does not have the shape, so it draws whole", () => {
    expect(splitCardEmailText("Hi Kiran, the text: is below")).toBeNull();
    expect(splitCardEmailText("A free line\n\nThe text:\nbody")).toBeNull();
    expect(splitCardEmailText(undefined)).toBeNull();
  });
});
