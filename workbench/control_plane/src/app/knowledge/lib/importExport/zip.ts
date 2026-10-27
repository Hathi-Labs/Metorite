// ── Zip read and write (WS-61 KB-2m) ─────────────────────────────────────────
//
// `fflate` loads on first use, with a dynamic import, so the first paint of
// Knowledge pays nothing for it.
//
// ⚠️ A zip header can lie about the size of an entry (a zip bomb). The reader
// therefore works in two passes. The first pass lists the entries and inflates
// none. `checkDeclared` refuses over a limit. The second pass streams only the
// page files through `Unzip`, which ignores the declared size and stops at the
// file limit, so memory stays bounded. An entry whose real size is not the
// declared size is refused by name, and never cut to the declared size (K-6).

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
import type { Unreadable } from "./plan";

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
  /** Entries refused because the header declares a size that is not the real size (K-6). */
  refused: Unreadable[];
}

/** Entries that carry no content of the member's. */
function isNoise(path: string): boolean {
  return path.startsWith("__MACOSX/") || baseName(path) === ".DS_Store" || baseName(path).startsWith("._");
}

/** Read the page files of a zip. Refuses over a limit and reads nothing then. */
export async function readZip(data: Uint8Array, alreadyBytes = 0): Promise<ZipRead> {
  const { Unzip, UnzipInflate, UnzipPassThrough, unzipSync, strFromU8 } = await import("fflate");
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

  const sizeOf = new Map(declared.map((d) => [d.name, d.size]));
  const entries: RawEntry[] = [];
  const refused: Unreadable[] = [];
  let bytes = 0;
  let overFile: string | null = null;
  try {
    // Pass 2: stream the page files only. A streamed entry is not cut to its declared size.
    const unzipper = new Unzip();
    unzipper.register(UnzipInflate);
    unzipper.register(UnzipPassThrough);
    unzipper.onfile = (file) => {
      if (!wanted.has(file.name)) return;
      const chunks: Uint8Array[] = [];
      let real = 0;
      file.ondata = (err, chunk, final) => {
        if (err) throw err;
        if (overFile !== null) return;
        real += chunk.length;
        if (real > MAX_FILE_BYTES) {
          overFile = file.name;
          file.terminate();
          return;
        }
        chunks.push(chunk);
        if (!final) return;
        const path = safeEntryPath(file.name);
        if (real !== sizeOf.get(file.name)) {
          refused.push({
            file: path,
            reason: `"${baseName(path)}" declares ${sizeOf.get(file.name)} bytes and holds ${real}. It was refused and not cut.`,
          });
          return;
        }
        const content = new Uint8Array(real);
        let at = 0;
        for (const c of chunks) {
          content.set(c, at);
          at += c.length;
        }
        bytes += real;
        if (alreadyBytes + bytes > MAX_TOTAL_BYTES) {
          overFile = file.name;
          return;
        }
        entries.push(isDocumentKind(pageKind(path)) ? { path, text: "", bytes: content } : { path, text: strFromU8(content) });
      };
      file.start();
    };
    unzipper.push(data, true);
  } catch (err) {
    if (err instanceof ImportRefusal) throw err;
    throw new ImportRefusal("This zip is damaged and cannot be read. Nothing was imported.");
  }
  if (overFile !== null) {
    if (alreadyBytes + bytes > MAX_TOTAL_BYTES) {
      throw new ImportRefusal(
        `The files are over ${mb(MAX_TOTAL_BYTES)} when they are read. The limit for one import is ${mb(MAX_TOTAL_BYTES)}. Nothing was imported.`,
      );
    }
    throw new ImportRefusal(
      `"${baseName(overFile)}" is over ${mb(MAX_FILE_BYTES)} when it is read. The limit for one file is ${mb(MAX_FILE_BYTES)}. Nothing was imported.`,
    );
  }
  return { entries, skipped, bytes, refused };
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
