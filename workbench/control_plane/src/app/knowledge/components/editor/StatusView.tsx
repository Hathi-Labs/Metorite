"use client";

/**
 * The status badge of the Knowledge editor (WS-61 KB-2p, §21.7).
 *
 * Adapted from Docmost (AGPL-3.0), apps/client/src/features/editor/components/status/status-view.tsx
 *
 * What it keeps from Docmost: a badge that a writer clicks to name it and
 * pick its colour, a popover that opens by itself on a new badge, Enter
 * that closes the popover and puts the caret after the badge, and a badge
 * with no text that goes away when the popover closes.
 *
 * What is ours: the look. The badge is the house badge shape with the chip
 * of `statusAccent.ts`, the one status vocabulary (AGENTS.md rule 4), and
 * the popover is `EditorPopover` with `Input` and `Button`. No colour is
 * written here.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { NodeViewWrapper, type NodeViewProps } from "@tiptap/react";

import Button from "@/components/ui/Button";
import { BADGE_SHAPE } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import { accentForHue, resolveHue } from "@/lib/statusAccent";

import { STATUS_COLORS, STATUS_COLOR_LABELS, STATUS_MAX, isStatusColor, type StatusColor } from "../../lib/editor/status";
import EditorPopover from "./EditorPopover";

/** The chip classes of a status colour, through the one status vocabulary. */
export function statusChip(color: unknown): string {
  return accentForHue(resolveHue({ color: isStatusColor(color) ? color : "gray" })).chip;
}

/** The dot of a status colour, for a swatch. */
function statusDot(color: StatusColor): string {
  return accentForHue(resolveHue({ color })).dot;
}

export default function StatusView({ node, editor, updateAttributes, deleteNode, getPos }: NodeViewProps) {
  const text = String(node.attrs.text ?? "");
  const color: StatusColor = isStatusColor(node.attrs.color) ? node.attrs.color : "gray";
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const input = useRef<HTMLInputElement | null>(null);
  const editable = editor.isEditable;

  // A badge the menu just inserted opens its popover, once.
  useEffect(() => {
    const storage = editor.storage.status;
    if (editable && storage?.autoOpen) {
      storage.autoOpen = false;
      setOpen(true);
    }
  }, [editor, editable]);

  useEffect(() => {
    if (open) window.setTimeout(() => input.current?.focus(), 0);
  }, [open]);

  const close = useCallback(
    (caretAfter = false) => {
      setOpen(false);
      if (!text.trim()) {
        deleteNode();
        editor.commands.focus();
        return;
      }
      const pos = typeof getPos === "function" ? getPos() : undefined;
      if (caretAfter && typeof pos === "number") editor.chain().focus(pos + node.nodeSize).run();
      else editor.commands.focus();
    },
    [text, deleteNode, editor, getPos, node.nodeSize],
  );

  const chip = `${BADGE_SHAPE} kb-status ${statusChip(color)}`;
  const shown = text || (editable ? "Set status" : "");

  return (
    <NodeViewWrapper as="span" className="kb-status-wrap" data-drag-handle="">
      {editable ? (
        <button
          ref={setAnchor}
          type="button"
          className={`${chip} kb-status-button`}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={text ? `Status: ${text}. Change it` : "Set the status"}
          onClick={() => setOpen((o) => !o)}
        >
          {shown}
        </button>
      ) : (
        <span className={chip}>{shown}</span>
      )}
      <StatusPopover
        open={open}
        anchor={anchor}
        onClose={() => close(false)}
        text={text}
        color={color}
        inputRef={input}
        onText={(value) => updateAttributes({ text: value.slice(0, STATUS_MAX) })}
        onColor={(value) => updateAttributes({ color: value })}
        onDone={() => close(true)}
      />
    </NodeViewWrapper>
  );
}

/** The popover: the text, then the six colours. */
function StatusPopover({
  open,
  anchor,
  onClose,
  text,
  color,
  inputRef,
  onText,
  onColor,
  onDone,
}: {
  open: boolean;
  anchor: HTMLElement | null;
  onClose: () => void;
  text: string;
  color: StatusColor;
  inputRef: React.RefObject<HTMLInputElement | null>;
  onText: (value: string) => void;
  onColor: (value: StatusColor) => void;
  onDone: () => void;
}) {
  return (
    <EditorPopover anchor={anchor} open={open} onClose={onClose} label="Status">
      <div className="space-y-2">
        <Input
          ref={inputRef}
          aria-label="Status text"
          placeholder="For example: Done"
          value={text}
          maxLength={STATUS_MAX}
          onChange={(e) => onText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onDone();
            }
          }}
          className="w-full"
        />
        <div role="group" aria-label="Status colour" className="flex flex-wrap gap-1">
          {STATUS_COLORS.map((c) => (
            <Button
              key={c}
              variant="ghost"
              size="icon-sm"
              aria-label={STATUS_COLOR_LABELS[c]}
              title={STATUS_COLOR_LABELS[c]}
              selected={c === color}
              onClick={() => onColor(c)}
            >
              <span className={`h-3 w-3 rounded-full ${statusDot(c)}`} aria-hidden />
            </Button>
          ))}
        </div>
      </div>
    </EditorPopover>
  );
}
