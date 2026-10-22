// ── Collect the files of an import (WS-61 KB-2m) ─────────────────────────────
//
// Pure apart from the reads. A loose page file and each page file in a zip
// become one `RawEntry`. A file of any other kind is counted as skipped.

import { ImportRefusal, MAX_FILE_BYTES, MAX_TOTAL_BYTES, mb } from "./limits";
import { pageKind } from "./names";
import { readZip, type RawEntry } from "./zip";

/** What the dialog hands over for each chosen file. */
export interface SourceFile {
  name: string;
  size: number;
  read: () => Promise<Uint8Array>;
}

export interface Collected {
  entries: RawEntry[];
  skipped: string[];
}

/** The most bytes a zip file may have on the disk. */
const MAX_ZIP_BYTES = MAX_TOTAL_BYTES;

/** Read every chosen file. Refuses over a limit and reads nothing more. */
export async function collectSources(files: readonly SourceFile[]): Promise<Collected> {
  const entries: RawEntry[] = [];
  const skipped: string[] = [];
  let bytes = 0;
  for (const file of files) {
    const lower = file.name.toLowerCase();
    if (lower.endsWith(".zip")) {
      if (file.size > MAX_ZIP_BYTES) {
        throw new ImportRefusal(`"${file.name}" is ${mb(file.size)}. The limit is ${mb(MAX_ZIP_BYTES)}. Nothing was imported.`);
      }
      const zip = await readZip(await file.read(), bytes);
      entries.push(...zip.entries);
      skipped.push(...zip.skipped);
      bytes += zip.bytes;
    } else if (pageKind(lower)) {
      if (file.size > MAX_FILE_BYTES) {
        throw new ImportRefusal(
          `"${file.name}" is ${mb(file.size)}. The limit for one file is ${mb(MAX_FILE_BYTES)}. Nothing was imported.`,
        );
      }
      bytes += file.size;
      if (bytes > MAX_TOTAL_BYTES) {
        throw new ImportRefusal(`The files are over ${mb(MAX_TOTAL_BYTES)}. Nothing was imported.`);
      }
      entries.push({ path: file.name, text: new TextDecoder("utf-8").decode(await file.read()) });
    } else {
      skipped.push(file.name);
    }
  }
  return { entries, skipped };
}
