import { zipSync, strToU8 } from "fflate";
import { describe, expect, it } from "vitest";

import { MAX_FILE_BYTES, MAX_OFFICE_PARTS } from "./limits";
import { DocumentRefusal, checkParts, listParts, readParts } from "./ooxml";

describe("office parts", () => {
  it("refuses a part over the file limit and too many parts", () => {
    expect(() => checkParts([{ name: "a", size: MAX_FILE_BYTES + 1 }])).toThrow(DocumentRefusal);
    expect(() => checkParts(Array.from({ length: MAX_OFFICE_PARTS + 1 }, (_, i) => ({ name: `p${i}`, size: 1 })))).toThrow(DocumentRefusal);
    expect(() => checkParts([{ name: "a", size: 10 }])).not.toThrow();
  });

  it("reads only the wanted parts, as text", async () => {
    const zip = zipSync({ "a.xml": strToU8("<a/>"), "b.bin": new Uint8Array([1, 2, 3]) });
    expect((await listParts(zip)).map((p) => p.name).sort()).toEqual(["a.xml", "b.bin"]);
    const parts = await readParts(zip, (n) => n.endsWith(".xml"));
    expect([...parts.entries()]).toEqual([["a.xml", "<a/>"]]);
  });

  it("refuses bytes that are not a zip", async () => {
    await expect(listParts(new Uint8Array([1, 2, 3, 4]))).rejects.toThrow(DocumentRefusal);
  });
});
