"use client";

/**
 * Export a page or a space as files (WS-61 KB-2m, `knowledge_base.md` §21.7).
 *
 * A format (Markdown or HTML) and, for a page, "With sub-pages". It reads
 * through `listPages` and `getPage`, so only the pages the member can see are
 * in the file. One page with no sub-pages downloads as one file. Anything
 * else downloads as a zip, in the layout that the import reads. All logic is
 * in `lib/importExport/`.
 */

import { useId, useRef, useState } from "react";

import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import Modal from "@/components/ui/Modal";
import ProgressBar from "@/components/ui/ProgressBar";
import SelectButton from "@/components/ui/SelectButton";
import { useToast } from "@/components/ui/Toast";
import { saveBlob } from "@/lib/export";

import { getPage, listPages } from "../lib/api";
import { fileStem, type ExportFormat } from "../lib/importExport/exportPages";
import { runExport } from "../lib/importExport/runExport";
import { writeZip } from "../lib/importExport/zip";

export interface ExportTarget {
  spaceId: string;
  spaceName: string;
  /** The page to export, or `null` for the whole space. */
  page: { id: string; title: string } | null;
}

const FORMATS = [
  { value: "md", label: "Markdown (.md)" },
  { value: "html", label: "HTML (.html)" },
];

export default function ExportDialog({ target, onClose }: { target: ExportTarget | null; onClose: () => void }) {
  const toast = useToast();
  const cancelled = useRef(false);
  const formatField = useId();
  const [format, setFormat] = useState<ExportFormat>("md");
  const [withSub, setWithSub] = useState(true);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [error, setError] = useState<string | null>(null);

  function close() {
    if (busy) {
      cancelled.current = true;
      return;
    }
    setError(null);
    onClose();
  }

  async function start() {
    if (!target) return;
    cancelled.current = false;
    setBusy(true);
    setError(null);
    setProgress({ done: 0, total: 0 });
    try {
      // The HTML writer needs the editor's schema, so it loads only for HTML.
      const renderHtml = format === "html" ? (await import("../lib/importExport/html")).markdownToHtml : String;
      const out = await runExport(
        { spaceId: target.spaceId, root: target.page, withSub, format },
        { listPages: (id) => listPages(id), getPage: (id) => getPage(id) },
        renderHtml,
        { onProgress: (done, total) => setProgress({ done, total }), shouldCancel: () => cancelled.current },
      );
      if (out.cancelled) {
        toast.show({ key: "kb-export", title: "Export stopped" });
      } else if (!out.files.length) {
        setError("No page could be read, so there is nothing to export.");
        return;
      } else {
        const single = out.files.length === 1 && !out.files[0].path.includes("/");
        if (single) {
          const type = format === "md" ? "text/markdown;charset=utf-8" : "text/html;charset=utf-8";
          saveBlob(new Blob([out.files[0].text], { type }), out.files[0].path);
        } else {
          const zip = await writeZip(out.files);
          const name = `${fileStem(target.page?.title ?? target.spaceName)}.zip`;
          saveBlob(new Blob([zip as BlobPart], { type: "application/zip" }), name);
        }
        toast.show({
          key: "kb-export",
          variant: out.failed.length ? "error" : "success",
          title: `Exported ${out.pages} ${out.pages === 1 ? "page" : "pages"}`,
          description: out.failed.length
            ? `${out.failed.length} could not be read: ${out.failed.map((f) => f.title).join(", ")}`
            : undefined,
        });
      }
      onClose();
    } catch (err) {
      setError(`The export stopped. ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={target !== null}
      onClose={close}
      title={target?.page ? "Export page" : "Export space"}
      icon="Download"
      size="sm"
      closeLabel={busy ? "Stop" : "Close"}
    >
      {target ? (
        <div className="flex flex-col">
          <div className="space-y-3 p-4 text-sm">
            <p className="text-muted-foreground">
              {target.page ? (
                <>
                  Save <span className="font-medium text-foreground">{target.page.title}</span> as a file.
                </>
              ) : (
                <>
                  Save every page of <span className="font-medium text-foreground">{target.spaceName}</span> that you can see, as a zip.
                </>
              )}
            </p>
            <div className="space-y-1">
              <label htmlFor={formatField} className="block text-xs font-medium text-foreground">
                Format
              </label>
              <SelectButton
                id={formatField}
                label="Format"
                value={format}
                options={FORMATS}
                onChange={(v) => setFormat(v === "html" ? "html" : "md")}
                widthClass="w-full"
                disabled={busy}
              />
            </div>
            {target.page ? (
              <label className="flex items-center gap-2 text-xs text-foreground">
                <Checkbox checked={withSub} disabled={busy} onChange={(e) => setWithSub(e.target.checked)} />
                With sub-pages
              </label>
            ) : null}
            <p className="text-xs text-muted-foreground">Attachments and images are not in the file yet.</p>
            {busy ? (
              <div className="space-y-1">
                <ProgressBar
                  label="Export progress"
                  percent={progress.total ? (progress.done / progress.total) * 100 : 0}
                  detail={`${progress.done} of ${progress.total}`}
                />
              </div>
            ) : null}
            {error ? (
              <p role="alert" className="text-xs text-destructive">
                {error}
              </p>
            ) : null}
          </div>
          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
            <Button variant="secondary" size="sm" onClick={close}>
              {busy ? "Stop" : "Cancel"}
            </Button>
            <Button size="sm" icon="Download" loading={busy} disabled={busy} onClick={() => void start()}>
              Export
            </Button>
          </footer>
        </div>
      ) : null}
    </Modal>
  );
}
