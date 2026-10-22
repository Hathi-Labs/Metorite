import { describe, expect, it } from "vitest";

import { collectSources, type SourceFile } from "./collect";
import { ImportRefusal, MAX_ENTRIES, MAX_FILE_BYTES } from "./limits";
import { readZip, writeZip } from "./zip";

async function zipOf(files: Record<string, string | Uint8Array>): Promise<Uint8Array> {
  const { zipSync, strToU8 } = await import("fflate");
  const tree: Record<string, Uint8Array> = {};
  for (const [name, content] of Object.entries(files)) tree[name] = typeof content === "string" ? strToU8(content) : content;
  return zipSync(tree);
}

/** A zip whose headers declare `fake` bytes for every entry. */
function forgeSize(zip: Uint8Array, fake: number): Uint8Array {
  const out = zip.slice();
  const view = new DataView(out.buffer);
  for (let i = 0; i + 4 < out.length; i++) {
    const sig = view.getUint32(i, true);
    if (sig === 0x02014b50) view.setUint32(i + 24, fake, true); // central: uncompressed size
    if (sig === 0x04034b50) view.setUint32(i + 22, fake, true); // local: uncompressed size
  }
  return out;
}

describe("readZip", () => {
  it("reads page files, skips the rest, and ignores Mac noise", async () => {
    const zip = await zipOf({
      "a/one.md": "# One",
      "a/two.html": "<p>two</p>",
      "a/pic.png": new Uint8Array([1, 2, 3]),
      "__MACOSX/a/._one.md": "junk",
      ".DS_Store": "x",
    });
    const out = await readZip(zip);
    expect(out.entries.map((e) => e.path).sort()).toEqual(["a/one.md", "a/two.html"]);
    expect(out.entries.find((e) => e.path === "a/one.md")?.text).toBe("# One");
    expect(out.skipped).toEqual(["a/pic.png"]);
  });
  it("refuses an entry with a path that leaves the folder", async () => {
    const zip = await zipOf({ "../evil.md": "x" });
    await expect(readZip(zip)).rejects.toThrow(/leaves its folder/);
  });
  it("refuses an absolute path", async () => {
    await expect(readZip(await zipOf({ "/abs.md": "x" }))).rejects.toThrow(/absolute/);
  });
  it("refuses more than 500 entries before it inflates", async () => {
    const files: Record<string, string> = {};
    for (let i = 0; i <= MAX_ENTRIES; i++) files[`f${i}.md`] = "x";
    await expect(readZip(await zipOf(files))).rejects.toThrow(/more than 500 entries/);
  });
  it("refuses an entry that declares more than the file limit", async () => {
    const zip = forgeSize(await zipOf({ "bomb.md": "tiny" }), MAX_FILE_BYTES + 1);
    await expect(readZip(zip)).rejects.toThrow(/limit for one file/);
  });
  it("cannot inflate more than the header declares", async () => {
    const zip = forgeSize(await zipOf({ "lie.md": "x".repeat(5000) }), 10);
    const out = await readZip(zip);
    expect(out.entries[0].text.length).toBeLessThanOrEqual(10);
  });
  it("refuses a real entry that is over the file limit", async () => {
    const big = new Uint8Array(MAX_FILE_BYTES + 1).fill(97);
    await expect(readZip(await zipOf({ "big.md": big }))).rejects.toThrow(/limit for one file/);
  });
  it("refuses a zip that is over the total limit", async () => {
    const chunk = new Uint8Array(20 * 1024 * 1024).fill(97);
    const files: Record<string, Uint8Array> = {};
    for (let i = 0; i < 6; i++) files[`f${i}.md`] = chunk;
    await expect(readZip(await zipOf(files))).rejects.toThrow(/limit for one import/);
  });
  it("refuses a file that is not a zip", async () => {
    await expect(readZip(new Uint8Array([1, 2, 3, 4]))).rejects.toBeInstanceOf(ImportRefusal);
  });
});

describe("writeZip", () => {
  it("writes files that readZip reads, with names that are not ASCII", async () => {
    const zip = await writeZip([
      { path: "Café/Menu.md", text: "# Menu\n" },
      { path: "Café.md", text: "# Café\n" },
    ]);
    const out = await readZip(zip);
    expect(out.entries.map((e) => e.path).sort()).toEqual(["Café.md", "Café/Menu.md"]);
  });
});

function loose(name: string, size: number, text = "x"): SourceFile {
  return { name, size, read: async () => new TextEncoder().encode(text) };
}

describe("collectSources", () => {
  it("collects loose files and zips together", async () => {
    const zip = await zipOf({ "z/in.md": "in" });
    const out = await collectSources([
      loose("a.md", 1, "# A"),
      loose("b.HTM", 1, "<p>b</p>"),
      { name: "z.zip", size: zip.length, read: async () => zip },
      loose("c.png", 1),
    ]);
    expect(out.entries.map((e) => e.path)).toEqual(["a.md", "b.HTM", "z/in.md"]);
    expect(out.skipped).toEqual(["c.png"]);
  });
  it("refuses a loose file over 25 MB", async () => {
    await expect(collectSources([loose("big.md", MAX_FILE_BYTES + 1)])).rejects.toThrow(/limit for one file/);
  });
  it("refuses when loose files add up over 100 MB", async () => {
    const many = Array.from({ length: 5 }, (_, i) => loose(`f${i}.md`, 24 * 1024 * 1024));
    await expect(collectSources(many)).rejects.toThrow(/over 100 MB/);
  });
});

describe("document files (KB-AI-1)", () => {
  it("carries the bytes of a document in a zip and as a loose file, and skips an image", async () => {
    const bytes = new Uint8Array([80, 75, 3, 4, 9]);
    const zip = await readZip(await zipOf({ "a/report.pdf": bytes, "a/note.md": "# N", "a/pic.png": new Uint8Array([1]) }));
    expect(zip.skipped).toEqual(["a/pic.png"]);
    const pdf = zip.entries.find((e) => e.path === "a/report.pdf");
    expect(pdf?.bytes).toEqual(bytes);
    expect(pdf?.text).toBe("");
    expect(zip.entries.find((e) => e.path === "a/note.md")?.bytes).toBeUndefined();

    const file: SourceFile = { name: "deck.PPTX", size: 5, read: async () => bytes };
    const out = await collectSources([file]);
    expect(out.entries).toEqual([{ path: "deck.PPTX", text: "", bytes }]);
  });
});
