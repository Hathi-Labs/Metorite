import { describe, expect, it } from "vitest";

import { curlyQuotes, memberLine } from "./receiptText";

describe("receiptText", () => {
  it("shows guillemets as curly quotes", () => {
    expect(curlyQuotes("Saved: «hi»")).toBe("Saved: “hi”");
  });

  it("a folded Tasks line has no tag and no id", () => {
    const line = "[next·LOCAL] «Call Raj» · due Fri · id=ab12cd";
    expect(memberLine(line)).toBe("“Call Raj” · due Fri");
  });

  it("drops an id in brackets", () => {
    expect(memberLine("• Fri Call Raj (id: 9f2)")).toBe("• Fri Call Raj");
  });
});
