"use client";

/**
 * Import files as pages (WS-61 KB-2m, `knowledge_base.md` §21.7).
 *
 * The member picks Markdown files, HTML files or a zip. The dialog reads
 * them, refuses over a limit with nothing made, and then makes draft pages
 * one by one, parents first, with a count and a Cancel. A final report says
 * what was made, what was skipped and what failed. All logic is in
 * `lib/importExport/`. This file only draws it.
 */

import { useRef, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import ProgressBar from "@/components/ui/ProgressBar";
import { invalidate } from "@/lib/dataCache";

import { KNOWLEDGE_CACHE, createPage, updatePage } from "../lib/api";
import { ImportRefusal, MAX_FILE_BYTES, MAX_PAGES, MAX_TOTAL_BYTES, mb } from "../lib/importExport/limits";
import { prepareImport, sourceOf } from "../lib/importExport/prepare";
import { runImport, type ImportReport } from "../lib/importExport/runImport";

const ACCEPT = ".md,.markdown,.html,.htm,.zip";

type Phase = "pick" | "reading" | "running" | "done";

export interface ImportTarget {
  spaceId: string;
  spaceName: string;
  /** The page to import under, or `null` for the top of the space. */
  parent: { id: string; title: string } | null;
}

function pages(n: number): string {
  return `${n} ${n === 1 ? "page" : "pages"}`;
}

export default function ImportDialog({
  target,
  onClose,
}: {
  /** The open target, or `null` when the dialog is closed. */
  target: ImportTarget | null;
  onClose: () => void;
}) {
  const picker = useRef<HTMLInputElement | null>(null);
  const cancelled = useRef(false);
  const [files, setFiles] = useState<File[]>([]);
  const [phase, setPhase] = useState<Phase>("pick");
  const [refusal, setRefusal] = useState<string | null>(null);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [stopping, setStopping] = useState(false);
  const [report, setReport] = useState<ImportReport | null>(null);

  function reset() {
    setFiles([]);
    setPhase("pick");
    setRefusal(null);
    setProgress({ done: 0, total: 0 });
    setStopping(false);
    setReport(null);
    cancelled.current = false;
  }

  function close() {
    // While pages are being made, Esc and the close button ask to stop.
    if (phase === "running") {
      cancelled.current = true;
      setStopping(true);
      return;
    }
    if (phase === "reading") return;
    reset();
    onClose();
  }

  async function start() {
    if (!target || !files.length) return;
    setRefusal(null);
    setPhase("reading");
    try {
      // HTML needs the editor's schema, so it loads now and not with the page.
      const { htmlToMarkdown } = await import("../lib/importExport/html");
      const plan = await prepareImport(files.map(sourceOf), htmlToMarkdown);
      cancelled.current = false;
      setProgress({ done: 0, total: plan.pages.length });
      setPhase("running");
      const out = await runImport(
        plan,
        { spaceId: target.spaceId, parentId: target.parent?.id ?? null },
        { create: (body) => createPage(body), patch: (id, body) => updatePage(id, body) },
        {
          onProgress: (done, total) => setProgress({ done, total }),
          shouldCancel: () => cancelled.current,
        },
      );
      invalidate(KNOWLEDGE_CACHE);
      setReport(out);
      setPhase("done");
    } catch (err) {
      setRefusal(err instanceof ImportRefusal ? err.message : `The import stopped. ${err instanceof Error ? err.message : String(err)}`);
      setPhase("pick");
    }
  }

  const where = target?.parent ? `under ${target.parent.title}` : `in ${target?.spaceName ?? "the space"}`;

  return (
    <Modal open={target !== null} onClose={close} title="Import pages" icon="Upload" size="md" closeLabel={phase === "running" ? "Stop" : "Close"}>
      {target ? (
        <div className="flex flex-col">
          <div className="space-y-3 p-4 text-sm">
            {phase === "pick" || phase === "reading" ? (
              <>
                <p className="text-muted-foreground">
                  Choose Markdown files, HTML files or a zip, such as a Notion export. Each file becomes a draft page{" "}
                  <span className="font-medium text-foreground">{where}</span>. A folder becomes a parent page.
                </p>
                <input
                  ref={picker}
                  type="file"
                  accept={ACCEPT}
                  multiple
                  className="hidden"
                  aria-label="Files to import"
                  data-testid="kb-import-input"
                  onChange={(e) => {
                    setFiles(Array.from(e.target.files ?? []));
                    setRefusal(null);
                    e.target.value = "";
                  }}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="secondary" size="sm" icon="Upload" disabled={phase === "reading"} onClick={() => picker.current?.click()}>
                    Choose files
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    .md, .html or .zip. Up to {mb(MAX_FILE_BYTES)} each, {mb(MAX_TOTAL_BYTES)} in all, {MAX_PAGES} pages.
                  </span>
                </div>
                {files.length ? (
                  <ul aria-label="Chosen files" className="space-y-1 text-xs">
                    {files.map((f, i) => (
                      <li key={`${f.name}:${i}`} className="flex items-center gap-2">
                        <Icon name="FileText" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="truncate">{f.name}</span>
                        <span className="shrink-0 text-muted-foreground">{Math.max(1, Math.round(f.size / 1024))} KB</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p className="text-xs text-muted-foreground">
                  Images and other files are not imported yet. An image becomes its description in italics.
                </p>
                {refusal ? (
                  <p role="alert" className="text-xs text-destructive">
                    {refusal}
                  </p>
                ) : null}
              </>
            ) : null}

            {phase === "running" ? (
              <div className="space-y-2">
                <ProgressBar
                  label="Import progress"
                  percent={progress.total ? (progress.done / progress.total) * 100 : 0}
                  detail={`${progress.done} of ${progress.total}`}
                />
                <p role="status" className="text-xs text-muted-foreground">
                  {stopping ? "Stopping after this page." : `Making pages ${where}.`}
                </p>
              </div>
            ) : null}

            {phase === "done" && report ? <Report report={report} /> : null}
          </div>
          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
            {phase === "running" ? (
              <Button variant="secondary" size="sm" disabled={stopping} onClick={close}>
                Cancel
              </Button>
            ) : phase === "done" ? (
              <Button size="sm" onClick={close}>
                Done
              </Button>
            ) : (
              <>
                <Button variant="secondary" size="sm" disabled={phase === "reading"} onClick={close}>
                  Cancel
                </Button>
                <Button size="sm" icon="Upload" loading={phase === "reading"} disabled={!files.length || phase === "reading"} onClick={() => void start()}>
                  Import
                </Button>
              </>
            )}
          </footer>
        </div>
      ) : null}
    </Modal>
  );
}

function Report({ report }: { report: ImportReport }) {
  const lines: string[] = [];
  if (report.skippedFiles) {
    lines.push(
      `${report.skippedFiles} ${report.skippedFiles === 1 ? "file that is not a page was" : "files that are not pages were"} skipped. Attachments wait for a later release.`,
    );
  }
  if (report.images) lines.push(`${report.images} ${report.images === 1 ? "image became" : "images became"} text.`);
  if (report.linksRewritten) lines.push(`${report.linksRewritten} ${report.linksRewritten === 1 ? "link now points" : "links now point"} to an imported page.`);
  return (
    <div data-testid="kb-import-report" className="space-y-3">
      <h3 className="text-sm font-medium text-foreground">
        {report.cancelled ? "Import stopped" : "Import finished"}
      </h3>
      <ul className="space-y-1 text-xs">
        <li className="flex items-center gap-2">
          <Icon name="Check" className="h-3.5 w-3.5 shrink-0 text-success" />
          <span>
            Created {pages(report.created)} as {report.created === 1 ? "a draft" : "drafts"}.
          </span>
        </li>
        {report.cancelled ? (
          <li className="flex items-center gap-2 text-muted-foreground">
            <Icon name="Ban" className="h-3.5 w-3.5 shrink-0" />
            <span>You stopped it. {pages(report.notStarted)} not made.</span>
          </li>
        ) : null}
        {lines.map((line) => (
          <li key={line} className="flex items-center gap-2 text-muted-foreground">
            <Icon name="Info" className="h-3.5 w-3.5 shrink-0" />
            <span>{line}</span>
          </li>
        ))}
        {report.linkFailures ? (
          <li className="flex items-center gap-2 text-warning">
            <Icon name="TriangleAlert" className="h-3.5 w-3.5 shrink-0" />
            <span>{pages(report.linkFailures)} kept links that point to files. Edit them by hand.</span>
          </li>
        ) : null}
      </ul>
      {report.failed.length || report.skippedChildren.length ? (
        <div role="alert" className="space-y-1 text-xs">
          <p className="font-medium text-destructive">
            {report.failed.length} failed, {report.skippedChildren.length} skipped
          </p>
          <ul className="space-y-1">
            {report.failed.map((f) => (
              <li key={`f:${f.title}:${f.reason}`} className="text-muted-foreground">
                <span className="text-foreground">{f.title}</span>. {f.reason}
              </li>
            ))}
            {report.skippedChildren.map((f) => (
              <li key={`s:${f.title}`} className="text-muted-foreground">
                <span className="text-foreground">{f.title}</span>. {f.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
