// ── Zip read and write (WS-61 KB-2m) ─────────────────────────────────────────
//
// `fflate` loads on first use, with a dynamic import, so the first paint of
// Knowledge pays nothing for it.
//
// ⚠️ A zip header can lie about the size of an entry (a zip bomb). The reader
// therefore works in two passes. The first pass lists the entries and inflates
// none. `checkDeclared` refuses over a limit. The second pass inflates only
// the page files, and the real size is checked again after it.

import {
  ImportRefusal,
  MAX_ENTRIES,
  MAX_FILE_BYTES,
  MAX_TOTAL_BYTES,
  checkDeclared,
  mb,
  safeEntryPath,
} from "./limits";
import { baseName, isDocumentKind, pageKind } from "./names";

/** One page file read from a zip or from the disk. */
export interface RawEntry {
  /** The path in the zip, or the file name for a loose file. */
  path: string;
  /** The text of a Markdown or HTML file. Empty for a document. */
  text: string;
  /** The bytes of a Word, PDF, PowerPoint or Excel file (KB-AI-1). */
  bytes?: Uint8Array;
}

export interface ZipRead {
  entries: RawEntry[];
  /** The files that are not pages (images, PDFs and so on). */
  skipped: string[];
  /** The bytes of the page files, once read. */
  bytes: number;
}

/** Entries that carry no content of the member's. */
function isNoise(path: string): boolean {
  return path.startsWith("__MACOSX/") || baseName(path) === ".DS_Store" || baseName(path).startsWith("._");
}

/** Read the page files of a zip. Refuses over a limit and reads nothing then. */
export async function readZip(data: Uint8Array, alreadyBytes = 0): Promise<ZipRead> {
  const { unzipSync, strFromU8 } = await import("fflate");
  const declared: Array<{ name: string; size: number }> = [];
  try {
    // Pass 1: the central directory only. The filter says no to each entry.
    unzipSync(data, {
      filter: (file) => {
        declared.push({ name: file.name, size: file.originalSize });
        if (declared.length > MAX_ENTRIES) {
          throw new ImportRefusal(`The zip has more than ${MAX_ENTRIES} entries. The limit is ${MAX_ENTRIES}. Nothing was imported.`);
        }
        return false;
      },
    });
  } catch (err) {
    if (err instanceof ImportRefusal) throw err;
    throw new ImportRefusal("This file is not a zip that can be read. Nothing was imported.");
  }
  checkDeclared(declared, alreadyBytes);

  const wanted = new Set<string>();
  const skipped: string[] = [];
  for (const { name } of declared) {
    const path = safeEntryPath(name);
    if (path.endsWith("/") || isNoise(path)) continue;
    if (pageKind(path)) wanted.add(name);
    else skipped.push(path);
  }

  let files: Record<string, Uint8Array>;
  try {
    // Pass 2: inflate the page files only.
    files = unzipSync(data, { filter: (file) => wanted.has(file.name) });
  } catch {
    throw new ImportRefusal("This zip is damaged and cannot be read. Nothing was imported.");
  }
  const entries: RawEntry[] = [];
  let bytes = 0;
  for (const [name, content] of Object.entries(files)) {
    if (content.length > MAX_FILE_BYTES) {
      throw new ImportRefusal(
        `"${baseName(name)}" is ${mb(content.length)} when it is read. The limit for one file is ${mb(MAX_FILE_BYTES)}. Nothing was imported.`,
      );
    }
    bytes += content.length;
    if (alreadyBytes + bytes > MAX_TOTAL_BYTES) {
      throw new ImportRefusal(
        `The files are over ${mb(MAX_TOTAL_BYTES)} when they are read. The limit for one import is ${mb(MAX_TOTAL_BYTES)}. Nothing was imported.`,
      );
    }
    const path = safeEntryPath(name);
    entries.push(isDocumentKind(pageKind(path)) ? { path, text: "", bytes: content } : { path, text: strFromU8(content) });
  }
  return { entries, skipped, bytes };
}

/** One file to write into a zip. */
export interface ZipFileSpec {
  path: string;
  text: string;
}

/** A zip of text files. */
export async function writeZip(files: readonly ZipFileSpec[]): Promise<Uint8Array> {
  const { zipSync, strToU8 } = await import("fflate");
  const tree: Record<string, Uint8Array> = {};
  for (const file of files) tree[file.path] = strToU8(file.text);
  return zipSync(tree, { level: 6 });
}
