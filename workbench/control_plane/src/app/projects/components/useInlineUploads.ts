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
  holdsPending,
  imageMarkdown,
  insertAtCaret,
  onOwnLine,
  partitionFiles,
  pastePlan,
  settleUpload,
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

/** One upload in flight. `token` is its placeholder, when it has one. */
interface Pending {
  id: number;
  name: string;
  token: string | null;
}

/** The number in the next placeholder. Module-wide, so two bodies never
 *  hand out the same token. */
let nextUpload = 0;

export function useInlineUploads({ taskId, onUploaded }: Options) {
  const toast = useToast();
  //: Every upload in flight, for the Files list and the save guard.
  const [pending, setPending] = useState<Pending[]>([]);
  //: The placeholders still waited on, as of now. A late callback reads
  //: this, because the state it closed over is stale.
  const tokensNow = useRef(new Set<string>());
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

      // One entry per file. An image going into a field gets its OWN token,
      // so two uploads of one name never trade places.
      const entries: (Pending & { file: File })[] = [...images, ...others].map((file) => {
        const id = ++nextUpload;
        const token = target && images.includes(file) ? uploadPlaceholder(file.name, id) : null;
        return { id, name: file.name, token, file };
      });
      const tokens = entries.flatMap((e) => (e.token ? [e.token] : []));

      // The placeholders go in NOW, at the caret, so the member sees where
      // each image will land while it uploads.
      if (target && tokens.length) {
        const box = target.box();
        const text = target.read();
        const start = box ? box.selectionStart : text.length;
        const end = box ? box.selectionEnd : text.length;
        const insertion = onOwnLine(text, start, end, tokens.join("\n"));
        const next = insertAtCaret(text, start, end, insertion);
        target.write(next.value);
        requestAnimationFrame(() => {
          box?.focus();
          box?.setSelectionRange(next.caret, next.caret);
        });
      }

      for (const token of tokens) tokensNow.current.add(token);
      setPending((now) => [...now, ...entries.map(({ id, name, token }) => ({ id, name, token }))]);

      const run = async () => {
        let changed = false;
        for (const entry of entries) {
          const { file, token } = entry;
          const swap = (markdown: string | null) => {
            if (!target || !token || currentTask.current !== forTask) return;
            const next = settleUpload(target.read(), token, markdown);
            tokensNow.current.delete(token);
            target.write(next);
            target.settled?.(next);
          };
          try {
            const row = await attachmentsApi.upload(forTask, file);
            changed = true;
            swap(imageMarkdown(file.name, row));
          } catch (err) {
            swap(null);
            toast.show({
              variant: "error",
              title: `Couldn't attach ${file.name}`,
              description: String((err as Error)?.message ?? err),
            });
          } finally {
            if (token) tokensNow.current.delete(token);
            setPending((now) => now.filter((p) => p.id !== entry.id));
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

  const pendingTokens = pending.flatMap((p) => (p.token ? [p.token] : []));

  return {
    /** The names going up now, for the Files list's "uploading" rows. */
    pending: pending.map((p) => p.name),
    /** For a render: does the text hold a placeholder this client waits on? */
    waiting: (text: string) => holdsPending(text, pendingTokens),
    /** The same answer, as of now. For a handler or a late callback. */
    waitingNow: (text: string) => holdsPending(text, tokensNow.current),
    pasteInto,
    allowDrop,
    dropInto,
  };
}
