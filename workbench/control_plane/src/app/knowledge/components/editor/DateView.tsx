"use client";

/**
 * The date of the Knowledge editor (WS-61 KB-2p, §21.7). Docmost core has
 * no date node, so this view is built from the spec.
 *
 * A date reads as a quiet chip with a calendar glyph and its label. A writer
 * clicks it to open a small picker: the shared `Input` as a date field, and
 * Today. A pick writes the day and the label "19 Oct 2026" together. A new
 * date opens its picker by itself, as a new status does.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { NodeViewWrapper, type NodeViewProps } from "@tiptap/react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { BADGE_SHAPE } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";

import { dateLabel, isIsoDate, todayIso } from "../../lib/editor/date";
import EditorPopover from "./EditorPopover";

export default function DateView({ node, editor, updateAttributes, getPos }: NodeViewProps) {
  const date = isIsoDate(node.attrs.date) ? String(node.attrs.date) : "";
  const label = String(node.attrs.label || dateLabel(date));
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const field = useRef<HTMLInputElement | null>(null);
  const editable = editor.isEditable;

  useEffect(() => {
    const storage = editor.storage.kbDate;
    if (editable && storage?.autoOpen) {
      storage.autoOpen = false;
      setOpen(true);
    }
  }, [editor, editable]);

  useEffect(() => {
    if (open) window.setTimeout(() => field.current?.focus(), 0);
  }, [open]);

  const pick = (iso: string) => {
    if (isIsoDate(iso) && iso !== date) updateAttributes({ date: iso, label: dateLabel(iso) });
  };

  const close = useCallback(
    (caretAfter = false) => {
      setOpen(false);
      const pos = typeof getPos === "function" ? getPos() : undefined;
      if (caretAfter && typeof pos === "number") editor.chain().focus(pos + node.nodeSize).run();
      else editor.commands.focus();
    },
    [editor, getPos, node.nodeSize],
  );

  const chip = `${BADGE_SHAPE} kb-date bg-secondary px-1.5 text-secondary-foreground`;
  const content = (
    <>
      <Icon name="Calendar" size={12} aria-hidden />
      <span>{label}</span>
    </>
  );

  return (
    <NodeViewWrapper as="span" className="kb-date-wrap" data-drag-handle="">
      {editable ? (
        <button
          ref={setAnchor}
          type="button"
          className={`${chip} kb-date-button`}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={`Date: ${label}. Change it`}
          onClick={() => setOpen((o) => !o)}
        >
          {content}
        </button>
      ) : (
        <time dateTime={date || undefined} className={chip}>
          {content}
        </time>
      )}
      <EditorPopover anchor={anchor} open={open} onClose={() => close(false)} label="Date">
        <div className="space-y-2">
          <Input
            ref={field}
            type="date"
            aria-label="Date"
            value={date}
            onChange={(e) => pick(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                close(true);
              }
            }}
            className="w-full"
          />
          <div className="flex items-center justify-between gap-2">
            <Button variant="ghost" size="sm" onClick={() => pick(todayIso())}>
              Today
            </Button>
            <Button variant="secondary" size="sm" onClick={() => close(true)}>
              Done
            </Button>
          </div>
        </div>
      </EditorPopover>
    </NodeViewWrapper>
  );
}
