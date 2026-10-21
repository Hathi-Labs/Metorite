"use client";

/**
 * The KB-2p controls of the bubble menu (WS-61, `knowledge_base.md` §21.7):
 * the alignment, superscript and subscript, and the colour picker for the
 * text colour and the highlight. `FormatBubble` in `menus.tsx` mounts this
 * once, after the marks.
 *
 * The alignment and the colours open a small panel inside the bubble. The
 * panel is part of the bubble, so a press keeps the focus and the selection
 * in the editor (the bubble's row stops the mouse-down). Escape closes the
 * panel and leaves the bubble.
 *
 * ⚠️ Every swatch draws a colour NAME through its `.kb-text-color` or
 * `.kb-highlight` token in `globals.css`. No colour is written here, and the
 * picker never offers a free value (`lib/editor/colors.ts`).
 */
import { useEffect, useState } from "react";
import type { Editor } from "@tiptap/core";
import { useEditorState } from "@tiptap/react";

import Button from "@/components/ui/Button";

import { HIGHLIGHT_COLORS, TEXT_COLORS, colorLabel } from "../../lib/editor/colors";
import { type TextAlign } from "../../lib/editor/textAlign";

/** The alignments the panel offers. `null` is left, the default. */
export const ALIGN_CHOICES: ReadonlyArray<{ value: TextAlign | null; label: string; icon: string }> = [
  { value: null, label: "Align left", icon: "AlignLeft" },
  { value: "center", label: "Align centre", icon: "AlignCenter" },
  { value: "right", label: "Align right", icon: "AlignRight" },
  { value: "justify", label: "Justify", icon: "AlignJustify" },
];

type Panel = "align" | "color" | null;

export default function FormatExtras({ editor }: { editor: Editor }) {
  const [panel, setPanel] = useState<Panel>(null);
  const state = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      const block = e.isActive("heading") ? "heading" : "paragraph";
      return {
        align: (e.getAttributes(block).align as TextAlign | null | undefined) ?? null,
        sup: e.isActive("superscript"),
        sub: e.isActive("subscript"),
        color: (e.getAttributes("textColor").color as string | undefined) ?? null,
        highlight: (e.getAttributes("highlight").color as string | undefined) ?? null,
      };
    },
  });

  // A new selection closes the panel, and so does Escape.
  useEffect(() => {
    if (!panel) return;
    const close = () => setPanel(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      setPanel(null);
    };
    editor.on("selectionUpdate", close);
    window.addEventListener("keydown", onKey, true);
    return () => {
      editor.off("selectionUpdate", close);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [editor, panel]);

  const align = state?.align ?? null;
  const alignIcon = ALIGN_CHOICES.find((a) => a.value === align)?.icon ?? "AlignLeft";
  const toggle = (next: Panel) => setPanel((p) => (p === next ? null : next));

  return (
    <>
      <span className="mx-1 h-5 w-px bg-border" aria-hidden />
      <span className="kb-panel-anchor relative inline-flex">
        <Button
          variant="ghost"
          size="icon-sm"
          icon={alignIcon}
          aria-label="Alignment"
          title="Alignment"
          aria-haspopup="true"
          aria-expanded={panel === "align"}
          selected={panel === "align"}
          onClick={() => toggle("align")}
        />
        {panel === "align" ? (
          <span role="group" aria-label="Alignment" className="kb-bubble-panel flex gap-0.5">
            {ALIGN_CHOICES.map((a) => (
              <Button
                key={a.label}
                variant="ghost"
                size="icon-sm"
                icon={a.icon}
                aria-label={a.label}
                title={a.label}
                selected={align === a.value}
                onClick={() => {
                  editor.chain().focus().setTextAlign(a.value).run();
                  setPanel(null);
                }}
              />
            ))}
          </span>
        ) : null}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        icon="Superscript"
        aria-label="Superscript"
        title="Superscript"
        selected={Boolean(state?.sup)}
        onClick={() => editor.chain().focus().toggleSuperscript().run()}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        icon="Subscript"
        aria-label="Subscript"
        title="Subscript"
        selected={Boolean(state?.sub)}
        onClick={() => editor.chain().focus().toggleSubscript().run()}
      />
      <span className="kb-panel-anchor relative inline-flex">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Colour"
          title="Colour"
          aria-haspopup="true"
          aria-expanded={panel === "color"}
          selected={panel === "color" || Boolean(state?.color || state?.highlight)}
          onClick={() => toggle("color")}
        >
          <span className="kb-text-color kb-color-glyph" data-color={state?.color ?? undefined} aria-hidden>
            A
          </span>
        </Button>
        {panel === "color" ? (
          <ColorPanel
            color={state?.color ?? null}
            highlight={state?.highlight ?? null}
            onColor={(value) => editor.chain().focus().setTextColor(value).run()}
            onHighlight={(value) => editor.chain().focus().setHighlight(value).run()}
          />
        ) : null}
      </span>
    </>
  );
}

/** The two swatch grids: the text colour, then the highlight. */
function ColorPanel({
  color,
  highlight,
  onColor,
  onHighlight,
}: {
  color: string | null;
  highlight: string | null;
  onColor: (value: string | null) => void;
  onHighlight: (value: string | null) => void;
}) {
  return (
    <span className="kb-bubble-panel kb-bubble-panel-end kb-color-panel">
      <span className="kb-color-heading">Text colour</span>
      <span role="group" aria-label="Text colour" className="kb-color-grid">
        <Swatch label="Default text colour" selected={!color} onClick={() => onColor(null)}>
          <span aria-hidden>A</span>
        </Swatch>
        {TEXT_COLORS.map((c) => (
          <Swatch key={c.name} label={`${c.label} text`} selected={color === c.name} onClick={() => onColor(c.name)}>
            <span className="kb-text-color" data-color={c.name} aria-hidden>
              A
            </span>
          </Swatch>
        ))}
      </span>
      <span className="kb-color-heading">Highlight</span>
      <span role="group" aria-label="Highlight" className="kb-color-grid">
        <Swatch label="No highlight" selected={!highlight} onClick={() => onHighlight(null)}>
          <span className="kb-swatch-none" aria-hidden>
            A
          </span>
        </Swatch>
        {HIGHLIGHT_COLORS.map((c) => (
          <Swatch
            key={c.name}
            label={`${c.label} highlight`}
            selected={highlight === c.name}
            onClick={() => onHighlight(c.name)}
          >
            <span className="kb-highlight kb-swatch-mark" data-color={c.name} aria-hidden>
              A
            </span>
          </Swatch>
        ))}
      </span>
      {color && !TEXT_COLORS.some((c) => c.name === color) ? (
        <span className="kb-color-heading">This text has the colour {colorLabel(TEXT_COLORS, color)}.</span>
      ) : null}
    </span>
  );
}

function Swatch({
  label,
  selected,
  onClick,
  children,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button variant="ghost" size="icon-sm" aria-label={label} title={label} selected={selected} onClick={onClick}>
      {children}
    </Button>
  );
}
