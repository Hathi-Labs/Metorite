// ── From the chosen files to a plan (WS-61 KB-2m) ────────────────────────────
//
// The one entry point of the import dialog. It reads the files, reads each
// page file as a draft, and builds the plan. A refusal throws `ImportRefusal`
// before anything is created.

import { collectSources, type SourceFile } from "./collect";
import { convertDocument } from "./documents";
import { cleanTitle, isDocumentKind, pageKind, titleFromPath } from "./names";
import { buildPlan, toDraft, type HtmlConverter, type ImportPlan, type PageDraft, type Unreadable } from "./plan";

/** The words for a file that failed in a way nobody planned for. */
function reasonOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : "The file could not be read.";
}

/**
 * Read the chosen files into a plan. A Word, PDF, PowerPoint or Excel file is
 * parsed here (KB-AI-1). One that fails is reported by name and does not stop
 * the others. Only a refusal over a limit (`ImportRefusal`) stops the run.
 */
export async function prepareImport(files: readonly SourceFile[], html: HtmlConverter): Promise<ImportPlan> {
  const { entries, skipped } = await collectSources(files);
  const drafts: PageDraft[] = [];
  const unreadable: Unreadable[] = [];
  const notices = new Set<string>();
  for (const entry of entries) {
    const kind = pageKind(entry.path);
    if (!isDocumentKind(kind)) {
      drafts.push(toDraft(entry, html));
      continue;
    }
    try {
      const doc = await convertDocument(kind, entry.bytes ?? new Uint8Array(), html);
      for (const note of doc.notices) notices.add(note);
      drafts.push({
        path: entry.path,
        title: doc.title?.trim() ? cleanTitle(doc.title) : titleFromPath(entry.path),
        body: doc.markdown,
        images: doc.images,
      });
    } catch (err) {
      unreadable.push({ file: entry.path, reason: reasonOf(err) });
    }
  }
  return buildPlan(drafts, skipped, { unreadable, notices: [...notices] });
}

/** A browser `File` as a source file. */
export function sourceOf(file: File): SourceFile {
  return { name: file.name, size: file.size, read: async () => new Uint8Array(await file.arrayBuffer()) };
}
