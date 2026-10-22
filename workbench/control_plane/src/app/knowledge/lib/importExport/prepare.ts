// ── From the chosen files to a plan (WS-61 KB-2m) ────────────────────────────
//
// The one entry point of the import dialog. It reads the files, reads each
// page file as a draft, and builds the plan. A refusal throws `ImportRefusal`
// before anything is created.

import { collectSources, type SourceFile } from "./collect";
import { buildPlan, toDraft, type HtmlConverter, type ImportPlan } from "./plan";

export async function prepareImport(files: readonly SourceFile[], html: HtmlConverter): Promise<ImportPlan> {
  const { entries, skipped } = await collectSources(files);
  return buildPlan(entries.map((entry) => toDraft(entry, html)), skipped);
}

/** A browser `File` as a source file. */
export function sourceOf(file: File): SourceFile {
  return { name: file.name, size: file.size, read: async () => new Uint8Array(await file.arrayBuffer()) };
}
