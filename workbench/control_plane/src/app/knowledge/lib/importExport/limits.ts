// ── The limits of an import (WS-61 KB-2m, `knowledge_base.md` §21.7) ─────────
//
// Pure. An import reads files that the member picked, and a zip can lie about
// its size or name a path outside the folder. Each limit is a constant here,
// and `limits.test.ts` holds each one. Over a limit the import refuses with a
// clear message and creates nothing.

/** The most bytes in one file, or in one zip entry once it is read. */
export const MAX_FILE_BYTES = 25 * 1024 * 1024;
/** The most bytes in one import, counted uncompressed. */
export const MAX_TOTAL_BYTES = 100 * 1024 * 1024;
/** The most entries in one zip, folders and skipped files included. */
export const MAX_ENTRIES = 500;
/** The most pages that one import makes, folder pages included. */
export const MAX_PAGES = 300;
/** The deepest nesting: a page at the top is at depth 1. */
export const MAX_DEPTH = 12;
/** The most PDF pages that one file gives. Pages after it are not read. */
export const MAX_PDF_PAGES = 300;
/** The most slides that one deck gives. */
export const MAX_SLIDES = 500;
/** The most rows and columns that one sheet gives. The rest are counted, not read. */
export const MAX_SHEET_ROWS = 200;
export const MAX_SHEET_COLS = 30;
/** The most parts in an Office file (a zip), and the deepest XML nesting. */
export const MAX_OFFICE_PARTS = 5000;
export const MAX_XML_DEPTH = 200;

/** The longest title that the New page form takes. */
export const MAX_TITLE = 300;

/** A refusal with words for the member. The import creates nothing. */
export class ImportRefusal extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ImportRefusal";
  }
}

/** Megabytes, for a message. */
export function mb(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

/**
 * The safe form of an entry path, with `/` between the parts and no `./`.
 * It refuses a NUL, an absolute path, a drive letter and a `..` part. A
 * trailing `/` (a folder entry) stays.
 */
export function safeEntryPath(raw: string): string {
  if (raw.includes("\0")) throw new ImportRefusal(`The zip has an entry with a NUL in its path. Nothing was imported.`);
  const path = raw.replace(/\\/g, "/");
  if (path.startsWith("/") || /^[A-Za-z]:/.test(path)) {
    throw new ImportRefusal(`The zip has an entry with an absolute path ("${shown(raw)}"). Nothing was imported.`);
  }
  const parts = path.split("/");
  if (parts.some((part) => part === "..")) {
    throw new ImportRefusal(`The zip has an entry that leaves its folder ("${shown(raw)}"). Nothing was imported.`);
  }
  const kept = parts.filter((part, i) => part !== "." && (part !== "" || i === parts.length - 1));
  return kept.join("/");
}

function shown(text: string): string {
  return text.length > 60 ? `${text.slice(0, 57)}...` : text;
}

/** What the zip reader knows about an entry before it inflates it. */
export interface DeclaredEntry {
  name: string;
  /** The size once inflated, as the zip header declares it. */
  size: number;
}

/**
 * Check the declared entries of a zip against the limits, before anything is
 * inflated. A header can lie, so the reader checks the real size too.
 */
export function checkDeclared(entries: readonly DeclaredEntry[], alreadyBytes = 0): void {
  if (entries.length > MAX_ENTRIES) {
    throw new ImportRefusal(
      `The zip has ${entries.length} entries. The limit is ${MAX_ENTRIES}. Nothing was imported.`,
    );
  }
  let total = alreadyBytes;
  for (const entry of entries) {
    safeEntryPath(entry.name);
    if (entry.size > MAX_FILE_BYTES) {
      throw new ImportRefusal(
        `"${shown(entry.name)}" is ${mb(entry.size)} when it is read. The limit for one file is ${mb(MAX_FILE_BYTES)}. Nothing was imported.`,
      );
    }
    total += entry.size;
  }
  if (total > MAX_TOTAL_BYTES) {
    throw new ImportRefusal(
      `The files are ${mb(total)} when they are read. The limit for one import is ${mb(MAX_TOTAL_BYTES)}. Nothing was imported.`,
    );
  }
}
