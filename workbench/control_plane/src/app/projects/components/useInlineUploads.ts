"use client";

/**
 * Projects · paste and drop files into a task (WS-57 TR-4).
 *
 * `TaskBody` mounts this once. The description field and the comment
 * composer hand it their text through a `TextTarget`, and the card hands it
 * a drop with no target. The rules (which file is an image, the placeholder,
 * the swap) live in `lib/inlineUploads.ts`, which is pure and tested. This
 * hook only wires them to the attachment route and the toast.
 *
 * - An image into a field: a placeholder at the caret, the upload, then the
 *   image Markdown in place of the placeholder.
 * - Any other file, or any file dropped on the card: the upload only.
 * - A failure removes the placeholder and shows the route's message.
 *
 * ⚠️ **One upload at a time, across every paste.** Each upload writes a
 * timeline row, and the gateway checks the task's 10 MB budget under a lock.
 * A chain keeps the rows in order and the refusals readable.
 *
 * ⚠️ **The task can change under an upload.** The file still goes to the task
 * it was dropped on. The text swap does not run when another task is open,
 * because the field then holds the other task's text.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ClipboardEvent, DragEvent } from "react";

import { useToast } from "@/components/ui/Toast";

import { attachmentsApi } from "../lib/api";
import {
  imageMarkdown,
  insertAtCaret,
  onOwnLine,
  partitionFiles,
  pastePlan,
  replaceFirst,
  uploadPlaceholder,
} from "../lib/inlineUploads";

/** A text field that an image may go into. */
export interface TextTarget {
  /** The text as it is now. Read when the upload ends, never earlier. */
  read: () => string;
  /** Write new text into the field's state. */
  write: (next: string) => void;
  /** The field's element, for the caret. */
  box: () => HTMLTextAreaElement | null;
  /** Runs after each swap, with the new text. The description saves here. */
  settled?: (next: string) => void;
}

interface Options {
  taskId: string;
  /** Runs after each batch, to read the Files list and the timeline again. */
  onUploaded: () => void;
}

/** True when a drag carries files, not text. */
function carriesFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

export function useInlineUploads({ taskId, onUploaded }: Options) {
  const toast = useToast();
  //: The names going up now, for the Files list's "uploading" rows.
  const [pending, setPending] = useState<string[]>([]);
  const chain = useRef<Promise<void>>(Promise.resolve());
  const currentTask = useRef(taskId);
  const uploaded = useRef(onUploaded);

  useEffect(() => {
    currentTask.current = taskId;
  }, [taskId]);
  useEffect(() => {
    uploaded.current = onUploaded;
  }, [onUploaded]);

  const attach = useCallback(
    (files: File[], target?: TextTarget) => {
      if (files.length === 0) return;
      const forTask = taskId;
      const { images, others } = target
        ? partitionFiles(files)
        : { images: [] as File[], others: files };

      // The placeholders go in NOW, at the caret, so the member sees where
      // each image will land while it uploads.
      if (target && images.length) {
        const box = target.box();
        const text = target.read();
        const start = box ? box.selectionStart : text.length;
        const end = box ? box.selectionEnd : text.length;
        const insertion = onOwnLine(
          text,
          start,
          end,
          images.map((f) => uploadPlaceholder(f.name)).join("\n"),
        );
        const next = insertAtCaret(text, start, end, insertion);
        target.write(next.value);
        requestAnimationFrame(() => {
          box?.focus();
          box?.setSelectionRange(next.caret, next.caret);
        });
      }

      const names = files.map((f) => f.name);
      setPending((now) => [...now, ...names]);

      const run = async () => {
        let changed = false;
        for (const file of [...images, ...others]) {
          const inline = images.includes(file);
          const token = uploadPlaceholder(file.name);
          try {
            const row = await attachmentsApi.upload(forTask, file);
            changed = true;
            if (target && inline && currentTask.current === forTask) {
              const next = replaceFirst(target.read(), token, imageMarkdown(file.name, row));
              target.write(next);
              target.settled?.(next);
            }
          } catch (err) {
            if (target && inline && currentTask.current === forTask) {
              const next = replaceFirst(target.read(), token, "");
              target.write(next);
              target.settled?.(next);
            }
            toast.show({
              variant: "error",
              title: `Couldn't attach ${file.name}`,
              description: String((err as Error)?.message ?? err),
            });
          } finally {
            setPending((now) => {
              const at = now.indexOf(file.name);
              return at < 0 ? now : [...now.slice(0, at), ...now.slice(at + 1)];
            });
          }
        }
        if (changed && currentTask.current === forTask) uploaded.current();
      };
      chain.current = chain.current.then(run, run);
    },
    [taskId, toast],
  );

  /** The `onPaste` of a field. A text paste is left to the browser. */
  const pasteInto = useCallback(
    (e: ClipboardEvent<HTMLTextAreaElement>, target: TextTarget) => {
      const data = e.clipboardData;
      if (!data) return;
      const plan = pastePlan(data.getData("text/plain"), Array.from(data.files));
      if (!plan) return;
      e.preventDefault();
      attach([...plan.images, ...plan.others], target);
    },
    [attach],
  );

  /** `onDragOver` for a field or the card: accept files, ignore text. */
  const allowDrop = useCallback((e: DragEvent) => {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  /**
   * The `onDrop` of a field (with a target) or of the card (without one).
   * Without this, a file dropped on the page opens in the tab and the task is
   * gone. A field's drop stops here, so the card does not attach it twice.
   */
  const dropInto = useCallback(
    (e: DragEvent, target?: TextTarget) => {
      if (!carriesFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      attach(Array.from(e.dataTransfer.files), target);
    },
    [attach],
  );

  return { pending, pasteInto, allowDrop, dropInto };
}
