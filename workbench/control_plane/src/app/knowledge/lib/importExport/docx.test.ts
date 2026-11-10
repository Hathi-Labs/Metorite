import { deflateRawSync } from "node:zlib";
import { strToU8, zipSync } from "fflate";
import { describe, expect, it, vi } from "vitest";

import { DocumentRefusal } from "./ooxml";

const convertToHtml = vi.fn();
vi.mock("mammoth", async (orig) => {
  const real = await orig<typeof import("mammoth")>();
  const mod = { ...real, convertToHtml: (...a: unknown[]) => (convertToHtml as (...x: unknown[]) => unknown)(...a) };
  return { ...mod, default: mod };
});

import { docxToHtml, isDocxPart } from "./docx";

/** One stored-name zip with a deflate stream of `real` zero bytes whose headers declare `declared`. */
function bombZip(name: string, real: number, declared: number): Uint8Array {
  const raw = Buffer.alloc(real);
  const comp = deflateRawSync(raw, { level: 9 });
  const n = Buffer.from(name);
  const lh = Buffer.alloc(30);
  lh.writeUInt32LE(0x04034b50, 0);
  lh.writeUInt16LE(20, 4);
  lh.writeUInt16LE(8, 8);
  lh.writeUInt32LE(comp.length, 18);
  lh.writeUInt32LE(declared, 22);
  lh.writeUInt16LE(n.length, 26);
  const body = Buffer.concat([lh, n, comp]);
  const cd = Buffer.alloc(46);
  cd.writeUInt32LE(0x02014b50, 0);
  cd.writeUInt16LE(20, 4);
  cd.writeUInt16LE(20, 6);
  cd.writeUInt16LE(8, 10);
  cd.writeUInt32LE(comp.length, 20);
  cd.writeUInt32LE(declared, 24);
  cd.writeUInt16LE(n.length, 28);
  const cdAll = Buffer.concat([cd, n]);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(1, 8);
  end.writeUInt16LE(1, 10);
  end.writeUInt32LE(cdAll.length, 12);
  end.writeUInt32LE(body.length, 16);
  return new Uint8Array(Buffer.concat([body, cdAll, end]));
}

describe("docxToHtml", () => {
  it("refuses a part that declares 100 bytes and inflates to 50 MB, and never calls mammoth", async () => {
    convertToHtml.mockReset();
    const file = bombZip("word/document.xml", 50 * 1024 * 1024, 100);
    expect(file.length).toBeLessThan(100 * 1024);
    await expect(docxToHtml(file)).rejects.toThrow(DocumentRefusal);
    await expect(docxToHtml(file)).rejects.toThrow(/damaged/);
    expect(convertToHtml).not.toHaveBeenCalled();
  });

  it("hands mammoth a rebuilt zip of the wanted parts only, with no picture", async () => {
    convertToHtml.mockReset();
    convertToHtml.mockResolvedValue({ value: "<p>x</p>" });
    const zip = zipSync({
      "[Content_Types].xml": strToU8("<Types/>"),
      "word/document.xml": strToU8("<d/>"),
      "word/media/image1.png": new Uint8Array(2000).fill(7),
    });
    await docxToHtml(zip);
    const input = convertToHtml.mock.calls[0][0] as { arrayBuffer: ArrayBuffer };
    expect(input.arrayBuffer.byteLength).not.toBe(zip.length);
    const { unzipSync } = await import("fflate");
    expect(Object.keys(unzipSync(new Uint8Array(input.arrayBuffer))).sort()).toEqual(["[Content_Types].xml", "word/document.xml"]);
  });

  it("chooses the parts mammoth needs", () => {
    for (const n of ["word/document.xml", "word/_rels/document.xml.rels", "word/styles.xml", "_rels/.rels", "docProps/core.xml"]) {
      expect(isDocxPart(n)).toBe(true);
    }
    expect(isDocxPart("word/media/image1.png")).toBe(false);
  });
});
