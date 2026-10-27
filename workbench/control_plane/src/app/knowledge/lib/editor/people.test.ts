/**
 * The person mention (WS-61 KB-2k1): the row builder, the link and the chip
 * matcher. The browser half is `e2e/knowledge-shortcuts.spec.ts`.
 */
import { describe, expect, it } from "vitest";

import { personItems } from "./mention";
import { chipPageId } from "./pageLinks";
import { MENTION_PEOPLE_MAX, peopleChoices, personChipId, personHref, personLinkText } from "./people";

const ID = "5b1d7e2a-3c4f-4a8b-9d6e-0f1a2b3c4d5e";
const row = (n: number, name = `Asha ${n}`) => ({
  id: `5b1d7e2a-3c4f-4a8b-9d6e-0f1a2b3c4d${String(n).padStart(2, "0")}`,
  name,
  email: `asha${n}@example.com`,
});

describe("the People rows", () => {
  it("shows at most five people, with the name and the email", () => {
    const rows = Array.from({ length: 9 }, (_, i) => row(i));
    const items = personItems(rows, "asha");
    expect(MENTION_PEOPLE_MAX).toBe(5);
    expect(items).toHaveLength(5);
    expect(items[0]).toMatchObject({ kind: "person", section: "People", label: "Asha 0", hint: "asha0@example.com" });
  });

  it("gives no row when the directory read failed or was refused", () => {
    expect(personItems(null, "as")).toEqual([]);
    expect(personItems(undefined, "as")).toEqual([]);
    expect(peopleChoices(null, "as")).toEqual([]);
  });

  it("keeps a row only when each typed word is in the name or the email", () => {
    const rows = [row(1, "Asha Rao"), row(2, "Ben Cole")];
    expect(peopleChoices(rows, "as ra").map((p) => p.name)).toEqual(["Asha Rao"]);
    expect(peopleChoices(rows, "ben").map((p) => p.name)).toEqual(["Ben Cole"]);
    expect(peopleChoices(rows, "zzz")).toEqual([]);
  });

  it("skips a row with no id or no name", () => {
    expect(peopleChoices([{ id: "", name: "X" }, { id: ID, name: "" }], "")).toEqual([]);
  });
});

describe("the person link", () => {
  it("writes the address and the text of a mention", () => {
    expect(personHref(ID)).toBe(`/people/${ID}`);
    expect(personLinkText("  Asha Rao ")).toBe("@Asha Rao");
  });
});

describe("the person chip matcher", () => {
  it("matches /people/<uuid> and nothing else", () => {
    expect(personChipId(`/people/${ID}`)).toBe(ID);
    expect(personChipId(`/people/${ID}#top`)).toBe(ID);
    expect(personChipId(`/people/${ID.toUpperCase()}`)).toBe(ID);
    expect(personChipId("/people/me")).toBeNull();
    expect(personChipId("/people/")).toBeNull();
    expect(personChipId(`/people/${ID}/skills`)).toBeNull();
    expect(personChipId(`https://x.test/people/${ID}`)).toBeNull();
    expect(personChipId(`/knowledge/pages/${ID}`)).toBeNull();
    expect(personChipId(null)).toBeNull();
  });

  it("is not a page link, and a page link is not a person chip", () => {
    expect(chipPageId(`/people/${ID}`)).toBeNull();
    expect(personChipId(`/knowledge/pages/${ID}`)).toBeNull();
  });
});
